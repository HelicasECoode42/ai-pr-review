"""Review a large diff unit by unit instead of truncating it.

A ReviewUnit is already a budget-sized, semantically grouped slice of the diff.
This module turns that decision into execution: build one prompt per unit from
the unit's own fragments, and merge the per-unit results back into one report.

Nothing here talks to a model: the caller supplies the per-unit runner, so the
whole path is testable offline and the engine keeps ownership of the provider.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

from src.analyzer.context_builder import ReviewContext, count_tokens
from src.application.protocol import (
    BudgetLine,
    BudgetReport,
    ChunkCoverage,
    ChunkCoverageSummary,
    Omission,
)
from src.models import ChangedFile

UNIT_CONTEXT_SCOPE = "build_unit_context"

# Upper bound on concurrent unit reviews. The constraint is the provider's rate
# limit and the reviewer's budget, not this machine, so the cap stays small.
MAX_UNIT_WORKERS = 8

# Measured on a real 251-file PR: 558,691 tokens over 9 reviewed units, i.e.
# ~4.7 model calls per unit with the prompt and evidence re-sent each round. Used
# only to refuse an obviously unaffordable dispatch before spending anything.
MEASURED_TOKENS_PER_UNIT = 62_077


def build_unit_context(unit) -> ReviewContext:
    """Build a prompt context containing exactly one unit's diff fragments.

    Units are bounded by the planner, so this context is never truncated: the
    reader can trust that every hunk inside it was visible to the model.
    """
    by_path: dict[str, list] = {}
    for slice_ in unit.slices:
        by_path.setdefault(slice_.path, []).append(slice_)
    parts: list[str] = []
    coverage: list[ChunkCoverage] = []
    for path in sorted(by_path):
        slices = sorted(by_path[path], key=lambda s: (s.hunk_index, s.fragment_index))
        parts.extend(["", f"### {path}", "```diff",
                      "\n".join(s.fragment for s in slices), "```"])
        hunk_ids = {s.hunk_id for s in slices}
        coverage.append(
            ChunkCoverage(
                path=path, mode="diff", status="included",
                hunks_total=len(hunk_ids), hunks_complete=len(hunk_ids),
            )
        )
    text = "\n".join(parts)
    tokens = count_tokens(text)
    return ReviewContext(
        text=text,
        truncated=False,
        token_count=tokens,
        patch_token_budget=unit.estimated_tokens,
        coverage=tuple(coverage),
        coverage_summary=ChunkCoverageSummary.from_entries(
            [entry.path for entry in coverage], tuple(coverage)
        ),
        budgets=BudgetReport(
            scope=UNIT_CONTEXT_SCOPE,
            lines=(
                BudgetLine(
                    name="unit_diff",
                    limit=unit.estimated_tokens,
                    used=unit.estimated_tokens,
                    covers="the unit's diff fragments as the planner measured them",
                ),
                BudgetLine(
                    name="unit_context_rendered",
                    limit=None,
                    used=tokens,
                    covers=(
                        "the rendered prompt block including file headers and code fences; "
                        "measured, not capped, and deliberately not charged to the planner budget"
                    ),
                ),
            ),
            omissions=tuple(
                Omission(path=entry.path, reason="unit_reviewed_separately")
                for entry in coverage
            ),
        ),
    )


def unit_files(unit, files: Sequence[ChangedFile]) -> list[ChangedFile]:
    """The changed files this unit touches, in the caller's order."""
    wanted = {slice_.path for slice_ in unit.slices}
    return [file for file in files if file.filename in wanted]


@dataclass
class UnitOutcome:
    unit_id: str
    # reviewed | failed | skipped_by_budget | skipped_by_cost | cancelled | resumed
    status: str
    estimated_tokens: int = 0
    error: str | None = None
    result: Any = None
    attempts: int = 0


