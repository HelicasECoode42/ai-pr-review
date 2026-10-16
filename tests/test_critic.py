from __future__ import annotations

from src.models import ChangedFile, FileStatus, PullRequest, Severity
from src.reviewer.engine import review_with_ai


class ReviewerThenCriticProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_json(self, _system: str, user: str) -> str:
        self.calls.append(user)
        if len(self.calls) == 1:
            return (
                '{"summary":"Review candidate.","risk_level":"high","suggestions":[{'
                '"file_path":"src/app.py","line":1,"severity":"high",'
                '"confidence":0.9,"title":"Cache collision",'
                '"reason":"The changed key omits tenant scope.",'
                '"recommendation":"Include tenant_id in the key.",'
                '"evidence":["cache.set(user_id, profile)"],'
                '"failure_scenario":"Two tenants sharing user_id receive the wrong profile."}]}'
            )
        return (
            '{"items":[{"suggestion_index":0,"decision":"dismissed",'
            '"confidence":0.88,"reason":"user_id is globally unique in this service."}]}'
        )


def test_critic_dismisses_evidence_grounded_candidate_and_lowers_risk() -> None:
    provider = ReviewerThenCriticProvider()
    files = [ChangedFile(
        filename="src/app.py", status=FileStatus.MODIFIED,
        patch="@@ -1 +1 @@\n+cache.set(user_id, profile)\n",
    )]
    report = review_with_ai(
        PullRequest(repo="demo/repo", number=1, title="Cache profile"),
        files, [], provider, max_suggestions=5, enable_critic=True,
    )

    assert len(provider.calls) == 2
    assert report.suggestions == []
    assert report.risk_level == Severity.LOW
    assert report.metrics["critic_request_count"] == 1
    assert report.metrics["critic_dismissed_count"] == 1
    assert report.critic_decisions[0]["decision"] == "dismissed"


def test_critic_is_not_called_when_disabled() -> None:
    provider = ReviewerThenCriticProvider()
    files = [ChangedFile(
        filename="src/app.py", status=FileStatus.MODIFIED,
        patch="@@ -1 +1 @@\n+cache.set(user_id, profile)\n",
    )]
    report = review_with_ai(
        PullRequest(repo="demo/repo", number=2, title="Cache profile"),
        files, [], provider, max_suggestions=5,
    )

    assert len(provider.calls) == 1
    assert len(report.suggestions) == 1
    assert report.metrics["critic_request_count"] == 0


def test_uncertain_candidate_survives_separately_without_becoming_confirmed() -> None:
    class UncertainProvider(ReviewerThenCriticProvider):
        def complete_json(self, system: str, user: str) -> str:
            return super().complete_json(system, user).replace('"dismissed"', '"uncertain"')

    report = review_with_ai(
        PullRequest(repo="demo/repo", number=1, title="Cache profile"),
        [ChangedFile(filename="src/app.py", status=FileStatus.MODIFIED,
                     patch="@@ -1 +1 @@\n+cache.set(user_id, profile)\n")],
        [], UncertainProvider(), max_suggestions=5, enable_critic=True,
    )
    assert report.suggestions == []
    assert len(report.pending_suggestions) == 1
    assert report.pending_suggestions[0].title == "Cache collision"
    assert report.pending_suggestions[0].source == "ai_needs_context"
    assert report.critic_decisions[0]["decision"] == "uncertain"
    from src.output.markdown import render_markdown
    assert "Candidates needing context" in render_markdown(report)


def test_truncated_success_is_reported_as_partial_coverage() -> None:
    from src.analyzer.context_builder import ReviewContext
    from src.models import StepStatus

    provider = ReviewerThenCriticProvider()
    files = [ChangedFile(filename="src/app.py", status=FileStatus.MODIFIED,
                         patch="@@ -1 +1 @@\n+cache.set(user_id, profile)\n")]
    report = review_with_ai(
        PullRequest(repo="demo/repo", number=1, title="Truncated"), files, [], provider, 5,
        review_context=ReviewContext(text="bounded patch", truncated=True, status=StepStatus.PARTIAL),
    )
    assert report.execution_status == "success"
    assert report.report_confidence == "partial"
    item = next(i for i in report.completeness if i.item == "AI 上下文文件")
    assert item.status == StepStatus.PARTIAL
    assert "全部文件进入上下文" not in item.detail


def test_critic_receives_changed_import_dependency_without_rule_conclusions():
    from src.reviewer.critic import _related_changed_modules

    candidate = ChangedFile(filename="src/evaluation/replay.py", status=FileStatus.ADDED,
                            patch="@@ -0,0 +1 @@\n+from src.reviewer.critic import apply_critic")
    dependency = ChangedFile(filename="src/reviewer/critic.py", status=FileStatus.MODIFIED,
                             patch="@@ -0,0 +1 @@\n+def apply_critic(): pass")
    related = _related_changed_modules(candidate, {dependency.filename: dependency})
    assert related[0]["file"] == dependency.filename
    assert "apply_critic" in related[0]["patch"]
    assert not related[0]["truncated"]


def test_replay_does_not_restore_dismissed_duplicate_identity():
    from src.evaluation.critic_replay import retained_findings

    duplicate = {"file_path": "app.py", "line": 1, "title": "Same"}
    findings = [{"message": "keep this"}, {"message": "remove this"}]
    assert retained_findings([duplicate, duplicate], findings, [
        {"suggestion_index": 0, "decision": "confirmed"},
        {"suggestion_index": 1, "decision": "dismissed"},
    ]) == [findings[0]]


def test_critic_fetches_unchanged_import_at_supplied_frozen_head():
    from src.reviewer.critic import _related_changed_modules

    candidate = ChangedFile(filename="src/replay.py", status=FileStatus.MODIFIED,
                            patch="@@ -1 +1 @@\n-old\n+new")
    reads = []
    def read(path):
        reads.append(path)
        return {"src/replay.py": "from src.reviewer.critic import apply_critic",
                "src/reviewer/critic.py": "def apply_critic(): return index_based_decisions"}.get(path)
    related = _related_changed_modules(candidate, {}, read)
    assert reads == ["src/replay.py", "src/reviewer/critic.py"]
    assert related[0]["context_kind"] == "frozen_head_file"
    assert "index_based_decisions" in related[0]["patch"]


def test_dependency_budget_prioritizes_function_named_in_failure_scenario():
    from src.reviewer.critic import _related_changed_modules

    candidate = ChangedFile(filename="src/replay.py", status=FileStatus.ADDED,
                            patch="@@ -0,0 +1 @@\n+new")
    source = ("from src.first import a\nfrom src.second import b\n"
              "from src.reviewer.critic import apply_critic")
    def read(path):
        return source if path == candidate.filename else "def function(): pass"
    related = _related_changed_modules(candidate, {}, read,
                                       focus="apply_critic removes one duplicate")
    assert related[0]["file"] == "src/reviewer/critic.py"
    assert len(related) == 2
