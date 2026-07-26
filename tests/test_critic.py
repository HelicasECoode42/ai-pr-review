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
