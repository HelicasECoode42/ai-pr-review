from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


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
    head_ref: str | None = None
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


class ReviewSuggestion(BaseModel):
    file_path: str
    line: int | None = None
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    title: str
    reason: str
    recommendation: str


class ReviewReport(BaseModel):
    pr: PullRequest
    files: list[ChangedFile]
    summary: str
    risk_level: Severity
    rule_findings: list[RiskFinding] = Field(default_factory=list)
    suggestions: list[ReviewSuggestion] = Field(default_factory=list)
    used_ai: bool = False
