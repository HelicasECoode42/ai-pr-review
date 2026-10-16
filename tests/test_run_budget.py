"""One run-level view of the budgets that shaped a review."""

from __future__ import annotations

from src.analyzer.context_builder import ReviewContext
from src.analyzer.repository_context import EvidenceBundle
from src.models import (
    BudgetLine,
    BudgetReport,
    ChangedFile,
    Omission,
    PullRequest,
)
from src.reviewer.budget import budget_warnings, run_budget_report
from src.reviewer.engine import review_with_ai
from src.reviewer.grounded_review import GroundedResult, build_grounded_report


class SnapshotStub:
    base = "1" * 40
    head = "2" * 40

    def __init__(self, sources: dict[str, str]):
        self.sources = sources

    def read(self, path, side="head"):
        return self.sources.get(path)


class StubProvider:
    def complete_json(self, _system: str, _user: str) -> str:
        return '{"summary": "ok", "risk_level": "low", "suggestions": []}'


def context_report(used: int = 10) -> BudgetReport:
    return BudgetReport(
        scope="build_review_context",
        lines=(
            BudgetLine(name="patch_context", limit=6000, used=used, covers="diff in the prompt"),
            BudgetLine(
                name="per_file_patch",
                limit=750,
                used=750,
                covers="sub-cap on one file",
                aggregate=False,
            ),
        ),
        omissions=(Omission(path="src/a.py", reason="patch_clipped"),),
    )


def empty_pr() -> PullRequest:
    return PullRequest(repo="owner/repo", number=1, title="t")


def test_run_total_counts_only_aggregate_lines_of_capped_scopes():
    plan_report = BudgetReport(
        scope="build_review_plan",
        lines=(BudgetLine(name="diff_fragments_total", limit=None, used=6900, covers="planner"),),
    )
    report = run_budget_report((context_report(used=100), plan_report))
    # The planner scope is not capped, and the per-file sub-cap must not be added.
    assert report.total_used == 100
    assert report.scopes == ("build_review_context", "build_review_plan")


def test_omissions_merge_across_scopes_without_duplicates():
    evidence = BudgetReport(
        scope="repository_evidence",
        omissions=(
            Omission(path="src/a.py", reason="patch_clipped"),
            Omission(path="src/b.py", reason="evidence_budget_exhausted"),
        ),
    )
    report = run_budget_report((context_report(), evidence))
    keys = [(o.path, o.reason) for o in report.omissions]
    assert keys == [
        ("src/a.py", "patch_clipped"),
        ("src/b.py", "evidence_budget_exhausted"),
    ]


def test_no_cap_means_measured_but_not_enforced():
    unbounded = BudgetReport(
        scope="build_review_context",
        lines=(BudgetLine(name="patch_context", limit=None, used=999999, covers="diff"),),
    )
    report = run_budget_report((unbounded,))
    assert report.cap is None
    assert report.over_cap is False
    assert report.total_used == 999999
    assert report.over_limit == ()
    assert budget_warnings(report) == []


def test_over_cap_and_over_limit_are_separate_signals():
    over_limit_line = BudgetReport(
        scope="build_review_context",
        lines=(BudgetLine(name="patch_context", limit=50, used=60, covers="diff"),),
    )
    report = run_budget_report((over_limit_line,), cap=10)
    assert report.over_limit == ("build_review_context:patch_context",)
    assert report.over_cap is True
    warnings = budget_warnings(report)
    assert len(warnings) == 2
    assert "patch_context" in warnings[0] and "cap" in warnings[1]


def test_evidence_bundle_reports_its_own_budget_and_omissions():
    bundle = EvidenceBundle(
        SnapshotStub({"src/a.py": "def f():\n    return 1\n"}), budget=1000
    )
    bundle.add("src/a.py", 1)
    bundle.add("src/missing.py", 1)
    report = bundle.budget_report
    assert report.scope == "repository_evidence"
    assert report.lines[0].name == "evidence_excerpts"
    assert report.lines[0].limit == 1000
    assert report.lines[0].used == bundle.tokens > 0
    assert [o.reason for o in report.omissions] == ["source_unavailable"]


def test_plain_review_report_carries_the_run_ledger():
    ctx = ReviewContext(
        text="## Patches\n", truncated=False, token_count=10, patch_token_budget=6000,
        budgets=context_report(used=10),
    )
    report = review_with_ai(
        empty_pr(), [ChangedFile(filename="src/a.py")], [], StubProvider(), max_suggestions=5,
        review_context=ctx,
    )
    assert report.budget_report.scope == "review_run"
    assert report.budget_report.scopes == ("build_review_context",)
    assert report.budget_report.total_used == 10
    assert report.budget_report.over_cap is False
    assert not any("budget" in warning.lower() for warning in report.analysis_warnings)


def test_configured_cap_is_reported_and_warned():
    ctx = ReviewContext(
        text="## Patches\n", truncated=False, token_count=10, patch_token_budget=6000,
        budgets=context_report(used=10),
    )
    report = review_with_ai(
        empty_pr(), [ChangedFile(filename="src/a.py")], [], StubProvider(), max_suggestions=5,
        review_context=ctx, max_input_tokens=1,
    )
    assert report.budget_report.cap == 1
    assert report.budget_report.over_cap is True
    assert any("exceed the configured cap" in warning for warning in report.analysis_warnings)


def test_grounded_report_counts_evidence_and_patch_context_together():
    ctx = ReviewContext(
        text="## Patches\n", truncated=False, token_count=10, patch_token_budget=6000,
        budgets=context_report(used=100),
    )
    result = GroundedResult(
        summary="ok", suggestions=[], pending=[], candidates=[], decisions=[],
        bundle={"omitted": [], "evicted": [], "excerpts": [], "snapshot": {}, "tokens": 25},
        discovery_calls=1, verification_calls=0,
        evidence_budget=BudgetReport(
            scope="repository_evidence",
            lines=(BudgetLine(name="evidence_excerpts", limit=6000, used=25, covers="evidence"),),
        ),
    )
    report = build_grounded_report(
        empty_pr(), [ChangedFile(filename="src/a.py")], [], result, ctx, max_input_tokens=1000
    )
    assert report.budget_report.scopes == ("build_review_context", "repository_evidence")
    assert report.budget_report.total_used == 125
    assert report.budget_report.over_cap is False

    capped = build_grounded_report(
        empty_pr(), [ChangedFile(filename="src/a.py")], [], result, ctx, max_input_tokens=100
    )
    assert capped.budget_report.over_cap is True
    assert any("configured cap" in warning for warning in capped.analysis_warnings)
