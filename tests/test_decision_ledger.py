"""The decision ledger: per-candidate attribution across runs."""

from __future__ import annotations

from src.reviewer.decision_ledger import (
    append_ledger,
    decision_records,
    filter_failure_counts,
    load_ledger,
)


class _Report:
    behavior_verifications = [
        {"candidate_id": 0, "file_path": "a.py", "line": 2, "decision": "needs_context",
         "validation_reason": "witness_eval: no behaviour difference",
         "filter_verdicts": [{"filter": "chain_completeness", "passed": True},
                             {"filter": "witness_eval", "passed": False}]},
        {"candidate_id": 1, "file_path": "b.py", "line": 9, "decision": "confirmed",
         "validation_reason": None,
         "filter_verdicts": [{"filter": "witness_eval", "passed": True}]},
    ]


def test_each_candidate_yields_one_record_with_its_failed_filters():
    records = decision_records(_Report(), run_id="run-1", criteria_version="grounded-v3")
    assert len(records) == 2
    assert records[0].failed_filters == ("witness_eval",)
    assert records[0].reason.startswith("witness_eval")
    assert records[1].failed_filters == ()


def test_records_survive_a_round_trip_and_name_the_criteria(tmp_path):
    path = tmp_path / "ledger.jsonl"
    written = append_ledger(path, decision_records(_Report(), run_id="r", criteria_version="v3"))
    assert written == 2
    loaded = load_ledger(path)
    assert [r["criteria_version"] for r in loaded] == ["v3", "v3"]
    assert loaded[0]["run_id"] == "r"


def test_filter_failures_are_aggregated_as_a_tuning_signal(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append_ledger(path, decision_records(_Report(), run_id="r", criteria_version="v3"))
    assert filter_failure_counts(load_ledger(path)) == {"witness_eval": 1}


def test_a_missing_or_torn_ledger_is_not_fatal(tmp_path):
    assert load_ledger(tmp_path / "absent.jsonl") == []
    torn = tmp_path / "torn.jsonl"
    torn.write_text('{"run_id": "a"}\n{"run_id": "b"\n')
    assert len(load_ledger(torn)) == 1
