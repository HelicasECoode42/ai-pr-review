from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.analyzer.context_builder import build_review_context
from src.analyzer.repository_context import EvidenceBundle, GitSnapshot, safe_source_path
from src.evaluation.reviewbench import parse_diff
from src.models import PullRequest
from src.reviewer.engine import review_with_ai
from src.reviewer.grounded_review import review_grounded


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    (tmp_path / "users.py").write_text('def get_profile():\n    return ("name", 42)\n')
    (tmp_path / "helper.py").write_text('def decision_helper():\n    return "frozen evidence"\n')
    (tmp_path / "caller.py").write_text('from users import get_profile\ndef render():\n    name, age = get_profile()\n    return age + 1\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "users.py").write_text('def get_profile():\n    return {"name": "name", "age": 42}\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "head")
    head = git(tmp_path, "rev-parse", "HEAD")
    return tmp_path, base, head


class Provider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
    def complete_json(self, system, prompt):
        self.calls.append((system, prompt))
        response = next(self.responses)
        if callable(response):
            response = response(system, prompt)
        return json.dumps(response)


def candidate(confidence=.4):
    return {"summary": "Return contract changed", "risk_level": "high", "suggestions": [{
        "file_path": "users.py", "line": 2, "severity": "high", "confidence": confidence,
        "title": "Caller still expects positional values", "reason": "Dictionary iteration returns keys, not the former values",
        "recommendation": "Preserve tuple return or migrate caller", "evidence": ['return {"name": "name", "age": 42}'],
        "failure_scenario": "render tries to add one to a string age"}]}


def verdict(**updates):
    item = {"candidate_id": 0, "decision": "confirmed", "confidence": .9,
            "trigger": "render calls get_profile", "call_path": ["caller.py:3", "users.py:2"],
            "expected_behavior": "age is an integer", "actual_behavior": "age is the dictionary key age, causing TypeError",
            "introduced_by": "return changed from tuple to dictionary", "reason": "Caller contract not migrated",
            "evidence": [{"path": "caller.py", "side": "head", "line": 3, "quote": "name, age = get_profile()"},
                         {"path": "users.py", "side": "base", "line": 2, "quote": 'return ("name", 42)'}],
            "counter_evidence": []}
    return {"items": [{**item, **updates}]}


def run(repo, responses, *, description="", trace_input_identity=None, evidence_tokens=6000,
        missing_evidence_followup=False, raw_discovery=False):
    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="Return contract", body=description,
                     base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False, allow_remote_file_fetch=False)
    provider = Provider(responses)
    report = review_with_ai(pr, files, [], provider, 20, min_confidence=.65,
                            repository_snapshot=snapshot, follow_changes=True,
                            repository_evidence_tokens=evidence_tokens,
                            review_context=context,
                            trace_input_identity=trace_input_identity,
                            grounded_missing_evidence_followup=missing_evidence_followup,
                            raw_discovery=raw_discovery)
    return report, provider


def planned_hypotheses():
    return [{
        "id": "H1",
        "goal": "Preserve the return contract used by callers",
        "constraints": ["Changed HEAD callers must accept the new shape"],
        "risk_hypotheses": ["An unchanged caller may still unpack the former tuple"],
        "unknowns": ["Whether all callers were migrated"],
        "source_refs": ["diff:users.py"],
    }]


def intent_search_request():
    return [{
        "hypothesis_id": "H1", "purpose": "Check the unchanged caller contract",
        "tool": "search", "symbol": "get_profile", "side": "head",
    }]


def task_evidence_refs(prompt, hypothesis_id="H1"):
    marker = "## Intent hypotheses (untrusted, evidence-linked annotations only)\n"
    plan, _ = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1].lstrip())
    return [ref for task in plan["tasks"] if task["hypothesis_id"] == hypothesis_id
            for ref in task["evidence_refs"]]


def test_intent_plan_executes_frozen_cross_file_task_and_verification_cites_it(repo):
    def assess(system, prompt):
        refs = task_evidence_refs(prompt)
        return {**verdict(), "intent_assessments": [{
            "hypothesis_id": "H1", "status": "supported",
            "evidence_refs": refs, "rationale": "The unchanged caller still unpacks two values",
            "unknowns": [],
        }]}

    report, provider = run(repo, [
        {"intent_hypotheses": planned_hypotheses(), "context_requests": intent_search_request()},
        {**candidate(), "intent_hypotheses": planned_hypotheses()},
        assess,
    ])

    intent = report.repository_context["intent_plan"]
    assert intent["status"] == "assessed"
    assert intent["tasks"][0]["hypothesis_id"] == "H1"
    assert intent["tasks"][0]["status"] == "executed_with_evidence"
    assert intent["tasks"][0]["evidence_refs"]
    assert "name, age = get_profile()" in provider.calls[-1][1]
    assert intent["assessments"][0]["evidence_refs"] == intent["tasks"][0]["evidence_refs"]
    assert len(report.suggestions) == 1


def test_declared_missing_evidence_uses_one_existing_bounded_followup_slot(repo):
    hypothesis = {
        **planned_hypotheses()[0],
        "missing_evidence": [{"id": "helper", "description": "Read the helper implementation"}],
    }

    def final_assessment(system, prompt):
        refs = task_evidence_refs(prompt)
        assert "def decision_helper()" in prompt
        return {**verdict(), "intent_assessments": [{
            "hypothesis_id": "H1", "status": "supported", "evidence_refs": refs,
            "rationale": "The declared helper source is visible in the frozen bundle.",
            "unknowns": [],
        }]}

    report, provider = run(repo, [
        {**candidate(), "intent_hypotheses": [hypothesis]},
        {"items": [{**verdict()["items"][0], "decision": "needs_context"}],
         "missing_evidence_requests": [{
            "hypothesis_id": "H1", "missing_id": "helper",
            "purpose": "Read the missing helper implementation",
            "tool": "read", "path": "helper.py", "line": 1, "side": "head",
        }]},
        final_assessment,
    ], missing_evidence_followup=True)

    trace = report.repository_context["review_trace"]
    assert len(provider.calls) == 3  # discovery + existing verify slots; no extra model round
    assert trace["missing_evidence_followup_enabled"] is True
    obligation = trace["intent"]["hypotheses"][0]["missing_evidence"][0]
    assert obligation["status"] == "evidence_visible"
    assert obligation["code_ranges"][0]["path"] == "helper.py"
    assert obligation["code_ranges"][0]["start_line"] <= 1
    task = next(task for task in trace["intent"]["tasks"] if task["followup"])
    assert task["missing_id"] == "helper"
    assert task["evidence_ref_ids"]
    candidate_record = trace["intent"]["candidate_evidence"][0]
    assert candidate_record["candidate_id"] == 0
    assert candidate_record["code_ranges"]


def test_needs_context_without_declared_obligation_does_not_invent_a_followup(repo):
    missing_context = {**verdict()["items"][0], "decision": "needs_context"}
    report, provider = run(repo, [
        {**candidate(), "intent_hypotheses": planned_hypotheses()},
        {"items": [missing_context]},
    ],
                           missing_evidence_followup=True)

    assert len(provider.calls) == 2
    assert report.pending_suggestions
    assert report.repository_context["requests"] == []
    trace = report.repository_context["review_trace"]
    assert trace["intent"]["hypotheses"][0]["missing_evidence"] == []
    assert trace["intent"]["tasks"] == []


def test_duplicate_missing_evidence_obligation_request_is_recorded_without_second_read(repo):
    hypothesis = {
        **planned_hypotheses()[0],
        "missing_evidence": [{"id": "helper", "description": "Read helper behavior"}],
    }
    request = {
        "hypothesis_id": "H1", "missing_id": "helper",
        "purpose": "Read the helper implementation",
        "tool": "read", "path": "helper.py", "line": 1, "side": "head",
    }
    report, _ = run(repo, [
        {**candidate(), "intent_hypotheses": [hypothesis]},
        {"missing_evidence_requests": [request, request]},
        verdict(),
    ], missing_evidence_followup=True)

    tasks = report.repository_context["review_trace"]["intent"]["tasks"]
    assert sorted(task["status"] for task in tasks if task["followup"]) == sorted([
        "executed_with_evidence", "duplicate_missing_evidence_request",
    ])
    executed_reads = [entry for entry in report.repository_context["requests"]
                      if entry.get("normalized_query", {}).get("tool") == "read"]
    assert len(executed_reads) == 1


def test_second_missing_evidence_request_is_recorded_at_single_followup_limit(repo):
    hypothesis = {
        **planned_hypotheses()[0],
        "missing_evidence": [
            {"id": "helper", "description": "Read helper behavior"},
            {"id": "caller", "description": "Read caller contract"},
        ],
    }
    first_request = {
        "hypothesis_id": "H1", "missing_id": "helper",
        "purpose": "Read the helper implementation",
        "tool": "read", "path": "helper.py", "line": 1, "side": "head",
    }
    second_request = {
        "hypothesis_id": "H1", "missing_id": "caller",
        "purpose": "Read the caller contract",
        "tool": "read", "path": "caller.py", "line": 3, "side": "head",
    }
    second_verification = {
        "items": [{**verdict()["items"][0], "decision": "needs_context"}],
        "missing_evidence_requests": [second_request],
    }
    report, _ = run(repo, [
        {**candidate(), "intent_hypotheses": [hypothesis]},
        {"missing_evidence_requests": [first_request]},
        second_verification,
    ], missing_evidence_followup=True)

    trace = report.repository_context["review_trace"]["intent"]
    tasks = [task for task in trace["tasks"] if task["followup"]]
    assert {task["status"] for task in tasks} == {
        "executed_with_evidence", "not_executed_followup_limit",
    }
    caller_obligation = next(
        obligation for hypothesis_trace in trace["hypotheses"]
        for obligation in hypothesis_trace["missing_evidence"]
        if obligation["missing_id"] == "caller"
    )
    assert caller_obligation["status"] == "followup_limit_rejected"
    assert len(report.repository_context["requests"]) == 1


