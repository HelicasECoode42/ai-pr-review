"""Offline end-to-end test for the Agent AI path.

External GitHub and model boundaries are replaced with deterministic fakes; all
internal steps from fetch through render execute through the real Agent Runner.
"""

from __future__ import annotations

import json
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


def test_auto_large_pr_keeps_grounded_cross_file_review():
    """The auto size policy must not disable frozen-snapshot evidence tools."""
    base_sha = "a" * 40
    head_sha = "b" * 40
    pr = PullRequest(
        repo="owner/repo", number=27, title="Change profile return shape",
        body="Update profile handling", author="alice",
        base_sha=base_sha, head_sha=head_sha,
    )
    files = [
        ChangedFile(
            filename=f"src/module_{index}.py", status=FileStatus.MODIFIED,
            additions=1, deletions=0,
            patch=f"@@ -1 +1 @@\n-old_{index} = 0\n+new_{index} = 1",
        )
        for index in range(19)
    ]
    files.append(ChangedFile(
        filename="src/users.py", status=FileStatus.MODIFIED,
        additions=1, deletions=1,
        patch='@@ -1 +1 @@\n-return ("Ada", 42)\n+return {"name": "Ada", "age": 42}',
    ))

    class FakeGithub:
        token = "fake-github-token"

        def __init__(self):
            self.reads = []
            self.tree_reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get_pull_request(self, _repo, _number):
            return pr

        def get_changed_files(self, _repo, _number):
            return files

        def _get(self, _path, *, params):
            self.tree_reads += 1
            paths = [file.filename for file in files] + ["src/caller.py"]
            response = MagicMock()
            response.json.return_value = {
                "truncated": False,
                "tree": [{"type": "blob", "path": path} for path in paths],
            }
            return response

        def get_file_contents(self, _repo, path, sha):
            self.reads.append((path, sha))
            if path == "src/caller.py":
                return (
                    "from users import get_profile\n"
                    "def render():\n"
                    "    name, age = get_profile()\n"
                    "    return age + 1\n"
                )
            if path == "src/users.py":
                return (
                    'def get_profile():\n'
                    '    return {"name": "Ada", "age": 42}\n'
                    if sha == head_sha else
                    'def get_profile():\n    return ("Ada", 42)\n'
                )
            return f"value_{path.rsplit('_', 1)[-1].split('.')[0]} = 1\n"

    class GroundedFakeProvider:
        prompts = []
        responses = [
            {
                "intent_hypotheses": [{
                    "id": "H1", "goal": "Preserve the profile return contract",
                    "constraints": [], "risk_hypotheses": [
                        "An unchanged caller may still unpack a tuple"
                    ], "unknowns": ["Whether the caller was migrated"],
                    "source_refs": ["diff:src/users.py"],
                }],
                "context_requests": [{
                    "hypothesis_id": "H1", "purpose": "Inspect unchanged caller",
                    "tool": "read", "path": "src/caller.py", "line": 3, "side": "head",
                }, {
                    "hypothesis_id": "H1", "purpose": "Locate other callers",
                    "tool": "search", "symbol": "get_profile", "side": "head",
                }],
            },
            {
                "summary": "The changed return shape may break an unchanged caller.",
                "risk_level": "high",
                "intent_hypotheses": [{
                    "id": "H1", "goal": "Preserve the profile return contract",
                    "constraints": [], "risk_hypotheses": [
                        "An unchanged caller may still unpack a tuple"
                    ], "unknowns": ["Whether the caller was migrated"],
                    "source_refs": ["diff:src/users.py"],
                }],
                "suggestions": [{
                    "file_path": "src/users.py", "line": 2, "severity": "high",
                    "confidence": 0.8, "title": "Caller still expects tuple values",
                    "reason": "The return value changed to a mapping.",
                    "recommendation": "Preserve the tuple contract or update callers.",
                    "evidence": ['return {"name": "Ada", "age": 42}'],
                    "failure_scenario": "The caller adds one to a string key.",
                }],
            },
            lambda _system, prompt: {
                "items": [{
                    "candidate_id": 0, "decision": "confirmed", "confidence": 0.9,
                    "trigger": "render unpacks get_profile() and uses age as a number",
                    "call_path": ["src/caller.py:3", "src/users.py:2"],
                    "expected_behavior": "The second return value is an integer",
                    "actual_behavior": "The caller receives the string mapping key",
                    "introduced_by": "The function now returns a mapping",
                    "reason": "The unchanged caller still unpacks the tuple-shaped result.",
                    "evidence": [
                        {"path": "src/caller.py", "side": "head", "line": 3,
                         "quote": "    name, age = get_profile()"},
                        {"path": "src/users.py", "side": "base", "line": 2,
                         "quote": '    return ("Ada", 42)'},
                    ],
                    "counter_evidence": [],
                }],
                "intent_assessments": [{
                    "hypothesis_id": "H1", "status": "supported",
                    "evidence_refs": _task_refs(prompt),
                    "rationale": "The unchanged caller unpacks two positional values.",
                    "unknowns": [], "revision": "",
                }],
            },
        ]

        def __init__(self, **kwargs):
            self.responses = iter(self.responses)
            self.request_budget = kwargs["request_budget"]

        def complete_json(self, system_prompt, user_prompt):
            self.request_budget.reserve(system_prompt, user_prompt)
            self.prompts.append((system_prompt, user_prompt))
            response = next(self.responses)
            if callable(response):
                response = response(system_prompt, user_prompt)
            return json.dumps(response)

        def close(self):
            return None

    def _task_refs(prompt):
        marker = "## Intent hypotheses (untrusted, evidence-linked annotations only)\n"
        plan, _ = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1].lstrip())
        return [
            ref for task in plan["tasks"] if task["hypothesis_id"] == "H1"
            for ref in task["evidence_refs"]
        ]

    settings = Settings(
        github_token="fake-github-token", openai_api_key="fake-model-key",
        review_model="fake-model", enable_repository_review=True,
        unit_review_max_units=0, max_review_http_attempts=3,
        max_input_token_upper_bound=200_000,
    )
    github = FakeGithub()
    with (
        patch("src.github.client.GitHubClient", return_value=github),
        patch("src.reviewer.provider.OpenAICompatibleProvider", GroundedFakeProvider),
    ):
        result = ReviewAgentRunner(settings).run(ReviewAgentRequest(
            repo="owner/repo", pr_number=27, use_ai=True, agent_mode="auto",
        ))

    assert result.state.strategy == "one_shot_ai"
    assert github.tree_reads == 1
    assert any(path == "src/caller.py" and sha == head_sha for path, sha in github.reads)
    assert any("name, age = get_profile()" in prompt for _, prompt in GroundedFakeProvider.prompts)
    plan = result.report.repository_context["intent_plan"]
    assert plan["status"] == "assessed"
    assert {task["status"] for task in plan["tasks"]} == {"executed_with_evidence"}
    queries = [task.get("query", {}) for task in plan["tasks"]]
    assert any(query.get("tool") == "read" and query.get("path") == "src/caller.py"
               for query in queries)
    assert any(query.get("tool") == "search" and query.get("symbol") == "get_profile"
               for query in queries)
    from src.reviewer.criteria import CRITERIA_VERSION

    assert result.report.behavior_verifications[0]["criteria_version"] == CRITERIA_VERSION
    assert result.report.context_tokenizer in {
        "cl100k_base", "utf8_byte_upper_bound",
    }
    assert result.report.suggestions[0].source == "ai_behavior_verified"
    assert result.report.metrics["request_http_attempts"] == 3
    assert result.report.metrics["request_http_attempt_cap"] == 3
    assert result.report.metrics["request_input_upper_bound_utf8_bytes"] <= 200_000


