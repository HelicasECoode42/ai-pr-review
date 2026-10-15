from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FrozenContract(BaseModel):
    """Immutable, closed contract shared by plans, contexts and reports."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class BudgetLine(FrozenContract):
    """One token budget inside one artifact.

    ``covers`` states what the number bounds and what it deliberately excludes,
    so two budgets can never be silently treated as the same budget.
    ``limit=None`` marks a measurement with no enforced cap. ``aggregate=False``
    marks a sub-cap that must not be added to a run-level total, such as a cap
    on one file's share of a larger context budget.
    """

    name: str
    limit: int | None = Field(default=None, ge=0)
    used: int = Field(ge=0)
    covers: str
    aggregate: bool = True


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


class RunBudgetReport(FrozenContract):
    """Every scoped budget of one review run, plus an optional global cap.

    The cap counts only the aggregate input budgets of the scopes named in
    ``capped_scopes``. It does not claim to bound provider-internal work such as
    prompt caching, and a run with no configured cap reports ``cap=None`` and
    ``over_cap=False`` instead of inventing a limit.
    """

    scope: str = "review_run"
    cap: int | None = Field(default=None, ge=0)
    capped_scopes: tuple[str, ...] = ()
    reports: tuple[BudgetReport, ...] = ()

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(report.scope for report in self.reports)

    @property
    def lines(self) -> tuple[BudgetLine, ...]:
        return tuple(line for report in self.reports for line in report.lines)

    @property
    def omissions(self) -> tuple[Omission, ...]:
        """All scoped omissions, deduplicated by (path, reason, hunk_id)."""
        seen: set[tuple[str, str, str | None]] = set()
        merged: list[Omission] = []
        for report in self.reports:
            for omission in report.omissions:
                key = (omission.path, omission.reason, omission.hunk_id)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(omission)
        return tuple(merged)

    @property
    def total_used(self) -> int:
        """Every aggregate input line of the capped scopes, sub-caps excluded.

        A line's own ``limit`` is irrelevant here: an input with no configured
        limit is still an input, and the run cap exists to bound the total.
        """
        scopes = set(self.capped_scopes)
        return sum(
            line.used
            for report in self.reports
            if report.scope in scopes
            for line in report.lines
            if line.aggregate
        )

    @property
    def over_cap(self) -> bool:
        return self.cap is not None and self.total_used > self.cap

    @property
    def over_limit(self) -> tuple[str, ...]:
        """Enforced per-line violations, prefixed with their scope."""
        return tuple(
            f"{report.scope}:{line.name}"
            for report in self.reports
            for line in report.lines
            if line.limit is not None and line.used > line.limit
        )


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FileStatus(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"
    RENAMED = "renamed"
    UNKNOWN = "unknown"


class PullRequest(BaseModel):
    repo: str
    number: int
    title: str
    body: str | None = None
    author: str | None = None
    base_ref: str | None = None
    base_sha: str | None = None
    head_ref: str | None = None
    head_sha: str | None = None
    html_url: str | None = None


class ChangedLine(BaseModel):
    file_path: str
    line: int
    content: str


class DiffHunk(BaseModel):
    file_path: str
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added_lines: list[ChangedLine] = Field(default_factory=list)
    removed_lines: list[str] = Field(default_factory=list)
    raw: str


class ChangedFile(BaseModel):
    filename: str
    status: FileStatus = FileStatus.UNKNOWN
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    patch: str | None = None
    previous_filename: str | None = None


class RiskFinding(BaseModel):
    file_path: str
    line: int | None = None
    severity: Severity
    rule_id: str
    title: str
    evidence: str
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)


class SkippedContextFile(BaseModel):
    file_path: str
    reason: str


class StepStatus(str, Enum):
    """Analysis completeness step status."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class CompletenessItem(BaseModel):
    """One row in the analysis completeness table."""
    item: str
    status: StepStatus
    detail: str


class ReviewMeta(BaseModel):
    """Metadata about this review run."""
    reviewed_commit: str | None = None
    trigger_event: str | None = None
    workflow_run_url: str | None = None
    updated_at: str | None = None
    review_mode: str = "full_pr"


class FixTrackingItem(BaseModel):
    """Tracks whether a previous review suggestion has been addressed."""
    previous_title: str
    file_path: str | None = None
    previous_line: int | None = None
    status: str = "unknown"  # fixed | still_present | unknown
    detail: str = ""


class ReviewSuggestion(BaseModel):
    file_path: str
    line: int | None = None
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    title: str
    reason: str
    recommendation: str
    source: str = "ai"
    evidence: list[str] = Field(default_factory=list)
    failure_scenario: str = ""
    # Where this suggestion sits in the code, so a reader can jump straight to
    # the function to change. `unavailable` always carries a reason.
    symbol: str | None = None
    symbol_status: str = "unavailable"  # resolved | unavailable | not_applicable
    symbol_reason: str | None = None


class ReviewReport(BaseModel):
    pr: PullRequest
    files: list[ChangedFile]
    summary: str
    risk_level: Severity
    rule_findings: list[RiskFinding] = Field(default_factory=list)
    suggestions: list[ReviewSuggestion] = Field(default_factory=list)
    used_ai: bool = False
    ai_failure_reason: str | None = None
    analysis_warnings: list[str] = Field(default_factory=list)
    context_truncated: bool = False
    context_token_count: int = 0
    patch_token_budget: int = 0
    context_tokenizer: str = "not_used"
    hidden_suggestions_count: int = 0
    skipped_context_files: list[SkippedContextFile] = Field(default_factory=list)
    hidden_rule_findings_count: int = 0
    # Stage 15: 运行状态
    reviewer_version: str = "pr-branch"  # "pr-branch" | "main-fallback"
    execution_status: str = "success"  # "success" | "degraded"
    degradation_reason: str | None = None
    report_confidence: str = "normal"  # "normal" | "fallback" | "partial" | "failed"
    # Stage 15: 分析完整性
    completeness: list[CompletenessItem] = Field(default_factory=list)
    pr_syntax_check_ok: bool = True
    review_meta: ReviewMeta = Field(default_factory=ReviewMeta)
    fix_tracking: list[FixTrackingItem] = Field(default_factory=list)
    confirmed_signals: list[dict] = Field(default_factory=list)
    dismissed_signals: list[dict] = Field(default_factory=list)  # rule findings the AI dismissed
    unresolved_signals: list[dict] = Field(default_factory=list)
    unverified_signals: list[dict] = Field(default_factory=list)
    evidence_rejections: list[dict] = Field(default_factory=list)
    critic_decisions: list[dict] = Field(default_factory=list)
    pending_suggestions: list[ReviewSuggestion] = Field(default_factory=list)
    stage_results: list[dict] = Field(default_factory=list)
    candidate_audit: list[dict] = Field(default_factory=list)
    behavior_verifications: list[dict] = Field(default_factory=list)
    repository_context: dict = Field(default_factory=dict)
    metrics: dict[str, int | float] = Field(default_factory=dict)
    # One run-level view of every scoped budget that shaped this review.
    budget_report: RunBudgetReport = Field(default_factory=RunBudgetReport)
