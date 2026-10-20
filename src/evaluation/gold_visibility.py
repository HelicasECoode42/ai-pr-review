"""Offline S ledger: how much of each gold finding's line range was visible?

The funnel from the review plan is N -> S -> D -> P: gold findings (N), gold lines
shown to the model (S), gold found by the model (D), gold published (P). D and P
need paid model calls; S is free and is the first thing to measure, because a
gold bug that never entered the prompt cannot be found at any model quality.

Visibility comes from structured ranges only, never from prompt text:

* ``rendered_head_ranges``: head lines that made it into the patch section of the
  prompt (``ReviewContext.rendered_head_ranges`` / the review trace).
* ``evidence_refs``: frozen source excerpts whose head-line range reached the
  prompt (the evidence bundle / the review trace).

The unit is the gold line range, not just "any overlap". A 26-line finding of
which only 3 changed lines are visible is not adequate evidence, and a plain
overlap test would hide exactly the CrabTrap failure (the diff hunk was visible
while the helper's ``context.Background()`` line was not).

Matching is positional. It is a necessary condition, not proof the model read
or understood those lines.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.evaluation.gold_scope import (
    PR_SCOPE_POSITIVE,
    GoldContractError,
    validate_gold_findings,
)


def _normalize_path(value: Any) -> str:
    path = str(value or "").strip()
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def _as_ranges(value: Any) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if not value:
        return ranges
    for item in value:
        if isinstance(item, Mapping):
            start, end = item.get("start_line"), item.get("end_line")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            start, end = item
        else:
            continue
        if isinstance(start, bool) or isinstance(end, bool):
            continue
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start > end:
            start, end = end, start
        ranges.append((start, end))
    return ranges


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def _covered_lines(start: int, end: int, ranges: Iterable[tuple[int, int]]) -> set[int]:
    """Lines in ``[start, end]`` covered by at least one range."""
    covered: set[int] = set()
    for range_start, range_end in ranges:
        low, high = max(start, range_start), min(end, range_end)
        if low <= high:
            covered.update(range(low, high + 1))
    return covered


@dataclass(frozen=True)
class GoldVisibility:
    gold_index: int
    file: str
    start_line: int | None
    end_line: int | None
    tp_fp: str | None
    scope: str | None
    diff_visible: bool
    source_visible: bool
    visible_lines: int
    range_lines: int
    reason: str

    @property
    def coverage_ratio(self) -> float:
        if not self.range_lines:
            return 0.0
        return self.visible_lines / self.range_lines

    @property
    def fully_visible(self) -> bool:
        return bool(self.range_lines) and self.visible_lines >= self.range_lines

    @property
    def visible(self) -> bool:
        return self.visible_lines > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "gold_index": self.gold_index,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "tp_fp": self.tp_fp,
            "scope": self.scope,
            "diff_visible": self.diff_visible,
            "source_visible": self.source_visible,
            "visible_lines": self.visible_lines,
            "range_lines": self.range_lines,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "fully_visible": self.fully_visible,
            "reason": self.reason,
        }


def _line_range(finding: Mapping[str, Any]) -> tuple[int, int] | None:
    start = finding.get("start_line")
    end = finding.get("end_line", start)
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if start < 1 or end < 1:
        return None
    return (start, end) if start <= end else (end, start)


def _index_ranges(
    value: Any, *, head_side_only: bool = False
) -> dict[str, list[tuple[int, int]]]:
    by_path: dict[str, list[tuple[int, int]]] = {}
    for item in value or []:
        if not isinstance(item, Mapping):
            continue
        if head_side_only and str(item.get("side", "head")) != "head":
            continue
        path = _normalize_path(item.get("path"))
        if not path:
            continue
        by_path.setdefault(path, []).extend(_as_ranges([item]))
    return by_path


def gold_visibility(
    findings: Any,
    *,
    rendered_ranges: Mapping[str, Iterable[Sequence[int]]] | None = None,
    evidence_refs: Iterable[Mapping[str, Any]] | None = None,
) -> list[GoldVisibility]:
    """Classify every gold finding by how much of its line range reached the prompt."""
    gold = validate_gold_findings(findings)
    diff_by_path: dict[str, list[tuple[int, int]]] = {}
    for path, ranges in (rendered_ranges or {}).items():
        normalized = _normalize_path(path)
        if normalized:
            diff_by_path.setdefault(normalized, []).extend(_as_ranges(ranges))
    source_by_path = _index_ranges(evidence_refs, head_side_only=True)

    results: list[GoldVisibility] = []
    for index, finding in enumerate(gold):
        matched_range = _line_range(finding)
        path = _normalize_path(finding.get("file"))
        tp_fp = finding.get("tp_fp")
        scope = finding.get("scope")
        if not path or matched_range is None:
            results.append(GoldVisibility(
                index, path, None, None, tp_fp, scope, False, False, 0, 0,
                "unlocatable_gold_line",
            ))
            continue
        start, end = matched_range
        diff_covered = _covered_lines(start, end, diff_by_path.get(path, []))
        source_covered = _covered_lines(start, end, source_by_path.get(path, []))
        union = diff_covered | source_covered
        range_lines = end - start + 1
        visible_lines = len(union)
        diff_visible = bool(diff_covered)
        source_visible = bool(source_covered)
        if not visible_lines:
            reason = "not_rendered"
        elif visible_lines >= range_lines:
            reason = "fully_visible"
        else:
            reason = "partially_visible"
        results.append(GoldVisibility(
            index, path, start, end, tp_fp, scope, diff_visible, source_visible,
            visible_lines, range_lines, reason,
        ))
    return results


def visibility_from_trace(trace: Mapping[str, Any], findings: Any) -> list[GoldVisibility]:
    """Build visibility from a saved ``review_trace`` (no prompt bodies needed)."""
    if not isinstance(trace, Mapping):
        raise GoldContractError("review trace must be a mapping")
    coverage = trace.get("coverage") if isinstance(trace.get("coverage"), Mapping) else {}
    rendered_raw = (coverage.get("rendered_head_ranges") if isinstance(coverage, Mapping)
                    else None) or []
    rendered: dict[str, list[list[int]]] = {}
    for item in rendered_raw:
        if not isinstance(item, Mapping):
            continue
        path = _normalize_path(item.get("path"))
        if path:
            rendered.setdefault(path, []).extend(_as_ranges(item.get("ranges")))
    refs = trace.get("evidence_refs") if isinstance(trace.get("evidence_refs"), list) else []
    return gold_visibility(findings, rendered_ranges=rendered, evidence_refs=refs)


def summarize_visibility(
    results: Sequence[GoldVisibility], *, pr_scope_only: bool = True
) -> dict[str, Any]:
    """Aggregate S for the project recall denominator (PR-scope TPs by default).

    ``s_recall`` is the strict measure: fully visible gold ranges / denominator.
    ``any_visible_recall`` also counts partial ranges and is reported so the gap
    between "some changed line shown" and "the finding is adequately evidenced"
    does not disappear.
    """
    selected = [
        item for item in results
        if item.tp_fp == "tp" and (
            not pr_scope_only or item.scope in PR_SCOPE_POSITIVE
        )
    ]
    denominator = len(selected)
    fully_visible = [item for item in selected if item.fully_visible]
    partially_visible = [
        item for item in selected if item.visible and not item.fully_visible
    ]
    any_visible = [item for item in selected if item.visible]
    unlocatable = [item for item in selected if item.reason == "unlocatable_gold_line"]
    mean_coverage = (
        sum(item.coverage_ratio for item in selected) / denominator
        if denominator else None
    )
    return {
        "scope": "pr_scope_positive" if pr_scope_only else "all_true_positives",
        "gold_denominator": denominator,
        "fully_visible": len(fully_visible),
        "partially_visible": len(partially_visible),
        "any_visible": len(any_visible),
        "not_visible": denominator - len(any_visible),
        "unlocatable": len(unlocatable),
        "s_recall": round(len(fully_visible) / denominator, 4) if denominator else None,
        "any_visible_recall": (
            round(len(any_visible) / denominator, 4) if denominator else None
        ),
        "mean_line_coverage": round(mean_coverage, 4) if mean_coverage is not None else None,
        "findings": [item.as_dict() for item in selected],
    }
