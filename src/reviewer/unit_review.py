"""Review a large diff unit by unit instead of truncating it.

A ReviewUnit is already a budget-sized, semantically grouped slice of the diff.
This module turns that decision into execution: build one prompt per unit from
the unit's own fragments, and merge the per-unit results back into one report.

Nothing here talks to a model: the caller supplies the per-unit runner, so the
whole path is testable offline and the engine keeps ownership of the provider.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
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
    status: str  # reviewed | failed | skipped
    estimated_tokens: int = 0
    error: str | None = None
    result: Any = None


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


def review_by_units(
    plan,
    files: Sequence[ChangedFile],
    run_unit: Callable[[ReviewContext, list[ChangedFile]], Any],
    *,
    max_units: int | None = None,
) -> UnitReviewSummary:
    """Run one review per unit, bounded, recording every outcome.

    A unit that raises is recorded as failed and does not abort the others: on a
    large PR, losing one unit must not lose the whole report.
    """
    summary = UnitReviewSummary()
    units = list(plan.units)
    if max_units is not None:
        summary.remaining_units = max(0, len(units) - max_units)
        units = units[:max_units]
    for unit in units:
        outcome = UnitOutcome(
            unit_id=unit.unit_id, status="skipped", estimated_tokens=unit.estimated_tokens
        )
        try:
            outcome.result = run_unit(build_unit_context(unit), unit_files(unit, files))
            outcome.status = "reviewed"
        except Exception as exc:  # one unit must not take the run down
            outcome.status = "failed"
            outcome.error = f"{type(exc).__name__}: {exc}"
        summary.outcomes.append(outcome)
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
