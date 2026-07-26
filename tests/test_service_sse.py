from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.service.app import app


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
