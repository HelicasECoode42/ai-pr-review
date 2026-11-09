"""Pins review_grounded's prompts and outcome so refactors can prove no change.

Regenerate only for an intended behaviour change:
UPDATE_GROUNDED_GOLDEN=1 pytest tests/test_grounded_characterization.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from src.analyzer.context_builder import build_review_context
from src.analyzer.repository_context import GitSnapshot
from src.evaluation.reviewbench import parse_diff
from src.models import PullRequest
from src.reviewer.grounded_review import build_grounded_report, review_grounded
from src.reviewer.provider import ProviderError

GOLDEN = Path(__file__).parent / "golden" / "grounded_review.json"
# Commit dates are pinned so the SHAs, and so the prompts, never vary.
FIXED_DATE = "2026-01-01T00:00:00+0000"
# The two fingerprints hash the source itself, so any refactor changes them by design.
VOLATILE_KEYS = {"runtime_sha256", "trace_sha256", "created_at", "generated_at", "duration_ms", "elapsed_ms", "elapsed_seconds"}


def git(root, *args):
    # Commit hooks export GIT_* for their own commit; none may leak into the fixture.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_DATE=FIXED_DATE, GIT_COMMITTER_DATE=FIXED_DATE,
               GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE, env=env
    ).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    (tmp_path / "users.py").write_text('def get_profile():\n    return ("name", 42)\n')
    (tmp_path / "helper.py").write_text('def decision_helper():\n    return "frozen evidence"\n')
    (tmp_path / "caller.py").write_text(
        "from users import get_profile\ndef render():\n    name, age = get_profile()\n    return age + 1\n"
    )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "users.py").write_text('def get_profile():\n    return {"name": "name", "age": 42}\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "head")
    return tmp_path, base, git(tmp_path, "rev-parse", "HEAD")


class Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, prompt):
        self.calls.append((system, prompt))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)


SUGGESTION = {
    "file_path": "users.py", "line": 2, "severity": "high", "confidence": .4,
    "title": "Caller still expects positional values",
    "reason": "Dictionary iteration returns keys, not the former values",
    "recommendation": "Preserve tuple return or migrate caller",
    "evidence": ['return {"name": "name", "age": 42}'],
    "failure_scenario": "render tries to add one to a string age",
}
CANDIDATE = {"summary": "Return contract changed", "risk_level": "high", "suggestions": [SUGGESTION]}
FINDING = {"file": "users.py", "line": 2, "title": SUGGESTION["title"], "reason": SUGGESTION["reason"]}


def verdict(**updates):
    item = {
        "candidate_id": 0, "decision": "confirmed", "confidence": .9,
        "trigger": "render calls get_profile", "call_path": ["caller.py:3", "users.py:2"],
        "expected_behavior": "age is an integer",
        "actual_behavior": "age is the dictionary key age, causing TypeError",
        "introduced_by": "return changed from tuple to dictionary", "reason": "Caller contract not migrated",
        "evidence": [{"path": "caller.py", "side": "head", "line": 3, "quote": "name, age = get_profile()"},
                     {"path": "users.py", "side": "base", "line": 2, "quote": 'return ("name", 42)'}],
        "counter_evidence": [],
    }
    return {"items": [{**item, **updates}]}


HYPOTHESIS = {"id": "h1", "goal": "Return shape", "constraints": [], "risk_hypotheses": ["callers break"],
              "unknowns": ["helper"], "source_refs": ["diff:users.py"], "focus_tags": ["caller_contract"]}
READ_TASK = {"hypothesis_id": "h1", "purpose": "read helper", "tool": "read", "path": "helper.py",
             "start_line": 1, "end_line": 2}

SCENARIOS = {
    "intent_confirmed": ({}, [CANDIDATE, verdict()]),
    "intent_not_reported_dismissed": ({}, [CANDIDATE, verdict(
        decision="dismissed", reason="not reachable",
        counter_evidence=[{"path": "caller.py", "side": "head", "line": 3,
                           "quote": "name, age = get_profile()"}])]),
    "intent_tool_round": ({}, [
        {"intent_hypotheses": [HYPOTHESIS], "context_requests": [READ_TASK]}, CANDIDATE, verdict()]),
    "intent_no_new_evidence": ({}, [
        {"intent_hypotheses": [HYPOTHESIS], "context_requests": [READ_TASK]},
        {"intent_hypotheses": [HYPOTHESIS], "context_requests": [READ_TASK]}, CANDIDATE, verdict()]),
    "intent_garbled_then_valid": ({}, ["not json", CANDIDATE, verdict()]),
    "verify_context_then_decide": ({}, [CANDIDATE, {"context_requests": [
        {"tool": "read", "path": "helper.py", "start_line": 1, "end_line": 2}]}, verdict()]),
    "verify_unknown_candidate": ({}, [CANDIDATE, verdict(candidate_id=9)]),
    "verify_provider_failure": ({}, [CANDIDATE, ProviderError("boom", kind="timeout")]),
    "verify_disabled": ({"verify_behavior": False}, [CANDIDATE]),
    "verify_insufficient_chain": ({}, [CANDIDATE, verdict(evidence=[])]),
    "verify_invalid_decision": ({}, [CANDIDATE, verdict(decision="perhaps")]),
    "anchor_outside_diff": ({}, [{**CANDIDATE, "suggestions": [
        {**SUGGESTION, "file_path": "other.py"}]}]),
    "per_file_cap": ({"max_suggestions_per_file": 0}, [CANDIDATE, verdict()]),
    "followup_missing_evidence": ({"missing_evidence_followup": True}, [
        {**CANDIDATE, "intent_hypotheses": [{**HYPOTHESIS, "missing_evidence": [
            {"id": "m1", "description": "helper", "question": "helper?"}]}]},
        {"items": [], "missing_evidence_requests": [{**READ_TASK, "missing_id": "m1"}]},
        verdict()]),
    "index_enabled": ({"repository_index_enabled": True}, [CANDIDATE, verdict()]),
    "raw_confirmed": ({"raw_discovery": True}, [
        {"findings": [FINDING]}, {"findings": [FINDING]}, {"findings": []}, verdict()]),
    "raw_hedged": ({"raw_discovery": True}, [
        {"findings": [FINDING]}, {"findings": []}, {"findings": []},
        verdict(reason="this may break callers")]),
    "raw_partial_failure": ({"raw_discovery": True}, [
        "not json", {"findings": [FINDING, "bad"]}, {"findings": []}, verdict()]),
}


def scrub(value):
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in sorted(value.items()) if key not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def observe(repo, kwargs, responses):
    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="t", body="", base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False, allow_remote_file_fetch=False)
    provider = Provider(responses)
    try:
        result = review_grounded(pr, files, provider, snapshot, context, **kwargs)
    except Exception as exc:
        outcome = {"raised": f"{type(exc).__name__}: {exc}"}
    else:
        report = build_grounded_report(pr, files, [], result, context)
        outcome = json.loads(json.dumps(report.model_dump(mode="json"), default=str))
        outcome["_result"] = json.loads(json.dumps({
            "candidates": result.candidates, "decisions": result.decisions, "bundle": result.bundle,
            "discovery_calls": result.discovery_calls, "verification_calls": result.verification_calls,
            "verification_failed": result.verification_failed,
        }, default=str))
    text = json.dumps(scrub(outcome), ensure_ascii=False, sort_keys=True).replace(str(root), "<root>")
    return {
        "prompts": [hashlib.sha256((system + "\0" + prompt).encode()).hexdigest()[:16]
                    for system, prompt in provider.calls],
        "unused_responses": len(provider.responses),
        "outcome_sha": hashlib.sha256(text.encode()).hexdigest()[:16],
        "outcome": json.loads(text),
    }


def load_golden():
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_review_grounded_matches_golden(repo, name):
    kwargs, responses = SCENARIOS[name]
    observed = observe(repo, kwargs, responses)
    if os.environ.get("UPDATE_GROUNDED_GOLDEN"):
        golden = load_golden()
        golden[name] = {key: observed[key] for key in ("prompts", "unused_responses", "outcome_sha")}
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
        return
    expected = load_golden()[name]
    assert {key: observed[key] for key in expected} == expected
