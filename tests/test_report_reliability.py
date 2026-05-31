"""Report reliability tests — verify all code paths produce a valid ReviewReport."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.models import (
    ChangedFile,
    FileStatus,
    PullRequest,
    ReviewReport,
    Severity,
)
from src.reviewer.engine import (
    build_diagnostic_report,
    build_rule_only_report,
    validate_report,
)


# ── Fixtures ────────────────────────────────────────────

@pytest.fixture
def sample_pr() -> PullRequest:
    return PullRequest(
        repo="owner/repo",
        number=1,
        title="Test PR",
        body="Test body",
        author="tester",
        base_ref="main",
        head_ref="feature",
        head_sha="abc123def456",
        html_url="https://github.com/owner/repo/pull/1",
    )


@pytest.fixture
def sample_files() -> list[ChangedFile]:
    return [
        ChangedFile(
            filename="src/main.py",
            status=FileStatus.MODIFIED,
            additions=5,
            deletions=2,
            patch="@@ -1,3 +1,5 @@\n+print(\"hello\")\n+print(\"world\")\n",
        ),
    ]


# ── Path coverage tests ────────────────────────────────

def test_ai_failure_still_produces_report(sample_pr: PullRequest) -> None:
    """Level 1→2 fallback: AI fails, rule-only report is generated."""
    report = build_rule_only_report(sample_pr, [], [])
    assert report is not None
    assert isinstance(report, ReviewReport)
    assert report.summary


def test_empty_pr_produces_diagnostic(sample_pr: PullRequest) -> None:
    """Empty PR (no files) still produces a report with summary."""
    report = build_rule_only_report(sample_pr, [], [])
    assert report.summary
    assert report.report_confidence in {"normal", "partial", "failed"}


def test_diagnostic_report_always_works(sample_pr: PullRequest) -> None:
    """Level 3: build_diagnostic_report always returns a valid report."""
    report = build_diagnostic_report(
        pr=sample_pr,
        files=None,
        error="Test error",
        language="en",
    )
    assert report is not None
    assert isinstance(report, ReviewReport)
    assert "Test error" in report.summary
    assert report.report_confidence == "failed"
    assert report.execution_status == "failed"


def test_diagnostic_report_chinese(sample_pr: PullRequest) -> None:
    """Level 3 in Chinese language."""
    report = build_diagnostic_report(
        pr=sample_pr,
        error="未知错误",
        language="zh",
    )
    assert "审查失败" in report.summary
    assert report.report_confidence == "failed"


# ── Validation tests ───────────────────────────────────

def test_validate_report_passes_good_report(sample_pr: PullRequest) -> None:
    """A well-formed report should have zero validation issues."""
    report = build_rule_only_report(sample_pr, [], [])
    issues = validate_report(report)
    assert len(issues) == 0, f"Unexpected issues: {issues}"


def test_validate_report_detects_missing_pr() -> None:
    """A report with empty PR metadata should trigger validation warnings."""
    report = build_rule_only_report(
        PullRequest(repo="", number=0, title=""),
        [],
        [],
    )
    issues = validate_report(report)
    assert any("PR metadata missing" in i for i in issues)


def test_validate_report_detects_confidence_inconsistency(
    sample_pr: PullRequest,
) -> None:
    """A report with used_ai=True but ai_failure_reason set."""
    report = build_rule_only_report(sample_pr, [], [])
    report.used_ai = True
    report.ai_failure_reason = "Something failed"
    issues = validate_report(report)
    assert any("Inconsistency" in i and "used_ai" in i for i in issues)


def test_validate_report_detects_missing_summary(
    sample_pr: PullRequest,
) -> None:
    """A report with suggestions but empty summary."""
    from src.models import ReviewSuggestion

    report = build_rule_only_report(sample_pr, [], [])
    report.summary = ""
    report.suggestions = [
        ReviewSuggestion(
            file_path="src/main.py",
            line=1,
            severity=Severity.HIGH,
            confidence=0.9,
            title="Test",
            reason="Test reason",
            recommendation="Test recommendation",
        ),
    ]
    issues = validate_report(report)
    assert any("summary is empty" in i for i in issues)


def test_validate_report_detects_unknown_confidence(
    sample_pr: PullRequest,
) -> None:
    """report_confidence with unknown value."""
    report = build_rule_only_report(sample_pr, [], [])
    report.report_confidence = "unknown_value"
    issues = validate_report(report)
    assert any("Unknown report_confidence" in i for i in issues)


def test_validate_report_detects_confidence_vs_syntax(
    sample_pr: PullRequest,
) -> None:
    """pr_syntax_check_ok=False but report_confidence=normal."""
    report = build_rule_only_report(sample_pr, [], [])
    # Manually set conflicting state after construction
    report.pr_syntax_check_ok = False
    report.report_confidence = "normal"
    issues = validate_report(report)
    assert any("pr_syntax_check" in i for i in issues)


# ── report_confidence path coverage ────────────────────

def test_report_confidence_is_always_set(sample_pr: PullRequest) -> None:
    """Every report path must set report_confidence to a valid value."""
    report = build_rule_only_report(sample_pr, [], [])
    assert report.report_confidence in {"normal", "fallback", "partial", "failed"}

    report2 = build_diagnostic_report(pr=sample_pr, error="test")
    assert report2.report_confidence in {"normal", "fallback", "partial", "failed"}


def test_report_confidence_normal(sample_pr: PullRequest) -> None:
    """Normal rule-only report -> confidence=normal."""
    report = build_rule_only_report(sample_pr, [], [])
    assert report.report_confidence == "normal"


def test_report_confidence_diagnostic_is_failed(sample_pr: PullRequest) -> None:
    """Diagnostic report -> confidence=failed."""
    report = build_diagnostic_report(pr=sample_pr, error="test")
    assert report.report_confidence == "failed"
