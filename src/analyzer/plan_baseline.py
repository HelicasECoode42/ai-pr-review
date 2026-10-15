"""Deterministic offline summary of a review plan; no model calls, no network.

The summary is the baseline artifact for chunk planning: it records how much of
the change reached a unit, what was left out and why, and how many budgets were
touched. It intentionally reports structure and accounting, never review quality.
"""

from __future__ import annotations

from src.analyzer.review_plan import ReviewPlan


def summarize_plan(plan: ReviewPlan) -> dict:
    """Return a stable, JSON-serializable view of one plan."""
    slices = [slice_ for unit in plan.units for slice_ in unit.slices]
    reason_counts: dict[str, int] = {}
    for omission in plan.budgets.omissions:
        reason_counts[omission.reason] = reason_counts.get(omission.reason, 0) + 1
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
        "omission_reasons": dict(sorted(reason_counts.items())),
        "budgets": [line.model_dump(mode="json") for line in plan.budgets.lines],
        "budget_over_limit": list(plan.budgets.over_limit),
        "cuts": plan.cuts.counts.model_dump(mode="json"),
        "limitations": list(plan.limitations),
    }