def test_declared_missing_evidence_without_request_stays_not_requested(repo):
    hypothesis = {
        **planned_hypotheses()[0],
        "missing_evidence": [{"id": "helper", "description": "Read helper behavior"}],
    }
    report, provider = run(repo, [
        {**candidate(), "intent_hypotheses": [hypothesis]},
        {**verdict(), "missing_evidence_requests": []},
    ], missing_evidence_followup=True)

    assert len(provider.calls) == 2
    obligation = report.repository_context["review_trace"]["intent"]["hypotheses"][0][
        "missing_evidence"
    ][0]
    assert obligation["status"] == "not_requested"
    assert report.repository_context["requests"] == []


def test_unbound_missing_id_cannot_execute_or_dismiss_without_counter_evidence(repo):
    hypothesis = {
        **planned_hypotheses()[0],
        "missing_evidence": [{"id": "helper", "description": "Read helper behavior"}],
    }
    dismissal = verdict()["items"][0] | {"decision": "dismissed", "counter_evidence": []}
    report, provider = run(repo, [
        {**candidate(), "intent_hypotheses": [hypothesis]},
        {"missing_evidence_requests": [{
            "hypothesis_id": "H1", "missing_id": "invented",
            "purpose": "Read the helper implementation",
            "tool": "read", "path": "helper.py", "line": 1, "side": "head",
        }]},
        {"items": [dismissal]},
    ], missing_evidence_followup=True)

    assert "def decision_helper()" not in provider.calls[-1][1]
    assert not report.suggestions
    assert report.pending_suggestions
    trace = report.repository_context["review_trace"]
    obligation = trace["intent"]["hypotheses"][0]["missing_evidence"][0]
    assert obligation["status"] == "not_requested"
    task = next(task for task in trace["intent"]["tasks"] if task["followup"])
    assert task["status"] == "unknown_missing_evidence_id"


@pytest.mark.parametrize("invalid_missing_id", [["helper"], {"id": "helper"}])
def test_unhashable_missing_id_is_rejected_without_aborting_verification(
    repo, invalid_missing_id
):
    hypothesis = {
        **planned_hypotheses()[0],
        "missing_evidence": [{"id": "helper", "description": "Read helper behavior"}],
    }
    report, provider = run(repo, [
        {**candidate(), "intent_hypotheses": [hypothesis]},
        {"missing_evidence_requests": [{
            "hypothesis_id": "H1", "missing_id": invalid_missing_id,
            "purpose": "Read the helper implementation",
            "tool": "read", "path": "helper.py", "line": 1, "side": "head",
        }]},
        verdict(),
    ], missing_evidence_followup=True)

    assert len(provider.calls) == 2
    task = next(task for task in report.repository_context["review_trace"]["intent"]["tasks"]
                if task.get("followup"))
    assert task["status"] == "missing_evidence_id_required"


def test_missing_evidence_followup_budget_omission_is_not_called_failed_read(repo):
    hypothesis = {
        **planned_hypotheses()[0],
        "missing_evidence": [{"id": "helper", "description": "Read helper behavior"}],
    }
    report, provider = run(repo, [
        {**candidate(), "intent_hypotheses": [hypothesis]},
        {"missing_evidence_requests": [{
            "hypothesis_id": "H1", "missing_id": "helper",
            "purpose": "Read the helper implementation",
            "tool": "read", "path": "helper.py", "line": 1, "side": "head",
        }]},
        verdict(),
    ], evidence_tokens=1, missing_evidence_followup=True)

    assert len(provider.calls) == 3
    obligation = report.repository_context["review_trace"]["intent"]["hypotheses"][0][
        "missing_evidence"
    ][0]
    assert obligation["status"] == "budget_rejected"


def test_same_visible_query_reuses_refs_for_another_hypothesis_obligation(repo):
    hypotheses = [
        {**planned_hypotheses()[0], "missing_evidence": [
            {"id": "first", "description": "Read helper implementation"},
        ]},
        {"id": "H2", "goal": "Check helper contract", "source_refs": ["diff:users.py"],
         "missing_evidence": [
             {"id": "second", "description": "Read helper implementation"},
         ]},
    ]
    report, _ = run(repo, [
        {**candidate(), "intent_hypotheses": hypotheses, "context_requests": [{
            "hypothesis_id": "H1", "missing_id": "first",
            "purpose": "Read helper source", "tool": "read",
            "path": "helper.py", "line": 1, "side": "head",
        }]},
        {**candidate(), "intent_hypotheses": hypotheses},
        {"missing_evidence_requests": [{
            "hypothesis_id": "H2", "missing_id": "second",
            "purpose": "Read the same helper source for the second hypothesis",
            "tool": "read", "path": "helper.py", "line": 1, "side": "head",
        }]},
        verdict(),
    ], missing_evidence_followup=True)

    trace = report.repository_context["review_trace"]
    tasks = trace["intent"]["tasks"]
    second = next(task for task in tasks if task["missing_id"] == "second")
    assert second["reused"] is True
    assert second["evidence_ref_ids"]
    second_obligation = next(
        item for item in trace["intent"]["hypotheses"][1]["missing_evidence"]
        if item["missing_id"] == "second"
    )
    assert second_obligation["status"] == "evidence_visible"


def test_same_empty_query_reuses_no_evidence_outcome_for_another_obligation(repo):
    hypotheses = [
        {**planned_hypotheses()[0], "missing_evidence": [
            {"id": "first", "description": "Find helper by name"},
        ]},
        {"id": "H2", "goal": "Check the same helper contract", "source_refs": ["diff:users.py"],
         "missing_evidence": [
             {"id": "second", "description": "Find helper by name"},
         ]},
    ]
    request = {
        "purpose": "Search for the absent helper",
        "tool": "search", "symbol": "absent_helper_xyz", "side": "head",
    }
    report, _ = run(repo, [
        {**candidate(), "intent_hypotheses": hypotheses,
         "context_requests": [{**request, "hypothesis_id": "H1", "missing_id": "first"}]},
        {**candidate(), "intent_hypotheses": hypotheses},
        {"missing_evidence_requests": [{
            **request, "hypothesis_id": "H2", "missing_id": "second",
        }]},
        verdict(),
    ], missing_evidence_followup=True)

    trace = report.repository_context["review_trace"]
    second = next(task for task in trace["intent"]["tasks"]
                  if task.get("missing_id") == "second")
    first = next(task for task in trace["intent"]["tasks"]
                 if task.get("missing_id") == "first")
    assert first["status"] == "executed_no_evidence"
    assert second["status"] == "executed_no_evidence"
    assert second["reused"] is True
    assert second["evidence_ref_ids"] == []


def test_missing_request_overflow_is_aggregated_and_bounded(repo):
    hypothesis = {**planned_hypotheses()[0], "missing_evidence": [
        {"id": f"missing_{index}", "description": f"Read source {index}"}
        for index in range(4)
    ]}
    requests = [{
        "hypothesis_id": "H1", "missing_id": f"missing_{index}",
        "purpose": f"Read source {index}", "tool": "read",
        "path": "helper.py", "line": 1, "side": "head",
    } for index in range(9)]
    report, _ = run(repo, [
        {**candidate(), "intent_hypotheses": [hypothesis]},
        {"missing_evidence_requests": requests},
        verdict(),
    ], missing_evidence_followup=True)

    intent = report.repository_context["review_trace"]["intent"]
    assert len(intent["tasks"]) <= 4
    assert intent["request_overflow_count"] == 5
    assert intent["error_counts"]["request_limit_exceeded"] == 1


def test_mixed_verification_requests_are_both_accounted_in_one_bounded_batch(repo):
    hypothesis = {
        **planned_hypotheses()[0],
        "missing_evidence": [{"id": "helper", "description": "Read helper implementation"}],
    }
    report, _ = run(repo, [
        {**candidate(), "intent_hypotheses": [hypothesis]},
        {"missing_evidence_requests": [{
            "hypothesis_id": "H1", "missing_id": "helper",
            "purpose": "Read the helper implementation",
            "tool": "read", "path": "helper.py", "line": 1, "side": "head",
        }], "context_requests": [{
            "hypothesis_id": "H1", "purpose": "Check the changed caller",
            "tool": "read", "path": "caller.py", "line": 3, "side": "head",
        }]},
        verdict(),
    ], missing_evidence_followup=True)

    tasks = report.repository_context["review_trace"]["intent"]["tasks"]
    assert len(tasks) == 2
    assert {task["status"] for task in tasks} <= {
        "executed_with_evidence", "executed_no_evidence",
        "evidence_not_visible_after_prior_request",
    }
    assert all("task_id" in task for task in tasks)


