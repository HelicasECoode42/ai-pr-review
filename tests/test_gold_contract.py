"""Data-contract tests: gold scope is derived, never hand-written."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.evaluation.gold_scope import (
    GoldContractError,
    pr_scope_positive_indexes,
    summarize_gold_scope,
)


def _f(label, scope):
    return {"tp_fp": label, "scope": scope}


@pytest.fixture
def synthetic_gold():
    return {
        "findings": [
            _f("tp", "introduced-by-pr"),
            _f("tp", "introduced-by-pr"),
            _f("tp", "exacerbated-by-pr"),
            _f("tp", "pre-existing"),
            _f("fp", "introduced-by-pr"),
            _f("fp", "pre-existing"),
            _f("fp", "unrelated"),
        ]
    }


def test_scope_summary_counts_only_true_positives_in_the_denominator(synthetic_gold):
    summary = summarize_gold_scope(synthetic_gold)
    assert summary["official_labels"] == {"tp": 4, "fp": 3}
    assert summary["tp_scope"] == {
        "introduced_by_pr": 2,
        "exacerbated_by_pr": 1,
        "pre_existing": 1,
        "unrelated": 0,
    }
    assert summary["pr_scope_positive_denominator"] == 3
    assert summary["pre_existing_excluded"] == 1


def test_pr_scope_indexes_skip_false_positives_and_pre_existing(synthetic_gold):
    assert pr_scope_positive_indexes(synthetic_gold) == [0, 1, 2]


def test_unrelated_is_a_valid_scope_only_for_false_positives():
    # The official corpus uses 'unrelated' on fp labels only.
    assert summarize_gold_scope([_f("fp", "unrelated")])["pr_scope_positive_denominator"] == 0
    with pytest.raises(GoldContractError):
        summarize_gold_scope([_f("tp", "unrelated")])


def test_missing_or_unknown_labels_fail_closed():
    with pytest.raises(GoldContractError):
        summarize_gold_scope([{"tp_fp": "tp"}])
    with pytest.raises(GoldContractError):
        summarize_gold_scope([{"scope": "introduced-by-pr"}])
    with pytest.raises(GoldContractError):
        summarize_gold_scope([_f("tp", "maybe-a-bug")])
    with pytest.raises(GoldContractError):
        summarize_gold_scope([_f("true-positive", "introduced-by-pr")])


def test_committed_dev_gold_matches_the_contract_when_available():
    """The sibling eval-data checkout is optional; CI skips without it."""
    root = Path(__file__).resolve().parents[2]
    gold_dir = root / "eval-data" / "reviewbench" / "golden"
    if not gold_dir.is_dir():
        pytest.skip("eval-data gold checkout is not available")
    for path in sorted(gold_dir.glob("*.json")):
        findings = json.loads(path.read_text(encoding="utf-8"))["findings"]
        summary = summarize_gold_scope(findings)
        assert summary["official_labels"]["tp"] >= summary["pr_scope_positive_denominator"]
    crab = gold_dir / "brexhq_CrabTrap_15-5ef0884c.json"
    if crab.exists():
        summary = summarize_gold_scope(json.loads(crab.read_text(encoding="utf-8"))["findings"])
        assert summary["pr_scope_positive_denominator"] == 14
        assert summary["tp_scope"]["introduced_by_pr"] == 11
        assert summary["tp_scope"]["exacerbated_by_pr"] == 3
        assert summary["tp_scope"]["pre_existing"] == 2
