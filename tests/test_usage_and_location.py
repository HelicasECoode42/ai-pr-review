"""Usage/cache accounting, symbol location, and where publishing thresholds live."""

from __future__ import annotations

from src.analyzer.context_builder import build_review_context
from src.application.review import ReviewApplication
from src.models import (
    BudgetReport,
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


class _Snapshot:
    """Minimal frozen snapshot: one caller outside the diff, one inside."""

    base = "1" * 40
    head = "2" * 40

    def __init__(self):
        self.sources = {
            "src/a.py": "def foo(x):\n    return x\n",
            "src/other.py": "from a import foo\nfoo(1)\n",
        }

    def read(self, path, side="head"):
        return self.sources.get(path)

    def search(self, symbol, side="head", limit=6):
        return [("src/a.py", 1), ("src/other.py", 2)][:limit]


def test_contract_callers_are_pulled_in_and_changed_files_are_skipped():
    from src.analyzer.repository_context import EvidenceBundle
    from src.reviewer.grounded_review import _add_contract_callers

    files = [ChangedFile(filename="src/a.py", patch=CODE_PATCH)]
    bundle = EvidenceBundle(_Snapshot(), budget=6000)
    _add_contract_callers(bundle, _Snapshot(), files, suggestion("src/a.py", 11))
    kinds = {e["path"]: e["kind"] for e in bundle.excerpts}
    # The changed file is already visible in the diff; only the caller was added.
    assert kinds.get("src/other.py") == "candidate_caller"
    assert "src/a.py" not in kinds


def test_contract_caller_lookup_failure_is_recorded():
    from src.analyzer.repository_context import EvidenceBundle
    from src.reviewer.grounded_review import _add_contract_callers

    class Broken(_Snapshot):
        def search(self, symbol, side="head", limit=6):
            raise RuntimeError("search backend down")

    bundle = EvidenceBundle(Broken(), budget=6000)
    _add_contract_callers(bundle, Broken(), [ChangedFile(filename="src/a.py", patch=CODE_PATCH)],
                          suggestion("src/a.py", 11))
    assert [o["reason"] for o in bundle.omitted] == ["caller_search_failed"]


def test_documentation_candidates_do_not_trigger_caller_search():
    from src.analyzer.repository_context import EvidenceBundle
    from src.reviewer.grounded_review import _add_contract_callers

    files = [ChangedFile(filename="docs/x.md", patch=DOC_PATCH)]
    bundle = EvidenceBundle(_Snapshot(), budget=6000)
    _add_contract_callers(bundle, _Snapshot(), files, suggestion("docs/x.md", 1))
    assert bundle.excerpts == [] and bundle.omitted == []


def test_default_orchestration_is_auto():
    """The agent path is the default; `off` is the explicit legacy opt-out."""
    import inspect

    from src.cli.main import analyze

    info = inspect.signature(analyze).parameters["agent_mode"].default
    assert info.default == "auto"


class _MemSnapshot:
    base = "1" * 40
    head = "2" * 40

    def __init__(self, sources):
        self.sources = sources

    def read(self, path, side="head"):
        return self.sources.get(path)


def test_unit_coverage_counts_units_that_never_reached_the_prompt():
    from src.analyzer.plan_baseline import summarize_unit_coverage
    from src.analyzer.review_plan import build_review_plan

    patch = "@@ -1,1 +1,1 @@\n-old\n+new\n"
    sources = {"src/a.py": "value = 1\n", "src/b.py": "value = 2\n"}
    files = [ChangedFile(filename=p, patch=patch) for p in sources]
    plan = build_review_plan(_MemSnapshot(sources), files, list(sources), token_budget=1000)
    # Only src/a.py reached the prompt.
    ctx = build_review_context(
        PullRequest(repo="o/r", number=1, title="t"), files[:1], allow_remote_file_fetch=False
    )
    summary = summarize_unit_coverage(plan, ctx)
    assert summary["units_planned"] == 2
    assert summary["units_with_evidence_in_prompt"] == 1
    assert summary["units_not_reviewed"] == 1
    assert summary["unreviewed_paths_sample"] == ["src/b.py"]


def test_unit_accounting_is_skipped_when_the_prompt_fit():
    from src.reviewer.grounded_review import _unit_coverage

    ctx = build_review_context(
        PullRequest(repo="o/r", number=1, title="t"),
        [ChangedFile(filename="src/a.py", patch=CODE_PATCH)],
        allow_remote_file_fetch=False,
    )
    assert ctx.truncated is False
    assert _unit_coverage(None, [], ctx, {}) is None


def test_grounded_report_names_unreviewed_units_and_counts_them():
    from src.analyzer.context_builder import ReviewContext
    from src.reviewer.grounded_review import GroundedResult, build_grounded_report

    ctx = ReviewContext(
        text="## Patches\n", truncated=True, token_count=10, patch_token_budget=6000,
        budgets=BudgetReport(scope="build_review_context"),
    )
    result = GroundedResult(
        summary="ok", suggestions=[], pending=[], candidates=[], decisions=[],
        bundle={"omitted": [], "evicted": [], "excerpts": [], "snapshot": {}, "tokens": 0},
        discovery_calls=1, verification_calls=0,
        unit_coverage={
            "units_planned": 14, "units_with_evidence_in_prompt": 10,
            "units_fully_in_prompt": 8, "units_not_reviewed": 4,
            "unreviewed_paths_sample": ["docs/a.md", "src/b.py"],
        },
    )
    report = build_grounded_report(
        PullRequest(repo="o/r", number=1, title="t"),
        [ChangedFile(filename="src/a.py", patch=CODE_PATCH)], [], result, ctx,
    )
    assert report.metrics["units_planned"] == 14
    assert report.metrics["units_not_reviewed"] == 4
    assert any("4 of 14 semantic review unit(s) were not reviewed" in w
               for w in report.analysis_warnings)
    assert report.report_confidence == "partial"
