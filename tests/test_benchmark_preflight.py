from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation.benchmark_preflight import (
    _case_snapshot,
    _fixed_label_critic_replay,
    _git,
    _prepared_manifest_trusted,
    _replay_saved_evidence_gate,
)

BASE = "a" * 40
HEAD = "b" * 40
CASE = "owner_repo_12-" + HEAD[:8]


def _prepared(tmp_path: Path) -> tuple[Path, Path, dict]:
    prepared = tmp_path / "prepared"
    inputs = prepared / "inputs" / CASE
    inputs.mkdir(parents=True)
    (inputs / "pr.json").write_text(
        json.dumps(
            {
                "nwo": "owner/repo",
                "pr_number": 12,
                "base": BASE,
                "head": HEAD,
            }
        )
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1 +1 @@\n-print('old')\n+print('new')\n"
    )
    (inputs / "diff.patch").write_text(diff)
    record = {
        "case_id": CASE,
        "input_dir": str(inputs),
        "metadata": json.loads((inputs / "pr.json").read_text()),
        "diff_sha256": "placeholder",
    }
    import hashlib

    record["diff_sha256"] = hashlib.sha256(diff.encode()).hexdigest()
    return prepared, inputs, record


def test_snapshot_preflight_never_fetches_and_reports_missing_object(tmp_path: Path) -> None:
    prepared, _, record = _prepared(tmp_path)
    mirrors = tmp_path / "mirrors"
    mirror = mirrors / "owner_repo"
    (mirror / ".git").mkdir(parents=True)

    with patch(
        "scripts.evaluation.benchmark_preflight._git",
        side_effect=lambda _repo, *args: subprocess.CompletedProcess(
            ["git", *args], 0 if args[-1] == f"{HEAD}^{{commit}}" else 1, "", ""
        ),
    ) as mocked:
        result = _case_snapshot(record, prepared, mirrors, manifest_trusted=True)

    assert result["status"] == "not_ready"
    assert result["reasons"] == ["base_commit_not_cached"]
    assert all("fetch" not in call.args[1:] for call in mocked.call_args_list)


def test_local_git_probe_disables_lazy_fetch(monkeypatch, tmp_path: Path) -> None:
    observed = {}

    def fake_run(args, **kwargs):
        observed["args"] = args
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("scripts.evaluation.benchmark_preflight.subprocess.run", fake_run)
    _git(tmp_path, "cat-file", "-e", f"{HEAD}^{{commit}}")
    assert observed["env"]["GIT_NO_LAZY_FETCH"] == "1"
    assert "fetch" not in observed["args"]


def test_prepared_manifest_hash_must_match_split() -> None:
    raw = b'{"records": []}'
    import hashlib

    expected = hashlib.sha256(raw).hexdigest()
    assert _prepared_manifest_trusted(raw, {"prepared_sha256": expected})
    assert not _prepared_manifest_trusted(raw, {"prepared_sha256": "0" * 64})


def test_case_metadata_and_diff_must_match_trusted_record(tmp_path: Path) -> None:
    prepared, inputs, record = _prepared(tmp_path)
    metadata = json.loads((inputs / "pr.json").read_text())
    metadata["title"] = "tampered"
    (inputs / "pr.json").write_text(json.dumps(metadata))
    mirrors = tmp_path / "mirrors"
    with patch("scripts.evaluation.benchmark_preflight._commit_available", return_value=True):
        result = _case_snapshot(record, prepared, mirrors, manifest_trusted=True)
    assert result["status"] == "not_ready"
    assert "prepared_metadata_record_mismatch" in result["reasons"]

    _, inputs, record = _prepared(tmp_path / "second")
    (inputs / "diff.patch").write_text("tampered diff")
    with patch("scripts.evaluation.benchmark_preflight._commit_available", return_value=True):
        result = _case_snapshot(
            record, tmp_path / "second" / "prepared", mirrors, manifest_trusted=True
        )
    assert result["status"] == "not_ready"
    assert "prepared_diff_hash_mismatch" in result["reasons"]