@dataclass
class UnitReviewSummary:
    outcomes: list[UnitOutcome] = field(default_factory=list)
    remaining_units: int = 0

    @property
    def reviewed(self) -> list[UnitOutcome]:
        return [o for o in self.outcomes if o.status == "reviewed"]

    @property
    def failed(self) -> list[UnitOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    @property
    def units_planned(self) -> int:
        return len(self.outcomes) + self.remaining_units

    def counts(self) -> dict[str, int]:
        return {
            "units_planned": self.units_planned,
            "units_reviewed": len(self.reviewed),
            "units_failed": len(self.failed),
            "units_skipped_by_budget": self.remaining_units,
        }


TERMINAL_OK = {"reviewed", "resumed"}
TERMINAL_SKIPPED = {"skipped_by_budget", "skipped_by_cost", "cancelled"}


def load_checkpoint(path: Path | str | None) -> dict[str, str]:
    """Read a run's per-unit outcomes so a crashed run can be resumed."""
    if not path:
        return {}
    location = Path(path)
    if not location.exists():
        return {}
    done: dict[str, str] = {}
    for line in location.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn last line must not lose the rest of the checkpoint
        if isinstance(record, dict) and record.get("unit_id"):
            done[str(record["unit_id"])] = str(record.get("status", ""))
    return done


def append_checkpoint(path: Path | str | None, outcome: UnitOutcome) -> None:
    """Append one terminal outcome. Written per unit so a crash keeps progress."""
    if not path:
        return
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    with location.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "unit_id": outcome.unit_id, "status": outcome.status,
            "attempts": outcome.attempts, "error": outcome.error,
            "estimated_tokens": outcome.estimated_tokens,
        }, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def review_by_units(
    plan,
    files: Sequence[ChangedFile],
    run_unit: Callable[[ReviewContext, list[ChangedFile]], Any],
    *,
    max_units: int | None = None,
    max_workers: int = 1,
    retry_backoff_seconds: float = 0.0,
    checkpoint_path: Path | str | None = None,
    max_attempts: int = 2,
    cancel: Event | None = None,
) -> UnitReviewSummary:
    """Run one review per unit, bounded, recording every outcome.

    Units are independent, so they may run concurrently - the unit loop is I/O
    bound on the provider, and a large PR is only affordable this way. Three
    properties are kept regardless of concurrency:

    * **Deterministic output.** Outcomes are ordered by the plan's unit order,
      not by completion order, so two runs of the same input merge identically.
    * **Failure isolation.** A unit that raises is recorded as failed and does
      not abort the others, and does not take the pool down.
    * **Per-unit budgets.** Each unit gets its own context and evidence budget,
      so running units in parallel cannot silently pool or overspend a shared
      budget. `run_unit` must be safe to call from several threads; callers give
      each unit its own provider.

    Concurrency is capped at `MAX_UNIT_WORKERS` to bound provider rate limits and
    spend; callers should check estimated cost before raising it.
    """
    if max_workers < 1:
        raise ValueError(f"max_workers must be >= 1, got {max_workers}")
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    workers = min(max_workers, MAX_UNIT_WORKERS)
    summary = UnitReviewSummary()
    units = list(plan.units)
    if max_units is not None:
        summary.remaining_units = max(0, len(units) - max_units)
        units = units[:max_units]

    completed = load_checkpoint(checkpoint_path)

    def run(unit) -> UnitOutcome:
        outcome = UnitOutcome(
            unit_id=unit.unit_id, status="failed", estimated_tokens=unit.estimated_tokens
        )
        if completed.get(unit.unit_id) == "reviewed":
            # Resumed: a previous run already produced a terminal outcome for it.
            outcome.status = "resumed"
            return outcome
        if cancel is not None and cancel.is_set():
            outcome.status = "cancelled"
            append_checkpoint(checkpoint_path, outcome)
            return outcome
        for attempt in range(1, max_attempts + 1):
            outcome.attempts = attempt
            try:
                outcome.result = run_unit(build_unit_context(unit), unit_files(unit, files))
                outcome.status = "reviewed"
                outcome.error = None
                break
            except Exception as exc:  # bounded retry, then a recorded failure
                outcome.status = "failed"
                outcome.error = f"{type(exc).__name__}: {exc}"
                if attempt < max_attempts and retry_backoff_seconds:
                    time.sleep(retry_backoff_seconds * attempt)
        append_checkpoint(checkpoint_path, outcome)
        return outcome

    if workers > 1 and len(units) > 1:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="review-unit") as pool:
            outcomes = list(pool.map(run, units))
    else:
        outcomes = [run(unit) for unit in units]
    summary.outcomes.extend(outcomes)
    return summary


def _dedupe(items: list, key) -> list:
    seen: set = set()
    merged: list = []
    for item in items:
        identity = key(item)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)
    return merged


def _merge_bundles(bundles: list[dict]) -> dict:
    """Concatenate list-valued audit fields, sum counters, keep caps as-is."""
    merged: dict = {}
    for bundle in bundles:
        for key, value in bundle.items():
            if isinstance(value, list):
                merged.setdefault(key, [])
                merged[key].extend(value)
            elif isinstance(value, bool):
                merged.setdefault(key, value)
            elif isinstance(value, int) and not key.endswith(("_budget", "_limit")):
                merged[key] = merged.get(key, 0) + value
            else:
                merged.setdefault(key, value)
    return merged


