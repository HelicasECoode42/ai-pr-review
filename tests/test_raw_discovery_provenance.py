from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from src.analyzer.context_builder import build_review_context
from src.analyzer.repository_context import GitSnapshot
from src.evaluation.reviewbench import parse_diff
from src.models import PullRequest
from src.reviewer.candidate_dedupe import dedupe_candidates
from src.reviewer.decision_ledger import append_candidate_ledger, build_candidate_lifecycle, decision_records
from src.reviewer.grounded_review import review_grounded


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    (tmp_path / "users.py").write_text('def get_profile():\n    return ("name", 42)\n')
    (tmp_path / "caller.py").write_text('from users import get_profile\ndef render():\n    name, age = get_profile()\n    return age + 1\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "users.py").write_text('def get_profile():\n    return {"name": "name", "age": 42}\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "head")
    head = git(tmp_path, "rev-parse", "HEAD")
    return tmp_path, base, head


class RawProvider:
    """Replies in order; a str is sent verbatim, anything else is JSON-encoded."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, prompt):
        self.calls.append(prompt)
        item = self.responses.pop(0)
        return item if isinstance(item, str) else json.dumps(item)


FINDING = {"file": "users.py", "line": 2, "title": "Caller still expects positional values",
           "reason": "Dictionary iteration returns keys, not the former values"}


def confirmed_verdict():
    return {"items": [{
        "candidate_id": 0, "decision": "confirmed", "confidence": .9,
        "trigger": "render calls get_profile", "call_path": ["caller.py:3", "users.py:2"],
        "expected_behavior": "age is an integer", "actual_behavior": "age is the dictionary key age",
        "introduced_by": "return changed from tuple to dictionary", "reason": "Caller contract not migrated",
        "evidence": [{"path": "caller.py", "side": "head", "line": 3, "quote": "name, age = get_profile()"},
                     {"path": "users.py", "side": "base", "line": 2, "quote": 'return ("name", 42)'}],
        "counter_evidence": [],
    }]}


def raw_review(repo, responses):
    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="t", base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False, allow_remote_file_fetch=False)
    provider = RawProvider(responses)
    result = review_grounded(pr, files, provider, snapshot, context, raw_discovery=True)
    return result, provider


def sample_trace(result):
    return [entry for entry in result.bundle["discovery_trace"] if entry.get("mode") == "raw"]


def test_one_unparseable_sample_keeps_the_other_samples(repo):
    result, provider = raw_review(repo, [
        "not json at all",                       # diff sample: unparseable
        {"findings": [FINDING]},                 # diff+evidence sample
        {"findings": []},                        # lenses sample
        confirmed_verdict(),
    ])
    assert len(provider.calls) == 4
    assert [s.title for s in result.suggestions] == [FINDING["title"]]
    failed, ok, _ = sample_trace(result)
    assert failed["input"] == "diff" and failed["status"] == "failed"
    assert failed["error_type"] == "JSONDecodeError"
    assert failed["raw_excerpt"] == "not json at all"
    assert ok["status"] == "ok" and ok["suggestions"] == 1


def test_every_sample_unparseable_still_fails_the_lane(repo):
    with pytest.raises(ValueError):
        raw_review(repo, ["nope", "nope", "nope"])


def test_mixed_valid_and_invalid_items_keep_valid_and_record_each_invalid(repo):
    broken = [
        "just a string",
        {"file": "users.py", "line": "two", "title": "line is not a number", "reason": "r"},
        {"title": "no file", "line": 2, "reason": "r"},
        {"file": "users.py", "title": "line missing", "reason": "r"},
    ]
    result, _ = raw_review(repo, [
        {"findings": [*broken, FINDING]},
        {"findings": []},
        {"findings": []},
        confirmed_verdict(),
    ])
    assert [s.title for s in result.suggestions] == [FINDING["title"]]
    first = sample_trace(result)[0]
    assert [(e["index"], e["reason"]) for e in first["parse_errors"]] == [
        (0, "not_an_object"), (1, "line_not_integer"), (2, "missing_file"), (3, "line_not_integer"),
    ]
    assert all(e["origin"] == "diff" and e["excerpt"] for e in first["parse_errors"])
    dedupe = next(e for e in result.bundle["discovery_trace"] if e.get("mode") == "dedupe")
    assert dedupe["parse_error_count"] == 4


def _duplicated_responses():
    """The same finding from all three samples, so dedupe merges them."""
    twin = {**FINDING, "title": "Caller still expects positional values (lens)"}
    return [
        {"findings": [FINDING]},
        {"findings": [{**FINDING, "title": "Caller still expects positional values"}]},
        {"findings": [twin]},
        confirmed_verdict(),
    ]


def test_same_responses_processed_twice_give_identical_candidate_ids(repo):
    first, _ = raw_review(repo, _duplicated_responses())
    second, _ = raw_review(repo, _duplicated_responses())
    ids_first = [(c["candidate_id"], c["suggestion"]["raw_candidate_id"]) for c in first.candidates]
    ids_second = [(c["candidate_id"], c["suggestion"]["raw_candidate_id"]) for c in second.candidates]
    assert ids_first == ids_second
    assert ids_first[0][1] == "diff:0"


def test_merged_candidate_keeps_every_member_id_and_origin(repo):
    result, _ = raw_review(repo, _duplicated_responses())
    assert len(result.candidates) == 1
    kept = result.candidates[0]["suggestion"]
    assert kept["raw_candidate_id"] == "diff:0"
    assert kept["candidate_origin"] == "diff"
    assert kept["merged_candidates"] == [
        {"raw_candidate_id": "diff+evidence:0", "origin": "diff+evidence"},
        {"raw_candidate_id": "lenses:0", "origin": "lenses"},
    ]
    inventory = result.bundle["raw_discovery_candidates"]
    assert [(row["raw_candidate_id"], row["merged_into"], row["status"]) for row in inventory] == [
        ("diff:0", None, "confirmed"),
        ("diff+evidence:0", "diff:0", "confirmed"),
        ("lenses:0", "diff:0", "confirmed"),
    ]


def test_dedupe_keeps_member_list_when_rule_candidate_replaces_model_candidate():
    model = {"file": "users.py", "line": 2, "title": "Patched name is bound locally", "reason": "r",
             "origin": "diff", "raw_candidate_id": "diff:0"}
    rule = {"file": "users.py", "line": 2, "title": "[TQ001] Patched target", "reason": "r",
            "rule_id": "TQ001", "origin": "test_quality_rules", "raw_candidate_id": "test_quality_rules:0"}
    kept, merges = dedupe_candidates([model, rule])
    assert len(kept) == 1
    assert kept[0]["raw_candidate_id"] == "test_quality_rules:0"
    assert kept[0]["merged_candidates"] == [{"raw_candidate_id": "diff:0", "origin": "diff"}]
    assert merges[0]["merged"]["raw_candidate_id"] == "diff:0"
    assert "merged_candidates" not in model  # the caller's pool items are not mutated


def test_ledger_rows_and_audit_carry_candidate_id_and_origin(repo, tmp_path):
    result, _ = raw_review(repo, _duplicated_responses())
    report = SimpleNamespace(
        candidate_audit=result.candidates,
        behavior_verifications=result.decisions,
        suggestions=result.suggestions,
        pending_suggestions=result.pending,
        pr=SimpleNamespace(repo="fixture/repo", number=1, head_sha="c" * 40),
        ai_failure_reason=None,
        used_ai=True,
        repository_context={"review_trace": {"candidate_counts": {"raw": len(result.candidates)}}},
    )
    records = [r.as_dict() for r in decision_records(report, run_id="run-1", criteria_version="v")]
    assert records[0]["candidate_id"] == 0
    assert records[0]["raw_candidate_id"] == "diff:0"
    assert records[0]["candidate_origin"] == "diff"
    assert records[0]["merged_candidate_ids"] == ["diff+evidence:0", "lenses:0"]

    lifecycle = build_candidate_lifecycle(report, run_id="run-1", criteria_version="v")
    row = lifecycle["candidates"][0]
    assert row["raw_candidate_id"] == "diff:0"
    assert row["candidate_origin"] == "diff"

    ledger = tmp_path / "ledger.jsonl"
    append_candidate_ledger(ledger, lifecycle)
    persisted = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    candidate_rows = [item for item in persisted if item.get("record_type") == "candidate"]
    assert candidate_rows[0]["raw_candidate_id"] == "diff:0"
    assert candidate_rows[0]["candidate_origin"] == "diff"
    assert candidate_rows[0]["merged_candidate_ids"] == ["diff+evidence:0", "lenses:0"]


def test_failed_sample_and_malformed_items_make_the_report_partial(repo):
    from src.reviewer.grounded_review import build_grounded_report

    root, base, head = repo
    result, _ = raw_review(repo, [
        "not json at all",
        {"findings": [FINDING, "not an object"]},
        {"findings": []},
        confirmed_verdict(),
    ])
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="t", base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False, allow_remote_file_fetch=False)
    report = build_grounded_report(pr, files, [], result, context)
    assert report.report_confidence == "partial"
    assert any("Discovery sample(s) failed to parse: diff" in w for w in report.analysis_warnings)
    assert any("1 discovery item(s) were malformed" in w for w in report.analysis_warnings)
