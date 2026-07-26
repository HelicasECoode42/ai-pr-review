"""Tests for ReviewAgentRunner — rule-only path with mocked GitHub client.

Batch A changes verified:
- fetch_pull_request / fetch_changed_files are separate tools
- strategy chosen after scan_risks (with real files_count/additions/findings_count)
- agent_sidecar includes strategy_reason and warnings
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agent.runner import ReviewAgentRequest, ReviewAgentResult, ReviewAgentRunner
from src.models import (
    ChangedFile,
    FileStatus,
    PullRequest,
    ReviewReport,
)
from src.utils.config import Settings


# ── fixtures ──────────────────────────────────────────────────


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        github_token="test-token",
        openai_api_key="",
        openai_base_url="https://api.openai.com/v1",
        review_model="gpt-4.1-mini",
        request_timeout_seconds=30,
        max_suggestions=15,
        min_comment_confidence=0.65,
        max_suggestions_per_file=5,
    )


@pytest.fixture
def sample_pr() -> PullRequest:
    return PullRequest(
        repo="test/repo",
        number=1,
        title="Add login",
        body="Implement OAuth",
        author="alice",
        head_sha="abc1234",
    )


@pytest.fixture
def sample_files() -> list[ChangedFile]:
    return [
        ChangedFile(
            filename="src/auth.py",
            status=FileStatus.MODIFIED,
            additions=5,
            deletions=2,
            patch="@@ -10,5 +10,8 @@\n context\n-old\n+new\n+extra",
        ),
        ChangedFile(
            filename="src/utils.py",
            status=FileStatus.MODIFIED,
            additions=3,
            deletions=1,
            patch="@@ -1,3 +1,5 @@\n+import os\n\n def foo():\n-    pass\n+    return True",
        ),
    ]


# ── tests ─────────────────────────────────────────────────────


class TestRunnerRuleOnly:
    def test_rule_only_produces_report(
        self, mock_settings: Settings, sample_pr: PullRequest, sample_files: list[ChangedFile]
    ) -> None:
        """Runner with agent_mode=rule_only should produce a valid ReviewReport."""
        with patch("src.github.client.GitHubClient") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh.get_pull_request.return_value = sample_pr
            mock_gh.get_changed_files.return_value = sample_files
            mock_gh_class.return_value.__enter__.return_value = mock_gh

            runner = ReviewAgentRunner(mock_settings)
            request = ReviewAgentRequest(
                repo="test/repo",
                pr_number=1,
                use_ai=False,
                agent_mode="rule_only",
            )
            result = runner.run(request)

            assert isinstance(result, ReviewAgentResult)
            assert isinstance(result.report, ReviewReport)
            assert result.report.pr.repo == "test/repo"
            assert result.report.pr.number == 1
            assert result.report.used_ai is False
            assert len(result.markdown) > 0
            assert isinstance(result.json_report, dict)

    def test_rule_only_agent_sidecar(
        self, mock_settings: Settings, sample_pr: PullRequest, sample_files: list[ChangedFile]
    ) -> None:
        """Agent sidecar should contain strategy, strategy_reason, steps, warnings, degradation_path."""
        with patch("src.github.client.GitHubClient") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh.get_pull_request.return_value = sample_pr
            mock_gh.get_changed_files.return_value = sample_files
            mock_gh_class.return_value.__enter__.return_value = mock_gh

            runner = ReviewAgentRunner(mock_settings)
            request = ReviewAgentRequest(
                repo="test/repo",
                pr_number=1,
                use_ai=False,
                agent_mode="rule_only",
            )
            result = runner.run(request)

            sidecar = result.agent_sidecar
            assert sidecar["strategy"] == "rule_only"
            assert isinstance(sidecar["strategy_reason"], str)
            assert len(sidecar["strategy_reason"]) > 0
            assert isinstance(sidecar["degradation_path"], list)
            assert isinstance(sidecar["warnings"], list)
            assert isinstance(sidecar["steps"], list)

            # Steps: fetch_pull_request, fetch_changed_files, scan_risks,
            #         build_rule_only_report, render_markdown, render_json
            assert len(sidecar["steps"]) >= 6

            # Every step should have required fields and be "success"
            for step in sidecar["steps"]:
                assert "tool" in step
                assert "status" in step
                assert step["status"] == "success"

            # Verify step order (fetch before scan, scan before review, review before render)
            tool_names = [s["tool"] for s in sidecar["steps"]]
            assert "fetch_pull_request" in tool_names
            assert "fetch_changed_files" in tool_names
            assert "scan_risks" in tool_names
            assert "build_rule_only_report" in tool_names
            assert "render_markdown" in tool_names
            assert "render_json" in tool_names

            # fetch must come before scan
            idx_fetch = tool_names.index("fetch_pull_request")
            idx_scan = tool_names.index("scan_risks")
            assert idx_fetch < idx_scan, "fetch_pull_request must execute before scan_risks"

            # scan must come before review
            idx_review = tool_names.index("build_rule_only_report")
            assert idx_scan < idx_review, "scan_risks must execute before build_rule_only_report"

    def test_strategy_reason_is_set(
        self, mock_settings: Settings, sample_pr: PullRequest, sample_files: list[ChangedFile]
    ) -> None:
        """strategy_reason should be non-empty for both explicit and auto modes."""
        with patch("src.github.client.GitHubClient") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh.get_pull_request.return_value = sample_pr
            mock_gh.get_changed_files.return_value = sample_files
            mock_gh_class.return_value.__enter__.return_value = mock_gh

            # Explicit rule_only
            runner = ReviewAgentRunner(mock_settings)
            result = runner.run(ReviewAgentRequest(
                repo="test/repo", pr_number=1, use_ai=False, agent_mode="rule_only",
            ))
            assert result.state.strategy_reason is not None
            assert len(result.state.strategy_reason) > 0
            assert result.agent_sidecar["strategy_reason"] == result.state.strategy_reason

            # Auto mode (should pick one_shot_ai or rule_only depending on API key)
            result2 = runner.run(ReviewAgentRequest(
                repo="test/repo", pr_number=1, use_ai=True, agent_mode="auto",
            ))
            assert result2.state.strategy_reason is not None
            assert len(result2.state.strategy_reason) > 0

    def test_rule_only_suggestions_from_rules(
        self, mock_settings: Settings, sample_pr: PullRequest
    ) -> None:
        """Rule findings should be converted to suggestions in the report."""
        files_with_risk = [
            ChangedFile(
                filename="src/auth.py",
                status=FileStatus.MODIFIED,
                additions=5,
                deletions=2,
                patch='@@ -10,5 +10,8 @@\n+import subprocess\n+subprocess.call("rm -rf /")\n',
            ),
        ]

        with patch("src.github.client.GitHubClient") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh.get_pull_request.return_value = sample_pr
            mock_gh.get_changed_files.return_value = files_with_risk
            mock_gh_class.return_value.__enter__.return_value = mock_gh

            runner = ReviewAgentRunner(mock_settings)
            request = ReviewAgentRequest(
                repo="test/repo",
                pr_number=1,
                use_ai=False,
                agent_mode="rule_only",
            )
            result = runner.run(request)

            assert isinstance(result.report, ReviewReport)
            assert result.report.used_ai is False

    def test_rule_only_json_report_shape(
        self, mock_settings: Settings, sample_pr: PullRequest, sample_files: list[ChangedFile]
    ) -> None:
        """JSON report from runner should be compatible with existing schema."""
        with patch("src.github.client.GitHubClient") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh.get_pull_request.return_value = sample_pr
            mock_gh.get_changed_files.return_value = sample_files
            mock_gh_class.return_value.__enter__.return_value = mock_gh

            runner = ReviewAgentRunner(mock_settings)
            request = ReviewAgentRequest(
                repo="test/repo",
                pr_number=1,
                use_ai=False,
                agent_mode="rule_only",
            )
            result = runner.run(request)

            jr = result.json_report
            assert "pr" in jr
            assert "files" in jr
            assert "summary" in jr
            assert "risk_level" in jr
            assert "suggestions" in jr
            assert "used_ai" in jr
            assert "report_confidence" in jr
            assert "completeness" in jr

    def test_rule_only_state_language(
        self, mock_settings: Settings, sample_pr: PullRequest, sample_files: list[ChangedFile]
    ) -> None:
        """Language parameter should flow through to agent state and markdown output."""
        with patch("src.github.client.GitHubClient") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh.get_pull_request.return_value = sample_pr
            mock_gh.get_changed_files.return_value = sample_files
            mock_gh_class.return_value.__enter__.return_value = mock_gh

            runner = ReviewAgentRunner(mock_settings)
            request = ReviewAgentRequest(
                repo="test/repo",
                pr_number=1,
                use_ai=False,
                agent_mode="rule_only",
                language="zh",
            )
            result = runner.run(request)

            assert result.state.language == "zh"
            # Markdown summary should contain Chinese markers
            assert "个文件" in result.markdown


class TestRunnerDegradation:
    def test_one_shot_without_api_key_degrades(
        self, mock_settings: Settings, sample_pr: PullRequest, sample_files: list[ChangedFile]
    ) -> None:
        """When agent_mode=auto with use_ai=True but no API key, should record degradation."""
        with patch("src.github.client.GitHubClient") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh.get_pull_request.return_value = sample_pr
            mock_gh.get_changed_files.return_value = sample_files
            mock_gh_class.return_value.__enter__.return_value = mock_gh

            runner = ReviewAgentRunner(mock_settings)
            request = ReviewAgentRequest(
                repo="test/repo",
                pr_number=1,
                use_ai=True,
                agent_mode="auto",
            )
            result = runner.run(request)

            # Policy returns rule_only when no API key
            assert result.agent_sidecar["strategy"] == "rule_only"
            assert result.agent_sidecar["strategy_reason"] is not None
            # Warnings should mention the degradation
            assert len(result.agent_sidecar["warnings"]) > 0
            assert any("API key" in w.lower() or "degraded" in w.lower()
                       for w in result.agent_sidecar["warnings"])
            assert result.report.used_ai is False

    def test_rule_only_no_warnings_when_explicit(
        self, mock_settings: Settings, sample_pr: PullRequest, sample_files: list[ChangedFile]
    ) -> None:
        """Explicit rule_only with use_ai=False should not produce degradation warnings."""
        with patch("src.github.client.GitHubClient") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh.get_pull_request.return_value = sample_pr
            mock_gh.get_changed_files.return_value = sample_files
            mock_gh_class.return_value.__enter__.return_value = mock_gh

            runner = ReviewAgentRunner(mock_settings)
            request = ReviewAgentRequest(
                repo="test/repo",
                pr_number=1,
                use_ai=False,
                agent_mode="rule_only",
            )
            result = runner.run(request)

            # No degradation when rule_only is explicitly requested
            assert result.agent_sidecar["degradation_path"] == []
            # No API-key-related warnings
            api_warnings = [w for w in result.agent_sidecar["warnings"]
                            if "api" in w.lower() or "degraded" in w.lower()]
            assert api_warnings == []

    def test_strategy_receives_real_data_not_zero(
        self, mock_settings: Settings, sample_pr: PullRequest, sample_files: list[ChangedFile]
    ) -> None:
        """choose_agent_strategy should receive real files_count/additions/findings_count after scan."""
        with patch("src.github.client.GitHubClient") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh.get_pull_request.return_value = sample_pr
            mock_gh.get_changed_files.return_value = sample_files
            mock_gh_class.return_value.__enter__.return_value = mock_gh

            # Record the actual values passed to choose_agent_strategy
            captured: dict[str, int] = {}

            original = __import__("src.agent.policy", fromlist=["choose_agent_strategy"])
            real_choose = original.choose_agent_strategy

            def _spy(*, use_ai, has_api_key, files_count, additions, findings_count,
                     high_severity_count, signal_files=None,
                     critical_signal_files=None, requested_mode="auto"):
                captured["files_count"] = files_count
                captured["additions"] = additions
                captured["findings_count"] = findings_count
                captured["high_severity_count"] = high_severity_count
                return real_choose(
                    use_ai=use_ai, has_api_key=has_api_key,
                    files_count=files_count, additions=additions,
                    findings_count=findings_count,
                    high_severity_count=high_severity_count,
                    signal_files=signal_files,
                    critical_signal_files=critical_signal_files,
                    requested_mode=requested_mode,
                )

            with patch("src.agent.runner.choose_agent_strategy", side_effect=_spy):
                runner = ReviewAgentRunner(mock_settings)
                runner.run(ReviewAgentRequest(
                    repo="test/repo", pr_number=1, use_ai=False, agent_mode="rule_only",
                ))

            # After scan, files_count should reflect actual files (2), not 0
            assert captured.get("files_count") == 2, (
                f"Expected files_count=2, got {captured.get('files_count')}"
            )
            assert captured.get("additions") == 8, (
                f"Expected additions=8 (5+3), got {captured.get('additions')}"
            )

    def test_fetch_changed_files_failure_produces_minimal_report(
        self, mock_settings: Settings, sample_pr: PullRequest
    ) -> None:
        """When fetch_changed_files fails, runner should warn and still produce a report."""
        with patch("src.github.client.GitHubClient") as mock_gh_class:
            mock_gh = MagicMock()
            mock_gh.get_pull_request.return_value = sample_pr
            mock_gh.get_changed_files.side_effect = RuntimeError("GitHub API rate limit")
            mock_gh_class.return_value.__enter__.return_value = mock_gh

            runner = ReviewAgentRunner(mock_settings)
            request = ReviewAgentRequest(
                repo="test/repo",
                pr_number=1,
                use_ai=False,
                agent_mode="rule_only",
            )
            result = runner.run(request)

            # Should still produce a report (with PR metadata, empty files)
            assert isinstance(result.report, ReviewReport)
            assert result.report.pr.repo == "test/repo"

            # The fetch_changed_files step should be marked as failed
            fetch_steps = [s for s in result.agent_sidecar["steps"]
                           if s["tool"] == "fetch_changed_files"]
            assert len(fetch_steps) == 1
            assert fetch_steps[0]["status"] == "failed"
            assert "rate limit" in fetch_steps[0]["error"].lower()

            # Warnings should mention the failure
            assert any("fetch" in w.lower() or "files" in w.lower()
                       for w in result.agent_sidecar["warnings"])

            # Subsequent steps (scan, report, render) should still succeed
            later_tools = [s["tool"] for s in result.agent_sidecar["steps"]
                           if s["tool"] != "fetch_changed_files"
                           and s["tool"] != "fetch_pull_request"]
            for tool_name in later_tools:
                steps = [s for s in result.agent_sidecar["steps"] if s["tool"] == tool_name]
                assert steps[0]["status"] == "success", (
                    f"{tool_name} should succeed after fetch_changed_files failure"
                )


# ── Batch B tests ────────────────────────────────────────────


class TestBatchBAgentTool:
    def test_agent_tool_has_description_and_risk(self) -> None:
        from src.agent.registry import AgentTool
        tool = AgentTool(name="test", execute=lambda ctx: None)
        assert hasattr(tool, "description")
        assert tool.description == ""
        assert hasattr(tool, "risk")
        assert tool.risk == "read"

    def test_agent_tool_custom_risk(self) -> None:
        from src.agent.registry import AgentTool
        tool = AgentTool(
            name="publish_comment",
            execute=lambda ctx: None,
            description="Post a review comment to GitHub",
            risk="write",
        )
        assert tool.description == "Post a review comment to GitHub"
        assert tool.risk == "write"


class TestBatchBRequestResult:
    def test_request_is_pydantic(self) -> None:
        from src.agent.runner import ReviewAgentRequest
        from pydantic import BaseModel
        req = ReviewAgentRequest(repo="a/b", pr_number=1)
        assert isinstance(req, BaseModel)

    def test_request_defaults(self) -> None:
        from src.agent.runner import ReviewAgentRequest
        req = ReviewAgentRequest(repo="a/b", pr_number=1)
        assert req.agent_mode == "auto"
        assert req.review_mode == "full_pr"
        assert req.use_ai is True
        assert req.language is None

    def test_request_model_dump(self) -> None:
        from src.agent.runner import ReviewAgentRequest
        req = ReviewAgentRequest(repo="a/b", pr_number=1, use_ai=False, language="zh")
        d = req.model_dump()
        assert d["repo"] == "a/b"
        assert d["pr_number"] == 1
        assert d["use_ai"] is False
        assert d["language"] == "zh"

    def test_result_is_pydantic(self) -> None:
        from src.agent.runner import ReviewAgentResult
        from src.agent.state import ReviewAgentState
        from src.models import ReviewReport, PullRequest, Severity
        from pydantic import BaseModel

        pr = PullRequest(repo="a/b", number=1, title="T")
        report = ReviewReport(
            pr=pr,
            files=[],
            summary="ok",
            risk_level=Severity.LOW,
        )
        state = ReviewAgentState(repo="a/b", pr_number=1)
        result = ReviewAgentResult(
            report=report,
            markdown="# ok",
            json_report={},
            agent_sidecar={},
            state=state,
        )
        assert isinstance(result, BaseModel)
        assert result.report is report
        assert result.state is state

    def test_result_report_type_is_review_report(self) -> None:
        from src.agent.runner import ReviewAgentResult
        from src.agent.state import ReviewAgentState
        from src.models import ReviewReport, PullRequest, Severity

        pr = PullRequest(repo="a/b", number=1, title="T")
        report = ReviewReport(pr=pr, files=[], summary="ok", risk_level=Severity.LOW)
        state = ReviewAgentState(repo="a/b", pr_number=1)
        result = ReviewAgentResult(
            report=report, markdown="# ok", json_report={},
            agent_sidecar={}, state=state,
        )
        assert isinstance(result.report, ReviewReport)
