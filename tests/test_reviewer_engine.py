from __future__ import annotations

import pytest

from src.models import (
    ChangedFile,
    FileStatus,
    PullRequest,
    RiskFinding,
    ReviewSuggestion,
    Severity,
)
from src.reviewer.engine import (
    _filter_suggestions,
    _parse_model_payload,
    build_rule_only_report,
    review_with_ai,
)
from src.reviewer.prompt import build_user_prompt
from src.reviewer.provider import ProviderError


# ── fixtures ──────────────────────────────────────────────

@pytest.fixture
def sample_pr() -> PullRequest:
    return PullRequest(
        repo="test/repo",
        number=1,
        title="Add login",
        body="Implement OAuth flow",
        author="alice",
    )


@pytest.fixture
def sample_files() -> list[ChangedFile]:
    return [
        ChangedFile(
            filename="src/auth.py",
            status=FileStatus.MODIFIED,
            additions=5,
            deletions=2,
            patch="@@ -10,5 +10,8 @@ def login():\n ctx\n-old\n+new\n+extra",
        ),
    ]


# ── JSON parsing ──────────────────────────────────────────

def test_parse_valid_json() -> None:
    raw = '{"summary": "ok", "risk_level": "low", "suggestions": []}'
    payload = _parse_model_payload(raw)
    assert payload.summary == "ok"
    assert payload.risk_level == Severity.LOW


def test_parse_json_in_fenced_block() -> None:
    raw = 'Here is the result:\n\n```json\n{"summary": "s", "risk_level": "medium", "suggestions": []}\n```\n\nHope that helps.'
    payload = _parse_model_payload(raw)
    assert payload.summary == "s"
    assert payload.risk_level == Severity.MEDIUM


def test_parse_json_with_surrounding_text() -> None:
    raw = 'Analysis complete.\n{"summary": "x", "risk_level": "high", "suggestions": []}\nLet me know if you need more.'
    payload = _parse_model_payload(raw)
    assert payload.summary == "x"
    assert payload.risk_level == Severity.HIGH


def test_parse_invalid_json_raises() -> None:
    with pytest.raises(ValueError, match="invalid review JSON"):
        _parse_model_payload("not json at all")


def test_parse_malformed_json_in_block_raises() -> None:
    with pytest.raises(ValueError, match="invalid review JSON"):
        _parse_model_payload("```json\n{broken\n```")


# ── filtering ─────────────────────────────────────────────

def test_filter_drops_unchanged_line_suggestions() -> None:
    files = [
        ChangedFile(
            filename="src/app.py",
            status=FileStatus.MODIFIED,
            patch="@@ -1,3 +1,4 @@\n ctx\n+new_line\n tail",
        )
    ]
    suggestions = [
        ReviewSuggestion(
            file_path="src/app.py",
            line=2,  # this is the added line
            severity=Severity.MEDIUM,
            confidence=0.8,
            title="Check input",
            reason="...",
            recommendation="...",
        ),
        ReviewSuggestion(
            file_path="src/app.py",
            line=99,  # not in patch
            severity=Severity.HIGH,
            confidence=0.9,
            title="Bad",
            reason="...",
            recommendation="...",
        ),
    ]
    result = _filter_suggestions(suggestions, files, max_suggestions=10)
    assert len(result) == 1
    assert result[0].line == 2


def test_filter_drops_low_confidence() -> None:
    files = [
        ChangedFile(
            filename="src/app.py",
            status=FileStatus.MODIFIED,
            patch="@@ -1,3 +1,4 @@\n ctx\n+new_line\n tail",
        )
    ]
    suggestions = [
        ReviewSuggestion(
            file_path="src/app.py",
            line=2,
            severity=Severity.LOW,
            confidence=0.5,
            title="Weak",
            reason="...",
            recommendation="...",
        ),
        ReviewSuggestion(
            file_path="src/app.py",
            line=2,
            severity=Severity.HIGH,
            confidence=0.9,
            title="Strong",
            reason="...",
            recommendation="...",
        ),
    ]
    result = _filter_suggestions(suggestions, files, max_suggestions=10, min_confidence=0.65)
    assert len(result) == 1
    assert result[0].title == "Strong"


def test_filter_deduplicates_same_line_title() -> None:
    files = [
        ChangedFile(
            filename="src/app.py",
            status=FileStatus.MODIFIED,
            patch="@@ -1,3 +1,4 @@\n ctx\n+new_line\n tail",
        )
    ]
    suggestions = [
        ReviewSuggestion(
            file_path="src/app.py",
            line=2,
            severity=Severity.MEDIUM,
            confidence=0.8,
            title="Same Issue",
            reason="a",
            recommendation="x",
        ),
        ReviewSuggestion(
            file_path="src/app.py",
            line=2,
            severity=Severity.HIGH,
            confidence=0.9,
            title="Same Issue",  # same title, same file, same line
            reason="b",
            recommendation="y",
        ),
    ]
    result = _filter_suggestions(suggestions, files, max_suggestions=10)
    assert len(result) == 1


def test_filter_sorts_by_severity_and_confidence() -> None:
    files = [
        ChangedFile(
            filename="src/app.py",
            status=FileStatus.MODIFIED,
            patch="@@ -1,3 +1,4 @@\n ctx\n+new_line\n tail",
        )
    ]
    suggestions = [
        ReviewSuggestion(
            file_path="src/app.py",
            line=2,
            severity=Severity.LOW,
            confidence=0.5,
            title="Low and weak",
            reason="...",
            recommendation="...",
        ),
        ReviewSuggestion(
            file_path="src/app.py",
            line=2,
            severity=Severity.CRITICAL,
            confidence=0.7,
            title="Critical",
            reason="...",
            recommendation="...",
        ),
    ]
    result = _filter_suggestions(suggestions, files, max_suggestions=10)
    assert result[0].severity == Severity.CRITICAL


