"""Review a large diff unit by unit instead of truncating it.

A ReviewUnit is already a budget-sized, semantically grouped slice of the diff.
This module turns that decision into execution: build one prompt per unit from
the unit's own fragments, and merge the per-unit results back into one report.

Nothing here talks to a model: the caller supplies the per-unit runner, so the
whole path is testable offline and the engine keeps ownership of the provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.analyzer.context_builder import ReviewContext, count_tokens
from src.application.protocol import (
    BudgetLine,
    BudgetReport,
    ChunkCoverage,
    ChunkCoverageSummary,
    Omission,
)
from src.models import ChangedFile, ReviewSuggestion

UNIT_CONTEXT_SCOPE = "build_unit_context"

# Upper bound on concurrent unit reviews. The constraint is the provider's rate
# limit and the reviewer's budget, not this machine, so the cap stays small.
MAX_UNIT_WORKERS = 8
MAX_CHECKPOINT_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_CHECKPOINT_FILE_BYTES = 16 * 1024 * 1024
_checkpoint_write_lock = Lock()

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
    checkpoint_issues: list[str] = field(default_factory=list)

    @property
    def reviewed(self) -> list[UnitOutcome]:
        # A live model result remains reportable if saving its resume artifact
        # failed; that outcome is partial and must be retried on the next run.
        return [
            o for o in self.outcomes
            if o.status in {"reviewed", "checkpoint_failed", "resumed"}
        ]

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


class _GroundedCheckpointResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=100_000)
    suggestions: list[ReviewSuggestion] = Field(max_length=2_000)
    pending: list[ReviewSuggestion] = Field(max_length=2_000)
    candidates: list[dict[str, Any]] = Field(max_length=10_000)
    decisions: list[dict[str, Any]] = Field(max_length=10_000)
    bundle: dict[str, Any]
    discovery_calls: int = Field(ge=0)
    verification_calls: int = Field(ge=0)
    verification_failed: bool = False
    evidence_budget: BudgetReport | None = None
    unit_coverage: dict[str, Any] | None = None


class _GroundedCheckpointArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    result: _GroundedCheckpointResult


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _checkpoint_directory(path: Path) -> Path:
    directory = path.with_name(path.name + ".results")
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise OSError("checkpoint artifact directory must be a real directory")
    return directory


def _artifact_name(unit_id: str) -> str:
    # Do not use caller-controlled identifiers as filesystem paths.
    return f"{hashlib.sha256(unit_id.encode('utf-8')).hexdigest()}.json"


def _encode_result(result: Any) -> bytes:
    from src.reviewer.grounded_review import GroundedResult

    if not isinstance(result, GroundedResult):
        raise TypeError("unit checkpoint requires a GroundedResult")
    artifact = _GroundedCheckpointArtifact(
        schema_version=1,
        result=_GroundedCheckpointResult(
            summary=result.summary,
            suggestions=result.suggestions,
            pending=result.pending,
            candidates=result.candidates,
            decisions=result.decisions,
            bundle=result.bundle,
            discovery_calls=result.discovery_calls,
            verification_calls=result.verification_calls,
            verification_failed=result.verification_failed,
            evidence_budget=result.evidence_budget,
            unit_coverage=result.unit_coverage,
        ),
    )
    encoded = artifact.model_dump_json().encode("utf-8")
    if len(encoded) > MAX_CHECKPOINT_ARTIFACT_BYTES:
        raise ValueError("unit checkpoint artifact exceeds size limit")
    return encoded


def _decode_result(data: bytes) -> Any:
    from src.reviewer.grounded_review import GroundedResult

    if len(data) > MAX_CHECKPOINT_ARTIFACT_BYTES:
        raise ValueError("unit checkpoint artifact exceeds size limit")
    artifact = _GroundedCheckpointArtifact.model_validate_json(data)
    payload = artifact.result
    return GroundedResult(
        summary=payload.summary,
        suggestions=[ReviewSuggestion.model_validate(item) for item in payload.suggestions],
        pending=[ReviewSuggestion.model_validate(item) for item in payload.pending],
        candidates=payload.candidates,
        decisions=payload.decisions,
        bundle=payload.bundle,
        discovery_calls=payload.discovery_calls,
        verification_calls=payload.verification_calls,
        verification_failed=payload.verification_failed,
        evidence_budget=payload.evidence_budget,
        unit_coverage=payload.unit_coverage,
    )


def _write_artifact(path: Path, unit_id: str, result: Any) -> tuple[str, str]:
    directory = _checkpoint_directory(path)
    name = _artifact_name(unit_id)
    destination = directory / name
    data = _encode_result(result)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".unit-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return name, hashlib.sha256(data).hexdigest()
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name)


def load_checkpoint(
    path: Path | str | None, *, identity_hashes: dict[str, str]
) -> tuple[dict[str, Any], list[str]]:
    """Load only results whose typed artifact and exact run identity both match."""
    if not path or not identity_hashes:
        return {}, (["resume_disabled_missing_identity"] if path else [])
    location = Path(path)
    if not location.exists():
        return {}, []
    try:
        if location.is_symlink() or location.stat().st_size > MAX_CHECKPOINT_FILE_BYTES:
            return {}, ["checkpoint_file_unavailable_or_oversized"]
        raw = location.read_bytes()
        directory = _checkpoint_directory(location)
    except OSError:
        return {}, ["checkpoint_file_unavailable"]
    restored: dict[str, Any] = {}
    issues: list[str] = []
    for line_number, line in enumerate(raw.splitlines()):
        if not line or len(line) > 4096:
            if line:
                issues.append(f"checkpoint_record_{line_number}_oversized")
            continue
        try:
            record = json.loads(line)
            unit_id = record["unit_id"]
            if (not isinstance(record, dict) or not isinstance(unit_id, str)
                    or not unit_id or len(unit_id) > 128):
                raise ValueError("invalid checkpoint record")
            if record.get("status") != "reviewed":
                continue
            expected_identity = identity_hashes.get(unit_id)
            if expected_identity is None:
                continue
            if record.get("identity_hash") != expected_identity:
                issues.append(f"checkpoint_identity_mismatch:{unit_id}")
                continue
            name = _artifact_name(unit_id)
            if record.get("artifact") != name:
                raise ValueError("invalid artifact reference")
            artifact = directory / name
            if artifact.is_symlink() or not artifact.is_file():
                issues.append(f"checkpoint_artifact_missing:{unit_id}")
                continue
            if artifact.stat().st_size > MAX_CHECKPOINT_ARTIFACT_BYTES:
                issues.append(f"checkpoint_artifact_oversized:{unit_id}")
                continue
            data = artifact.read_bytes()
            if hashlib.sha256(data).hexdigest() != record.get("artifact_sha256"):
                issues.append(f"checkpoint_artifact_hash_mismatch:{unit_id}")
                continue
            restored[unit_id] = _decode_result(data)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError,
                ValidationError) as exc:
            issues.append(f"checkpoint_record_invalid:{type(exc).__name__}")
    return restored, issues


def append_checkpoint(
    path: Path | str | None,
    outcome: UnitOutcome,
    *,
    identity_hash: str,
    artifact: str,
    artifact_sha256: str,
) -> None:
    """Append result metadata only after its bounded artifact is durable."""
    if not path:
        return
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "unit_id": outcome.unit_id,
        "status": outcome.status,
        "attempts": outcome.attempts,
        "estimated_tokens": outcome.estimated_tokens,
        "identity_hash": identity_hash,
        "artifact": artifact,
        "artifact_sha256": artifact_sha256,
    }
    data = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > 4096:
        raise ValueError("unit checkpoint metadata exceeds size limit")
    with _checkpoint_write_lock:
        if location.is_symlink():
            raise OSError("checkpoint metadata path cannot be a symlink")
        current_size = location.stat().st_size if location.exists() else 0
        if current_size + len(data) > MAX_CHECKPOINT_FILE_BYTES:
            raise ValueError("unit checkpoint metadata exceeds file size limit")
        descriptor = os.open(location, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            remaining = memoryview(data)
            while remaining:
                written = os.write(descriptor, remaining)
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def review_by_units(
    plan,
    files: Sequence[ChangedFile],
    run_unit: Callable[[ReviewContext, list[ChangedFile]], Any],
    *,
    max_units: int | None = None,
    max_workers: int = 1,
    retry_backoff_seconds: float = 0.0,
    checkpoint_path: Path | str | None = None,
    checkpoint_identity: dict[str, Any] | None = None,
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

    identity_hashes: dict[str, str] = {}
    if checkpoint_identity is not None:
        try:
            _canonical_hash(checkpoint_identity)  # reject secrets/objects not representable as JSON
            for unit in units:
                identity_hashes[unit.unit_id] = _canonical_hash({
                    "run": checkpoint_identity,
                    "base": getattr(plan, "base", None),
                    "head": getattr(plan, "head", None),
                    "unit_id": unit.unit_id,
                    "slices": [
                        {
                            "path": s.path,
                            "hunk_id": s.hunk_id,
                            "fragment_index": s.fragment_index,
                            "fragment_sha256": hashlib.sha256(
                                s.fragment.encode("utf-8")
                            ).hexdigest(),
                        }
                        for s in unit.slices
                    ],
                })
        except (TypeError, ValueError):
            identity_hashes = {}
    completed, checkpoint_issues = load_checkpoint(
        checkpoint_path, identity_hashes=identity_hashes
    )
    summary.checkpoint_issues.extend(checkpoint_issues)
    issue_lock = Lock()

    def run(unit) -> UnitOutcome:
        outcome = UnitOutcome(
            unit_id=unit.unit_id, status="failed", estimated_tokens=unit.estimated_tokens
        )
        if unit.unit_id in completed:
            # Resume only when both the exact inputs and a typed result artifact exist.
            outcome.status = "resumed"
            outcome.result = completed[unit.unit_id]
            return outcome
        if cancel is not None and cancel.is_set():
            outcome.status = "cancelled"
            return outcome
        for attempt in range(1, max_attempts + 1):
            outcome.attempts = attempt
            try:
                result = run_unit(build_unit_context(unit), unit_files(unit, files))
                outcome.result = result
                outcome.error = None
                if checkpoint_path:
                    identity_hash = identity_hashes.get(unit.unit_id)
                    if identity_hash is None:
                        raise ValueError("checkpoint resume disabled: run identity unavailable")
                    artifact, artifact_sha256 = _write_artifact(
                        Path(checkpoint_path), unit.unit_id, result
                    )
                    append_checkpoint(
                        checkpoint_path,
                        UnitOutcome(
                            unit_id=outcome.unit_id,
                            status="reviewed",
                            attempts=outcome.attempts,
                            estimated_tokens=outcome.estimated_tokens,
                        ),
                        identity_hash=identity_hash,
                        artifact=artifact,
                        artifact_sha256=artifact_sha256,
                    )
                outcome.status = "reviewed"
                break
            except Exception as exc:  # bounded retry, then a recorded failure
                checkpoint_failure = outcome.result is not None
                outcome.status = "checkpoint_failed" if checkpoint_failure else "failed"
                outcome.error = (
                    f"checkpoint_failed: {type(exc).__name__}: {exc}"
                    if checkpoint_failure
                    else f"{type(exc).__name__}: {exc}"
                )
                if checkpoint_failure:
                    with issue_lock:
                        summary.checkpoint_issues.append(
                            f"checkpoint_write_failed:{unit.unit_id}"
                        )
                    break
                if attempt < max_attempts and retry_backoff_seconds:
                    time.sleep(retry_backoff_seconds * attempt)
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
    checkpoint_identity: dict[str, Any] | None = None,
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
                             checkpoint_identity=checkpoint_identity,
                             max_attempts=max_attempts, cancel=cancel)
    merged = merge_grounded_results([outcome.result for outcome in summary.reviewed])
    details = {
        **summary.counts(),
        "unit_ids_reviewed": [o.unit_id for o in summary.reviewed],
        "units_resumed": sum(o.status == "resumed" for o in summary.outcomes),
        # The merged GroundedResult retains every unit's original counters for
        # audit. These separate fields describe work performed by this process
        # so a resume does not report restored calls as fresh requests.
        "discovery_calls_this_run": sum(
            o.result.discovery_calls for o in summary.outcomes
            if o.status in {"reviewed", "checkpoint_failed"} and o.result is not None
        ),
        "verification_calls_this_run": sum(
            o.result.verification_calls for o in summary.outcomes
            if o.status in {"reviewed", "checkpoint_failed"} and o.result is not None
        ),
        "discovery_calls_restored": sum(
            o.result.discovery_calls for o in summary.outcomes
            if o.status == "resumed" and o.result is not None
        ),
        "verification_calls_restored": sum(
            o.result.verification_calls for o in summary.outcomes
            if o.status == "resumed" and o.result is not None
        ),
        "units_cancelled": sum(o.status == "cancelled" for o in summary.outcomes),
        "unit_attempts": sum(o.attempts for o in summary.outcomes),
        "unit_errors": [o.error for o in summary.failed if o.error],
        "unit_checkpoint_failed": sum(o.status == "checkpoint_failed" for o in summary.outcomes),
        "unit_checkpoint_issues": summary.checkpoint_issues,
        "unit_review": 1,
    }
    return merged, details
