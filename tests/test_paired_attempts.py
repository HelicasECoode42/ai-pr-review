"""Paired-run retry and request-budget behavior without live model calls."""

import argparse
import hashlib
import json
from threading import Thread
from types import SimpleNamespace

import pytest

import src.evaluation.paired as paired_module
from src.reviewer.provider import ProviderError

DIFF = "@@ -1 +1 @@\n-old\n+new\n"


class FakeProvider:
    def __init__(self, *args, **kwargs):
        self.usage_records = []
        self.http_attempts = 0

    def close(self):
        pass


def _setup(monkeypatch, tmp_path, calls):
    def failing_review(meta, diff, provider, **kwargs):
        calls.append(meta["nwo"])
        raise ProviderError("AI model request timed out", kind="timeout")

    monkeypatch.setattr(paired_module, "get_settings", lambda: SimpleNamespace(
        openai_api_key="test-key", review_model="fake-model", openai_base_url="http://fake"))
    identity = {"working_tree_dirty": False, "commit": "abc"}
    monkeypatch.setattr(paired_module, "source_identity", lambda: identity)
    monkeypatch.setattr(paired_module, "OpenAICompatibleProvider", FakeProvider)
    monkeypatch.setattr(paired_module, "run_review", failing_review)

    work = tmp_path / "input"
    work.mkdir()
    (work / "diff.patch").write_text(DIFF)
    task = {"case_id": "case-1", "status": "ready", "input_dir": str(work),
            "diff_sha256": hashlib.sha256(DIFF.encode()).hexdigest(),
            "metadata": {"nwo": "owner/repo", "base": "a", "head": "b"}}
    return {"benchmark_commit": "bench", "records": [task]}


def test_max_attempts_one_makes_exactly_one_attempt_and_records_it(monkeypatch, tmp_path):
    calls = []
    prepared = _setup(monkeypatch, tmp_path, calls)
    output = tmp_path / "out"

    records = paired_module.paired(prepared, tmp_path / "mirrors", output,
                                   arms=("control",), max_attempts=1)

    record = records["control"]["case-1"]
    assert record["status"] == "failed"
    assert len(calls) == 1
    assert [a["attempt"] for a in record["attempts"]] == [1]
    protocol = json.loads((output / "protocol.json").read_text())
    assert protocol["max_attempts_per_task"] == 1
    run = json.loads((output / "control" / "run.json").read_text())
    assert run["protocol"]["max_attempts_per_task"] == 1
    assert len(run["records"][0]["attempts"]) == 1


def test_default_max_attempts_keeps_two_attempts(monkeypatch, tmp_path):
    calls = []
    prepared = _setup(monkeypatch, tmp_path, calls)
    output = tmp_path / "out"

    records = paired_module.paired(prepared, tmp_path / "mirrors", output, arms=("control",))

    assert len(calls) == 2
    assert len(records["control"]["case-1"]["attempts"]) == 2
    assert json.loads((output / "protocol.json").read_text())["max_attempts_per_task"] == 2


def test_max_attempts_below_one_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        paired_module.paired({"benchmark_commit": "b", "records": []}, tmp_path, tmp_path / "o",
                             max_attempts=0)


def test_cli_max_attempts_validation():
    assert paired_module._positive_int("1") == 1
    with pytest.raises(argparse.ArgumentTypeError):
        paired_module._positive_int("0")


