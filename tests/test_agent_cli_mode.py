from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from src.cli.main import app


def test_cli_two_stage_flag_overrides_auto_agent_mode(monkeypatch):
    seen = []

    class FakeRunner:
        def __init__(self, _settings):
            pass

        def run(self, request):
            seen.append(request.agent_mode)
            return SimpleNamespace(
                report=None,
                json_report={"risk_level": "low"},
                markdown="# mocked",
                agent_sidecar={"warnings": []},
            )

    monkeypatch.setattr("src.agent.runner.ReviewAgentRunner", FakeRunner)
    result = CliRunner().invoke(app, ["owner/repo", "1", "--two-stage"])

    assert result.exit_code == 0, result.output
    assert seen == ["two_stage"]


def test_cli_without_legacy_flag_keeps_auto_mode(monkeypatch):
    seen = []

    class FakeRunner:
        def __init__(self, _settings):
            pass

        def run(self, request):
            seen.append(request.agent_mode)
            return SimpleNamespace(
                report=None,
                json_report={"risk_level": "low"},
                markdown="# mocked",
                agent_sidecar={"warnings": []},
            )

    monkeypatch.setattr("src.agent.runner.ReviewAgentRunner", FakeRunner)
    result = CliRunner().invoke(app, ["owner/repo", "1"])

    assert result.exit_code == 0, result.output
    assert seen == ["auto"]