def test_grounded_trace_is_versioned_and_valid_focus_prioritizes_bounded_tasks(repo):
    def assess(system, prompt):
        return {**verdict(), "intent_assessments": [{
            "hypothesis_id": "H1", "status": "supported",
            "evidence_refs": task_evidence_refs(prompt),
            "rationale": "The changed contract has a frozen diff source.", "unknowns": [],
        }]}

    responses = [
        {
            "intent_hypotheses": [
                {**planned_hypotheses()[0], "focus_tags": ["caller_contract"]},
                {"id": "H2", "goal": "Check a lower-priority detail",
                 "source_refs": ["pr:description"]},
            ],
            "context_requests": [
                {"hypothesis_id": "H2", "purpose": "low 1", "tool": "search",
                 "symbol": "missing_one"},
                {"hypothesis_id": "H2", "purpose": "low 2", "tool": "search",
                 "symbol": "missing_two"},
                {"hypothesis_id": "H2", "purpose": "low 3", "tool": "search",
                 "symbol": "missing_three"},
                {"hypothesis_id": "H2", "purpose": "low 4", "tool": "search",
                 "symbol": "missing_four"},
                {"hypothesis_id": "H1", "purpose": "check caller contract",
                 "tool": "search", "symbol": "get_profile"},
            ],
        },
        candidate(),
        assess,
    ]
    report, _ = run(
        repo, responses, description="untrusted summary",
        trace_input_identity={
            "benchmark_case_id": "fixture-case-1",
            "prepared_metadata_sha256": "a" * 64,
            "prepared_diff_sha256": "b" * 64,
            "input_set_sha256": "c" * 64,
        },
    )

    plan = report.repository_context["intent_plan"]
    assert plan["schema_version"] == 2
    assert plan["hypotheses"][0]["focus_tags"] == ["caller_contract"]
    assert plan["tasks"][0]["hypothesis_id"] == "H1"
    assert plan["tasks"][0]["status"] == "executed_with_evidence"
    assert plan["assessments"][0]["status"] == "supported"
    assert report.suggestions
    trace = report.repository_context["review_trace"]
    assert trace["schema_version"] == 1
    assert trace["identity"]["benchmark_case_id"] == "fixture-case-1"
    assert trace["identity"]["prepared_metadata_sha256"] == "a" * 64
    assert trace["identity"]["prepared_diff_sha256"] == "b" * 64
    assert trace["identity"]["input_set_sha256"] == "c" * 64
    assert trace["intent"]["hypotheses"][0]["id"] == "H1"
    assert trace["intent"]["tasks"][0]["evidence_ref_ids"]
    assert "Return contract" not in json.dumps(trace)
    assert trace["trace_sha256"]


def test_trace_records_only_host_proven_source_bound_task_omissions(repo):
    source_hypothesis = {
        **planned_hypotheses()[0], "focus_tags": ["caller_contract"],
    }
    requests = [
        {"hypothesis_id": "H1", "purpose": f"check caller {index}",
         "tool": "search", "symbol": f"caller_symbol_{index}"}
        for index in range(5)
    ]

    def assess(system, prompt):
        return {**verdict(), "intent_assessments": [{
            "hypothesis_id": "H1", "status": "supported",
            "evidence_refs": task_evidence_refs(prompt),
            "rationale": "The changed contract is linked to frozen source evidence.",
            "unknowns": [],
        }]}

    report, _ = run(repo, [
        {"intent_hypotheses": [source_hypothesis], "context_requests": requests},
        {**candidate(), "intent_hypotheses": [source_hypothesis]},
        assess,
    ])

    trace = report.repository_context["review_trace"]
    assert len(trace["intent"]["tasks"]) == 4
    assert len(trace["intent"]["omitted_tasks"]) == 1
    omission = trace["intent"]["omitted_tasks"][0]
    assert omission["reason"] == "task_limit"
    assert omission["hypothesis_id"] == "H1"
    assert omission["valid_source_refs"] == ["diff:users.py"]
    assert trace["mechanical_failures"] == [{
        "failure_id": omission["task_id"],
        "failure_tag": "cross_file_task_budget_omitted",
        "reason": "task_limit",
        "hypothesis_id": "H1",
        "task_id": omission["task_id"],
        "valid_source_refs": ["diff:users.py"],
        "omission_reason": None,
    }]


def test_trace_marks_real_evidence_budget_omission_as_partial(repo):
    hypothesis = {**planned_hypotheses()[0], "focus_tags": ["caller_contract"]}

    def assess(system, prompt):
        return {**verdict(), "intent_assessments": [{
            "hypothesis_id": "H1", "status": "supported",
            "evidence_refs": task_evidence_refs(prompt),
            "rationale": "No source ref is available to support this assessment.",
            "unknowns": [],
        }]}

    report, _ = run(repo, [
        {
            "intent_hypotheses": [hypothesis],
            "context_requests": [{
                "hypothesis_id": "H1", "purpose": "check unchanged caller",
                "tool": "search", "symbol": "get_profile",
            }],
        },
        candidate(),
        assess,
    ], evidence_tokens=1)

    trace = report.repository_context["review_trace"]
    assert trace["intent"]["tasks"]
    assert trace["intent"]["tasks"][0]["omission_reason"] == "evidence_budget_exhausted"
    assert trace["intent"]["omitted_tasks"][0]["reason"] == "evidence_budget"
    assert trace["mechanical_failures"][0]["failure_tag"] == (
        "cross_file_task_budget_omitted"
    )
    assert report.repository_context["intent_plan"]["status"] == "partial"


def test_discovery_budget_stop_keeps_same_round_candidates_pending(repo):
    from src.reviewer.provider import ReviewRequestBudget

    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="Return contract",
                     base_sha=base, head_sha=head)
    context = build_review_context(
        pr, files, include_context_pack=False, allow_remote_file_fetch=False
    )
    budget = ReviewRequestBudget(1_000_000, max_http_attempts=1, total_timeout_seconds=30)

    class CappedProvider:
        calls = 0

        def complete_json(self, system, prompt):
            budget.reserve(system, prompt)
            self.calls += 1
            if self.calls == 1:
                return json.dumps({
                    **candidate(),
                    "intent_hypotheses": planned_hypotheses(),
                    "context_requests": intent_search_request(),
                })
            raise AssertionError("the shared request budget must deny before another send")

    provider = CappedProvider()
    result = review_grounded(
        pr, files, provider, snapshot, context, discovery_rounds=3, verify_rounds=2
    )

    assert provider.calls == 1
    assert budget.snapshot()["request_http_attempts"] == 1
    assert len(result.candidates) == 1
    assert result.candidates[0]["status"] == "needs_context"
    assert result.pending
    assert result.bundle["intent_plan"]["status"] == "partial"
    assert "discovery_stopped:http_attempt_budget_exhausted" in result.bundle[
        "intent_plan"
    ]["errors"]


def test_pr_description_is_only_a_hypothesis_and_cannot_confirm_a_candidate(repo):
    def needs_context_with_refutation(system, prompt):
        refs = task_evidence_refs(prompt)
        item = {**verdict()["items"][0], "decision": "needs_context", "evidence": []}
        return {"items": [item], "intent_assessments": [{
            "hypothesis_id": "H1", "status": "refuted",
            "evidence_refs": refs, "rationale": "The unchanged caller still unpacks the old shape",
            "unknowns": [],
        }]}

    report, provider = run(repo, [
        {"intent_hypotheses": [dict(planned_hypotheses()[0],
            source_refs=["pr:description"],
            goal="The description claims every caller now handles a mapping")],
         "context_requests": intent_search_request()},
        {**candidate(), "intent_hypotheses": [dict(planned_hypotheses()[0],
            source_refs=["pr:description"],
            goal="The description claims every caller now handles a mapping")]},
        needs_context_with_refutation,
    ], description="All callers have been migrated to the mapping return shape.")

    intent = report.repository_context["intent_plan"]
    assert "All callers have been migrated" in provider.calls[0][1]
    assert intent["assessments"][0]["status"] == "refuted"
    assert intent["assessments"][0]["evidence_refs"]
    assert report.suggestions == []
    assert report.pending_suggestions


def test_hypothesis_cannot_borrow_another_hypothesis_task_evidence(repo):
    hypotheses = [*planned_hypotheses(), {
        "id": "H2", "goal": "Check the base-side caller",
        "constraints": [], "risk_hypotheses": [], "unknowns": [],
        "source_refs": ["diff:users.py"],
    }]
    requests = [intent_search_request()[0], {
        "hypothesis_id": "H2", "purpose": "Compare the base caller",
        "tool": "read", "path": "caller.py", "line": 3, "side": "base",
    }]

    def borrow_other_hypothesis_evidence(system, prompt):
        marker = "## Intent hypotheses (untrusted, evidence-linked annotations only)\n"
        plan = json.loads(prompt.split(marker, 1)[1])
        refs = [ref for task in plan["tasks"] if task["hypothesis_id"] == "H2"
                for ref in task["evidence_refs"]]
        return {**verdict(), "intent_assessments": [{
            "hypothesis_id": "H1", "status": "supported", "evidence_refs": refs,
            "rationale": "Only cites the other hypothesis's evidence", "unknowns": [],
        }]}

    report, _ = run(repo, [
        {"intent_hypotheses": hypotheses, "context_requests": requests},
        {**candidate(), "intent_hypotheses": hypotheses},
        borrow_other_hypothesis_evidence,
    ])

    assessment = report.repository_context["intent_plan"]["assessments"][0]
    assert assessment["hypothesis_id"] == "H1"
    assert assessment["status"] == "unknown"
    assert assessment["evidence_refs"] == []
    assert report.suggestions


def test_verification_request_for_unknown_hypothesis_is_not_executed(repo):
    request = {"hypothesis_id": "H1", "purpose": "Find caller",
               "tool": "search", "symbol": "get_profile", "side": "head"}

    unknown_request = {
            "hypothesis_id": "H2", "purpose": "Not in the plan",
            "tool": "search", "symbol": "render", "side": "head",
    }

    def verify_with_unknown_request(system, prompt):
        return {"context_requests": [unknown_request]}

    def final_verdict(system, prompt):
        refs = task_evidence_refs(prompt)
        return {**verdict(), "context_requests": [unknown_request], "intent_assessments": [{
            "hypothesis_id": "H1", "status": "supported", "evidence_refs": refs,
            "rationale": "The caller source supports this hypothesis", "unknowns": [],
        }]}

    report, _ = run(repo, [
        {"intent_hypotheses": planned_hypotheses(), "context_requests": [request]},
        {**candidate(), "intent_hypotheses": planned_hypotheses()},
        verify_with_unknown_request,
        final_verdict,
    ])

    verification_searches = [
        entry["normalized_query"]["symbol"] for entry in report.repository_context["requests"]
        if entry.get("normalized_query", {}).get("tool") == "search"
    ]
    assert verification_searches == ["get_profile"]
    assert [task["status"] for task in report.repository_context["intent_plan"]["tasks"][-2:]] == [
        "unknown_hypothesis_id", "not_executed_no_new_evidence",
    ]


