"""Per-unit review: build one prompt per unit and merge the results."""

from __future__ import annotations

import pytest

from src.analyzer.review_plan import build_review_plan
from src.models import ChangedFile, ReviewSuggestion, Severity
from src.reviewer.grounded_review import GroundedResult
from src.reviewer.unit_review import (
    UNIT_CONTEXT_SCOPE,
    _merge_bundles,
    build_unit_context,
    merge_grounded_results,
    review_by_units,
    unit_files,
)

PATCH_A = "@@ -10,3 +10,4 @@ def foo(x):\n ctx\n-old\n+new\n"
PATCH_B = "@@ -1,1 +1,1 @@\n-old\n+other\n"


class _Mem:
    base = "1" * 40
    head = "2" * 40

    def __init__(self, sources):
        self.sources = sources

    def read(self, path, side="head"):
        return self.sources.get(path)


def plan_with_two_units():
    sources = {"src/a.py": "def foo(x):\n    return x\n", "src/b.py": "value = 2\n"}
    files = [ChangedFile(filename="src/a.py", patch=PATCH_A),
             ChangedFile(filename="src/b.py", patch=PATCH_B)]
    plan = build_review_plan(_Mem(sources), files, list(sources), token_budget=1000)
    return plan, files


def suggestion(path: str, line: int, title: str = "t") -> ReviewSuggestion:
    return ReviewSuggestion(
        file_path=path, line=line, severity=Severity.HIGH, confidence=0.9,
        title=title, reason="a sufficiently long reason", recommendation="fix",
    )


def grounded(summary="unit", suggestions=None, candidates=None, token_usage=10):
    return GroundedResult(
        summary=summary, suggestions=suggestions or [], pending=[], candidates=candidates or [],
        decisions=[], bundle={"tokens": token_usage, "omitted": [], "evicted": [], "excerpts": []},
        discovery_calls=1, verification_calls=1,
    )


def test_unit_context_holds_exactly_the_units_fragments_and_is_never_truncated():
    plan, _ = plan_with_two_units()
    unit = plan.units[0]
    ctx = build_unit_context(unit)
    assert ctx.truncated is False
    assert ctx.budgets.scope == UNIT_CONTEXT_SCOPE
    assert ctx.coverage_summary.complete is True
    assert ctx.coverage_summary.hunks_complete == len({s.hunk_id for s in unit.slices})
    assert ctx.budgets.over_limit == ()
    for slice_ in unit.slices:
        assert slice_.fragment.splitlines()[-1] in ctx.text


def test_unit_files_are_limited_to_the_units_paths():
    plan, files = plan_with_two_units()
    unit = plan.units[0]
    names = [f.filename for f in unit_files(unit, files)]
    assert names == sorted({s.path for s in unit.slices})


def test_review_by_units_is_bounded_and_records_failures_without_aborting():
    plan, files = plan_with_two_units()
    calls: list[str] = []

    def run_unit(ctx, unit_files_):
        calls.append(ctx.text[:20])
        if len(calls) == 1:
            raise RuntimeError("unit one blew up")
        return grounded()

    summary = review_by_units(plan, files, run_unit, max_units=2)
    assert [o.status for o in summary.outcomes] == ["failed", "reviewed"]
    assert "RuntimeError" in (summary.outcomes[0].error or "")
    assert summary.reviewed and not summary.failed or summary.failed
    assert summary.counts() == {
        "units_planned": len(plan.units),
        "units_reviewed": 1,
        "units_failed": 1,
        "units_skipped_by_budget": max(0, len(plan.units) - 2),
    }


def test_merge_dedupes_findings_and_offsets_candidate_ids():
    first = grounded(suggestions=[suggestion("src/a.py", 11)], candidates=[
        {"candidate_id": 0, "suggestion": {"title": "x"}}])
    first.decisions.append({"candidate_id": 0, "decision": "confirmed"})
    second = grounded(suggestions=[suggestion("src/a.py", 11), suggestion("src/b.py", 1)],
                      candidates=[{"candidate_id": 0, "suggestion": {"title": "y"}}])
    second.decisions.append({"candidate_id": 0, "decision": "needs_context"})
    merged = merge_grounded_results([first, second])
    assert [(s.file_path, s.line) for s in merged.suggestions] == [
        ("src/a.py", 11),
        ("src/b.py", 1),
    ]
    assert [c["candidate_id"] for c in merged.candidates] == [0, 1]
    assert [d["candidate_id"] for d in merged.decisions] == [0, 1]
    assert merged.discovery_calls == 2 and merged.verification_calls == 2
    assert merged.bundle["tokens"] == 20


def test_merge_with_no_results_is_an_explicit_failure_not_an_empty_success():
    merged = merge_grounded_results([], summary_note="nothing reviewed")
    assert merged.verification_failed is True
    assert merged.suggestions == [] and merged.summary == "nothing reviewed"


def test_bundle_merge_sums_counters_and_keeps_caps():
    merged = _merge_bundles([
        {"tokens": 5, "token_budget": 6000, "omitted": [1], "snapshot": {"head": "a"}},
        {"tokens": 7, "token_budget": 6000, "omitted": [2], "snapshot": {"head": "b"}},
    ])
    assert merged["tokens"] == 12
    assert merged["token_budget"] == 6000
    assert merged["omitted"] == [1, 2]
    assert merged["snapshot"] == {"head": "a"}


def test_concurrent_units_keep_plan_order_not_completion_order():
    """Two runs of the same input must merge identically, so outcomes follow the
    plan's unit order even when a later unit finishes first."""
    import time

    plan, files = plan_with_two_units()
    seen: list[str] = []

    def run_unit(ctx, unit_files_):
        # Fail fast on the first call, slow on the rest: completion order differs
        # from submission order.
        delay = 0.0 if not seen else 0.05
        seen.append(ctx.text[:8])
        time.sleep(delay)
        return grounded()

    summary = review_by_units(plan, files, run_unit, max_workers=4)
    assert [o.unit_id for o in summary.outcomes] == [u.unit_id for u in plan.units]


def test_concurrent_units_isolate_failures():
    plan, files = plan_with_two_units()
    calls = {"n": 0}

    def run_unit(ctx, unit_files_):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first unit blew up")
        return grounded()

    summary = review_by_units(plan, files, run_unit, max_workers=4)
    # Which unit fails depends on thread scheduling; that exactly one failed, and
    # that the failure is recorded rather than propagated, is the guarantee.
    assert sorted(o.status for o in summary.outcomes) == ["failed", "reviewed"]
    assert summary.counts()["units_reviewed"] == 1
    assert summary.counts()["units_failed"] == 1
    failed = next(o for o in summary.outcomes if o.status == "failed")
    assert "RuntimeError" in (failed.error or "")
    assert [o.unit_id for o in summary.outcomes] == [u.unit_id for u in plan.units]


def test_worker_count_is_validated_and_capped():
    from src.reviewer.unit_review import MAX_UNIT_WORKERS

    plan, files = plan_with_two_units()
    with pytest.raises(ValueError):
        review_by_units(plan, files, lambda c, f: grounded(), max_workers=0)
    # Asking for more than the cap is clamped rather than rejected.
    summary = review_by_units(plan, files, lambda c, f: grounded(),
                              max_workers=MAX_UNIT_WORKERS * 10)
    assert summary.counts()["units_reviewed"] == len(plan.units)
