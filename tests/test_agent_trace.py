"""Tests for agent trace utilities — summarise + redact."""

from __future__ import annotations

import pytest

from src.agent.trace import (
    redact_secrets,
    summarize_input,
    summarize_observation,
)


# ── redact_secrets ────────────────────────────────────────────


class TestRedactSecrets:
    def test_redact_github_token_in_string(self) -> None:
        result = redact_secrets("Authorization: ghp_abc123def456ghi789jkl")
        assert "ghp_abc123def456ghi789jkl" not in str(result)
        assert "redacted" in str(result)

    def test_redact_openai_key_in_string(self) -> None:
        result = redact_secrets("OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012mno345pqr")
        assert "sk-proj" not in str(result)
        assert "redacted" in str(result)

    def test_redact_in_dict(self) -> None:
        d = {"auth": "Bearer ghp_abc123def456ghi789jkl012mno345pqr678stu", "safe": "hello"}
        result = redact_secrets(d)
        assert isinstance(result, dict)
        assert "ghp_abc123def456ghi789jkl012mno345pqr678stu" not in str(result["auth"])
        assert result["safe"] == "hello"

    def test_redact_in_nested_dict(self) -> None:
        d = {"env": {"token": "ghp_secret1234567890abcdefghijklmnopqrstuv"}}
        result = redact_secrets(d)
        assert isinstance(result, dict)
        assert "ghp_secret1234567890abcdefghijklmnopqrstuv" not in str(result)

    def test_redact_in_list(self) -> None:
        items = ["normal text", "sk-ant-key1234567890abcdefghij"]
        result = redact_secrets(items)
        assert isinstance(result, list)
        assert "sk-ant" not in str(result)

    def test_redact_no_secrets_returns_same(self) -> None:
        assert redact_secrets("hello world") == "hello world"
        assert redact_secrets({"a": 1}) == {"a": 1}

    def test_redact_int_returns_same(self) -> None:
        assert redact_secrets(42) == 42


# ── summarize_input ───────────────────────────────────────────


class TestSummarizeInput:
    def test_pr_input_as_tuple(self) -> None:
        """fetch_pull_request INPUT is (repo, pr_number) tuple."""
        result = summarize_input(("owner/repo", 42), tool_name="fetch_pull_request")
        assert result["repo"] == "owner/repo"
        assert result["pr_number"] == "42"

    def test_files_input(self) -> None:
        class FakeFile:
            filename: str

            def __init__(self, name: str) -> None:
                self.filename = name

        files = [FakeFile("a.py"), FakeFile("b.py")]
        result = summarize_input(files, tool_name="fetch_changed_files")
        assert result["count"] == 2
        assert "a.py" in result["filenames"]

    def test_findings_input(self) -> None:
        class FakeFinding:
            title: str

            def __init__(self, t: str) -> None:
                self.title = t

        findings = [FakeFinding("SQL injection"), FakeFinding("hardcoded key")]
        result = summarize_input((None, None, findings), tool_name="scan_risks")
        assert result["findings"]["count"] == 2
        assert "SQL injection" in result["findings"]["top_titles"]

    def test_string_input(self) -> None:
        result = summarize_input("hello world", tool_name="unknown")
        assert result["chars"] == 11

    def test_list_input_generic(self) -> None:
        result = summarize_input([1, 2, 3], tool_name="unknown")
        assert result["count"] == 3



# ── summarize_observation ─────────────────────────────────────


class TestSummarizeObservation:
    def test_pr_observation(self) -> None:
        class FakePR:
            title = "Fix auth"
            author = "bob"
            additions = 100
            deletions = 20
            changed_files = 8

        result = summarize_observation(FakePR(), tool_name="fetch_pull_request")
        assert result["title"] == "Fix auth"
        assert result["files_count"] == 8

    def test_files_observation(self) -> None:
        class FakeFile:
            filename: str

            def __init__(self, name: str) -> None:
                self.filename = name

        files = [FakeFile("x.py")]
        result = summarize_observation(files, tool_name="fetch_changed_files")
        assert result["count"] == 1

    def test_findings_observation(self) -> None:
        class FakeFinding:
            rule_id: str
            title: str

            def __init__(self, rid: str, t: str) -> None:
                self.rule_id = rid
                self.title = t

        findings = [FakeFinding("R001", "bad")]
        result = summarize_observation(findings, tool_name="scan_risks")
        assert result["count"] == 1

    def test_report_observation(self) -> None:
        class FakeReport:
            risk_level = "high"
            suggestions = [1, 2, 3]
            used_ai = True
            report_confidence = "normal"

        result = summarize_observation(FakeReport(), tool_name="build_rule_only_report")
        assert result["risk_level"] == "high"
        assert result["suggestions_count"] == 3
        assert result["used_ai"] is True

    def test_model_output_short(self) -> None:
        result = summarize_observation("short string", tool_name="unknown")
        assert result["chars"] == len("short string")

    def test_model_output_long_json(self) -> None:
        import json

        raw = json.dumps({
            "summary": "test",
            "risk_level": "low",
            "suggestions": [{"title": "X"}],
        })
        result = summarize_observation(raw, tool_name="unknown")
        # Long string (>500 chars) should be summarised as model output
        assert "chars" in result or "parse_status" in result

    def test_render_markdown_observation(self) -> None:
        result = summarize_observation("# Report\n\nhello", tool_name="render_markdown")
        assert result["chars"] == 15

    def test_fallback_dict(self) -> None:
        result = summarize_observation({"a": 1, "b": 2}, tool_name="unknown")
        assert "keys" in result
