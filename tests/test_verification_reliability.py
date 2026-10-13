"""Regression cases for evidence bypass, partial review and fatal benchmark errors."""

import json

from src.evaluation.experiment import run
from src.evaluation.scoring import GoldenCase
from src.models import ChangedFile, FileStatus, PullRequest, RiskFinding, Severity
from src.reviewer.critic import _candidate_window
from src.reviewer.engine import review_with_ai
from src.reviewer.provider import ProviderError

PR = PullRequest(repo="test/repo", number=1, title="Change")
FILE = ChangedFile(
    filename="app.py", status=FileStatus.MODIFIED, patch="@@ -1 +1 @@\n-old\n+print(token)\n"
)
SIGNAL = RiskFinding(
    file_path="app.py",
    line=1,
    rule_id="secret-logging",
    title="Secret",
    severity=Severity.HIGH,
    evidence="print(token)",
    recommendation="Mask",
)


class SequenceProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append((system, user))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)


EMPTY = {"summary": "No defects", "risk_level": "low", "suggestions": []}
HOTSPOT = {"file_path": "app.py", "start_line": 1, "end_line": 1, "reason": "Inspect"}


def signal_response(*, complete):
    item = {
        "key": {"rule_id": "secret-logging", "file_path": "app.py", "line": 1},
        "decision": "confirmed",
        "confidence": 0.9,
        "reason": "Looks suspicious",
    }
    if complete:
        item.update(
            evidence=["print(token)"],
            failure_scenario="A credential supplied as token is written to stdout",
        )
    return {"items": [item]}


def test_signal_confirmation_cannot_substitute_reason_for_evidence():
    provider = SequenceProvider([EMPTY, signal_response(complete=False)])
    report = review_with_ai(PR, [FILE], [SIGNAL], provider, 10, verify_rule_signals=True)
    assert report.suggestions == []
    assert report.evidence_rejections[0]["source"] == "rule_confirmed"
    assert report.confirmed_signals[0]["decision"] == "confirmed"  # audit, not final proof
    assert "Secret" not in provider.calls[0][1]


def test_rule_candidate_passes_critic_after_merge():
    provider = SequenceProvider(
        [
            EMPTY,
            signal_response(complete=True),
            {
                "items": [
                    {
                        "suggestion_index": 0,
                        "decision": "dismissed",
                        "confidence": 0.9,
                        "reason": "Token is public",
                    }
                ]
            },
        ]
    )
    report = review_with_ai(
        PR, [FILE], [SIGNAL], provider, 10, verify_rule_signals=True, enable_critic=True
    )
    assert report.suggestions == []
    assert len(provider.calls) == 3
    assert report.metrics["critic_dismissed_count"] == 1


def test_no_hotspot_triggers_general_review_not_clean_shortcut():
    provider = SequenceProvider([{"hotspots": []}, EMPTY])
    report = review_with_ai(PR, [FILE], [], provider, 10, two_stage=True)
    assert len(provider.calls) == 2
    assert "print(token)" in provider.calls[0][1]
    assert report.stage_results[-1]["stage"] == "one_shot_fallback"
    assert report.execution_status == "success"


def test_all_deep_reviews_failed_is_failed_report():
    provider = SequenceProvider([{"hotspots": [HOTSPOT]}, ProviderError("offline")])
    report = review_with_ai(PR, [FILE], [], provider, 10, two_stage=True)
    assert report.execution_status == "failed"
    assert report.report_confidence == "failed"
    assert report.stage_results[-1]["status"] == "failed"
    assert report.suggestions == []


def test_partial_deep_review_preserves_explicit_coverage():
    provider = SequenceProvider(
        [{"hotspots": [HOTSPOT, HOTSPOT]}, ProviderError("offline"), {"suggestions": []}]
    )
    report = review_with_ai(PR, [FILE], [], provider, 10, two_stage=True)
    assert report.execution_status == "partial"
    assert report.report_confidence == "partial"
    assert [s["status"] for s in report.stage_results] == ["success", "failed", "success"]


def test_critic_uses_later_candidate_hunk():
    file = FILE.model_copy(
        update={
            "patch": "@@ -1 +1 @@\n-old\n+"
            + "x" * 4000
            + "\n@@ -500 +500 @@\n-old\n+print(token)\n"
        }
    )
    excerpt = _candidate_window(file, 500)
    assert "print(token)" in excerpt
    assert len(excerpt) <= 3000
    assert "x" * 100 not in excerpt


def test_402_stops_all_remaining_experiment_calls():
    provider = SequenceProvider([ProviderError("Insufficient balance", status_code=402)])
    case = GoldenCase(
        case_id="negative",
        source="synthetic",
        split="dev",
        group="repo",
        metadata={
            "nwo": "test/repo",
            "pr_number": 1,
            "title": "Change",
            "base": "a" * 40,
            "head": "b" * 40,
        },
        diff="diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n" + FILE.patch,
        findings=[],
        negative_verified=True,
        label_provenance="Fixture only",
    )
    results = run([case], provider, "test-model")
    assert len(provider.calls) == 1
    assert [r["status"] for r in results["records"]] == ["failed", "not_run", "not_run", "not_run"]
    assert results["records"][0]["failure"]["http_status"] == 402
    assert results["reviewer_source_sha256"]


def test_metadata_only_and_newline_marker_are_real_git_changes():
    from src.analyzer.diff_parser import parse_file_hunks
    from src.evaluation.reviewbench import parse_diff

    moved = parse_diff(
        "diff --git a/old.py b/new.py\nsimilarity index 100%\n"
        "rename from old.py\nrename to new.py\n"
    )[0]
    assert moved.status == FileStatus.RENAMED
    assert moved.previous_filename == "old.py"
    empty = parse_diff("diff --git a/empty.py b/empty.py\nnew file mode 100644\n")[0]
    assert empty.status == FileStatus.ADDED
    file = FILE.model_copy(
        update={
            "patch": "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n"
            "+new\n\\ No newline at end of file\n"
        }
    )
    assert parse_file_hunks(file)[0].added_lines[0].line == 1
