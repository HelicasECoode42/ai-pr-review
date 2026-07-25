"""Offline end-to-end test for the Agent AI path.

External GitHub and model boundaries are replaced with deterministic fakes; all
internal steps from fetch through render execute through the real Agent Runner.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agent.runner import ReviewAgentRequest, ReviewAgentRunner
from src.models import ChangedFile, FileStatus, PullRequest
from src.utils.config import Settings


class FakeProvider:
    prompts: list[tuple[str, str]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        return (
            '{"summary":"权限逻辑已变更，需要关注鉴权边界。",'
            '"risk_level":"medium","suggestions":[]}'
        )

    def close(self) -> None:
        return None


def test_agent_one_shot_pipeline_fetches_scans_reviews_and_renders():
    FakeProvider.prompts.clear()
    settings = Settings(
        github_token="fake-github-token",
        openai_api_key="fake-model-key",
        review_model="fake-model",
    )
    pr = PullRequest(
        repo="owner/repo",
        number=7,
        title="Tighten authorization",
        body="Require project membership before writes",
        author="alice",
        head_sha="abcdef123456",
    )
    files = [
        ChangedFile(
            filename="src/auth.py",
            status=FileStatus.MODIFIED,
            additions=2,
            deletions=1,
            patch="@@ -1,2 +1,3 @@\n-old_check()\n+check_membership(user)\n+write_project()",
        )
    ]

    with (
        patch("src.github.client.GitHubClient") as github_class,
        patch("src.reviewer.provider.OpenAICompatibleProvider", FakeProvider),
    ):
        github = MagicMock()
        github.get_pull_request.return_value = pr
        github.get_changed_files.return_value = files
        github_class.return_value.__enter__.return_value = github

        result = ReviewAgentRunner(settings).run(ReviewAgentRequest(
            repo="owner/repo",
            pr_number=7,
            use_ai=True,
            agent_mode="auto",
            language="zh",
        ))

    tools = [step.tool for step in result.state.steps]
    assert tools == [
        "fetch_pull_request",
        "fetch_changed_files",
        "scan_risks",
        "analyze_cross_file",
        "one_shot_ai_review",
        "render_markdown",
        "render_json",
    ]
    assert result.state.strategy == "one_shot_ai"
    assert result.report.used_ai is True
    assert result.report.summary.startswith("权限逻辑已变更")
    assert result.json_report["pr"]["number"] == 7
    assert FakeProvider.prompts
