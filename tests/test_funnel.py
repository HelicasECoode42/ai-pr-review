"""Tests for the N -> S -> D -> P funnel accounting."""

from __future__ import annotations

from src.evaluation.funnel import (
    funnel_row,
    match_gold_indexes,
    summarize_funnel,
)
from src.evaluation.gold_visibility import gold_visibility


def _gold(index, file, start, end, scope="introduced-by-pr", label="tp"):
    return {"file": file, "start_line": start, "end_line": end,
            "tp_fp": label, "scope": scope}


def _candidate(file, start, end=None):
    return {"file": file, "start_line": start, "end_line": end or start}


def test_positional_match_requires_same_file_and_overlap():
    gold = [_gold(0, "a.py", 10, 12), _gold(1, "b.py", 20, 20)]
    assert match_gold_indexes([_candidate("a.py", 11)], gold) == {0}
    assert match_gold_indexes([_candidate("a.py", 13)], gold) == set()
    assert match_gold_indexes([_candidate("c.py", 11)], gold) == set()


def test_positional_match_does_not_reuse_a_gold_finding():
    gold = [_gold(0, "a.py", 10, 12)]
    assert match_gold_indexes([_candidate("a.py", 10), _candidate("a.py", 11)], gold) == {0}


def test_funnel_row_splits_n_s_d_p_losses():
    gold = [
        _gold(0, "a.py", 10, 12),
        _gold(1, "b.py", 20, 20),
        _gold(2, "c.py", 30, 30, scope="pre-existing"),
        _gold(3, "d.py", 40, 40, label="fp"),
    ]
    visibility = gold_visibility(
        gold,
        rendered_ranges={"a.py": [(10, 12)], "b.py": [(20, 20)]},
        evidence_refs=[],
    )
    row = funnel_row(
        "case", gold, visibility=visibility,
        published=[], pending=[_candidate("b.py", 20)],
    )
    assert row.gold_pr_scope == 2
    assert row.s_fully_visible == 2
    assert row.d_identified == 1
    assert row.p_published == 0
    assert row.losses == {
        "not_selected": 0,
        "selected_not_identified": 1,
        "identified_not_published": 1,
    }


def test_funnel_row_leaves_d_p_unmeasured_without_an_artifact():
    gold = [_gold(0, "a.py", 10, 12)]
    row = funnel_row(
        "case", gold,
        visibility=gold_visibility(gold, rendered_ranges={"a.py": [(10, 12)]}),
    )
    assert row.d_identified is None
    assert row.p_published is None
    assert row.losses["selected_not_identified"] is None


def test_funnel_summary_aggregates_and_never_hides_a_layer():
    gold = [_gold(0, "a.py", 10, 12), _gold(1, "b.py", 20, 20)]
    visibility = gold_visibility(gold, rendered_ranges={"a.py": [(10, 12)]})
    row = funnel_row("case", gold, visibility=visibility, published=[], pending=[])
    summary = summarize_funnel([row])
    assert summary["gold_pr_scope"] == 2
    assert summary["s_fully_visible"] == 1
    assert summary["s_recall"] == 0.5
    assert summary["d_recall"] == 0.0
    assert summary["p_recall"] == 0.0
    assert summary["losses"] == {
        "not_selected": 1,
        "selected_not_identified": 1,
        "identified_not_published": 0,
    }


def test_semantic_adjudication_overrides_coarse_positional_match():
    # A candidate can sit inside a gold line range while describing a different
    # defect; blind adjudication is what decides D, not the overlap.
    gold = [_gold(0, "a.py", 10, 12), _gold(1, "b.py", 20, 20)]
    visibility = gold_visibility(
        gold, rendered_ranges={"a.py": [(10, 12)], "b.py": [(20, 20)]}
    )
    row = funnel_row(
        "case", gold, visibility=visibility,
        published=[], pending=[_candidate("a.py", 10), _candidate("b.py", 20)],
        adjudication={"published": [], "pending": [None, 1]},
    )
    assert row.d_identified == 2          # positional overcount
    assert row.d_semantic == 1            # only the b.py candidate is the real bug
    assert row.discovery_basis == "semantic"
    assert row.losses["selected_not_identified"] == 1


def test_semantic_adjudication_rejects_a_wrong_length_or_unknown_gold():
    import pytest

    from src.evaluation.gold_scope import GoldContractError

    gold = [_gold(0, "a.py", 10, 12)]
    visibility = gold_visibility(gold, rendered_ranges={"a.py": [(10, 12)]})
    with pytest.raises(GoldContractError):
        funnel_row(
            "case", gold, visibility=visibility,
            published=[], pending=[_candidate("a.py", 10)],
            adjudication={"published": [], "pending": []},
        )
    with pytest.raises(GoldContractError):
        funnel_row(
            "case", gold, visibility=visibility,
            published=[], pending=[_candidate("a.py", 10)],
            adjudication={"published": [], "pending": [7]},
        )