@pytest.mark.parametrize("entrypoint", ["discovery", "verification"])
def test_intent_host_rejections_are_stable_across_request_entrypoints(repo, entrypoint):
    requests = [
        {"hypothesis_id": "H1", "purpose": "Read a source file", "tool": "read",
         "path": "../outside.py", "line": 1},
        {"hypothesis_id": "H1", "purpose": "Run a command", "tool": "exec",
         "command": "touch sentinel"},
        {"hypothesis_id": "H2", "purpose": "Use an unplanned hypothesis",
         "tool": "search", "symbol": "render"},
    ]
    if entrypoint == "discovery":
        responses = [
            {"intent_hypotheses": planned_hypotheses(), "context_requests": requests},
            {**candidate(), "intent_hypotheses": planned_hypotheses()},
            verdict(),
        ]
    else:
        responses = [
            {**candidate(), "intent_hypotheses": planned_hypotheses()},
            {"context_requests": requests},
            verdict(),
        ]

    report, _ = run(repo, responses)

    assert [task["status"] for task in report.repository_context["intent_plan"]["tasks"]] == [
        "invalid_read_query", "unsupported_tool", "unknown_hypothesis_id",
    ]
    assert report.repository_context["requests"] == []


def test_budget_omission_uses_same_host_validation_and_cross_file_scope():
    from src.reviewer.grounded_review import (
        _new_intent_plan,
        _record_omitted_intent_task,
    )

    plan = _new_intent_plan("planning")
    plan["hypotheses"] = [{
        "id": "H1", "source_refs": ["diff:users.py"], "priority_eligible": True,
    }]
    changed_paths = {"users.py"}
    invalid = [
        {"hypothesis_id": "H1", "purpose": "unsafe read", "tool": "read",
         "path": "../outside.py", "line": 1},
        {"hypothesis_id": "H1", "purpose": "unsupported action", "tool": "exec"},
        {"hypothesis_id": "H2", "purpose": "out of scope", "tool": "search",
         "symbol": "render"},
    ]
    for request in invalid:
        _record_omitted_intent_task(plan, request, "task_limit", changed_paths)
    assert plan["omitted_tasks"] == []

    _record_omitted_intent_task(
        plan,
        {"hypothesis_id": "H1", "purpose": "read unchanged caller", "tool": "read",
         "path": "caller.py", "line": 3},
        "task_limit",
        changed_paths,
    )
    assert len(plan["omitted_tasks"]) == 1
    assert plan["omitted_tasks"][0]["status"] == "not_executed_budget"
    assert plan["omitted_tasks"][0]["reason"] == "task_limit"
    assert plan["omitted_tasks"][0]["query"] == {
        "tool": "read", "path": "caller.py", "line": 3, "side": "head",
    }


def test_follows_unchanged_caller_and_verifies_low_confidence_candidate(repo):
    report, provider = run(repo, [candidate(), verdict()])
    assert report.used_ai
    assert len(report.suggestions) == 1
    assert report.suggestions[0].source == "ai_behavior_verified"
    assert report.suggestions[0].confidence == .9
    assert "name, age = get_profile()" in provider.calls[0][1]
    assert report.behavior_verifications[0]["source_evidence_valid"]
    assert not report.behavior_verifications[0]["tests_executed"]
    assert report.candidate_audit[0]["suggestion"]["confidence"] == .4


def test_false_quote_cannot_promote_candidate_to_verified(repo):
    report, _ = run(repo, [candidate(), verdict(evidence=[
        {"path": "caller.py", "side": "head", "line": 3, "quote": "totally invented code"}])])
    assert report.suggestions == []
    assert len(report.pending_suggestions) == 1
    assert report.behavior_verifications[0]["decision"] == "needs_context"


def test_dismissal_requires_counter_evidence(repo):
    report, _ = run(repo, [candidate(), verdict(decision="dismissed")])
    assert report.suggestions == []
    assert report.pending_suggestions
    assert report.behavior_verifications[0]["validation_reason"] == "dismissal_has_no_valid_counter_evidence"


def test_model_can_request_frozen_caller_before_verification(repo):
    request = {"hypothesis_id": "H1", "purpose": "Find caller", "tool": "search",
               "symbol": "render", "side": "head"}
    report, provider = run(repo, [
        {"intent_hypotheses": planned_hypotheses(), "context_requests": [request]},
        {**candidate(), "intent_hypotheses": planned_hypotheses()}, verdict(),
    ])
    assert len(provider.calls) == 3
    assert report.repository_context["requests"][0]["executed"]
    assert report.metrics["discovery_request_count"] == 2
    assert report.suggestions


def test_unbound_legacy_context_request_does_not_touch_snapshot(repo):
    report, provider = run(repo, [
        {**candidate(), "context_requests": [
            {"tool": "search", "symbol": "render", "side": "head"}
        ]},
        candidate(), verdict(),
    ])

    assert len(provider.calls) == 3
    assert report.repository_context["intent_plan"]["tasks"][0]["status"] == "unbound_task"
    assert report.repository_context["requests"] == []
    assert len(report.suggestions) == 1


def test_legacy_verification_request_without_plan_is_scoped_to_candidate(repo):
    report, _ = run(repo, [candidate(), {
        "context_requests": [{"tool": "search", "symbol": "get_profile", "side": "head"}]
    }, verdict()])

    legacy = [entry for entry in report.repository_context["requests"]
              if entry.get("scope") == "legacy_candidate_verification"]
    assert len(legacy) == 1
    assert report.repository_context["intent_plan"]["status"] == "not_reported"


def test_explicit_unknown_verification_hypothesis_is_rejected_without_a_plan(repo):
    report, _ = run(repo, [candidate(), {
        "context_requests": [{
            "hypothesis_id": "H2", "purpose": "unknown", "tool": "search",
            "symbol": "render", "side": "head",
        }]
    }, verdict()])

    assert report.repository_context["requests"] == []
    assert report.repository_context["intent_plan"]["status"] == "partial"
    assert report.repository_context["intent_plan"]["tasks"][0]["status"] == (
        "unknown_hypothesis_id"
    )


def test_archived_negative_identity_replay_rejects_ids_from_invalid_hypotheses(repo):
    replay_path = (Path(__file__).parent / "fixtures" / "missing_evidence_trigger"
                   / "negative" / "identity_replay.json")
    replay = json.loads(replay_path.read_text())
    report, provider = run(repo, replay["responses"])

    plan = report.repository_context["intent_plan"]
    assert len(provider.calls) == 2
    assert plan["error_counts"]["invalid_hypothesis_fields"] == 2
    assert [task["hypothesis_id"] for task in plan["tasks"]] == ["h1", "h2"]
    assert [task["status"] for task in plan["tasks"]] == [
        "unknown_hypothesis_id", "unknown_hypothesis_id",
    ]
    assert [task["task_id"] for task in plan["tasks"]] == [
        "9f49f63522c025be5ec0fc12c3d88d5a8630e5d3b3f8c792e7732fcc01e19234",
        "6b078f8ca9d714744d282e121bfaa40ec14cc633ebd649de52d60c82a45ab167",
    ]
    assert [hypothesis["id"] for hypothesis in plan["hypotheses"]] == ["h1", "h2"]
    assert all(not hypothesis["missing_evidence"] for hypothesis in plan["hypotheses"])
    assert report.repository_context["requests"] == []


def test_task_for_unknown_hypothesis_is_not_executed(repo):
    request = {"hypothesis_id": "H2", "purpose": "Find caller", "tool": "search",
               "symbol": "render", "side": "head"}
    report, _ = run(repo, [
        {"intent_hypotheses": planned_hypotheses(), "context_requests": [request]},
        {**candidate(), "intent_hypotheses": planned_hypotheses()}, verdict(),
    ])

    assert report.repository_context["intent_plan"]["tasks"][0]["status"] == "unknown_hypothesis_id"
    assert report.repository_context["requests"] == []


def test_duplicate_snapshot_search_executes_once_and_reuses_evidence(repo):
    root, base, head = repo

    class CountingSnapshot(GitSnapshot):
        def __init__(self, *args):
            super().__init__(*args)
            self.search_calls = 0

        def search(self, symbol, side="head", limit=6):
            self.search_calls += 1
            return super().search(symbol, side, limit)

    bundle = EvidenceBundle(CountingSnapshot(root, base, head))
    request = {"hypothesis_id": "H1", "purpose": "Find caller", "tool": "search",
               "symbol": "get_profile", "side": "head"}
    outcomes = bundle.execute([request, request])

    assert bundle.snapshot.search_calls == 1
    assert outcomes[0]["status"] == outcomes[1]["status"] == "executed_with_evidence"
    assert outcomes[1]["reused"] is True
    assert outcomes[0]["evidence_refs"] == outcomes[1]["evidence_refs"]


def test_requested_evidence_is_protected_from_later_budget_pressure(repo):
    root, base, head = repo
    bundle = EvidenceBundle(GitSnapshot(root, base, head), budget=6000)
    first_ref = bundle.add("caller.py", 3, kind="changed_definition")
    outcome = bundle.execute([{
        "hypothesis_id": "H1", "purpose": "Keep the caller evidence available",
        "tool": "read", "path": "caller.py", "line": 3, "side": "head",
    }])[0]
    bundle.budget = bundle.tokens + 1
    bundle.add("users.py", 2, kind="requested_definition")

    assert outcome["evidence_refs"] == [first_ref]
    assert any(item["evidence_ref"] == first_ref for item in bundle.excerpts)
    assert next(item for item in bundle.excerpts if item["evidence_ref"] == first_ref)["kind"] == (
        "requested_definition"
    )
    assert any(item["reason"] == "evidence_budget_exhausted" for item in bundle.omitted)


