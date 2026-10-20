"""Per-unit review: build one prompt per unit and merge the results."""

from __future__ import annotations

import hashlib
import json

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
    run_units_for_review,
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

    summary = review_by_units(plan, files, run_unit, max_units=2, max_attempts=1)
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


def test_merge_preserves_each_unit_intent_plan():
    first = grounded()
    first.bundle["intent_plan"] = {"status": "assessed", "hypotheses": [{"id": "H1"}]}
    second = grounded()
    second.bundle["intent_plan"] = {"status": "partial", "hypotheses": [{"id": "H2"}]}

    merged = merge_grounded_results([first, second])

    assert merged.bundle["intent_plan"]["status"] == "aggregate"
    plan_ids = [plan["hypotheses"][0]["id"] for plan in merged.bundle["intent_plan"]["plans"]]
    assert plan_ids == ["H1", "H2"]


def test_unit_checkpoint_round_trip_preserves_intent_plan():
    from src.reviewer.unit_review import _decode_result, _encode_result

    result = grounded()
    result.bundle["intent_plan"] = {
        "schema_version": 1,
        "status": "assessed",
        "hypotheses": [{"id": "H1", "goal": "preserve caller contract"}],
        "tasks": [{"hypothesis_id": "H1", "status": "executed_with_evidence",
                   "evidence_refs": ["a" * 64]}],
        "assessments": [{"hypothesis_id": "H1", "status": "supported",
                         "evidence_refs": ["a" * 64]}],
    }

    restored = _decode_result(_encode_result(result))

    assert restored.bundle["intent_plan"] == result.bundle["intent_plan"]


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

    summary = review_by_units(plan, files, run_unit, max_workers=4, max_attempts=1)
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


