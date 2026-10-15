"""Deterministic offline summary of chunk planning and patch context; no model calls.

These summaries are the baseline artifacts for chunk handling: they record how
much of the change reached a unit or the prompt, what was left out and why, and
which budgets were touched. They intentionally report structure and accounting,
never review quality.
"""

from __future__ import annotations

from src.analyzer.context_builder import ReviewContext
from src.analyzer.review_plan import ReviewPlan


def _reason_histogram(reasons: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def summarize_plan(plan: ReviewPlan) -> dict:
    """Return a stable, JSON-serializable view of one plan."""
    slices = [slice_ for unit in plan.units for slice_ in unit.slices]
    return {
        "plan_version": plan.version,
        "base": plan.base,
        "head": plan.head,
        "token_budget": plan.token_budget,
        "units": len(plan.units),
        "slices": len(slices),
        "relationships": {
            "total": len(plan.relationships),
            "strong": sum(edge.strength == "strong" for edge in plan.relationships),
            "weak": sum(edge.strength == "weak" for edge in plan.relationships),
        },
        "fragment_line_mapping": {
            "slices": len(slices),
            "slices_with_a_line_span": sum(
                slice_.lines.new_start is not None or slice_.lines.old_start is not None
                for slice_ in slices
            ),
            "slices_cutting_a_diff_line": sum(
                slice_.lines.cuts_a_diff_line for slice_ in slices
            ),
        },
        "coverage": plan.coverage_summary.model_dump(mode="json"),
        "paths_in_units": sorted({slice_.path for slice_ in slices}),
        "omission_reasons": _reason_histogram(
            [omission.reason for omission in plan.budgets.omissions]
        ),
        "budgets": [line.model_dump(mode="json") for line in plan.budgets.lines],
        "budget_over_limit": list(plan.budgets.over_limit),
        "cuts": plan.cuts.counts.model_dump(mode="json"),
        "limitations": list(plan.limitations),
    }


def summarize_context(context: ReviewContext) -> dict:
    """Return a stable, JSON-serializable view of one production patch context."""
    summary = context.coverage_summary
    context_paths_with_diff = {
        entry.path
        for entry in context.coverage
        if entry.status in {"included", "partial"} and entry.mode == "diff"
    }
    return {
        "scope": context.budgets.scope,
        "status": context.status.value,
        "truncated": context.truncated,
        "changed_files": summary.changed_files,
        "files": {
            "included": summary.files_included,
            "partial": summary.files_partial,
            "excluded": summary.files_excluded,
            "substituted": summary.substituted_files,
            "with_diff_in_context": len(context_paths_with_diff),
            "unaccounted": list(summary.unaccounted_files),
        },
        "hunks": {
            "total": summary.hunks_total,
            "complete": summary.hunks_complete,
            "partial": summary.hunks_partial,
            "omitted": summary.hunks_omitted,
        },
        "complete": summary.complete,
        "coverage": [entry.model_dump(mode="json") for entry in context.coverage],
        "exclusion_reasons": _reason_histogram(
            [entry.reason or "unspecified" for entry in context.coverage if entry.reason]
        ),
        "omission_reasons": _reason_histogram(
            [omission.reason for omission in context.omissions]
        ),
        "token_count": context.token_count,
        "patch_token_budget": context.patch_token_budget,
        "budgets": [line.model_dump(mode="json") for line in context.budgets.lines],
        "budget_over_limit": list(context.budgets.over_limit),
    }


def compare_planner_and_context(plan_summary: dict, context_summary: dict) -> dict:
    """State the gap between what the planner planned and what the prompt got.

    Both sides read the same changed files, so the hunk totals are expected to
    match; a mismatch means the two paths disagree about the diff itself and is
    reported rather than smoothed over.
    """
    planned_paths = set(plan_summary["paths_in_units"])
    context_paths = {
        entry["path"]
        for entry in context_summary["coverage"]
        if entry.get("mode") == "diff" and entry.get("status") in {"included", "partial"}
    }
    return {
        "planned_hunks_total": plan_summary["coverage"]["parsed_hunks"],
        "context_hunks_total": context_summary["hunks"]["total"],
        "hunk_totals_match": (
            plan_summary["coverage"]["parsed_hunks"] == context_summary["hunks"]["total"]
        ),
        "planned_hunks": plan_summary["coverage"]["planned_hunks"],
        "context_hunks_complete": context_summary["hunks"]["complete"],
        "hunks_planned_not_complete_in_context": max(
            0,
            plan_summary["coverage"]["planned_hunks"] - context_summary["hunks"]["complete"],
        ),
        "changed_files": plan_summary["coverage"]["changed_files"],
        "files_planned_but_not_in_context": sorted(planned_paths - context_paths),
        "files_in_context_but_not_planned": sorted(context_paths - planned_paths),
        "note": (
            "The planner writes a standalone ReviewPlan; the production context builds a prompt. "
            "Planning a unit does not put it in front of the model, so these two numbers are "
            "reported side by side instead of being treated as one pipeline."
        ),
    }