def test_empty_context_result_forces_final_discovery_without_repeating_search(repo):
    request = {"hypothesis_id": "H1", "purpose": "Find a missing caller",
               "tool": "search", "symbol": "absent_symbol", "side": "head"}
    report, provider = run(repo, [
        {"intent_hypotheses": planned_hypotheses(), "context_requests": [request]},
        {"intent_hypotheses": planned_hypotheses(), "context_requests": [request]},
        {**candidate(), "intent_hypotheses": planned_hypotheses()}, verdict(),
    ])

    assert len(provider.calls) == 4
    assert "added no new evidence" in provider.calls[1][1]
    assert report.repository_context["intent_plan"]["tasks"][0]["status"] == "executed_no_evidence"
    assert report.repository_context["intent_plan"]["tasks"][1]["status"] == (
        "not_executed_no_new_evidence"
    )
    assert len(report.repository_context["requests"]) == 1
    assert len(report.repository_context["discovery_trace"]) == 3


def test_intent_request_overflow_is_aggregated_without_unbounded_plan_growth(repo):
    requests = [{"hypothesis_id": "H2", "purpose": "invalid", "tool": "search",
                 "symbol": "render", "side": "head"} for _ in range(100)]
    report, _ = run(repo, [
        {"intent_hypotheses": planned_hypotheses(), "context_requests": requests},
        {**candidate(), "intent_hypotheses": planned_hypotheses()}, verdict(),
    ])

    plan = report.repository_context["intent_plan"]
    assert len(plan["tasks"]) == 4
    assert plan["request_overflow_count"] == 96
    assert plan["errors"][:2] == ["request_limit_exceeded", "unknown_hypothesis_id"]
    assert report.repository_context["requests"] == []


def test_final_discovery_round_records_requests_without_executing_them(repo):
    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="Return contract",
                     base_sha=base, head_sha=head)
    context = build_review_context(
        pr, files, include_context_pack=False, allow_remote_file_fetch=False
    )
    request = {**intent_search_request()[0], "symbol": "get_profile"}
    provider = Provider([
        {"intent_hypotheses": planned_hypotheses(), "context_requests": [request]},
        {**candidate(), "intent_hypotheses": planned_hypotheses(), "context_requests": [request]},
        verdict(),
    ])

    result = review_grounded(pr, files, provider, snapshot, context, discovery_rounds=2)

    assert result.bundle["intent_plan"]["status"] == "partial"
    assert [task["status"] for task in result.bundle["intent_plan"]["tasks"]] == [
        "executed_with_evidence", "not_executed_no_new_evidence",
    ]
    assert len(result.bundle["requests"]) == 1


def test_missing_causal_chain_is_pending_not_confirmed(repo):
    report, _ = run(repo, [candidate(), verdict(expected_behavior="")])
    assert not report.suggestions
    assert report.pending_suggestions


def test_snapshot_never_reads_working_tree_or_moving_branch(repo):
    root, base, head = repo
    (root / "users.py").write_text("future fix")
    snapshot = GitSnapshot(root, base, head)
    assert "future fix" not in snapshot.read("users.py")
    assert '("name", 42)' in snapshot.read("users.py", "base")
    with pytest.raises(ValueError):
        GitSnapshot(root, "main", head)
    for path in ("../outside.py", "/tmp/out.py", ".env", ".git/config", "reports/candidate.json"):
        assert not safe_source_path(path)
        assert snapshot.read(path) is None


def test_invalid_tool_request_cannot_execute_code(repo):
    root, base, head = repo
    bundle = EvidenceBundle(GitSnapshot(root, base, head))
    bundle.execute([{"tool": "exec", "command": "touch sentinel"},
                    {"tool": "read", "path": "../outside.py", "line": 1}])
    assert bundle.requests[0]["error"] == "invalid_request"
    assert not (root / "sentinel").exists()


def test_same_input_demonstrates_actual_base_head_contract_regression(repo):
    root, base, head = repo
    # Controlled fixtures only; target repository code is not executed by the reviewer.
    base_namespace, head_namespace = {}, {}
    exec(git(root, "show", base + ":users.py"), base_namespace)
    exec(git(root, "show", head + ":users.py"), head_namespace)
    def consume(fn):
        name, age = fn()
        return age + 1
    assert consume(base_namespace["get_profile"]) == 43
    with pytest.raises(TypeError):
        consume(head_namespace["get_profile"])


def test_supported_counter_evidence_can_dismiss_without_losing_audit(repo):
    false_candidate = candidate()
    false_candidate["suggestions"][0].update(title="get_profile removed", reason="Function no longer exists")
    report, _ = run(repo, [false_candidate, verdict(decision="dismissed", reason="Function is still defined", counter_evidence=[
        {"path": "users.py", "side": "head", "line": 1, "quote": "def get_profile():"}])])
    assert report.suggestions == []
    assert report.pending_suggestions == []
    assert report.candidate_audit[0]["status"] == "dismissed"
    assert report.behavior_verifications[0]["counter_evidence_valid"]


def test_provider_outage_keeps_candidate_and_marks_partial(repo):
    from src.reviewer.provider import ProviderError
    class FailingVerifier(Provider):
        def complete_json(self, system, prompt):
            if self.calls:
                raise ProviderError("timeout", kind="timeout")
            return super().complete_json(system, prompt)
    root, base, head = repo
    pr = PullRequest(repo="fixture/repo", number=1, title="Contract", base_sha=base, head_sha=head)
    files = parse_diff(git(root, "diff", base, head))
    report = review_with_ai(pr, files, [], FailingVerifier([candidate()]), 20,
                            repository_snapshot=GitSnapshot(root, base, head), follow_changes=True)
    assert report.execution_status == "partial"
    assert not report.suggestions
    assert len(report.pending_suggestions) == 1
    assert report.behavior_verifications[0]["decision"] == "unverified"


@pytest.mark.parametrize("malformed", [
    {},
    {"items": [{"candidate_id": 7, "decision": "confirmed"}]},
])
def test_malformed_verifier_response_keeps_discovery_candidate_pending(repo, malformed):
    report, _ = run(repo, [candidate(), malformed])
    assert report.used_ai
    assert report.execution_status == "partial"
    assert report.suggestions == []
    assert len(report.pending_suggestions) == 1
    assert report.candidate_audit[0]["status"] == "needs_context"


def test_explicit_behavior_proof_does_not_depend_on_uncalibrated_confidence(repo):
    report, _ = run(repo, [candidate(), verdict(confidence=.4, call_path="caller.py:3 -> users.py:2")])
    assert len(report.suggestions) == 1
    assert report.suggestions[0].confidence == .4
    assert report.behavior_verifications[0]["call_path_original"]


def test_only_safe_read_search_dsml_arrays_are_adapted():
    from src.reviewer.grounded_review import _json
    class Raw:
        def __init__(self, text): self.text = text
        def complete_json(self, *_): return self.text
    suffix = "</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>"
    value = _json(Raw('[{"tool":"read","path":"caller.py"}]' + suffix), "", "")
    assert value["response_format_notice"] == "dsml_request_array"
    with pytest.raises(ValueError):
        _json(Raw('[{"tool":"exec","command":"rm -rf /"}]' + suffix), "", "")
    with pytest.raises(ValueError):
        _json(Raw('{"suggestions":['), "", "")


def test_control_character_adapter_does_not_invent_missing_json_fields():
    from src.reviewer.grounded_review import _json
    class Raw:
        def complete_json(self, *_): return '{"summary":"first\nsecond","suggestions":[]}'
    result = _json(Raw(), "", "")
    assert result["summary"] == "first\nsecond"
    assert result["response_format_notice"] == "literal_control_characters"


def test_brace_window_contains_full_ts_method_beyond_default_window():
    from src.analyzer.repository_context import function_window
    source = "class Service {\n  async check(input: string) {\n" + "    input = input.trim();\n" * 70 + "    return input;\n  }\n}\n"
    start, end, symbol, extent = function_window("service.ts", source, 3)
    assert start <= 2
    assert end >= 74
    assert symbol == "check"
    assert extent == "heuristic_brace_definition"


def test_closing_container_repair_preserves_values_and_rejects_truncation():
    from src.reviewer.grounded_review import _repair_closed_containers
    raw = '{"items":[],"context_requests":[{"path":"caller.py","reason":"read body."]}]}'
    repaired = json.loads(_repair_closed_containers(raw))
    assert repaired == {"items": [], "context_requests": [{"path": "caller.py", "reason": "read body."}]}
    for value in ('{"items":[', '{"items":[]', '{"reason":"unfinished', '{"a":1}{"b":2}'):
        with pytest.raises(ValueError):
            _repair_closed_containers(value)


def test_path_only_request_pages_forward_instead_of_rereading_prefix(repo):
    root, base, head = repo
    text = "// header\n" * 160 + "const sentinel = 1;\n"
    class Snapshot:
        base = "a" * 40
        head = "b" * 40
        def read(self, path, side="head"): return text
    bundle = EvidenceBundle(Snapshot(), budget=8000)
    bundle.add("large.ts", 1)
    first_end = bundle.excerpts[0]["end_line"]
    bundle.execute([{"path": "large.ts", "side": "head"}])
    assert bundle.requests[-1]["continuation_line"] == first_end + 1
    assert bundle.requests[-1]["format_notice"] == "path_only_read_request"
    assert any(e["end_line"] > first_end for e in bundle.excerpts)


def test_unparseable_discovery_answer_is_retried_once(repo):
    """A garbled final answer must not discard the whole review."""
    report, provider = run(repo, ["not json at all", candidate(), verdict()])
    assert len(report.suggestions) == 1
    # One discovery call, one retry, one verification call.
    assert len(provider.calls) == 3
    assert "could not be parsed" in provider.calls[1][1]


