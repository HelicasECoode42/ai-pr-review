"""Tests for the free S measurement: were gold lines actually shown to the model?"""

from __future__ import annotations

from src.analyzer.context_builder import _rendered_head_ranges
from src.evaluation.gold_visibility import (
    gold_visibility,
    summarize_visibility,
    visibility_from_trace,
)


def _f(file, start, end, label="tp", scope="introduced-by-pr"):
    return {"file": file, "start_line": start, "end_line": end,
            "tp_fp": label, "scope": scope}


def test_diff_and_source_visibility_are_measured_per_line():
    findings = [
        _f("a.py", 10, 12),      # fully in the diff
        _f("b.py", 40, 60),      # only a source excerpt covers it
        _f("c.py", 1, 3),        # nowhere
    ]
    results = gold_visibility(
        findings,
        rendered_ranges={"a.py": [(10, 12), (30, 35)]},
        evidence_refs=[
            {"path": "b.py", "side": "head", "start_line": 38, "end_line": 62},
        ],
    )
    assert [item.reason for item in results] == [
        "fully_visible", "fully_visible", "not_rendered",
    ]
    assert results[0].diff_visible and not results[0].source_visible
    assert results[1].source_visible and not results[1].diff_visible
    assert results[2].visible_lines == 0


def test_partial_line_coverage_is_not_counted_as_visible():
    # CrabTrap shape: the diff hunk only shows the tail of a 26-line finding.
    findings = [_f("reader.go", 370, 395)]
    results = gold_visibility(
        findings,
        rendered_ranges={"reader.go": [(388, 396)]},
        evidence_refs=[],
    )
    item = results[0]
    assert item.reason == "partially_visible"
    assert item.visible_lines == 8
    assert item.range_lines == 26
    assert not item.fully_visible


def test_base_side_excerpts_do_not_mask_head_visibility():
    findings = [_f("a.py", 5, 6)]
    results = gold_visibility(
        findings,
        rendered_ranges={},
        evidence_refs=[{"path": "a.py", "side": "base", "start_line": 1, "end_line": 10}],
    )
    assert results[0].reason == "not_rendered"


def test_range_boundaries_are_inclusive():
    findings = [_f("a.py", 7, 9)]
    results = gold_visibility(
        findings,
        rendered_ranges={"a.py": [(7, 9)]},
        evidence_refs=[],
    )
    assert results[0].fully_visible


def test_unlocatable_gold_is_reported_not_silently_visible():
    findings = [{"file": None, "start_line": None, "end_line": None, "tp_fp": "tp",
                 "scope": "introduced-by-pr"}]
    results = gold_visibility(findings, rendered_ranges={}, evidence_refs=[])
    assert results[0].reason == "unlocatable_gold_line"
    summary = summarize_visibility(results)
    assert summary["unlocatable"] == 1
    assert summary["not_visible"] == 1


def test_summary_scopes_to_pr_scope_true_positives():
    findings = [
        _f("a.py", 1, 1, scope="introduced-by-pr"),
        _f("b.py", 1, 1, scope="exacerbated-by-pr"),
        _f("c.py", 1, 1, scope="pre-existing"),
        _f("d.py", 1, 1, label="fp", scope="introduced-by-pr"),
        _f("e.py", 1, 2, scope="introduced-by-pr"),
    ]
    results = gold_visibility(
        findings,
        rendered_ranges={"a.py": [(1, 1)], "b.py": [(1, 1)], "c.py": [(1, 1)],
                         "d.py": [(1, 1)], "e.py": [(1, 1)]},
        evidence_refs=[],
    )
    summary = summarize_visibility(results)
    assert summary["gold_denominator"] == 3
    assert summary["fully_visible"] == 2
    assert summary["partially_visible"] == 1
    assert summary["s_recall"] == 0.6667
    assert summary["mean_line_coverage"] == 0.8333


def test_visibility_can_be_rebuilt_from_a_saved_review_trace():
    trace = {
        "coverage": {"rendered_head_ranges": [
            {"path": "api.go", "ranges": [[264, 342]]},
        ]},
        "evidence_refs": [
            {"path": "helper.go", "side": "head", "start_line": 60, "end_line": 121},
            {"path": "helper.go", "side": "base", "start_line": 60, "end_line": 121},
        ],
    }
    findings = [_f("api.go", 300, 320), _f("helper.go", 100, 110)]
    results = visibility_from_trace(trace, findings)
    assert [item.reason for item in results] == ["fully_visible", "fully_visible"]


def test_rendered_head_ranges_ignore_removed_lines_and_later_hunks():
    fragment = (
        "@@ -10,4 +10,5 @@\n"
        " context one\n"
        "-removed line\n"
        "+added line\n"
        "+added two\n"
        " context two\n"
        "@@ -40,2 +41,3 @@\n"
        " context three\n"
        "+added three\n"
    )
    assert _rendered_head_ranges(fragment) == ((10, 13), (41, 42))


def test_rendered_head_ranges_do_not_claim_a_clipped_tail():
    # Truncation can leave a fragment line without a valid diff prefix.
    fragment = "@@ -1,3 +1,3 @@\n context\n+added\nfragment cut mid-line"
    assert _rendered_head_ranges(fragment) == ((1, 2),)