def test_auto_large_pr_without_frozen_shas_reports_grounded_degradation():
    pr = PullRequest(
        repo="owner/repo", number=28, title="Large refactor",
        body="", author="alice",
    )
    files = [
        ChangedFile(
            filename=f"src/file_{index}.py", status=FileStatus.MODIFIED,
            additions=1, deletions=0,
            patch=f"@@ -1 +1 @@\n-old_{index} = 0\n+new_{index} = 1",
        )
        for index in range(20)
    ]

    class LegacyFakeProvider:
        def __init__(self, **_kwargs):
            pass

        def complete_json(self, _system_prompt, _user_prompt):
            return json.dumps({
                "summary": "Review completed without a frozen repository snapshot.",
                "risk_level": "low", "suggestions": [],
            })

        def close(self):
            return None

    github = MagicMock()
    github.get_pull_request.return_value = pr
    github.get_changed_files.return_value = files
    github_context = MagicMock()
    github_context.__enter__.return_value = github
    settings = Settings(
        github_token="fake-github-token", openai_api_key="fake-model-key",
        review_model="fake-model", enable_repository_review=True,
    )
    with (
        patch("src.github.client.GitHubClient", return_value=github_context),
        patch("src.reviewer.provider.OpenAICompatibleProvider", LegacyFakeProvider),
    ):
        result = ReviewAgentRunner(settings).run(ReviewAgentRequest(
            repo="owner/repo", pr_number=28, use_ai=True, agent_mode="auto",
        ))

    assert result.state.strategy == "one_shot_ai"
    assert result.report.execution_status == "success"
    assert result.report.report_confidence == "partial"
    assert any(
        "immutable base/head SHAs are unavailable" in warning
        for warning in result.report.analysis_warnings
    )