def test_run_request_budget_is_atomic_and_rejections_are_not_attempts():
    from threading import Event

    budget = paired_module.RunRequestBudget(3, Event())
    outcomes = []

    def reserve():
        try:
            budget.begin_attempt()
            outcomes.append("started")
        except ProviderError as exc:
            outcomes.append(exc.kind)

    threads = [Thread(target=reserve) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    snapshot = budget.snapshot()
    assert snapshot["attempted"] == 3
    assert snapshot["rejected_before_send"] == 9
    assert snapshot["stopped_before_send"] == 0
    assert outcomes.count("started") == 3
    assert outcomes.count("request_budget_exhausted") == 9
    assert outcomes.count("run_stopped") == 0


def test_fatal_stop_rejects_later_sends_without_mislabeling_budget():
    from threading import Event

    budget = paired_module.RunRequestBudget(5, Event())
    budget.begin_attempt()
    budget.stop_after_fatal_error()

    with pytest.raises(ProviderError) as exc_info:
        budget.begin_attempt()

    assert exc_info.value.kind == "run_stopped"
    assert budget.snapshot()["attempted"] == 1
    assert budget.snapshot()["stopped_before_send"] == 1
    assert budget.snapshot()["stop_reason"] == "fatal_provider_error"


def test_http_attempt_cap_stops_later_tasks_without_spending_more(monkeypatch, tmp_path):
    calls = []
    prepared = _setup(monkeypatch, tmp_path, calls)
    second = dict(prepared["records"][0], case_id="case-2")
    prepared["records"].append(second)

    sent = []

    class HookedProvider:
        def __init__(self, *args, request_attempt_hook=None, **kwargs):
            self.request_attempt_hook = request_attempt_hook
            self.http_attempts = 0
            self.usage_records = []

        def complete_json(self, system, prompt):
            self.request_attempt_hook()
            self.http_attempts += 1
            sent.append(True)
            self.usage_records.append({"usage": {"total_tokens": 7}})
            return "{}"

        def close(self):
            pass

    def successful_review(meta, diff, provider, **kwargs):
        provider.complete_json("system", "prompt")
        return {"findings": []}, {}

    monkeypatch.setattr(paired_module, "OpenAICompatibleProvider", HookedProvider)
    monkeypatch.setattr(paired_module, "run_review", successful_review)
    output = tmp_path / "out"

    records = paired_module.paired(
        prepared, tmp_path / "mirrors", output, arms=("control",), concurrency=1,
        max_attempts=2, max_http_attempts=1,
    )

    assert records["control"]["case-1"]["status"] == "success"
    second_record = records["control"]["case-2"]
    assert second_record["status"] == "failed"
    assert second_record["attempts"][0]["http_attempts"] == 0
    assert second_record["attempts"][0]["usage_status"]["unknown_http_attempts"] == 0
    run = json.loads((output / "control" / "run.json").read_text())
    assert run["run_http_attempt_budget"]["attempted"] == 1
    assert run["run_http_attempt_budget"]["rejected_before_send"] == 1

    resumed = paired_module.paired(
        prepared, tmp_path / "mirrors", output, arms=("control",), concurrency=1,
        max_attempts=2, max_http_attempts=1,
    )
    assert sent == [True]
    assert resumed["control"]["case-2"]["attempts"] == []
    resumed_run = json.loads((output / "control" / "run.json").read_text())
    assert resumed_run["run_http_attempt_budget"]["attempted"] == 1


def test_resume_reads_budget_snapshot_from_unselected_arm(monkeypatch, tmp_path):
    calls = []
    prepared = _setup(monkeypatch, tmp_path, calls)
    prepared["records"].append(dict(prepared["records"][0], case_id="case-2"))
    sent = []

    class HookedProvider:
        def __init__(self, *args, request_attempt_hook=None, **kwargs):
            self.request_attempt_hook = request_attempt_hook
            self.http_attempts = 0
            self.usage_records = []

        def complete_json(self, system, prompt):
            self.request_attempt_hook()
            self.http_attempts += 1
            sent.append(True)
            self.usage_records.append({"usage": {
                "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
            }})
            return "{}"

        def close(self):
            pass

    def successful_review(meta, diff, provider, **kwargs):
        provider.complete_json("system", "prompt")
        return {"findings": []}, {}

    monkeypatch.setattr(paired_module, "OpenAICompatibleProvider", HookedProvider)
    monkeypatch.setattr(paired_module, "run_review", successful_review)
    output = tmp_path / "out"
    paired_module.paired(
        prepared, tmp_path / "mirrors", output, arms=("control",), concurrency=1,
        limit=1, max_attempts=1, max_http_attempts=2,
    )

    protocol = json.loads((output / "protocol.json").read_text())
    grounded_dir = output / "grounded"
    grounded_dir.mkdir()
    (grounded_dir / "run.json").write_text(json.dumps({
        "protocol": protocol,
        "run_http_attempt_budget": {
            "limit": 2, "attempted": 2, "rejected_before_send": 0,
            "stopped_before_send": 0, "exhausted": False,
        },
        "records": [],
    }))

    resumed = paired_module.paired(
        prepared, tmp_path / "mirrors", output, arms=("control",), concurrency=1,
        max_attempts=1, max_http_attempts=2,
    )

    assert sent == [True]
    assert resumed["control"]["case-2"]["reason"] == "run_http_attempt_budget_exhausted"
    control_run = json.loads((output / "control" / "run.json").read_text())
    assert control_run["run_http_attempt_budget"]["attempted"] == 2


def test_provider_request_budget_error_short_circuits_later_phases():
    from src.evaluation.runtime import ObservedProvider

    class ExhaustedProvider:
        calls = 0

        def complete_json(self, system, prompt):
            self.calls += 1
            raise ProviderError("request cap", kind="request_budget_exhausted")

    provider = ExhaustedProvider()
    observed = ObservedProvider(provider)
    for _ in range(2):
        with pytest.raises(ProviderError) as exc_info:
            observed.complete_json("system", "prompt")
        assert exc_info.value.kind == "request_budget_exhausted"
    assert provider.calls == 1