def test_filter_limits_suggestions() -> None:
    files = [
        ChangedFile(
            filename="src/app.py",
            status=FileStatus.MODIFIED,
            patch="@@ -1,7 +1,10 @@\n ctx\n+line1\n+line2\n+line3\n+line4\n+line5\n tail",
        )
    ]
    suggestions = [
        ReviewSuggestion(
            file_path="src/app.py",
            line=i,
            severity=Severity.MEDIUM,
            confidence=0.8,
            title=f"Issue {i}",
            reason="...",
            recommendation="...",
        )
        for i in range(2, 7)
    ]
    result = _filter_suggestions(suggestions, files, max_suggestions=3)
    assert len(result) <= 3


# ── rule-only report ──────────────────────────────────────

def test_rule_only_report_no_findings(sample_pr: PullRequest) -> None:
    report = build_rule_only_report(sample_pr, [], [])
    assert report.used_ai is False
    assert "0 file" in report.summary


def test_rule_only_report_with_findings(sample_pr: PullRequest, sample_files: list[ChangedFile]) -> None:
    findings = [
        RiskFinding(
            file_path="src/auth.py",
            line=12,
            severity=Severity.HIGH,
            rule_id="secret-logging",
            title="Token logged",
            evidence="print(token)",
            recommendation="Remove log",
        )
    ]
    report = build_rule_only_report(sample_pr, sample_files, findings)
    assert report.used_ai is False
    assert "1 potential risk" in report.summary
    assert len(report.suggestions) == 1


# ── AI fallback ───────────────────────────────────────────

class FailingProvider:
    def complete_json(self, _system: str, _user: str) -> str:
        raise ProviderError("simulated failure")


def test_filter_enforces_per_file_cap() -> None:
    """Suggestions beyond max_suggestions_per_file are dropped from that file."""
    files = [
        ChangedFile(
            filename="src/app.py",
            status=FileStatus.MODIFIED,
            patch="@@ -1,7 +1,10 @@\n ctx\n+line1\n+line2\n+line3\n+line4\n+line5\n tail",
        )
    ]
    suggestions = [
        ReviewSuggestion(
            file_path="src/app.py",
            line=i,
            severity=Severity.MEDIUM,
            confidence=0.8,
            title=f"Issue {i}",
            reason="valid",
            recommendation="fix it",
        )
        for i in range(2, 8)  # 6 suggestions for same file
    ]
    result = _filter_suggestions(
        suggestions, files, max_suggestions=20, max_suggestions_per_file=3
    )
    assert len(result) == 3  # capped at 3 per file


def test_filter_drops_empty_reason() -> None:
    files = [
        ChangedFile(
            filename="src/app.py",
            status=FileStatus.MODIFIED,
            patch="@@ -1,3 +1,4 @@\n ctx\n+new_line\n tail",
        )
    ]
    suggestions = [
        ReviewSuggestion(
            file_path="src/app.py",
            line=2,
            severity=Severity.MEDIUM,
            confidence=0.8,
            title="Empty reason",
            reason="   ",  # whitespace only
            recommendation="ok",
        ),
        ReviewSuggestion(
            file_path="src/app.py",
            line=2,
            severity=Severity.HIGH,
            confidence=0.9,
            title="Valid",
            reason="real reason",
            recommendation="real fix",
        ),
    ]
    result = _filter_suggestions(suggestions, files, max_suggestions=10)
    assert len(result) == 1
    assert result[0].title == "Valid"


def test_filter_drops_empty_recommendation() -> None:
    files = [
        ChangedFile(
            filename="src/app.py",
            status=FileStatus.MODIFIED,
            patch="@@ -1,3 +1,4 @@\n ctx\n+new_line\n tail",
        )
    ]
    suggestions = [
        ReviewSuggestion(
            file_path="src/app.py",
            line=2,
            severity=Severity.MEDIUM,
            confidence=0.8,
            title="No fix",
            reason="real reason",
            recommendation="",  # empty
        ),
    ]
    result = _filter_suggestions(suggestions, files, max_suggestions=10)
    assert len(result) == 0


def test_review_falls_back_when_provider_fails(
    sample_pr: PullRequest, sample_files: list[ChangedFile]
) -> None:
    report = review_with_ai(
        sample_pr, sample_files, [], FailingProvider(), max_suggestions=10
    )
    assert report.used_ai is False
    assert report.ai_failure_reason is not None
    assert "simulated failure" in report.ai_failure_reason
    assert any("AI review unavailable" in w for w in report.analysis_warnings)


# ── prompt ─────────────────────────────────────────────────

def test_build_prompt_includes_context() -> None:
    result = build_user_prompt("diff goes here", max_suggestions=5)
    assert "diff goes here" in result
    assert "max_suggestions" not in result  # it's substituted
    assert "5" in result


def test_build_prompt_english() -> None:
    result = build_user_prompt("ctx", max_suggestions=3, language="en")
    assert "Chinese" not in result


def test_build_prompt_chinese() -> None:
    result = build_user_prompt("ctx", max_suggestions=3, language="zh")
    assert "Chinese (Simplified)" in result
