"""The outcome of a grounded review."""
from __future__ import annotations

from dataclasses import dataclass

from src.models import (
    BudgetReport,
    ReviewSuggestion,
)


@dataclass
class GroundedResult:
    summary: str
    suggestions: list[ReviewSuggestion]
    pending: list[ReviewSuggestion]
    candidates: list[dict]
    decisions: list[dict]
    bundle: dict
    discovery_calls: int
    verification_calls: int
    verification_failed: bool = False
    # This bundle's own budget ledger; kept out of the dataclass fields above so
    # existing positional callers keep working.
    evidence_budget: BudgetReport | None = None
    # Which planned units reached the prompt; None when the prompt fit.
    unit_coverage: dict | None = None
