"""The N -> S -> D -> P review funnel.

The project goal is not "publish many comments" or "use many agents"; it is to
find real bugs with evidence at an acceptable cost. That is a funnel, and every
metric must name the layer it measures:

* N: PR-scope true positives in the official gold.
* S: gold whose head line range actually reached the prompt (selection).
* D: gold the model identified as a candidate, regardless of final verdict
  (discovery). Two measurements are reported:
    - ``d_identified``: positional match against published + pending candidates
      (cheap, but coarse: a candidate in the same file/line range may describe a
      different defect, so it can overcount).
    - ``d_semantic``: blind adjudication of each candidate against the gold
      message (accurate, but manual). It is the number to trust for a claim.
* P: gold that was published after verification (publication). Also positional
  until a semantic adjudication of published findings exists.

A loss at S cannot be fixed by a better model, and a loss at D cannot be fixed
by a stricter verifier. Keeping the layers separate is what makes an experiment
attributable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.evaluation.gold_scope import (
    PR_SCOPE_POSITIVE,
    GoldContractError,
    summarize_gold_scope,
    validate_gold_findings,
)
from src.evaluation.gold_visibility import GoldVisibility, summarize_visibility


def _normalize(path: Any) -> str:
    value = str(path or "").strip()
    return value[2:] if value.startswith(("a/", "b/")) else value


def _finding_range(item: Mapping[str, Any]) -> tuple[str, int, int] | None:
    path = _normalize(item.get("file") or item.get("file_path"))
    start = item.get("start_line", item.get("line"))
    end = item.get("end_line", start)
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if not path or start < 1 or end < 1:
        return None
    return (path, min(start, end), max(start, end))


def _gold_range(item: Mapping[str, Any]) -> tuple[str, int, int] | None:
    path = _normalize(item.get("file"))
    start, end = item.get("start_line"), item.get("end_line", item.get("start_line"))
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if not path or start < 1 or end < 1:
        return None
    return (path, min(start, end), max(start, end))


def match_gold_indexes(
    candidates: Iterable[Mapping[str, Any]], gold: Sequence[Mapping[str, Any]]
) -> set[int]:
    """Positional match: same file and overlapping head line range.

    One gold finding is matched at most once, and it is not reused across
    candidates, mirroring the ReviewBench raw-match rule used in the existing
    evaluation scripts. This is a pre-filter for semantic adjudication, not the
    D number to quote in a quality claim.
    """
    used: set[int] = set()
    for item in candidates:
        finding = _finding_range(item)
        if finding is None:
            continue
        path, start, end = finding
        for index, gold_item in enumerate(gold):
            if index in used:
                continue
            target = _gold_range(gold_item)
            if target is None:
                continue
            gold_path, gold_start, gold_end = target
            if path == gold_path and start <= gold_end and gold_start <= end:
                used.add(index)
                break
    return used


def overlapping_gold(
    candidate: Mapping[str, Any],
    gold: Sequence[Mapping[str, Any]],
    *,
    adjacency: int = 5,
) -> list[tuple[int, Mapping[str, Any]]]:
    """Gold findings a human should compare a candidate against.

    Exact overlap first; otherwise findings on the same file within ``adjacency``
    lines, so an off-by-a-few-lines candidate still gets the right gold message
    next to it during adjudication.
    """
    finding = _finding_range(candidate)
    if finding is None:
        return []
    path, start, end = finding
    exact: list[int] = []
    nearby: list[int] = []
    for index, gold_item in enumerate(gold):
        target = _gold_range(gold_item)
        if target is None or target[0] != path:
            continue
        _, gold_start, gold_end = target
        if start <= gold_end + adjacency and gold_start <= end + adjacency:
            nearby.append(index)
            if start <= gold_end and gold_start <= end:
                exact.append(index)
    return [(index, gold[index]) for index in (exact or nearby)]


@dataclass(frozen=True)
class FunnelRow:
    case_id: str
    gold_pr_scope: int
    s_fully_visible: int
    s_any_visible: int
    d_identified: int | None
    p_published: int | None
    published_findings: int
    pending_findings: int
    d_matched_indexes: tuple[int, ...] = ()
    p_matched_indexes: tuple[int, ...] = ()
    d_semantic: int | None = None
    d_semantic_indexes: tuple[int, ...] = ()

    @property
    def discovery_basis(self) -> str | None:
        if self.d_semantic is not None:
            return "semantic"
        if self.d_identified is not None:
            return "positional"
        return None

    @property
    def d_used(self) -> int | None:
        if self.d_semantic is not None:
            return self.d_semantic
        return self.d_identified

    @property
    def losses(self) -> dict[str, int | None]:
        if self.d_used is None or self.p_published is None:
            return {
                "not_selected": self.gold_pr_scope - self.s_fully_visible,
                "selected_not_identified": None,
                "identified_not_published": None,
            }
        # Clamp so an inconsistent artifact cannot produce a negative budget that
        # looks like a gain.
        selected_not_identified = max(0, self.s_fully_visible - self.d_used)
        identified_not_published = max(0, self.d_used - self.p_published)
        return {
            "not_selected": self.gold_pr_scope - self.s_fully_visible,
            "selected_not_identified": selected_not_identified,
            "identified_not_published": identified_not_published,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "gold_pr_scope": self.gold_pr_scope,
            "s_fully_visible": self.s_fully_visible,
            "s_any_visible": self.s_any_visible,
            "d_identified": self.d_identified,
            "d_semantic": self.d_semantic,
            "discovery_basis": self.discovery_basis,
            "p_published": self.p_published,
            "published_findings": self.published_findings,
            "pending_findings": self.pending_findings,
            "d_matched_indexes": list(self.d_matched_indexes),
            "p_matched_indexes": list(self.p_matched_indexes),
            "d_semantic_indexes": list(self.d_semantic_indexes),
            "losses": self.losses,
        }


def _semantic_indexes(
    adjudication: Mapping[str, Sequence[int | None]],
    findings: Sequence[Mapping[str, Any]],
    *,
    published_count: int,
    pending_count: int,
) -> set[int]:
    """Validate a candidate-by-candidate adjudication and return identified gold.

    Format: ``{"published": [gold_index|null, ...], "pending": [...]}``, aligned
    with the candidate order passed to ``funnel_row``. A ``null`` means the
    candidate does not describe any PR-scope gold finding.
    """
    pr_scope = {
        index for index, item in enumerate(findings)
        if item.get("tp_fp") == "tp" and item.get("scope") in PR_SCOPE_POSITIVE
    }
    identified: set[int] = set()
    for source, expected in (("published", published_count), ("pending", pending_count)):
        values = adjudication.get(source)
        if not isinstance(values, list):
            raise GoldContractError(f"adjudication is missing the {source!r} list")
        if len(values) != expected:
            raise GoldContractError(
                f"adjudication has {len(values)} {source} entries but the run has {expected}"
            )
        for value in values:
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise GoldContractError("adjudicated gold ids must be integers or null")
            if value not in pr_scope:
                raise GoldContractError(
                    f"adjudicated gold id {value} is not a PR-scope true positive"
                )
            identified.add(value)
    return identified


def funnel_row(
    case_id: str,
    gold: Any,
    *,
    visibility: GoldVisibility | Sequence[GoldVisibility],
    published: Sequence[Mapping[str, Any]] | None = None,
    pending: Sequence[Mapping[str, Any]] | None = None,
    adjudication: Mapping[str, Sequence[int | None]] | None = None,
) -> FunnelRow:
    findings = validate_gold_findings(gold)
    scope_summary = summarize_gold_scope(findings)
    results = (
        [visibility] if isinstance(visibility, GoldVisibility) else list(visibility)
    )
    visibility_summary = summarize_visibility(results)
    pr_scope_indexes = {
        index for index, item in enumerate(findings)
        if item.get("tp_fp") == "tp" and item.get("scope") in PR_SCOPE_POSITIVE
    }
    measured = published is not None or pending is not None
    published_list = list(published or ())
    pending_list = list(pending or ())
    published_matches = (
        match_gold_indexes(published_list, findings) & pr_scope_indexes
    )
    pending_matches = match_gold_indexes(pending_list, findings) & pr_scope_indexes
    identified = published_matches | pending_matches
    semantic_indexes: tuple[int, ...] = ()
    d_semantic: int | None = None
    if adjudication is not None:
        semantic_indexes = tuple(sorted(_semantic_indexes(
            adjudication, findings,
            published_count=len(published_list), pending_count=len(pending_list),
        )))
        d_semantic = len(semantic_indexes)
    return FunnelRow(
        case_id=case_id,
        gold_pr_scope=scope_summary["pr_scope_positive_denominator"],
        s_fully_visible=visibility_summary["fully_visible"],
        s_any_visible=visibility_summary["any_visible"],
        d_identified=len(identified) if measured else None,
        p_published=len(published_matches) if measured else None,
        published_findings=len(published_list),
        pending_findings=len(pending_list),
        d_matched_indexes=tuple(sorted(identified)) if measured else (),
        p_matched_indexes=tuple(sorted(published_matches)) if measured else (),
        d_semantic=d_semantic,
        d_semantic_indexes=semantic_indexes,
    )


def summarize_funnel(rows: Sequence[FunnelRow]) -> dict[str, Any]:
    gold = sum(row.gold_pr_scope for row in rows)
    s_full = sum(row.s_fully_visible for row in rows)
    s_any = sum(row.s_any_visible for row in rows)
    with_outcome = [row for row in rows if row.d_identified is not None]
    d_total = sum(row.d_identified or 0 for row in with_outcome)
    p_total = sum(row.p_published or 0 for row in with_outcome)
    with_semantic = [row for row in rows if row.d_semantic is not None]
    d_semantic_total = sum(row.d_semantic or 0 for row in with_semantic)
    return {
        "cases": len(rows),
        "gold_pr_scope": gold,
        "s_fully_visible": s_full,
        "s_any_visible": s_any,
        "d_identified": d_total if with_outcome else None,
        "d_semantic": d_semantic_total if with_semantic else None,
        "p_published": p_total if with_outcome else None,
        "s_recall": round(s_full / gold, 4) if gold else None,
        "d_recall": round(d_total / gold, 4) if with_outcome and gold else None,
        "d_semantic_recall": (
            round(d_semantic_total / gold, 4) if with_semantic and gold else None
        ),
        "p_recall": round(p_total / gold, 4) if with_outcome and gold else None,
        "losses": {
            "not_selected": gold - s_full,
            "selected_not_identified": (
                max(0, s_full - d_total) if with_outcome else None
            ),
            "identified_not_published": (
                max(0, d_total - p_total) if with_outcome else None
            ),
        },
        "rows": [row.as_dict() for row in rows],
    }