def test_saved_final_candidate_runs_only_current_evidence_gate(tmp_path: Path) -> None:
    prepared, inputs, record = _prepared(tmp_path)
    audit_dir = tmp_path / "audits"
    candidate_dir = tmp_path / "candidates"
    audit_dir.mkdir()
    candidate_dir.mkdir()
    title = "Changed output differs"
    suggestion = {
        "file_path": "app.py",
        "line": 1,
        "severity": "medium",
        "confidence": 0.8,
        "title": title,
        "reason": "The output changes.",
        "recommendation": "Check callers.",
        "source": "ai",
        "evidence": ["print('new')"],
        "failure_scenario": "The caller observes a changed output.",
    }
    off_patch = {**suggestion, "line": 7, "title": "Citation outside diff"}
    (audit_dir / f"{CASE}.json").write_text(
        json.dumps(
            {
                "report": {
                    "pr": {"base_sha": BASE, "head_sha": HEAD},
                    "suggestions": [suggestion, off_patch],
                }
            }
        )
    )
    (candidate_dir / f"{CASE}.json").write_text(
        json.dumps(
            {
                "pr": {"base": BASE, "head": HEAD},
                "findings": [
                    {"file": "app.py", "start_line": 1, "message": title},
                    {"file": "app.py", "start_line": 7, "message": off_patch["title"]},
                ],
            }
        )
    )

    result = _replay_saved_evidence_gate(
        audit_dir, candidate_dir, {CASE: record}, prepared, manifest_trusted=True
    )

    assert result["full_chain_replay"]["candidate_count"] == 2
    assert result["full_chain_replay"]["not_replayable"] == 2
    assert result["component_replay"]["totals"] == {
        "input_candidates": 2,
        "retained": 1,
        "rejected": 1,
        "not_replayable": 0,
    }
    assert result["cases"][0]["evidence_gate"][0]["status"] == "retained"
    assert result["cases"][0]["evidence_gate"][1]["status"] == "rejected"
    assert "not an added line" in result["cases"][0]["evidence_gate"][1]["reason"]

    untrusted = _replay_saved_evidence_gate(
        audit_dir, candidate_dir, {CASE: record}, prepared, manifest_trusted=False
    )
    assert untrusted["component_replay"]["totals"]["input_candidates"] == 0
    assert untrusted["cases"][0]["reason"] == "prepared_manifest_hash_mismatch"


def test_missing_saved_candidate_is_not_counted_as_rejection(tmp_path: Path) -> None:
    prepared, _, record = _prepared(tmp_path)
    audit_dir = tmp_path / "audits"
    candidate_dir = tmp_path / "candidates"
    audit_dir.mkdir()
    candidate_dir.mkdir()
    (audit_dir / f"{CASE}.json").write_text(
        json.dumps({"report": {"pr": {"base_sha": BASE, "head_sha": HEAD}, "suggestions": [{}]}})
    )

    result = _replay_saved_evidence_gate(
        audit_dir, candidate_dir, {CASE: record}, prepared, manifest_trusted=True
    )
    case = result["cases"][0]
    assert case["reason"] == "saved_candidate_missing_or_invalid_count"
    assert case["candidate_count_status"] == "unknown"
    assert result["full_chain_replay"]["candidate_count_status"] == "partial_unknown"
    assert result["full_chain_replay"]["unknown_candidate_cases"] == [CASE]
    assert "evidence_gate_summary" not in case


def test_fixed_label_critic_replay_recomputes_from_joined_artifacts(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    critic_dir = tmp_path / "critic"
    audit_dir = tmp_path / "audits"
    for directory in (baseline_dir, critic_dir, audit_dir):
        directory.mkdir()
    findings = [
        {"file": "a.py", "start_line": 1, "message": "one"},
        {"file": "b.py", "start_line": 2, "message": "two"},
        {"file": "c.py", "start_line": 3, "message": "three"},
    ]
    (baseline_dir / f"{CASE}.json").write_text(json.dumps({"findings": findings}))
    (critic_dir / f"{CASE}.json").write_text(json.dumps({"findings": findings[:2]}))
    detail = [
        {
            "pr_key": CASE,
            "candidate_findings": [
                {
                    **findings[0],
                    "status": "matched_tp",
                    "golden_tp_fp": "tp",
                    "covered_golden_indices": [1, 2],
                },
                {
                    **findings[1],
                    "status": "matched_fp",
                    "golden_tp_fp": "fp",
                    "covered_golden_indices": [],
                },
                {
                    **findings[2],
                    "status": "novel_tp",
                    "golden_tp_fp": None,
                    "covered_golden_indices": [],
                },
            ],
        }
    ]
    detail_path = tmp_path / "details.json"
    detail_path.write_text(json.dumps(detail))
    score_path = tmp_path / "score.json"
    score_path.write_text(
        json.dumps(
            {
                "micro": {
                    "overall": {
                        "candidate_count": 3,
                        "golden_tp_count": 7,
                        "matched_count": 2,
                        "matched_tp_count": 1,
                        "unmatched_tp_count": 1,
                        "golden_covered_count": 2,
                    }
                }
            }
        )
    )
    (tmp_path / "run.json").write_text(json.dumps({"protocol": "fixed-label"}))
    (audit_dir / f"{CASE}.json").write_text(
        json.dumps(
            {
                "request_count": 1,
                "usage_records": [
                    {"usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}
                ],
            }
        )
    )

    result = _fixed_label_critic_replay(
        baseline_dir,
        critic_dir,
        detail_path,
        score_path,
        tmp_path / "run.json",
        audit_dir,
    )

    assert result["status"] == "recomputed_from_artifact_joins"
    assert result["baseline"]["candidate_count"] == 3
    assert result["baseline"]["gold_count"] == 7
    assert result["baseline"]["gold_linked_count"] == 2
    assert result["baseline"]["novel_tp"] == 1
    assert result["critic_fixed_labels"]["candidate_count"] == 2
    assert result["critic_fixed_labels"]["grounded_precision"] == 0.5
    assert result["critic_usage"]["total_tokens"] == 6