def test_retry_is_recorded_in_the_discovery_trace(repo):
    report, _ = run(repo, ["not json at all", candidate(), verdict()])
    trace = report.repository_context["discovery_trace"]
    assert any(entry.get("retry_after_unparseable_response") for entry in trace)


class _Bundle:
    def __init__(self, excerpts):
        self.excerpts = excerpts


class _SnapshotWith:
    base = "a" * 40
    head = "b" * 40

    def __init__(self, text):
        self.text = text

    def read(self, path, side="head"):
        return self.text


PATCH = "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
SNIPPET_SENT = "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"


def test_citation_to_a_hunk_that_was_never_sent_is_rejected():
    """A quote that matches the snapshot is still not evidence if it was never
    in the prompt: an omitted hunk must not be citable."""
    from src.models import ChangedFile
    from src.reviewer.grounded_review import _valid_citation

    snapshot = _SnapshotWith("def f():\n    return 2\n")
    citation = {"path": "x.py", "side": "head", "line": 2, "quote": "return 2"}
    # The prompt we sent did not contain the line, whatever the file contains.
    assert _valid_citation(citation, snapshot, _Bundle([]), "no diff here") is False
    # The same citation is accepted when the line really was in the sent text.
    assert _valid_citation(citation, snapshot, _Bundle([]), SNIPPET_SENT) is True


def test_citation_from_a_rendered_evidence_excerpt_is_accepted():
    from src.models import ChangedFile
    from src.reviewer.grounded_review import _valid_citation

    snapshot = _SnapshotWith("def f():\n    return 2\n")
    excerpts = [{"path": "x.py", "side": "head", "start_line": 1, "end_line": 4}]
    citation = {"path": "x.py", "side": "head", "line": 2, "quote": "return 2"}
    assert _valid_citation(citation, snapshot, _Bundle(excerpts), "") is True


def test_citation_that_does_not_match_the_snapshot_is_still_rejected():
    from src.models import ChangedFile
    from src.reviewer.grounded_review import _valid_citation

    snapshot = _SnapshotWith("def f():\n    return 2\n")
    citation = {"path": "x.py", "side": "head", "line": 2, "quote": "return 999"}
    assert _valid_citation(citation, snapshot, _Bundle([]), SNIPPET_SENT) is False


@pytest.fixture
def equivalent_repo(tmp_path):
    """A PR whose rewrite is logically equivalent: P -> not Q with swapped branches."""
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    (tmp_path / "select.py").write_text(
        "def pick(values, index):\n"
        "    return values[index] if 0 <= index < len(values) else None\n"
    )
    git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "select.py").write_text(
        "def pick(values, index):\n"
        "    return None if index < 0 or index >= len(values) else values[index]\n"
    )
    git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "head")
    head = git(tmp_path, "rev-parse", "HEAD")
    return tmp_path, base, head


def equivalent_candidate():
    return {"summary": "Guard changed", "risk_level": "medium", "suggestions": [{
        "file_path": "select.py", "line": 2, "severity": "medium", "confidence": .9,
        "title": "Guard inversion changes which inputs reach the subscript",
        "reason": "The lower bound check was replaced by a disjunction",
        "recommendation": "Restore the chained comparison",
        "evidence": ['return values[index] if 0 <= index < len(values) else None'],
        "failure_scenario": "index below zero now reaches values[index]"}]}


def equivalent_verdict(**updates):
    item = {"candidate_id": 0, "decision": "confirmed", "confidence": .9,
            "trigger": "pick is called with a negative index",
            "call_path": ["select.py:2"],
            "expected_behavior": "returns None for out of range input",
            "actual_behavior": "returns an element from the end of the sequence",
            "introduced_by": "the guard was rewritten",
            "reason": "the two guards are not equivalent",
            "evidence": [
                {"path": "select.py", "side": "head", "line": 2,
                 "quote": "return None if index < 0 or index >= len(values) else values[index]"},
                {"path": "select.py", "side": "base", "line": 2,
                 "quote": "return values[index] if 0 <= index < len(values) else None"},
            ],
            "counter_evidence": []}
    return {"items": [{**item, **updates}]}


def run_equivalent(repo, responses):
    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="Guard", base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False, allow_remote_file_fetch=False)
    provider = Provider(responses)
    report = review_with_ai(pr, files, [], provider, 20, min_confidence=.65,
                            repository_snapshot=snapshot, follow_changes=True,
                            review_context=context)
    return report, provider


def test_witness_filter_blocks_confirmation_of_an_equivalent_rewrite(equivalent_repo):
    """The acceptance test for the criteria chain: a rewrite that is provably
    equivalent cannot be published as a confirmed behaviour change."""
    report, _ = run_equivalent(equivalent_repo, [equivalent_candidate(), equivalent_verdict()])
    assert report.suggestions == []
    assert len(report.pending_suggestions) == 1
    decision = report.behavior_verifications[0]
    assert decision["validation_reason"].startswith("witness_eval:")
    verdicts = {v["filter"]: v for v in decision["filter_verdicts"]}
    assert verdicts["witness_eval"]["passed"] is False
    assert verdicts["witness_eval"]["domain_size"] > 0
    assert verdicts["chain_completeness"]["passed"] is True
    assert verdicts["source_provenance"]["passed"] is True
    assert report.metrics["witness_blocked_count"] == 1


def test_unsupported_witness_blocks_confirmation_and_keeps_candidate_pending(
    equivalent_repo, monkeypatch,
):
    from src.reviewer import criteria
    from src.reviewer.witness import WitnessResult

    monkeypatch.setattr(
        criteria,
        "witness_for_evidence",
        lambda _evidence: WitnessResult("unsupported", "value size limit exceeded"),
    )
    report, _ = run_equivalent(equivalent_repo, [equivalent_candidate(), equivalent_verdict()])
    assert report.suggestions == []
    assert len(report.pending_suggestions) == 1
    verdicts = {v["filter"]: v for v in report.behavior_verifications[0]["filter_verdicts"]}
    assert verdicts["witness_eval"]["passed"] is False
    assert verdicts["witness_eval"]["status"] == "unsupported"


def verdict_with_expression_pair(**updates):
    """Same verdict, but citing the changed expression on both sides so the
    witness check has something to evaluate."""
    item = verdict()["items"][0]
    item["evidence"] = [
        {"path": "users.py", "side": "head", "line": 2,
         "quote": 'return {"name": "name", "age": 42}'},
        {"path": "users.py", "side": "base", "line": 2, "quote": 'return ("name", 42)'},
    ]
    return {"items": [{**item, **updates}]}


def test_witness_is_recorded_when_a_real_behaviour_change_is_confirmed(repo):
    report, _ = run(repo, [candidate(), verdict_with_expression_pair()])
    assert len(report.suggestions) == 1
    verdicts = {v["filter"]: v for v in report.behavior_verifications[0]["filter_verdicts"]}
    assert verdicts["witness_eval"]["passed"] is True
    assert verdicts["witness_eval"]["status"] == "differs"
    assert verdicts["witness_eval"]["base_value"] == repr(("name", 42))
    assert report.metrics["witness_blocked_count"] == 0


@pytest.fixture
def falsy_guard_repo(tmp_path):
    """safe-division: behaviour differs only for count=None, which the contract excludes."""
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    (tmp_path / "avg.py").write_text(
        "def average(total, count):\n    return total / count if count else 0\n"
    )
    git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "avg.py").write_text(
        "def average(total, count):\n    return 0 if count == 0 else total / count\n"
    )
    git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "head")
    head = git(tmp_path, "rev-parse", "HEAD")
    return tmp_path, base, head


def falsy_guard_payloads():
    suggestion = {
        "file_path": "avg.py", "line": 2, "severity": "medium", "confidence": .85,
        "title": "Narrowed falsy guard turns None count into a TypeError",
        "reason": "the truthiness guard accepted every falsy count, the equality guard does not",
        "recommendation": "keep the truthiness guard", "evidence": ["return 0 if count == 0 else total / count"],
        "failure_scenario": "average(total, None) now raises",
    }
    verdict_item = {
        "candidate_id": 0, "decision": "confirmed", "confidence": .85,
        "trigger": "average is called with count None", "call_path": ["avg.py:2"],
        "expected_behavior": "returns 0", "actual_behavior": "raises TypeError",
        "introduced_by": "the guard was narrowed", "reason": "the falsy case is no longer handled",
        "evidence": [
            {"path": "avg.py", "side": "head", "line": 2,
             "quote": "return 0 if count == 0 else total / count"},
            {"path": "avg.py", "side": "base", "line": 2,
             "quote": "return total / count if count else 0"},
        ],
        "counter_evidence": [],
    }
    return ({"summary": "guard narrowed", "risk_level": "medium", "suggestions": [suggestion]},
            {"items": [verdict_item]})


def run_with_contract(repo, responses, contract_domains):
    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="Guard", base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False, allow_remote_file_fetch=False)
    provider = Provider(responses)
    report = review_with_ai(pr, files, [], provider, 20, min_confidence=.65,
                            repository_snapshot=snapshot, follow_changes=True,
                            review_context=context, contract_domains=contract_domains)
    return report, provider


def test_declared_contract_blocks_a_witness_built_outside_it(falsy_guard_repo):
    payloads = falsy_guard_payloads()
    report, _ = run_with_contract(falsy_guard_repo, list(payloads), {"count": "int"})
    assert report.suggestions == []
    verdicts = {v["filter"]: v for v in report.behavior_verifications[0]["filter_verdicts"]}
    assert verdicts["witness_eval"]["passed"] is False
    assert "outside the declared contract" in verdicts["witness_eval"]["reason"]
    assert report.metrics["witness_blocked_count"] == 1


def test_without_a_declared_contract_the_behaviour_claim_is_published(falsy_guard_repo):
    """Honest boundary: behaviour alone cannot settle a contract question."""
    payloads = falsy_guard_payloads()
    report, _ = run_with_contract(falsy_guard_repo, list(payloads), {})
    assert len(report.suggestions) == 1
    verdicts = {v["filter"]: v for v in report.behavior_verifications[0]["filter_verdicts"]}
    assert verdicts["witness_eval"]["status"] == "differs"


