"""Typed contracts for transport-independent execution and planning."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.models import ChangedFile, PullRequest, ReviewReport, RiskFinding


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
    report: ReviewReport | None = None
    markdown: str = ""
    json_report: str | dict = Field(default_factory=dict)


class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Coverage(FrozenContract):
    path: str
    hunk_id: str | None = None
    status: Literal["planned", "excluded", "unavailable"]
    reason: str | None = None


class BudgetLedger(FrozenContract):
    token_limit: int = Field(gt=0)
    estimated_tokens: int = Field(ge=0, default=0)
    calls: int = Field(ge=0, default=0)


class BudgetLine(FrozenContract):
    """One token budget inside one artifact.

    ``covers`` states what the number bounds and what it deliberately excludes,
    so two budgets can never be silently treated as the same budget.
    ``limit=None`` marks a measurement with no enforced cap.
    """

    name: str
    limit: int | None = Field(default=None, ge=0)
    used: int = Field(ge=0)
    covers: str


class Omission(FrozenContract):
    """Anything a budget, parser, policy or missing source kept out of an artifact."""

    path: str
    reason: str
    hunk_id: str | None = None
    detail: str | None = None


class BudgetReport(FrozenContract):
    """One artifact's budgets and omissions in a single auditable structure.

    ``scope`` names the artifact, because budgets from different stages are not
    interchangeable even when the unit and the field names match.
    """

    scope: str = ""
    lines: tuple[BudgetLine, ...] = ()
    omissions: tuple[Omission, ...] = ()

    @property
    def over_limit(self) -> tuple[str, ...]:
        """Names of enforced budgets that were exceeded; empty means none were."""
        return tuple(
            line.name for line in self.lines if line.limit is not None and line.used > line.limit
        )


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
