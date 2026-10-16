"""Assemble one run-level view of the budgets that shaped a review.

Scoped ledgers stay with the artifact that produced them; this module only
aggregates them, so a report can state one input total without pretending that
artifacts from different stages share a single limit.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.models import BudgetReport, RunBudgetReport

# Scopes that count toward the model-input cap: both end up in the prompt.
REVIEW_INPUT_SCOPES = ("build_review_context", "repository_evidence")


def run_budget_report(
    reports: Iterable[BudgetReport], cap: int | None = None
) -> RunBudgetReport:
    return RunBudgetReport(
        cap=cap, capped_scopes=REVIEW_INPUT_SCOPES, reports=tuple(reports)
    )


def budget_warnings(report: RunBudgetReport) -> list[str]:
    """Warnings a reader must see; empty when nothing was exceeded."""
    warnings: list[str] = []
    if report.over_limit:
        warnings.append("Enforced token budget exceeded: " + ", ".join(report.over_limit))
    if report.over_cap:
        warnings.append(
            f"Model input tokens {report.total_used} exceed the configured cap {report.cap}."
        )
    return warnings
