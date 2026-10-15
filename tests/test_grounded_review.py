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


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    (tmp_path / "users.py").write_text('def get_profile():\n    return ("name", 42)\n')
    (tmp_path / "caller.py").write_text('from users import get_profile\ndef render():\n    name, age = get_profile()\n    return age + 1\n')
    git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "users.py").write_text('def get_profile():\n    return {"name": "name", "age": 42}\n')
    git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "head")
    head = git(tmp_path, "rev-parse", "HEAD")
    return tmp_path, base, head


class Provider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
    def complete_json(self, system, prompt):
        self.calls.append((system, prompt))
        return json.dumps(next(self.responses))


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


def run(repo, responses):
    root, base, head = repo
    snapshot = GitSnapshot(root, base, head)
    files = parse_diff(git(root, "diff", base, head))
    pr = PullRequest(repo="fixture/repo", number=1, title="Return contract", base_sha=base, head_sha=head)
    context = build_review_context(pr, files, include_context_pack=False, allow_remote_file_fetch=False)
    provider = Provider(responses)
    report = review_with_ai(pr, files, [], provider, 20, min_confidence=.65,
                            repository_snapshot=snapshot, follow_changes=True,
                            review_context=context)
    return report, provider


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
    response = {**candidate(), "context_requests": [{"tool": "search", "symbol": "render", "side": "head"}]}
    report, provider = run(repo, [response, candidate(), verdict()])
    assert len(provider.calls) == 3
    assert report.repository_context["requests"][0]["executed"]
    assert report.metrics["discovery_request_count"] == 2
    assert report.suggestions


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

    files = [ChangedFile(filename="x.py", patch=PATCH)]
    snapshot = _SnapshotWith("def f():\n    return 2\n")
    citation = {"path": "x.py", "side": "head", "line": 2, "quote": "return 2"}
    # The line is inside the file's hunk, but the prompt we sent did not contain it.
    assert _valid_citation(citation, snapshot, _Bundle([]), files, "no diff here") is False
    # The same citation is accepted when the line really was in the sent text.
    assert _valid_citation(citation, snapshot, _Bundle([]), files, SNIPPET_SENT) is True


def test_citation_from_a_rendered_evidence_excerpt_is_accepted():
    from src.models import ChangedFile
    from src.reviewer.grounded_review import _valid_citation

    files = [ChangedFile(filename="x.py", patch=PATCH)]
    snapshot = _SnapshotWith("def f():\n    return 2\n")
    excerpts = [{"path": "x.py", "side": "head", "start_line": 1, "end_line": 4}]
    citation = {"path": "x.py", "side": "head", "line": 2, "quote": "return 2"}
    assert _valid_citation(citation, snapshot, _Bundle(excerpts), files, "") is True


def test_citation_that_does_not_match_the_snapshot_is_still_rejected():
    from src.models import ChangedFile
    from src.reviewer.grounded_review import _valid_citation

    files = [ChangedFile(filename="x.py", patch=PATCH)]
    snapshot = _SnapshotWith("def f():\n    return 2\n")
    citation = {"path": "x.py", "side": "head", "line": 2, "quote": "return 999"}
    assert _valid_citation(citation, snapshot, _Bundle([]), files, SNIPPET_SENT) is False
