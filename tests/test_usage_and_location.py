"""Usage/cache accounting, symbol location, and where publishing thresholds live."""

from __future__ import annotations

from src.analyzer.context_builder import build_review_context
from src.application.review import ReviewApplication
from src.models import (
    ChangedFile,
    PullRequest,
    ReviewReport,
    ReviewSuggestion,
    Severity,
)
from src.reviewer.suggestion_filter import filter_suggestions, locate_symbol
from src.reviewer.usage import summarize_usage
from src.utils.config import Settings

DOC_PATCH = "@@ -1,1 +1,1 @@\n-old text\n+new text\n"
CODE_PATCH = "@@ -10,3 +10,4 @@ def foo(x):\n ctx\n-old\n+new\n"


def suggestion(path: str, line: int) -> ReviewSuggestion:
    return ReviewSuggestion(
        file_path=path, line=line, severity=Severity.HIGH, confidence=0.9,
        title="t", reason="a sufficiently long reason", recommendation="fix it",
    )


def test_deepseek_style_cache_counters_are_summarized():
    summary = summarize_usage([
        {"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                   "prompt_cache_hit_tokens": 90, "prompt_cache_miss_tokens": 10}},
        {"usage": {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55,
                   "prompt_cache_hit_tokens": 50, "prompt_cache_miss_tokens": 0}},
    ])
    assert summary["usage_total_tokens"] == 175
    assert summary["usage_responses_known"] == 2
    assert summary["cache_hit_tokens"] == 140
    assert summary["cache_miss_tokens"] == 10
    assert summary["cache_hit_rate"] == 0.9333


def test_openai_style_cached_tokens_are_understood():
    summary = summarize_usage([
        {"usage": {"prompt_tokens": 100, "completion_tokens": 1, "total_tokens": 101,
                   "prompt_tokens_details": {"cached_tokens": 80}}},
    ])
    assert summary["cache_hit_tokens"] == 80
    assert summary["cache_miss_tokens"] == 20
    assert summary["cache_hit_rate"] == 0.8


def test_unknown_cache_state_is_not_reported_as_a_perfect_miss():
    summary = summarize_usage([
        {"usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}},
    ])
    assert "cache_hit_rate" not in summary
    assert summary["usage_total_tokens"] == 11


def test_incomplete_or_missing_usage_is_counted_not_zeroed():
    summary = summarize_usage([
        {"usage": {"prompt_tokens": 10}},
        {"usage": None},
        {},
    ])
    assert summary["usage_responses_known"] == 0
    assert summary["usage_responses_unknown"] == 3
    assert summary["usage_total_tokens"] == 0


def test_symbol_is_resolved_from_the_hunk_header():
    files = [ChangedFile(filename="src/a.py", patch=CODE_PATCH)]
    assert locate_symbol(files, "src/a.py", 11) == ("foo", "resolved", "diff_hunk_header")


def test_every_unavailable_symbol_names_a_reason():
    assert locate_symbol([], "src/missing.py", 1) == (
        None, "unavailable", "file_not_in_changed_set"
    )
    assert locate_symbol([ChangedFile(filename="a.py", patch=None)], "a.py", 1) == (
        None, "unavailable", "no_parseable_hunk"
    )
    assert locate_symbol([ChangedFile(filename="a.py", patch=CODE_PATCH)], "a.py", None) == (
        None, "unavailable", "no_line"
    )
    bare = [ChangedFile(filename="b.py", patch="@@ -1,1 +1,1 @@\n-a\n+b\n")]
    assert locate_symbol(bare, "b.py", 1) == (
        None, "unavailable", "hunk_header_has_no_context"
    )


def test_documentation_is_not_applicable_not_unavailable():
    files = [ChangedFile(filename="docs/x.md", patch=DOC_PATCH)]
    assert locate_symbol(files, "docs/x.md", 1) == (None, "not_applicable", "documentation")


def test_filter_suggestions_populates_the_symbol_fields():
    files = [ChangedFile(filename="src/a.py", patch=CODE_PATCH)]
    filtered = filter_suggestions([suggestion("src/a.py", 11)], files, 5)
    assert [s.symbol for s in filtered] == ["foo"]
    assert filtered[0].symbol_status == "resolved"


def test_documentation_does_not_consume_the_patch_budget():
    files = [
        ChangedFile(filename="docs/big.md", patch=DOC_PATCH),
        ChangedFile(filename="src/a.py", patch=CODE_PATCH),
    ]
    ctx = build_review_context(
        PullRequest(repo="o/r", number=1, title="t"), files, allow_remote_file_fetch=False
    )
    reasons = {entry.path: entry.reason for entry in ctx.coverage}
    assert reasons["docs/big.md"] == "documentation_only_change"
    assert reasons["src/a.py"] is None
    # The file list still names it; its diff no longer consumes patch budget.
    assert "+new text" not in ctx.text
    assert "@@ -10,3 +10,4 @@ def foo(x):" in ctx.text


class _Provider:
    def __init__(self, records):
        self.usage_records = records

    def close(self):
        pass


def test_application_stamps_usage_and_the_single_publish_threshold():
    provider = _Provider([
        {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
                   "prompt_cache_hit_tokens": 60, "prompt_cache_miss_tokens": 40}},
    ])
    report = ReviewReport(
        pr=PullRequest(repo="o/r", number=1, title="t"), files=[], summary="s",
        risk_level=Severity.LOW,
    )

    def engine(**kwargs):
        return report

    def factory(**kwargs):
        return provider

    application = ReviewApplication(
        Settings(publish_min_confidence=0.7, enable_critic=False),
        provider_factory=factory,
        engine=engine,
    )
    result = application.review(PullRequest(repo="o/r", number=1, title="t"), [], [])
    assert result is report
    assert result.metrics["usage_total_tokens"] == 150
    assert result.metrics["cache_hit_rate"] == 0.6
    assert result.metrics["publish_min_confidence"] == 0.7


def test_application_tolerates_a_provider_without_usage_records():
    class Bare:
        def close(self):
            pass

    report = ReviewReport(
        pr=PullRequest(repo="o/r", number=1, title="t"), files=[], summary="s",
        risk_level=Severity.LOW,
    )
    application = ReviewApplication(
        Settings(enable_critic=False), provider_factory=lambda **kw: Bare(),
        engine=lambda **kw: report,
    )
    result = application.review(PullRequest(repo="o/r", number=1, title="t"), [], [])
    assert result is report
    assert "usage_total_tokens" not in result.metrics
    assert result.metrics["publish_min_confidence"] == 0.80