def test_playbook_addendum_reaches_the_grounded_prompt_and_stays_first(repo):
    """The knowledge layer must actually be sent, and it must be the stable prefix
    so provider prefix caching can reuse it across every call of a run."""
    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="Return contract",
                     base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False,
                                  allow_remote_file_fetch=False)
    provider = Provider([candidate(), verdict()])
    review_with_ai(pr, files, [], provider, 20, repository_snapshot=snapshot,
                   follow_changes=True, review_context=context,
                   prompt_addendum="## Project knowledge\n- [contract] count is an int")
    user_prompts = [prompt for _system, prompt in provider.calls]
    assert user_prompts, "no calls recorded"
    for prompt in user_prompts:
        assert prompt.startswith("## Project knowledge"), prompt[:60]
    # Every call shares the same prefix, which is what the cache keys on.
    prefixes = {prompt[: len("## Project knowledge\n- [contract] count is an int")] for prompt in user_prompts}
    assert len(prefixes) == 1


def test_unit_prompts_share_identical_knowledge_prefix_and_actual_unit_content(tmp_path):
    """Observe real per-unit provider calls: shared knowledge leads, unit diffs vary."""
    for args in (
        ("init",),
        ("config", "user.email", "f@e.invalid"),
        ("config", "user.name", "Fixture"),
    ):
        git(tmp_path, *args)
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text(
            f"def value_{index}():\n    return 'before_{index}'\n"
        )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text(
            f"def value_{index}():\n    return 'unit_payload_{index}'\n"
        )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "head")
    head = git(tmp_path, "rev-parse", "HEAD")

    snapshot = GitSnapshot(tmp_path, base, head)
    files = parse_diff(git(tmp_path, "diff", base, head))
    pr = PullRequest(
        repo="fixture/repo", number=9, title="Independent module changes",
        base_sha=base, head_sha=head,
    )
    context = build_review_context(
        pr, files, include_context_pack=False, allow_remote_file_fetch=False,
        max_patch_tokens=1,
    )
    assert context.truncated
    provider = Provider([
        {"summary": "Reviewed one unit", "risk_level": "low", "suggestions": []}
        for _ in range(3)
    ])
    knowledge = "## Project knowledge\n- Preserve each module's return contract"

    report = review_with_ai(
        pr, files, [], provider, 20, repository_snapshot=snapshot,
        follow_changes=True, review_context=context, prompt_addendum=knowledge,
        unit_review_max_units=3, unit_review_workers=1,
        repository_evidence_tokens=1000, unit_evidence_tokens=1000,
    )

    prompts = [prompt for _system, prompt in provider.calls]
    assert len(prompts) == report.metrics["units_reviewed"] == 3
    assert all(prompt.startswith(knowledge + "\n") for prompt in prompts)
    assert {prompt[:len(knowledge)] for prompt in prompts} == {knowledge}
    payload_markers = [f"return 'unit_payload_{index}'" for index in range(3)]
    matched_units = []
    for prompt in prompts:
        matches = [index for index, marker in enumerate(payload_markers) if marker in prompt]
        assert len(matches) == 1
        matched_units.extend(matches)
    assert set(matched_units) == {0, 1, 2}


def test_unit_followup_slot_rejects_generic_context_request_without_missing_id(tmp_path):
    for args in (("init",), ("config", "user.email", "f@e.invalid"),
                 ("config", "user.name", "Fixture")):
        git(tmp_path, *args)
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text(
            f"def value_{index}():\n    return 'before_{index}'\n"
        )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text(
            f"def value_{index}():\n    return 'after_{index}'\n"
        )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "head")
    head = git(tmp_path, "rev-parse", "HEAD")
    snapshot = GitSnapshot(tmp_path, base, head)
    files = parse_diff(git(tmp_path, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=10, title="Unit review",
                     base_sha=base, head_sha=head)
    context = build_review_context(
        pr, files, include_context_pack=False, allow_remote_file_fetch=False,
        max_patch_tokens=1,
    )
    provider = Provider([
        {"summary": "Candidate", "risk_level": "medium", "suggestions": [{
            "file_path": "module_0.py", "line": 2, "severity": "medium",
            "confidence": .9, "title": "Check contract", "reason": "Possible mismatch",
            "recommendation": "Read caller", "evidence": [],
        }]},
        {"context_requests": [{"path": "helper.py", "line": 1, "side": "head"}],
         "items": [{"candidate_id": 0, "decision": "needs_context"}]},
    ])

    report = review_with_ai(
        pr, files, [], provider, 20, repository_snapshot=snapshot,
        follow_changes=True, review_context=context, unit_review_max_units=1,
        unit_review_workers=1, repository_evidence_tokens=1000, unit_evidence_tokens=1000,
        grounded_missing_evidence_followup=True,
    )

    assert len(provider.calls) == 2
    trace = report.repository_context["review_trace"]
    assert trace["policy"]["grounded_missing_evidence_followup"] is True
    assert all(task.get("missing_id") is None for task in trace["intent"]["tasks"])


def test_impact_closure_coverage_reaches_the_report_metrics(repo):
    """Hunk counting says how much was shown; this says how much of the change's
    influence surface was, which is what decides whether a defect is findable."""
    report, _ = run(repo, [candidate(), verdict()])
    # The metric must always be present and self-consistent. A fixture whose hunk
    # headers carry no function context legitimately resolves to zero symbols, in
    # which case coverage is 1.0 by definition rather than undefined.
    nodes = report.metrics["impact_closure_nodes"]
    seen = report.metrics["impact_closure_seen"]
    coverage = report.metrics["impact_coverage"]
    assert nodes >= 0 and seen <= nodes
    assert coverage == (seen / nodes if nodes else 1.0)


def test_review_trace_records_rendered_head_ranges_for_the_patch(repo):
    """S is auditable from a saved report: which head lines reached the prompt."""
    report, _ = run(repo, [
        {**candidate(), "intent_hypotheses": planned_hypotheses()},
        verdict(),
    ])
    trace = report.repository_context["review_trace"]
    ranges = {
        item["path"]: item["ranges"]
        for item in trace["coverage"]["rendered_head_ranges"]
    }
    assert "users.py" in ranges
    assert any(start <= 2 <= end for start, end in ranges["users.py"])
    # caller.py is unchanged in this fixture, so it has no rendered patch lines.
    assert "caller.py" not in ranges


def test_raw_discovery_unions_two_samples_and_still_passes_the_verification_gate(repo):
    finding = {
        "file": "users.py", "line": 2,
        "title": "Caller still expects positional values",
        "reason": "Dictionary iteration returns keys, not the former values",
        "evidence": ['return {"name": "name", "age": 42}'],
    }
    report, provider = run(repo, [
        {"findings": [finding]},
        {"findings": [{**finding, "title": "Same anchor, other words"}]},
        verdict(),
    ], raw_discovery=True)

    assert len(provider.calls) == 3  # diff + diff/evidence discovery, one verification
    assert provider.calls[0][0].startswith("You are a senior code reviewer")
    assert provider.calls[2][0].startswith("You verify code-review candidates")
    diff_only, with_evidence = provider.calls[0][1], provider.calls[1][1]
    for raw_prompt in (diff_only, with_evidence):
        assert "## Diff\n--- a/users.py\n+++ b/users.py\n" in raw_prompt
        assert '+    return {"name": "name", "age": 42}' in raw_prompt
        assert "## Changed diff" not in raw_prompt  # budgeted context.text is bypassed
    assert "## Frozen repository evidence" not in diff_only
    assert "## Frozen repository evidence" in with_evidence
    trace = report.repository_context["review_trace"]
    assert trace["discovery_mode"] == "raw"
    assert trace["intent"]["status"] == "not_reported"
    assert len(report.suggestions) == 1  # the duplicate anchor was merged


MULTI_SOURCE = (
    "def f():\n"
    "    mocker.patch(\n"
    '        "pkg.mod.fn",\n'
    "        side_effect=fake,\n"
    "    )\n"
)
MULTI_SENT = "@@ -0,0 +1,5 @@\n" + "".join("+" + line + "\n" for line in MULTI_SOURCE.splitlines())


def test_multi_line_citation_is_matched_line_by_line():
    from src.reviewer.grounded_review import _valid_citation

    snapshot = _SnapshotWith(MULTI_SOURCE)
    quote = '    mocker.patch(\n        "pkg.mod.fn",\n        side_effect=fake,'
    citation = {"path": "t.py", "side": "head", "line": 2, "quote": quote}
    assert _valid_citation(citation, snapshot, _Bundle([]), MULTI_SENT) is True
    # One continuation line that is not in the source rejects the whole quote.
    wrong = {**citation, "quote": '    mocker.patch(\n        "pkg.other.fn",'}
    assert _valid_citation(wrong, snapshot, _Bundle([]), MULTI_SENT) is False
    # Anchored at the wrong line, the run of lines does not line up.
    shifted = {**citation, "line": 3}
    assert _valid_citation(shifted, snapshot, _Bundle([]), MULTI_SENT) is False


def test_multi_line_citation_needs_every_line_to_have_been_shown():
    from src.reviewer.grounded_review import _valid_citation

    snapshot = _SnapshotWith(MULTI_SOURCE)
    sent_first_two = "\n".join(MULTI_SENT.splitlines()[:3])  # header + lines 1-2
    quote = '    mocker.patch(\n        "pkg.mod.fn",'
    citation = {"path": "t.py", "side": "head", "line": 2, "quote": quote}
    assert _valid_citation(citation, snapshot, _Bundle([]), sent_first_two) is False
    excerpts = [{"path": "t.py", "side": "head", "start_line": 2, "end_line": 3}]
    assert _valid_citation(citation, snapshot, _Bundle(excerpts), "") is True


def test_dismissal_whose_counter_evidence_repeats_the_evidence_is_not_accepted(repo):
    defective = {"path": "users.py", "side": "head", "line": 2,
                 "quote": 'return {"name": "name", "age": 42}'}
    report, _ = run(repo, [candidate(), verdict(
        decision="dismissed", reason="the caller already handles the dictionary",
        evidence=[defective], counter_evidence=[defective],
    )])
    assert report.suggestions == []
    verification = report.behavior_verifications[0]
    assert verification["decision"] == "needs_context"
    assert verification["validation_reason"] == "dismissal_has_no_valid_counter_evidence"


def test_raw_verification_sees_the_same_full_diff_as_discovery(repo):
    _, provider = run(repo, [
        {"findings": [{"file": "users.py", "line": 2, "title": "t", "reason": "r"}]},
        {"findings": []},
        verdict(),
    ], raw_discovery=True)
    verify_prompt = provider.calls[2][1]
    assert "--- a/users.py\n+++ b/users.py\n" in verify_prompt


def _dismissal(**updates):
    return verdict(
        decision="dismissed",
        counter_evidence=[{"path": "caller.py", "side": "head", "line": 4,
                           "quote": "return age + 1"}],
        **updates,
    )


@pytest.mark.parametrize("updates", [
    {"reason": "A single spurious reset is benign and self-correcting; the PR documents it."},
    {"reason": "This is intended behaviour.", "dismissal_basis": "intended"},
    {"reason": "Real but low impact.", "dismissal_basis": "low_impact"},
])
def test_dismissal_on_intent_or_impact_becomes_needs_context(repo, updates):
    report, _ = run(repo, [candidate(), _dismissal(**updates)])
    verification = report.behavior_verifications[0]
    assert verification["decision"] == "needs_context"
    assert verification["validation_reason"] == "dismissal_rests_on_intent_or_impact"


def test_dismissal_with_a_factual_basis_still_dismisses(repo):
    report, _ = run(repo, [candidate(), _dismissal(
        reason="The caller never reaches this path; it is harmless.",
        dismissal_basis="unreachable",
    )])
    item = report.behavior_verifications[0]
    assert item["decision"] == "dismissed", item.get("validation_reason")


def test_strong_judgement_overrides_a_declared_factual_basis(repo):
    report, _ = run(repo, [candidate(), _dismissal(
        reason="Explicitly intended and documented in the PR; the reset is self-correcting.",
        dismissal_basis="not_introduced",
    )])
    verification = report.behavior_verifications[0]
    assert verification["decision"] == "needs_context"
    assert verification["validation_reason"] == "dismissal_rests_on_intent_or_impact"


def test_rule_candidates_do_not_use_the_per_file_publication_cap(repo):
    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="t", base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False,
                                   allow_remote_file_fetch=False)
    model = {"file": "users.py", "line": 2, "title": "Caller still expects positional values",
             "reason": "r"}
    rule = {"file": "users.py", "line": 1, "title": "[TQ001] Structural rule fact", "reason": "r"}
    confirmed = verdict()["items"][0]
    provider = Provider([
        {"findings": [model]}, {"findings": [rule]},
        {"items": [confirmed, {**confirmed, "candidate_id": 1}]},
    ])
    result = review_grounded(pr, files, provider, snapshot, context,
                             max_suggestions_per_file=1, raw_discovery=True)
    titles = [suggestion.title for suggestion in result.suggestions]
    assert titles == [model["title"], rule["title"]]