def _unit_review_report(
    tmp_path, *, units_enabled: int, checkpoint_path=None, repo_ready=False, call_counter=None,
    strategy_policy_id="baseline", strategy_policy_content_sha256=None,
    provider_close_fails=False,
):
    """Drive the engine's unit path with a scripted provider over a small repo.

    Three files with independent changes keep the diff small; the context budget
    is squeezed so the prompt is truncated, which is what gates the unit path.
    """
    import subprocess

    from src.analyzer.context_builder import build_review_context
    from src.analyzer.repository_context import GitSnapshot
    from src.evaluation.reviewbench import parse_diff
    from src.models import PullRequest
    from src.reviewer.engine import review_with_ai

    tmp_path.mkdir(parents=True, exist_ok=True)

    def run(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    if not repo_ready:
        run("init")
        run("config", "user.email", "f@e.invalid")
        run("config", "user.name", "F")

    def body(index: int, name: str) -> str:
        """Renaming the accumulator changes every line, so the patch really is
        larger than the context budget and the prompt gets truncated."""
        lines = [f"def f{index}(x):", f"    {name} = 0"]
        lines += [f"    {name} += x + {i}" for i in range(30)]
        lines += [f"    return {name}"]
        return "\n".join(lines) + "\n"

    if not repo_ready:
        for index in range(3):
            (tmp_path / f"m{index}.py").write_text(body(index, "total"))
        run("add", ".")
        run("commit", "-m", "base")
        for index in range(3):
            (tmp_path / f"m{index}.py").write_text(body(index, "accumulated"))
        run("add", ".")
        run("commit", "-m", "head")
    base = run("rev-parse", "HEAD^")
    head = run("rev-parse", "HEAD")

    class Scripted:
        """Answers every unit with a candidate anchored in that unit's file."""

        _model = "scripted-test-model"
        usage_records: list = []

        def __init__(self):
            self.calls = 0

        def complete_json(self, system, prompt):
            import json
            import re as _re

            self.calls += 1
            if call_counter is not None:
                call_counter.append(system)
            path = _re.search(r"### (\S+\.py)", prompt)
            name = path.group(1) if path else "m0.py"
            if "candidate_id" in system:  # the verification schema lives in the system prompt
                return json.dumps({"items": [{
                    "candidate_id": 0, "decision": "confirmed", "confidence": 0.9,
                    "trigger": "caller passes x", "call_path": [f"{name}:1"],
                    "expected_behavior": "the accumulator is named total",
                    "actual_behavior": "it is named accumulated",
                    "introduced_by": "the return expression changed",
                    "reason": "callers still expect the original value",
                    "evidence": [
                        {"path": name, "side": "head", "line": 2, "quote": "accumulated = 0"},
                        {"path": name, "side": "base", "line": 2, "quote": "total = 0"},
                    ],
                    "counter_evidence": []}]})
            return json.dumps({"summary": "s", "risk_level": "medium", "suggestions": [{
                "file_path": name, "line": 2, "severity": "medium", "confidence": 0.9,
                "title": f"{name}: initial total changed",
                "reason": "the accumulator starts from a different value for every caller",
                "recommendation": "restore the name", "evidence": ["accumulated = 0"],
                "failure_scenario": "every caller now gets a total one higher"}]})

        def close(self):
            if provider_close_fails:
                raise OSError("fixture cleanup failure")

    snapshot = GitSnapshot(tmp_path, base, head)
    files = parse_diff(run("diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="t", base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False,
                                  allow_remote_file_fetch=False, max_patch_tokens=200)
    assert context.truncated is True
    provider = Scripted()
    report = review_with_ai(
        pr, files, [], provider, 20, min_confidence=.65, repository_snapshot=snapshot,
        follow_changes=True, review_context=context,
        unit_review_max_units=units_enabled, unit_review_workers=1,
        unit_checkpoint_path=str(checkpoint_path) if checkpoint_path is not None else None,
        unit_provider_factory=Scripted,
        strategy_policy_id=strategy_policy_id,
        strategy_policy_content_sha256=strategy_policy_content_sha256,
    )
    return report, provider


def test_unit_review_reviews_every_unit_when_the_prompt_is_truncated(tmp_path):
    report, _ = _unit_review_report(tmp_path, units_enabled=8)
    assert report.metrics.get("unit_review") == 1
    # Tokenizer choice can change how many slices fit the planner budget. The
    # invariant is the configured execution cap and complete accounting, not a
    # fixed unit count tied to one local tokenizer cache.
    assert report.metrics["units_reviewed"] == 8
    assert report.metrics["units_skipped_by_budget"] > 0
    assert report.metrics["units_planned"] == (
        report.metrics["units_reviewed"]
        + report.metrics["units_failed"]
        + report.metrics["units_skipped_by_budget"]
    )
    assert report.metrics["units_failed"] == 0


def test_unit_checkpoint_write_failure_keeps_report_and_marks_it_partial(tmp_path):
    checkpoint_path = tmp_path / "metadata-is-a-directory"
    checkpoint_path.mkdir()
    report, _ = _unit_review_report(tmp_path / "repo", units_enabled=8,
                                    checkpoint_path=checkpoint_path)
    assert report.metrics["unit_checkpoint_failed"] == 8
    assert report.execution_status == "degraded"
    assert report.report_confidence == "partial"
    assert report.suggestions or report.pending_suggestions
    assert any("checkpoint persistence failed" in w for w in report.analysis_warnings)


def test_engine_resume_uses_restored_result_without_fake_provider_calls(tmp_path):
    checkpoint = tmp_path / "units.jsonl"
    call_counter: list[str] = []
    repo = tmp_path / "repo"
    first, _ = _unit_review_report(
        repo, units_enabled=8, checkpoint_path=checkpoint, call_counter=call_counter
    )
    expected = [s.model_dump(mode="json") for s in first.suggestions]
    first_discovery_calls = first.metrics["discovery_request_count"]
    first_verification_calls = first.metrics["behavior_verification_request_count"]
    assert call_counter

    call_counter.clear()
    second, _ = _unit_review_report(
        repo, units_enabled=8, checkpoint_path=checkpoint, repo_ready=True,
        call_counter=call_counter,
    )
    assert call_counter == []
    assert second.metrics["units_resumed"] == 8
    assert [s.model_dump(mode="json") for s in second.suggestions] == expected
    assert second.metrics["discovery_request_count"] == 0
    assert second.metrics["behavior_verification_request_count"] == 0
    assert second.metrics["discovery_calls_restored"] == first_discovery_calls > 0
    assert second.metrics["verification_calls_restored"] == first_verification_calls > 0


def test_engine_policy_change_rejects_previous_unit_checkpoint(tmp_path):
    checkpoint = tmp_path / "units.jsonl"
    call_counter: list[str] = []
    repo = tmp_path / "repo"
    _unit_review_report(
        repo, units_enabled=8, checkpoint_path=checkpoint, call_counter=call_counter,
        strategy_policy_id="policy-a", strategy_policy_content_sha256="a" * 64,
    )
    assert call_counter

    call_counter.clear()
    second, _ = _unit_review_report(
        repo, units_enabled=8, checkpoint_path=checkpoint, repo_ready=True,
        call_counter=call_counter, strategy_policy_id="policy-b",
        strategy_policy_content_sha256="b" * 64,
    )
    assert call_counter
    assert second.metrics["units_resumed"] == 0
    assert second.repository_context["unit_details"]["unit_checkpoint_issues"]


def test_unit_review_is_off_by_default(tmp_path):
    report, provider = _unit_review_report(tmp_path, units_enabled=0)
    assert "unit_review" not in report.metrics
    # The single-pass path makes far fewer calls than one per unit.
    assert provider.calls <= 3


def test_unit_review_is_refused_before_dispatch_when_it_would_cost_too_much(tmp_path):
    """H13: the expensive path must be refused before spending, with a reason."""
    import subprocess

    from src.analyzer.context_builder import build_review_context
    from src.analyzer.repository_context import GitSnapshot
    from src.evaluation.reviewbench import parse_diff
    from src.models import PullRequest
    from src.reviewer.engine import review_with_ai

    def run(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    run("init")
    run("config", "user.email", "f@e.invalid")
    run("config", "user.name", "F")
    for index in range(3):
        lines = [f"def f{index}(x):", "    total = 0"]
        lines += [f"    total += x + {i}" for i in range(30)]
        (tmp_path / f"m{index}.py").write_text("\n".join(lines + ["    return total"]) + "\n")
    run("add", "."); run("commit", "-m", "base")
    base = run("rev-parse", "HEAD")
    for index in range(3):
        lines = [f"def f{index}(x):", "    acc = 0"]
        lines += [f"    acc += x + {i}" for i in range(30)]
        (tmp_path / f"m{index}.py").write_text("\n".join(lines + ["    return acc"]) + "\n")
    run("add", "."); run("commit", "-m", "head")
    head = run("rev-parse", "HEAD")

    class Scripted:
        usage_records: list = []

        def complete_json(self, system, prompt):
            import json
            return json.dumps({"summary": "s", "risk_level": "low", "suggestions": []})

        def close(self):
            pass

    snapshot = GitSnapshot(tmp_path, base, head)
    files = parse_diff(run("diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="t", base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False,
                                  allow_remote_file_fetch=False, max_patch_tokens=200)
    report = review_with_ai(
        pr, files, [], Scripted(), 20, repository_snapshot=snapshot, follow_changes=True,
        review_context=context, unit_review_max_units=8, unit_review_workers=1,
        unit_review_cost_ceiling_tokens=1,   # anything positive is refused
        unit_provider_factory=Scripted,
    )
    assert "unit_review" not in report.metrics          # never dispatched
    assert report.metrics["unit_review_skipped_cost"] == 1
    assert report.metrics["unit_review_estimated_tokens"] > 1
    assert any("Per-unit review refused before dispatch" in w for w in report.analysis_warnings)


def test_a_failing_unit_is_retried_and_then_recorded(tmp_path):
    plan, files = plan_with_two_units()
    attempts: list[int] = []

    def flaky(ctx, unit_files_):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return grounded()

    summary = review_by_units(plan, files, flaky, max_attempts=2)
    assert [o.status for o in summary.outcomes][0] == "reviewed"
    assert summary.outcomes[0].attempts == 2


def test_a_run_can_be_resumed_from_its_checkpoint(tmp_path):
    plan, files = plan_with_two_units()
    checkpoint = tmp_path / "units.jsonl"

    calls: list[str] = []
    identity = {"repo": "owner/repo", "number": 7, "base_sha": plan.base,
                "head_sha": plan.head, "review_config_hash": "test"}
    # First write a result using the same identity required for resume.
    first = review_by_units(
        plan, files, lambda c, f: grounded(suggestions=[suggestion(f[0].filename, 1)]),
        checkpoint_path=checkpoint, checkpoint_identity=identity,
    )
    assert all(o.status == "reviewed" for o in first.outcomes)
    calls.clear()
    second = review_by_units(plan, files, lambda c, f: calls.append("x") or grounded(),
                             checkpoint_path=checkpoint, checkpoint_identity=identity)
    # Nothing was re-run: every unit was already terminal in the checkpoint.
    assert calls == []
    assert [o.status for o in second.outcomes] == ["resumed"] * len(plan.units)


def test_resumed_unit_results_are_restored_into_the_production_merge(tmp_path):
    plan, files = plan_with_two_units()
    snapshot = _Mem({"src/a.py": "def foo(x):\n    return x\n", "src/b.py": "value = 2\n"})
    checkpoint = tmp_path / "units.jsonl"

    def review(ctx, unit_files_):
        path = unit_files_[0].filename
        return grounded(
            summary=f"reviewed {path}",
            suggestions=[suggestion(path, 1, f"finding in {path}")],
            candidates=[{"candidate_id": 0, "suggestion": {"file_path": path}}],
        )

    first, _ = run_units_for_review(
        review, snapshot, files, token_budget=1000, max_units=8, checkpoint_path=checkpoint,
        checkpoint_identity={"repo": "owner/repo", "number": 7},
    )
    expected_suggestions = [s.model_dump(mode="json") for s in first.suggestions]
    expected_candidates = first.candidates

    def must_not_call(ctx, unit_files_):
        raise AssertionError("completed units should be restored without provider calls")

    resumed, details = run_units_for_review(
        must_not_call, snapshot, files, token_budget=1000, max_units=8,
        checkpoint_path=checkpoint, checkpoint_identity={"repo": "owner/repo", "number": 7},
    )
    assert details["units_resumed"] == len(plan.units)
    assert [s.model_dump(mode="json") for s in resumed.suggestions] == expected_suggestions
    assert resumed.candidates == expected_candidates


def test_checkpoint_identity_change_reexecutes_unit_instead_of_reusing_result(tmp_path):
    plan, files = plan_with_two_units()
    snapshot = _Mem({"src/a.py": "def foo(x):\n    return x\n", "src/b.py": "value = 2\n"})
    checkpoint = tmp_path / "units.jsonl"
    calls: list[str] = []

    def review(ctx, unit_files_):
        path = unit_files_[0].filename
        calls.append(path)
        return grounded(suggestions=[suggestion(path, 1, f"model result {len(calls)}")])

    run_units_for_review(
        review, snapshot, files, token_budget=1000, max_units=1, checkpoint_path=checkpoint,
        checkpoint_identity={"repo": "owner/repo", "number": 7, "model": "model-a"},
    )
    calls.clear()
    result, details = run_units_for_review(
        review, snapshot, files, token_budget=1000, max_units=1, checkpoint_path=checkpoint,
        checkpoint_identity={"repo": "owner/repo", "number": 7, "model": "model-b"},
    )
    assert len(calls) == 1
    assert details["units_resumed"] == 0
    assert details["unit_checkpoint_issues"]
    assert result.suggestions[0].title == "model result 1"


def test_witness_criteria_version_change_invalidates_old_unit_checkpoint(tmp_path):
    from src.reviewer.criteria import CRITERIA_VERSION

    plan, files = plan_with_two_units()
    snapshot = _Mem({"src/a.py": "def foo(x):\n    return x\n", "src/b.py": "value = 2\n"})
    checkpoint = tmp_path / "units.jsonl"
    calls: list[str] = []

    def review(ctx, unit_files_):
        calls.append(unit_files_[0].filename)
        return grounded(suggestions=[suggestion(unit_files_[0].filename, 1)])

    run_units_for_review(
        review, snapshot, files, token_budget=1000, max_units=1, checkpoint_path=checkpoint,
        checkpoint_identity={"repo": "owner/repo", "criteria_version": "grounded-v3"},
    )
    calls.clear()
    result, details = run_units_for_review(
        review, snapshot, files, token_budget=1000, max_units=1, checkpoint_path=checkpoint,
        checkpoint_identity={"repo": "owner/repo", "criteria_version": CRITERIA_VERSION},
    )
    assert calls
    assert details["units_resumed"] == 0
    assert details["unit_checkpoint_issues"]
    assert result.suggestions


@pytest.mark.parametrize("mutation", ["missing", "truncated", "schema"])
def test_invalid_checkpoint_artifact_reexecutes_unit(tmp_path, mutation):
    plan, files = plan_with_two_units()
    snapshot = _Mem({"src/a.py": "def foo(x):\n    return x\n", "src/b.py": "value = 2\n"})
    checkpoint = tmp_path / "units.jsonl"
    identity = {"repo": "owner/repo", "number": 7, "model": "model-a"}
    calls: list[str] = []

    def review(ctx, unit_files_):
        calls.append(unit_files_[0].filename)
        return grounded(suggestions=[suggestion(unit_files_[0].filename, 1)])

    run_units_for_review(
        review, snapshot, files, token_budget=1000, max_units=1, checkpoint_path=checkpoint,
        checkpoint_identity=identity,
    )
    record = json.loads(checkpoint.read_text(encoding="utf-8").splitlines()[-1])
    artifact = checkpoint.with_name(checkpoint.name + ".results") / record["artifact"]
    if mutation == "missing":
        artifact.unlink()
    elif mutation == "truncated":
        artifact.write_bytes(b'{"schema_version":1')
    else:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        payload["schema_version"] = 99
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        artifact.write_bytes(data)
        record["artifact_sha256"] = hashlib.sha256(data).hexdigest()
        checkpoint.write_text(json.dumps(record) + "\n", encoding="utf-8")

    calls.clear()
    result, details = run_units_for_review(
        review, snapshot, files, token_budget=1000, max_units=1, checkpoint_path=checkpoint,
        checkpoint_identity=identity,
    )
    assert len(calls) == 1
    assert details["units_resumed"] == 0
    assert details["unit_checkpoint_issues"]
    assert result.suggestions


def test_checkpoint_write_failure_keeps_current_findings_and_marks_partial(tmp_path):
    plan, files = plan_with_two_units()
    snapshot = _Mem({"src/a.py": "def foo(x):\n    return x\n", "src/b.py": "value = 2\n"})
    # A directory cannot be opened as the append-only metadata file; artifact
    # storage still succeeds, and this run must preserve its in-memory result.
    checkpoint = tmp_path / "not-a-file"
    checkpoint.mkdir()
    result, details = run_units_for_review(
        lambda ctx, unit_files_: grounded(
            suggestions=[suggestion(unit_files_[0].filename, 1)]
        ),
        snapshot, files, token_budget=1000, max_units=1, checkpoint_path=checkpoint,
        checkpoint_identity={"repo": "owner/repo", "number": 7, "model": "model-a"},
    )
    assert details["unit_checkpoint_failed"] == 1
    assert result.suggestions
    assert f"checkpoint_write_failed:{plan.units[0].unit_id}" in details[
        "unit_checkpoint_issues"
    ]


def test_orphan_artifact_without_metadata_is_never_treated_as_resumed(tmp_path):
    plan, files = plan_with_two_units()
    snapshot = _Mem({"src/a.py": "def foo(x):\n    return x\n", "src/b.py": "value = 2\n"})
    checkpoint = tmp_path / "units.jsonl"
    identity = {"repo": "owner/repo", "number": 7, "model": "model-a"}
    calls: list[str] = []

    def review(ctx, unit_files_):
        calls.append(unit_files_[0].filename)
        return grounded(suggestions=[suggestion(unit_files_[0].filename, 1)])

    run_units_for_review(
        review, snapshot, files, token_budget=1000, max_units=1, checkpoint_path=checkpoint,
        checkpoint_identity=identity,
    )
    # Simulate a process dying after the result artifact is durable but before
    # metadata references it.
    checkpoint.write_text("", encoding="utf-8")
    calls.clear()
    result, details = run_units_for_review(
        review, snapshot, files, token_budget=1000, max_units=1, checkpoint_path=checkpoint,
        checkpoint_identity=identity,
    )
    assert len(calls) == 1
    assert details["units_resumed"] == 0
    assert result.suggestions


def test_cancellation_stops_the_run_and_marks_the_rest(tmp_path):
    from threading import Event

    plan, files = plan_with_two_units()
    cancel = Event()
    cancel.set()
    summary = review_by_units(plan, files, lambda c, f: grounded(), cancel=cancel)
    assert {o.status for o in summary.outcomes} == {"cancelled"}
    assert summary.counts()["units_reviewed"] == 0


def test_cancellation_after_failure_prevents_another_paid_attempt():
    from threading import Event

    plan, files = plan_with_two_units()
    cancel = Event()
    calls = []

    def fail_and_cancel(context, unit_files_):
        calls.append(unit_files_[0].filename)
        cancel.set()
        raise RuntimeError("cancelled while the first request failed")

    summary = review_by_units(plan, files, fail_and_cancel, cancel=cancel, max_attempts=3)

    assert len(calls) == 1
    assert [outcome.status for outcome in summary.outcomes] == ["cancelled", "cancelled"]
    assert [outcome.attempts for outcome in summary.outcomes] == [1, 0]


def test_cancellation_interrupts_retry_backoff():
    from threading import Event, Thread

    plan, files = plan_with_two_units()
    backoff_started = Event()
    calls = []

    class SignallingEvent(Event):
        def wait(self, timeout=None):
            backoff_started.set()
            return super().wait(timeout)

    cancel = SignallingEvent()

    def fail_once(context, unit_files_):
        calls.append(unit_files_[0].filename)
        raise RuntimeError("transient")

    summaries = []
    worker = Thread(target=lambda: summaries.append(review_by_units(
        plan, files, fail_once, max_attempts=3, retry_backoff_seconds=30, cancel=cancel
    )))
    worker.start()
    assert backoff_started.wait(5)
    cancel.set()
    worker.join(5)

    assert not worker.is_alive()
    assert len(calls) == 1
    assert summaries[0].outcomes[0].status == "cancelled"
    assert summaries[0].outcomes[0].attempts == 1


def test_every_unit_has_a_terminal_status_even_if_the_provider_explodes(tmp_path):
    """Reliable return: no unit is ever left without an outcome."""
    plan, files = plan_with_two_units()

    def always_fails(ctx, unit_files_):
        raise RuntimeError("provider down")

    summary = review_by_units(plan, files, always_fails, max_attempts=2)
    assert len(summary.outcomes) == len(plan.units)
    assert all(o.status == "failed" for o in summary.outcomes)
    assert all(o.attempts == 2 for o in summary.outcomes)
    assert all(o.error and "provider down" in o.error for o in summary.outcomes)


def test_engine_does_not_dispatch_fallback_when_checkpoint_is_busy(tmp_path):
    from src.reviewer.unit_review import _CheckpointRunLock

    checkpoint = tmp_path / "units.jsonl"
    calls = []
    lock = _CheckpointRunLock(checkpoint)
    try:
        report, provider = _unit_review_report(
            tmp_path / "repo", units_enabled=8, checkpoint_path=checkpoint,
            call_counter=calls,
        )
    finally:
        lock.release()
    assert calls == []
    assert provider.calls == 0
    assert report.execution_status == "partial"
    assert report.report_confidence == "partial"
    assert report.metrics["units_reviewed"] == 0
    assert report.metrics["units_failed"] > 0


def test_unit_provider_cleanup_failure_does_not_retry_completed_model_work(tmp_path):
    normal_calls = []
    normal, _ = _unit_review_report(
        tmp_path / "normal", units_enabled=8, call_counter=normal_calls,
    )
    failed_calls = []
    failed, _ = _unit_review_report(
        tmp_path / "cleanup-fails", units_enabled=8,
        call_counter=failed_calls, provider_close_fails=True,
    )
    assert len(failed_calls) == len(normal_calls) > 0
    assert failed.metrics["units_reviewed"] == normal.metrics["units_reviewed"] == 8
    assert failed.metrics["unit_attempts"] == 8
    assert failed.report_confidence == "partial"
    assert any("cleanup failed" in message for message in failed.analysis_warnings)