def merge_grounded_results(results: Sequence[Any], *, summary_note: str = "") -> Any:
    """Merge per-unit GroundedResults into one, preserving every candidate."""
    from src.reviewer.grounded_review import GroundedResult

    live = [r for r in results if r is not None]
    if not live:
        return GroundedResult(
            summary=summary_note, suggestions=[], pending=[], candidates=[], decisions=[],
            bundle={}, discovery_calls=0, verification_calls=0,
            verification_failed=True,
        )
    key = lambda s: (s.file_path, s.line, s.title.casefold())  # noqa: E731
    candidates: list[dict] = []
    decisions: list[dict] = []
    for result in live:
        offset = len(candidates)
        for candidate in result.candidates:
            moved = dict(candidate)
            if isinstance(moved.get("candidate_id"), int):
                moved["candidate_id"] += offset
            candidates.append(moved)
        for decision in result.decisions:
            moved = dict(decision)
            if isinstance(moved.get("candidate_id"), int):
                moved["candidate_id"] += offset
            decisions.append(moved)
    budgets = [r.evidence_budget for r in live if r.evidence_budget]
    evidence_budget = None
    if budgets:
        from src.application.protocol import BudgetReport as _BudgetReport

        evidence_budget = _BudgetReport(
            scope=budgets[0].scope,
            lines=(
                BudgetLine(
                    name="evidence_excerpts",
                    limit=max((line.limit or 0) for b in budgets for line in b.lines),
                    used=sum(line.used for b in budgets for line in b.lines),
                    covers="frozen source excerpts across every reviewed unit",
                ),
            ),
            omissions=tuple(o for b in budgets for o in b.omissions),
        )
    summaries = [r.summary for r in live if getattr(r, "summary", "").strip()]
    return GroundedResult(
        summary=" ".join(dict.fromkeys(summaries)) if summaries else summary_note,
        suggestions=_dedupe([s for r in live for s in r.suggestions], key),
        pending=_dedupe([s for r in live for s in r.pending], key),
        candidates=candidates,
        decisions=decisions,
        bundle=_merge_bundles([r.bundle for r in live if isinstance(r.bundle, dict)]),
        discovery_calls=sum(r.discovery_calls for r in live),
        verification_calls=sum(r.verification_calls for r in live),
        verification_failed=any(r.verification_failed for r in live),
        evidence_budget=evidence_budget,
    )


def run_units_for_review(
    run_unit: Callable[[ReviewContext, list[ChangedFile]], Any],
    snapshot,
    files: Sequence[ChangedFile],
    *,
    token_budget: int,
    max_units: int,
    max_workers: int = 1,
    cost_ceiling_tokens: int = 0,
    checkpoint_path: Path | str | None = None,
    max_attempts: int = 2,
    cancel: Event | None = None,
) -> tuple[Any, dict]:
    """Plan the diff into units, review them, and merge into one result.

    The unit count is capped by the caller: reviewing every unit of a large PR is
    the expensive path, so it is bounded rather than open-ended.
    """
    from src.analyzer.review_plan import build_review_plan

    plan = build_review_plan(snapshot, files, [f.filename for f in files],
                             token_budget=max(128, token_budget))
    if cost_ceiling_tokens:
        planned = min(len(plan.units), max_units) if max_units else len(plan.units)
        estimate = planned * MEASURED_TOKENS_PER_UNIT
        if estimate > cost_ceiling_tokens:
            return None, {
                "unit_review_skipped_cost": 1,
                "units_planned": len(plan.units),
                "units_where_attempted": planned,
                "estimated_tokens": estimate,
                "cost_ceiling_tokens": cost_ceiling_tokens,
            }
    summary = review_by_units(plan, files, run_unit, max_units=max_units,
                             max_workers=max_workers, checkpoint_path=checkpoint_path,
                             max_attempts=max_attempts, cancel=cancel)
    merged = merge_grounded_results([outcome.result for outcome in summary.reviewed])
    details = {
        **summary.counts(),
        "unit_ids_reviewed": [o.unit_id for o in summary.reviewed],
        "units_resumed": sum(o.status == "resumed" for o in summary.outcomes),
        "units_cancelled": sum(o.status == "cancelled" for o in summary.outcomes),
        "unit_attempts": sum(o.attempts for o in summary.outcomes),
        "unit_errors": [o.error for o in summary.failed if o.error],
        "unit_review": 1,
    }
    return merged, details