def test_rule_rebuttal_helpers():
    from src.models import ReviewSuggestion
    from src.reviewer.grounded_review import _contradicted_by_rule, _rule_rebuttals

    rebutted = _rule_rebuttals([{
        "rule_id": "TQ001", "file": "t.py",
        "reason": "`load_info` is imported by name, so a patch on the module attribute ...",
    }])
    assert rebutted == {"t.py": {"load_info"}}

    def suggestion(title, reason="r", path="t.py"):
        return ReviewSuggestion(file_path=path, line=3, severity="medium", confidence=.6,
                                title=title, reason=reason, recommendation="")

    wrong = suggestion("load_info is mocked then called, so the test reads the mocked tuple")
    assert _contradicted_by_rule(wrong, rebutted) == "load_info"
    agrees = suggestion("Patch on load_info is bypassed by the direct import")
    assert _contradicted_by_rule(agrees, rebutted) is None
    unrelated = suggestion("send is mocked, so the test verifies nothing")
    assert _contradicted_by_rule(unrelated, rebutted) is None
    other_file = suggestion("load_info is mocked then called", path="u.py")
    assert _contradicted_by_rule(other_file, rebutted) is None
    rule = suggestion("[TQ001] load_info is mocked")
    assert _contradicted_by_rule(rule, rebutted) is None


def _raw_run(repo, discovery, verdict_items):
    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="t", base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False,
                                   allow_remote_file_fetch=False)
    provider = Provider([{"findings": discovery}, {"findings": []}, {"items": verdict_items}])
    return review_grounded(pr, files, provider, snapshot, context, raw_discovery=True)


def test_confirmed_duplicate_is_folded_into_its_confirmed_target(repo):
    first = {"file": "users.py", "line": 2, "title": "Caller still expects positional values",
             "reason": "r"}
    second = {"file": "users.py", "line": 1, "title": "Return shape changed for get_profile",
              "reason": "r"}
    confirmed = verdict()["items"][0]
    result = _raw_run(repo, [first, second], [
        {**confirmed, "duplicate_of": None},
        {**confirmed, "candidate_id": 1, "duplicate_of": 0},
    ])
    assert [suggestion.title for suggestion in result.suggestions] == [first["title"]]
    folded = result.candidates[1]
    assert folded["status"] == "needs_context"
    assert folded["reason"] == "duplicate_of_confirmed_candidate"
    assert folded["duplicate_of"] == 0
    # A folded candidate stays visible in the review queue.
    assert [suggestion.title for suggestion in result.pending] == [second["title"]]


def test_duplicate_of_an_unconfirmed_candidate_is_still_published(repo):
    first = {"file": "users.py", "line": 2, "title": "Caller still expects positional values",
             "reason": "r"}
    second = {"file": "users.py", "line": 1, "title": "Return shape changed for get_profile",
              "reason": "r"}
    confirmed = verdict()["items"][0]
    result = _raw_run(repo, [first, second], [
        {**confirmed, "decision": "needs_context"},
        {**confirmed, "candidate_id": 1, "duplicate_of": 0},
    ])
    assert [suggestion.title for suggestion in result.suggestions] == [second["title"]]


def test_confirmed_mock_claim_contradicted_by_tq001_is_held_back(repo, monkeypatch):
    import src.analyzer.test_quality_lane as lane

    monkeypatch.setattr(lane, "collect_test_quality_candidates", lambda snapshot, files: [{
        "file": "users.py", "line": 1, "rule_id": "TQ001",
        "title": "[TQ001] Patched target is bound locally before the patch is applied",
        "reason": "`get_profile` is imported by name, so a patch on the module attribute ...",
    }])
    claim = {"file": "users.py", "line": 2,
             "title": "get_profile is mocked, so the test only reads the mocked tuple",
             "reason": "r"}
    confirmed = verdict()["items"][0]
    result = _raw_run(repo, [claim], [confirmed, {**confirmed, "candidate_id": 1}])
    titles = [suggestion.title for suggestion in result.suggestions]
    assert claim["title"] not in titles
    assert any(title.startswith("[TQ001]") for title in titles)
    held = [item for item in result.decisions if item["candidate_id"] == 0][0]
    assert held["validation_reason"] == "contradicted_by_deterministic_rule"


def test_rule_candidate_is_never_folded_by_duplicate_of(repo):
    first = {"file": "users.py", "line": 2, "title": "Caller still expects positional values",
             "reason": "r"}
    rule = {"file": "users.py", "line": 1, "title": "[TQ008] Structural rule fact", "reason": "r"}
    confirmed = verdict()["items"][0]
    result = _raw_run(repo, [first, rule], [
        confirmed, {**confirmed, "candidate_id": 1, "duplicate_of": 0},
    ])
    assert [suggestion.title for suggestion in result.suggestions] == [first["title"], rule["title"]]


class _TagSnapshot:
    files = {
        "types.go": 'type Entry struct {\n\tID string `json:"id"`\n'
                    '\tRequestBody string `json:"request_body,omitempty"`\n}\n',
        "other.go": "// request_body mentioned in a comment\n",
    }

    def read(self, path, side="head"):
        return self.files.get(path)

    def search(self, symbol, side="head", limit=6):
        return [(path, number) for path, text in self.files.items()
                for number, line in enumerate(text.splitlines(), 1) if symbol in line]


class _RecordingBundle:
    def __init__(self):
        self.added, self.omitted = [], []

    def add(self, path, line=1, side="head", kind="related_code", max_lines=None):
        self.added.append((path, line, kind))


def _claim(title, reason):
    from src.models import ReviewSuggestion
    return ReviewSuggestion(file_path="reader.go", line=3, severity="medium", confidence=.6,
                            title=title, reason=reason, recommendation="")


def test_serialization_claim_adds_the_field_tag_line():
    from src.reviewer.grounded_review import _add_serialization_contract

    bundle = _RecordingBundle()
    _add_serialization_contract(bundle, _TagSnapshot(), _claim(
        "List response still contains payload fields",
        "The JSON response still contains request_body as an empty string.",
    ))
    assert bundle.added == [("types.go", 3, "candidate_serialization_contract")]


def test_non_serialization_claim_adds_nothing():
    from src.reviewer.grounded_review import _add_serialization_contract

    bundle = _RecordingBundle()
    _add_serialization_contract(bundle, _TagSnapshot(), _claim(
        "Loop skips the last request_body", "Off by one in the loop bound.",
    ))
    assert bundle.added == []
