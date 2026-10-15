"""Typed contracts for transport-independent execution and planning.

Budget and omission contracts live in `src.models` so the report contract can
carry a run-level view; they are re-exported here for the modules that already
import them from this path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.models import (
    BudgetLine,
    BudgetReport,
    ChangedFile,
    FrozenContract,
    Omission,
    PullRequest,
    ReviewReport,
    RiskFinding,
    RunBudgetReport,
)

__all__ = [
    "AgentContext",
    "BudgetLedger",
    "BudgetLine",
    "BudgetReport",
    "ChunkCoverage",
    "ChunkCoverageSummary",
    "Coverage",
    "FrozenContract",
    "Omission",
    "ReviewRun",
    "RunBudgetReport",
    "StageInput",
    "StageOutput",
]


class AgentContext(BaseModel):
    """Validated carrier while the existing traced tool registry is retained."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")
    repo: str
    pr_number: int
    language: str = "en"
    use_ai: bool = True
    strategy: Literal["rule_only", "one_shot_ai", "two_stage"] | None = None
    reviewer_version: str = "pr-branch"
    execution_status: str = "success"
    degradation_reason: str | None = None
    report_confidence: str = "normal"
    pr_syntax_ok: bool = True
    reviewed_commit: str | None = None
    trigger_event: str | None = None
    workflow_run_url: str | None = None
    review_mode: str = "full_pr"
    pr: PullRequest | None = None
    files: list[ChangedFile] = Field(default_factory=list)
    findings: list[RiskFinding] = Field(default_factory=list)
    cross_file_findings: list[RiskFinding] = Field(default_factory=list)
    # Analyses that could not finish; surfaced in the report, not just the log.
    analyzer_warnings: list[str] = Field(default_factory=list)
    report: ReviewReport | None = None
    markdown: str = ""
    json_report: str | dict = Field(default_factory=dict)


class Coverage(FrozenContract):
    path: str
    hunk_id: str | None = None
    status: Literal["planned", "excluded", "unavailable"]
    reason: str | None = None


class BudgetLedger(FrozenContract):
    token_limit: int = Field(gt=0)
    estimated_tokens: int = Field(ge=0, default=0)
    calls: int = Field(ge=0, default=0)


class ChunkCoverage(FrozenContract):
    """What happened to one changed file's diff while the prompt context was built.

    ``mode`` distinguishes a real diff fragment from whole-file substitution,
    because a substituted file has no per-hunk coverage at all and must not be
    counted as if its hunks had been reviewed.
    """

    path: str
    mode: Literal["diff", "substituted"] = "diff"
    status: Literal["included", "partial", "excluded"] = "included"
    hunks_total: int = Field(ge=0, default=0)
    hunks_complete: int = Field(ge=0, default=0)
    hunks_partial: int = Field(ge=0, default=0)
    hunks_omitted: int = Field(ge=0, default=0)
    reason: str | None = None


class ChunkCoverageSummary(FrozenContract):
    """Whether every changed file has an explicit coverage entry.

    ``complete`` only states that no file was left unexplained; it does not
    claim the diff was fully reviewed. ``hunks_*`` counts exclude substituted
    files, whose hunk-level coverage is unknown by construction.
    """

    changed_files: int = Field(ge=0, default=0)
    files_included: int = Field(ge=0, default=0)
    files_partial: int = Field(ge=0, default=0)
    files_excluded: int = Field(ge=0, default=0)
    substituted_files: int = Field(ge=0, default=0)
    hunks_total: int = Field(ge=0, default=0)
    hunks_complete: int = Field(ge=0, default=0)
    hunks_partial: int = Field(ge=0, default=0)
    hunks_omitted: int = Field(ge=0, default=0)
    unaccounted_files: tuple[str, ...] = ()
    complete: bool = False

    @classmethod
    def from_entries(
        cls, changed: list[str], entries: tuple[ChunkCoverage, ...]
    ) -> ChunkCoverageSummary:
        """Derive the summary, treating a missing or unprocessed entry as unaccounted."""
        by_path = {entry.path: entry for entry in entries}
        accounted = {
            path
            for path in changed
            if path in by_path and by_path[path].reason != "not_processed"
        }
        summary = cls(
            changed_files=len(changed),
            files_included=sum(by_path[p].status == "included" for p in accounted),
            files_partial=sum(by_path[p].status == "partial" for p in accounted),
            files_excluded=sum(by_path[p].status == "excluded" for p in accounted),
            substituted_files=sum(
                by_path[p].mode == "substituted" for p in accounted
            ),
            hunks_total=sum(
                by_path[p].hunks_total for p in accounted if by_path[p].mode == "diff"
            ),
            hunks_complete=sum(
                by_path[p].hunks_complete for p in accounted if by_path[p].mode == "diff"
            ),
            hunks_partial=sum(
                by_path[p].hunks_partial for p in accounted if by_path[p].mode == "diff"
            ),
            hunks_omitted=sum(
                by_path[p].hunks_omitted for p in accounted if by_path[p].mode == "diff"
            ),
            unaccounted_files=tuple(sorted(p for p in changed if p not in accounted)),
        )
        return summary.model_copy(update={"complete": not summary.unaccounted_files})


class ReviewRun(FrozenContract):
    run_id: str
    repo: str
    base: str
    head: str
    strategy_version: str


class StageInput(FrozenContract):
    run: ReviewRun
    stage: Literal["index", "plan", "discover", "verify", "report", "publish"]
    unit_id: str | None = None
    budget: BudgetLedger


class StageOutput(FrozenContract):
    stage: str
    status: Literal["success", "partial", "failed"]
    artifact_ref: str | None = None
    error: str | None = None
    coverage: tuple[Coverage, ...] = ()
