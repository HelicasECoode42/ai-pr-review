from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.service.app import app


def test_api_defaults_to_auto_agent_mode() -> None:
    from src.service.app import AnalyzeRequest

    assert AnalyzeRequest(repo="owner/repo", pr_number=1).agent_mode == "auto"


class FakeRunner:
    def __init__(self, _settings: object, progress_sink: object = None) -> None:
        self._progress_sink = progress_sink

    def run(self, _request: object) -> SimpleNamespace:
        assert callable(self._progress_sink)
        self._progress_sink("strategy_selected", {
            "strategy": "one_shot_ai", "reason": "test", "files_count": 1,
        })
        self._progress_sink("step_started", {"index": 0, "tool": "fetch_pull_request"})
        self._progress_sink("step_completed", {
            "index": 0, "tool": "fetch_pull_request", "duration_ms": 1, "status": "success",
        })
        return SimpleNamespace(
            json_report={"risk_level": "low"},
            markdown="# Done",
            agent_sidecar={"steps": []},
        )


def test_analyze_stream_emits_ordered_progress_and_complete(monkeypatch: object) -> None:
    monkeypatch.setattr("src.agent.runner.ReviewAgentRunner", FakeRunner)

    with TestClient(app) as client:
        response = client.post("/api/analyze/stream", json={
            "repo": "owner/repo", "pr_number": 1, "agent_mode": "one_shot_ai",
        })

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: started" in response.text
    assert "event: strategy_selected" in response.text
    assert "event: step_started" in response.text
    assert "event: step_completed" in response.text
    assert "event: complete" in response.text


def test_stream_default_is_auto(monkeypatch: object) -> None:
    seen = []

    class CapturingRunner(FakeRunner):
        def run(self, request: object) -> SimpleNamespace:
            seen.append(request.agent_mode)
            return super().run(request)

    monkeypatch.setattr("src.agent.runner.ReviewAgentRunner", CapturingRunner)
    with TestClient(app) as client:
        response = client.post("/api/analyze/stream", json={
            "repo": "owner/repo", "pr_number": 1,
        })

    assert response.status_code == 200
    assert seen == ["auto"]


def test_stream_explicit_off_uses_legacy_path(monkeypatch: object) -> None:
    from src.service.app import AnalyzeResponse

    def legacy(_request: object) -> AnalyzeResponse:
        return AnalyzeResponse(
            report={"risk_level": "low"}, markdown="# Legacy", duration_seconds=0,
            agent={"requested_mode": "off", "effective_mode": "legacy",
                   "strategy_reason": "Explicit legacy compatibility mode"},
        )

    monkeypatch.setattr("src.service.app.analyze", legacy)
    monkeypatch.setattr(
        "src.agent.runner.ReviewAgentRunner.run",
        lambda *_args, **_kwargs: pytest.fail("off must not invoke Agent"),
    )
    with TestClient(app) as client:
        response = client.post("/api/analyze/stream", json={
            "repo": "owner/repo", "pr_number": 1, "agent_mode": "off",
        })

    assert response.status_code == 200
    assert '"effective_mode": "legacy"' in response.text
    assert "# Legacy" in response.text
