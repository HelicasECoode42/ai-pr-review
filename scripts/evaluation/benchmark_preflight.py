#!/usr/bin/env python3
"""Offline benchmark readiness and saved-candidate evidence-gate replay.

This command never fetches repositories or creates a provider. Missing local
objects and incomplete historical evidence are reported as not ready instead
of being repaired or inferred.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.evaluation.reviewbench import parse_diff
from src.models import ReviewSuggestion
from src.reviewer.evidence_validator import validate_ai_evidence

SHA40 = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_BASELINE = "b30de7b8340b46a8c8ce8c34c09a9491e7df98b9"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Read local Git metadata only; disable promisor-object lazy fetching."""
    env = os.environ.copy()
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


def _commit_available(repo: Path, sha: str) -> bool:
    if not SHA40.fullmatch(sha):
        return False
    result = _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    return result.returncode == 0


def _case_snapshot(
    record: dict[str, Any],
    prepared_root: Path,
    mirrors_root: Path,
    *,
    manifest_trusted: bool,
) -> dict[str, Any]:
    case_id = str(record.get("case_id", ""))
    result: dict[str, Any] = {"case_id": case_id, "status": "not_ready", "reasons": []}
    input_dir = Path(str(record.get("input_dir", ""))).resolve()
    allowed_input_root = (prepared_root / "inputs").resolve()
    if not input_dir.is_relative_to(allowed_input_root):
        result["reasons"].append("input_path_outside_prepared_root")
        return result
    metadata_path = input_dir / "pr.json"
    diff_path = input_dir / "diff.patch"
    if not metadata_path.is_file() or not diff_path.is_file():
        result["reasons"].append("prepared_input_missing")
        return result

    try:
        metadata_bytes = metadata_path.read_bytes()
        diff_bytes = diff_path.read_bytes()
        metadata = json.loads(metadata_bytes)
    except (OSError, json.JSONDecodeError):
        result["reasons"].append("prepared_input_unreadable")
        return result

    metadata_sha = _sha256_bytes(metadata_bytes)
    diff_sha = _sha256_bytes(diff_bytes)
    result["metadata_bytes_sha256"] = metadata_sha
    result.update(
        {
            "metadata_sha256": _canonical_sha256(metadata),
            "diff_sha256": diff_sha,
            "base_sha": metadata.get("base"),
            "head_sha": metadata.get("head"),
        }
    )
    if not manifest_trusted:
        result["reasons"].append("prepared_manifest_hash_mismatch")
    if metadata != record.get("metadata"):
        result["reasons"].append("prepared_metadata_record_mismatch")
    if record.get("diff_sha256") != diff_sha:
        result["reasons"].append("prepared_diff_hash_mismatch")
    if not SHA40.fullmatch(str(metadata.get("base", ""))):
        result["reasons"].append("invalid_base_sha")
    if not SHA40.fullmatch(str(metadata.get("head", ""))):
        result["reasons"].append("invalid_head_sha")

    nwo = metadata.get("nwo")
    if not isinstance(nwo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", nwo):
        result["reasons"].append("invalid_repository")
        return result
    mirror = (mirrors_root / nwo.replace("/", "_")).resolve()
    if not mirror.is_relative_to(mirrors_root.resolve()) or not (mirror / ".git").exists():
        result["reasons"].append("local_mirror_missing")
        return result
    for side in ("base", "head"):
        sha = metadata.get(side)
        if SHA40.fullmatch(str(sha or "")) and not _commit_available(mirror, sha):
            result["reasons"].append(f"{side}_commit_not_cached")
    if not result["reasons"]:
        result["status"] = "ready"
    return result


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _prepared_manifest_trusted(prepared_bytes: bytes, split: dict[str, Any]) -> bool:
    """A roster's prepared hash authenticates the local case metadata and diff hashes."""
    expected = split.get("prepared_sha256")
    return isinstance(expected, str) and _sha256_bytes(prepared_bytes) == expected


def _replay_saved_evidence_gate(
    audit_dir: Path,
    candidate_dir: Path,
    prepared_records: dict[str, dict[str, Any]],
    prepared_root: Path,
    *,
    manifest_trusted: bool,
) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    unknown_candidate_cases: list[str] = []
    for audit_path in sorted(audit_dir.glob("*.json")):
        case_id = audit_path.stem
        record = prepared_records.get(case_id)
        per_case: dict[str, Any] = {
            "case_id": case_id,
            "candidate_source": "historical_final_report_suggestions",
            "full_criteria_chain": "not_replayable",
            "evidence_gate": [],
        }
        candidate_path = candidate_dir / f"{case_id}.json"
        saved_count: int | None = None
        if candidate_path.is_file():
            try:
                saved = _load_json(candidate_path)
                findings = saved.get("findings")
                if isinstance(findings, list):
                    saved_count = len(findings)
            except (OSError, json.JSONDecodeError, TypeError, AttributeError):
                pass
        per_case["full_chain_candidate_count"] = saved_count
        per_case["candidate_count_status"] = "known" if saved_count is not None else "unknown"
        if saved_count is None:
            per_case["reason"] = "saved_candidate_missing_or_invalid_count"
            unknown_candidate_cases.append(case_id)
            outcomes.append(per_case)
            continue
        if not manifest_trusted:
            per_case["reason"] = "prepared_manifest_hash_mismatch"
            per_case["not_replayable_count"] = saved_count
            outcomes.append(per_case)
            continue
        if record is None:
            per_case["reason"] = "no_matching_prepared_record"
            outcomes.append(per_case)
            continue
        input_dir = Path(str(record.get("input_dir", ""))).resolve()
        if not input_dir.is_relative_to((prepared_root / "inputs").resolve()):
            per_case["reason"] = "input_path_outside_prepared_root"
            outcomes.append(per_case)
            continue
        try:
            audit = _load_json(audit_path)
            metadata = _load_json(input_dir / "pr.json")
            diff_bytes = (input_dir / "diff.patch").read_bytes()
            diff = diff_bytes.decode("utf-8")
            parsed = parse_diff(diff)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            per_case["reason"] = f"input_unavailable:{type(exc).__name__}"
            outcomes.append(per_case)
            continue
        if metadata != record.get("metadata"):
            per_case["reason"] = "prepared_metadata_record_mismatch"
            outcomes.append(per_case)
            continue
        if _sha256_bytes(diff_bytes) != record.get("diff_sha256"):
            per_case["reason"] = "prepared_diff_hash_mismatch"
            outcomes.append(per_case)
            continue
        pr = audit.get("report", {}).get("pr", {})
        audit_base = pr.get("base_sha") or pr.get("base_ref")
        audit_head = pr.get("head_sha") or pr.get("head_ref")
        if audit_base != metadata.get("base") or audit_head != metadata.get("head"):
            per_case["reason"] = "historical_audit_snapshot_mismatch"
            outcomes.append(per_case)
            continue

        suggestions = audit.get("report", {}).get("suggestions")
        try:
            candidate = _load_json(candidate_path)
        except (OSError, json.JSONDecodeError):
            per_case["reason"] = "saved_candidate_missing_or_unreadable"
            outcomes.append(per_case)
            continue
        candidate_pr = candidate.get("pr", {})
        if candidate_pr.get("base") != metadata.get("base") or candidate_pr.get(
            "head"
        ) != metadata.get("head"):
            per_case["reason"] = "saved_candidate_snapshot_mismatch"
            outcomes.append(per_case)
            continue
        if not isinstance(suggestions, list):
            per_case["reason"] = "historical_suggestions_missing"
            outcomes.append(per_case)
            continue
        saved_findings = candidate.get("findings")
        if not isinstance(saved_findings, list) or len(saved_findings) != len(suggestions):
            per_case["reason"] = "audit_suggestions_do_not_match_saved_candidate_set"
            per_case["not_replayable_count"] = len(suggestions)
            outcomes.append(per_case)
            continue
        corroborated = all(
            any(
                finding.get("file") == suggestion.get("file_path")
                and finding.get("start_line") == suggestion.get("line")
                and isinstance(finding.get("message"), str)
                and finding["message"].startswith(str(suggestion.get("title", "")))
                for finding in saved_findings
            )
            for suggestion in suggestions
        )
        if not corroborated:
            per_case["reason"] = "audit_suggestions_do_not_match_saved_candidate_set"
            per_case["not_replayable_count"] = len(suggestions)
            outcomes.append(per_case)
            continue
        try:
            typed = [ReviewSuggestion.model_validate(item) for item in suggestions]
        except (ValidationError, TypeError):
            per_case["reason"] = "historical_suggestion_schema_incomplete"
            per_case["not_replayable_count"] = len(suggestions)
            outcomes.append(per_case)
            continue

        kept, rejected = validate_ai_evidence(typed, parsed)
        rejected_by_key = {
            (item.get("file_path"), item.get("line"), item.get("title")): item for item in rejected
        }
        for suggestion in typed:
            key = (suggestion.file_path, suggestion.line, suggestion.title)
            rejection = rejected_by_key.get(key)
            per_case["evidence_gate"].append(
                {
                    "file_path": suggestion.file_path,
                    "line": suggestion.line,
                    "finding_sha256": _canonical_sha256(suggestion.model_dump(mode="json")),
                    "status": "rejected" if rejection else "retained",
                    "reason": rejection.get("reason") if rejection else None,
                }
            )
        per_case["evidence_gate_summary"] = {
            "input_candidates": len(typed),
            "retained": len(kept),
            "rejected": len(rejected),
            "not_replayable": 0,
        }
        per_case["reason"] = (
            "evidence_gate_only; historical candidates are already filtered and do not "
            "contain structured base/head citations, counter-evidence, or full sent bundle"
        )
        outcomes.append(per_case)

    totals = Counter()
    for item in outcomes:
        summary = item.get("evidence_gate_summary", {})
        not_replayable = item.get("not_replayable_count", 0)
        totals.update(
            {
                key: int(summary.get(key, 0))
                for key in ("input_candidates", "retained", "rejected", "not_replayable")
            }
        )
        totals["not_replayable"] += int(not_replayable)
    full_candidate_count = sum(
        item["full_chain_candidate_count"]
        for item in outcomes
        if isinstance(item.get("full_chain_candidate_count"), int)
    )
    return {
        "scope": "saved_final_candidate_evidence_gate_only",
        "full_chain_replay": {
            "status": "not_replayable_from_saved_artifacts",
            "candidate_count": full_candidate_count,
            "candidate_count_status": "partial_unknown" if unknown_candidate_cases else "known",
            "unknown_candidate_cases": unknown_candidate_cases,
            "replayable": 0,
            "not_replayable": full_candidate_count,
            "reason": (
                "historical outputs lack structured base/head citations, original sent bundle, "
                "counter-evidence, and pre-filter model candidates"
            ),
        },
        "cases": outcomes,
        "component_replay": {
            "component": "current exact-added-line evidence gate only",
            "totals": dict(totals),
        },
    }


def _fixed_label_critic_replay(
    baseline_candidate_dir: Path,
    critic_candidate_dir: Path,
    baseline_detail_path: Path,
    baseline_score_path: Path,
    critic_run_path: Path,
    critic_audit_dir: Path,
) -> dict[str, Any]:
    """Recompute the critic ablation by retaining baseline judge labels unchanged."""
    baseline_by_key: dict[tuple[str, str, int, str], list[dict[str, Any]]] = {}
    detail_rows = _load_json(baseline_detail_path)
    for case in detail_rows:
        case_id = case.get("pr_key")
        for finding in case.get("candidate_findings", []):
            key = (case_id, finding.get("file"), finding.get("start_line"), finding.get("message"))
            baseline_by_key.setdefault(key, []).append(finding)

    baseline_candidates: Counter[tuple[str, str, int, str]] = Counter()
    for path in baseline_candidate_dir.glob("*.json"):
        candidate = _load_json(path)
        for finding in candidate.get("findings", []):
            key = (
                path.stem,
                finding.get("file"),
                finding.get("start_line"),
                finding.get("message"),
            )
            baseline_candidates[key] += 1

    critic_candidates: Counter[tuple[str, str, int, str]] = Counter()
    for path in critic_candidate_dir.glob("*.json"):
        candidate = _load_json(path)
        for finding in candidate.get("findings", []):
            key = (
                path.stem,
                finding.get("file"),
                finding.get("start_line"),
                finding.get("message"),
            )
            critic_candidates[key] += 1

    problems: list[str] = []
    baseline_labeled: list[dict[str, Any]] = []
    post_labeled: list[dict[str, Any]] = []
    for key, candidate_count in baseline_candidates.items():
        labels = baseline_by_key.get(key, [])
        if len(labels) != candidate_count:
            problems.append(f"baseline_label_join_count_mismatch:{key[0]}")
            continue
        if len({label.get("status") for label in labels}) > 1:
            problems.append(f"ambiguous_duplicate_baseline_labels:{key[0]}")
            continue
        kept_count = critic_candidates.get(key, 0)
        if kept_count > candidate_count:
            problems.append(f"critic_output_not_subset_of_baseline:{key[0]}")
            continue
        baseline_labeled.extend({**label, "_case_id": key[0]} for label in labels)
        post_labeled.extend({**label, "_case_id": key[0]} for label in labels[:kept_count])

    extra_critic = critic_candidates - baseline_candidates
    if extra_critic:
        problems.append("critic_output_contains_nonbaseline_findings")

    def score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
        counts = Counter(row.get("status") for row in rows)
        covered = {
            (row["_case_id"], index)
            for row in rows
            if row.get("status") == "matched_tp" and row.get("golden_tp_fp") == "tp"
            for index in row.get("covered_golden_indices", [])
        }
        candidate_count = len(rows)
        linked = counts["matched_tp"] + counts["matched_fp"]
        novel = counts["novel_tp"]
        gold_count = 0
        unique_covered = len(covered)
        return {
            "candidate_count": candidate_count,
            "gold_linked_count": linked,
            "matched_tp": counts["matched_tp"],
            "matched_fp": counts["matched_fp"],
            "novel_tp": novel,
            "grounded_precision": counts["matched_tp"] / linked if linked else None,
            "augmented_precision": (counts["matched_tp"] + novel) / candidate_count
            if candidate_count
            else None,
            "unique_gold_covered": unique_covered,
            "gold_count": gold_count,
            "grounded_recall": unique_covered / gold_count if gold_count else None,
            "augmented_recall": (unique_covered + novel) / (gold_count + novel)
            if gold_count
            else None,
        }

    baseline_score = score_rows(baseline_labeled)
    post_score = score_rows(post_labeled)
    score_artifact = _load_json(baseline_score_path)
    score_micro = score_artifact.get("micro", {}).get("overall", {})
    gold_count = score_micro.get("golden_tp_count")
    if type(gold_count) is not int or gold_count <= 0:
        problems.append("baseline_gold_denominator_missing_or_invalid")
    else:
        baseline_score["gold_count"] = gold_count
        post_score["gold_count"] = gold_count
        baseline_score["grounded_recall"] = baseline_score["unique_gold_covered"] / gold_count
        baseline_score["augmented_recall"] = (
            baseline_score["unique_gold_covered"] + baseline_score["novel_tp"]
        ) / (gold_count + baseline_score["novel_tp"])
        post_score["grounded_recall"] = post_score["unique_gold_covered"] / gold_count
        post_score["augmented_recall"] = (
            post_score["unique_gold_covered"] + post_score["novel_tp"]
        ) / (gold_count + post_score["novel_tp"])
        expected_counts = {
            "candidate_count": "candidate_count",
            "gold_linked_count": "matched_count",
            "matched_tp": "matched_tp_count",
            "matched_fp": "matched_count",
            "novel_tp": "unmatched_tp_count",
            "unique_gold_covered": "golden_covered_count",
        }
        for key, artifact_key in expected_counts.items():
            expected = score_micro.get(artifact_key)
            actual = baseline_score[key]
            if key == "matched_fp" and type(expected) is int:
                expected -= score_micro.get("matched_tp_count", 0)
            if expected != actual:
                problems.append(f"baseline_score_artifact_mismatch:{key}")

    critic_run = _load_json(critic_run_path)
    usage_tokens = Counter()
    usage_records = 0
    requests = 0
    for path in critic_audit_dir.glob("*.json"):
        audit = _load_json(path)
        requests += int(audit.get("request_count", 0) or 0)
        for item in audit.get("usage_records", []):
            usage = item.get("usage") if isinstance(item, dict) else None
            if not isinstance(usage, dict):
                continue
            usage_records += 1
            for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if type(usage.get(name)) is int:
                    usage_tokens[name] += usage[name]

    return {
        "protocol": critic_run.get("protocol"),
        "quality_status": critic_run.get("quality_status"),
        "reviewer_commit": critic_run.get("reviewer_commit"),
        "reviewer_source_sha256": critic_run.get("reviewer_source_sha256"),
        "working_tree_dirty": critic_run.get("working_tree_dirty"),
        "status": "recomputed_from_artifact_joins" if not problems else "not_replayable",
        "join_problems": problems,
        "source_labels": "baseline-score.details; reused unchanged, no fresh judge",
        "baseline": baseline_score if not problems else None,
        "critic_fixed_labels": post_score if not problems else None,
        "critic_usage": {
            "audit_request_count": requests,
            "usage_records": usage_records,
            "prompt_tokens": usage_tokens["prompt_tokens"],
            "completion_tokens": usage_tokens["completion_tokens"],
            "total_tokens": usage_tokens["total_tokens"],
            "cost_usd": None,
        },
        "note": (
            "Fixed-label filter ablation only. The fresh critic score artifact uses a new "
            "judge pass and is not this comparison. Historical judge labels are same-family "
            "LLM outputs, and both source runs were produced from dirty worktrees."
        ),
    }


def build_result(
    repo_root: Path,
    prepared_root: Path,
    mirrors_root: Path,
    split_path: Path,
    baseline_run_path: Path,
    audit_dir: Path,
    candidate_dir: Path,
) -> dict[str, Any]:
    current_head = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    current_tree = _git(repo_root, "rev-parse", "HEAD^{tree}").stdout.strip()
    status = _git(repo_root, "status", "--porcelain").stdout.splitlines()
    baseline_commit = DEFAULT_BASELINE
    baseline_ok = _commit_available(repo_root, baseline_commit)
    baseline_tree_result = _git(repo_root, "rev-parse", f"{baseline_commit}^{{tree}}")
    baseline_run = _load_json(baseline_run_path)
    prepared_bytes = (prepared_root / "prepared.json").read_bytes()
    prepared = json.loads(prepared_bytes)
    split = _load_json(split_path)
    manifest_trusted = _prepared_manifest_trusted(prepared_bytes, split)
    records = {item.get("case_id"): item for item in prepared.get("records", [])}
    roster_ids = list(split.get("dev", [])) + list(split.get("holdout", []))
    snapshots = []
    for case_id in roster_ids:
        if case_id in records:
            snapshots.append(
                _case_snapshot(
                    records[case_id],
                    prepared_root,
                    mirrors_root,
                    manifest_trusted=manifest_trusted,
                )
            )
        else:
            snapshots.append(
                {
                    "case_id": case_id,
                    "status": "not_ready",
                    "reasons": ["case_missing_from_prepared_manifest"],
                }
            )
    ready = sum(item["status"] == "ready" for item in snapshots)
    exposure = {case_id for case_id in roster_ids if (audit_dir / f"{case_id}.json").exists()}
    for item in snapshots:
        item["split"] = "dev" if item["case_id"] in split.get("dev", []) else "holdout"
        item["evaluation_role"] = (
            "regression_only_exposed"
            if item["case_id"] in exposure
            else ("not_yet_reviewed_locally")
        )

    audit_results = _replay_saved_evidence_gate(
        audit_dir,
        candidate_dir,
        records,
        prepared_root,
        manifest_trusted=manifest_trusted,
    )
    critic_replay = _fixed_label_critic_replay(
        repo_root / "reports/evaluation/reviewbench-baseline-v2-complete/candidates",
        repo_root / "reports/evaluation/reviewbench-critic-replay/candidates",
        repo_root / "reports/evaluation/reviewbench-baseline-score.details.json",
        repo_root / "reports/evaluation/reviewbench-baseline-score.json",
        repo_root / "reports/evaluation/reviewbench-critic-replay/run.json",
        repo_root / "reports/evaluation/reviewbench-critic-replay/audits",
    )
    usage_totals = Counter()
    usage_record_count = 0
    audit_count = 0
    for audit_path in audit_dir.glob("*.json"):
        try:
            audit = _load_json(audit_path)
        except (OSError, json.JSONDecodeError):
            continue
        audit_count += 1
        for usage_record in audit.get("usage_records", []):
            usage = usage_record.get("usage") if isinstance(usage_record, dict) else None
            if not isinstance(usage, dict):
                continue
            usage_record_count += 1
            for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(name)
                if type(value) is int:
                    usage_totals[name] += value
    score = _load_json(repo_root / "reports/evaluation/reviewbench-baseline-score.json")
    micro = score.get("micro", {}).get("overall", {})
    return {
        "schema_version": 1,
        "mode": "offline_preflight_and_shadow_replay",
        "network_or_provider_called": False,
        "source": {
            "baseline_ref": baseline_commit,
            "baseline_ref_available": baseline_ok,
            "baseline_tree": baseline_tree_result.stdout.strip()
            if baseline_tree_result.returncode == 0
            else None,
            "current_head": current_head,
            "current_tree": current_tree,
            "working_tree_clean": not status,
            "working_tree_status_lines": len(status),
            "benchmark_script_sha256": _sha256_bytes(Path(__file__).read_bytes()),
        },
        "benchmark_protocol": {
            "model_recorded": baseline_run.get("model"),
            "served_model_names": sorted(
                {
                    item.get("model")
                    for path in audit_dir.glob("*.json")
                    for item in [_load_json(path)]
                    for usage in item.get("usage_records", [])
                    if isinstance(usage, dict) and isinstance(usage.get("model"), str)
                }
            ),
            "baseline_quality_status": baseline_run.get("quality_status"),
            "baseline_working_tree_dirty": baseline_run.get("working_tree_dirty"),
            "baseline_success_records": sum(
                item.get("status") == "success" for item in baseline_run.get("records", [])
            ),
            "baseline_failed_records": sum(
                item.get("status") == "failed" for item in baseline_run.get("records", [])
            ),
            "per_case_run_records_reused": sum(
                bool(item.get("reused_from")) for item in baseline_run.get("records", [])
            ),
            "token_usage_source": "saved audit usage_records; excludes unrecorded attempts/judge",
            "audit_count": audit_count,
            "usage_record_count": usage_record_count,
            "known_prompt_tokens": usage_totals["prompt_tokens"],
            "known_completion_tokens": usage_totals["completion_tokens"],
            "known_total_tokens": usage_totals["total_tokens"],
            "cost_usd": None,
            "request_budget_protocol": "not fully recoverable from aggregate historical run",
        },
        "roster": {
            "dev_case_count": len(split.get("dev", [])),
            "holdout_case_count": len(split.get("holdout", [])),
            "local_snapshot_ready": ready,
            "local_snapshot_not_ready": len(snapshots) - ready,
            "holdout_status": split.get("holdout_status"),
            "test_status": split.get("test_status"),
            "prepared_manifest_sha256": _sha256_bytes(
                (prepared_root / "prepared.json").read_bytes()
            ),
            "prepared_manifest_matches_split": manifest_trusted,
            "split_manifest_sha256": _sha256_bytes(split_path.read_bytes()),
            "input_set_sha256": _canonical_sha256(
                [
                    {
                        "case_id": item["case_id"],
                        "metadata_sha256": item.get("metadata_sha256"),
                        "diff_sha256": item.get("diff_sha256"),
                        "base_sha": item.get("base_sha"),
                        "head_sha": item.get("head_sha"),
                    }
                    for item in snapshots
                ]
            )
            if manifest_trusted
            else None,
            "cases": snapshots,
        },
        "semantic_adjudication_protocol": {
            "schema_version": 1,
            "status": "schema_only_no_independent_labels",
            "record_fields": [
                "case_id",
                "arm",
                "finding_sha256",
                "blind_reviewer_id",
                "label",
                "matched_gold_ids",
                "reason_code",
                "evidence_refs",
                "rationale",
            ],
            "labels": ["tp", "fp", "unknown"],
            "finding_sha256_input": [
                "case_id",
                "arm",
                "file_path",
                "start_line",
                "end_line",
                "title",
                "reason",
                "evidence",
            ],
            "blind_review": (
                "Two reviewers label independently without arm/policy identity; a third "
                "adjudicates disagreements. Preserve unknown labels rather than forcing a vote."
            ),
            "duplicate_rule": (
                "Split multi-claim comments into atomic findings. Repeated predictions for the "
                "same case and root cause count once as TP; additional duplicates are FP."
            ),
            "gold_matching_rule": (
                "Only independently accepted root-cause matches cover a unique frozen gold ID; "
                "file/line overlap is a locator proxy and never a semantic TP by itself."
            ),
            "unknown_bounds": {
                "precision_lower": "TP/(TP+FP+unknown)",
                "precision_upper": "(TP+unknown)/(TP+FP+unknown)",
                "recall_lower": "known_unique_gold_TP/frozen_gold_count",
                "recall_upper": (
                    "min(frozen_gold_count, known_unique_gold_TP+unknown)/frozen_gold_count"
                ),
            },
            "metric_readiness": (
                "Publish point P/R/F1 only after all predictions in the scored arm are labeled "
                "and the frozen gold roster is complete; otherwise publish bounds and coverage."
            ),
        },
        "historical_score": {
            "source": "reports/evaluation/reviewbench-baseline-score.json",
            "match_method": "same-family LLM judge; not independent human adjudication",
            "matcher_model": score.get("eval_config", {}).get("matcher_model"),
            "classifier_model": score.get("eval_config", {}).get("classifier_model"),
            "candidate_count": micro.get("candidate_count"),
            "gold_records": micro.get("golden_tp_count"),
            "gold_linked_candidates": micro.get("matched_count"),
            "matched_tp": micro.get("matched_tp_count"),
            "matched_fp": micro.get("matched_count", 0) - micro.get("matched_tp_count", 0),
            "novel_tp": micro.get("unmatched_tp_count"),
            "grounded_precision": micro.get("grounded_precision"),
            "grounded_recall_unique_gold_coverage": micro.get("grounded_recall"),
            "augmented_precision": micro.get("augmented_precision"),
            "augmented_recall_with_novel_tp": micro.get("augmented_recall"),
            "unique_gold_covered": micro.get("golden_covered_count"),
            "score_semantics": (
                "grounded P=matched TP/gold-linked predictions; augmented P includes novel TP; "
                "grounded R is unique matched gold coverage; augmented R includes one novel TP"
            ),
        },
        "shadow_replay": audit_results,
        "critic_fixed_label_replay": critic_replay,
    }


def render_markdown(result: dict[str, Any]) -> str:
    src = result["source"]
    protocol = result["benchmark_protocol"]
    roster = result["roster"]
    hist = result["historical_score"]
    replay = result["shadow_replay"]
    totals = replay["component_replay"]["totals"]
    full = replay["full_chain_replay"]
    critic = result["critic_fixed_label_replay"]
    critic_pre = critic.get("baseline") or {}
    critic_post = critic.get("critic_fixed_labels") or {}
    cases = roster["cases"]
    dev_ready = sum(item["status"] == "ready" for item in cases if item["split"] == "dev")
    holdout_ready = sum(item["status"] == "ready" for item in cases if item["split"] == "holdout")
    lines = [
        "# 离线 Benchmark Preflight 与候选影子回放",
        "",
        "本报告由本地 dry-run 生成；没有调用模型、GitHub API 或网络。它不产生新模型质量结论。",
        "",
        "## 固定输入与来源",
        "",
        (
            f"- 基线源码：`{src['baseline_ref']}`，tree `{src['baseline_tree']}`；"
            f"本地存在：{src['baseline_ref_available']}。"
        ),
        (
            f"- 当前源码：`{src['current_head']}`，tree `{src['current_tree']}`；"
            f"工作区干净：{src['working_tree_clean']}。"
        ),
        (
            f"- roster：dev {roster['dev_case_count']} 个（本地快照 ready "
            f"{dev_ready}/"
            f"{roster['dev_case_count']}）；regression-only 旧 holdout "
            f"{roster['holdout_case_count']} 个（ready "
            f"{holdout_ready}/"
            f"{roster['holdout_case_count']}）。总计 ready "
            f"{roster['local_snapshot_ready']}，not_ready {roster['local_snapshot_not_ready']}。"
        ),
        (
            f"- prepared manifest 与 split hash 一致："
            f"{roster['prepared_manifest_matches_split']}；holdout 状态："
            f"`{roster['holdout_status']}`，test 状态：`{roster['test_status']}`。"
        ),
        f"- 输入集合 SHA-256：`{roster['input_set_sha256']}`；脚本 SHA-256："
        f"`{src['benchmark_script_sha256']}`。当前源码树字段是 HEAD tree，脚本另以内容哈希固定。",
        "- metadata 必须与受哈希 manifest 中的记录一致，diff 必须通过 manifest SHA。",
        "  缺少 base/head 对象只标 not_ready；脚本不 fetch、不补取输入。",
        "",
        "## 历史数据（不可作为新旧配对结论）",
        "",
        (
            f"- run记录模型 `{protocol['model_recorded']}`，audit请求名 "
            f"`{', '.join(protocol['served_model_names']) or '未记录'}`；成功 "
            f"{protocol['baseline_success_records']} / 失败 "
            f"{protocol['baseline_failed_records']}，复用 "
            f"{protocol['per_case_run_records_reused']} 条；质量状态 "
            f"`{protocol['baseline_quality_status']}`，源码dirty="
            f"{protocol['baseline_working_tree_dirty']}。"
        ),
        (
            f"- audit {protocol['audit_count']} 份、usage "
            f"{protocol['usage_record_count']} 条：prompt "
            f"{protocol['known_prompt_tokens']:,} + completion "
            f"{protocol['known_completion_tokens']:,} = "
            f"{protocol['known_total_tokens']:,} tokens。"
        ),
        "- cost_usd 未记录；失败尝试及 judge 成本/重试账目不完整，不能当作完整总成本。",
        (
            f"- 评分：{hist['candidate_count']} 预测、{hist['gold_records']} gold；"
            f"gold-linked {hist['gold_linked_candidates']}（{hist['matched_tp']} TP + "
            f"{hist['matched_fp']} FP），另有 {hist['novel_tp']} 条 novel TP。"
        ),
        (
            f"- grounded P={hist['grounded_precision']:.4%}；augmented P="
            f"{hist['augmented_precision']:.4%}。unique gold coverage="
            f"{hist['unique_gold_covered']}/{hist['gold_records']}，grounded R="
            f"{hist['grounded_recall_unique_gold_coverage']:.4%}；含 novel TP 后 "
            f"augmented R={hist['augmented_recall_with_novel_tp']:.4%}。"
        ),
        (
            f"- matcher `{hist['matcher_model']}` 与审查模型同家族，"
            "不是独立人审；14 是去重 gold 覆盖数，不是预测数。"
        ),
        "- 以上分母/precision 定义不同，不能压成一个不带口径的‘精确率’。",
        "  5-case b7b 使用 raw 文件/行位置匹配，是另一套 proxy，不能并入本表。",
        "",
        "## 零 API verifier 影子回放",
        "",
        (
            f"- 当前单行证据子门：输入 {totals.get('input_candidates', 0)} 条，"
            f"保留 {totals.get('retained', 0)}，拒绝 {totals.get('rejected', 0)}，"
            f"子门不可回放 {totals.get('not_replayable', 0)}。"
        ),
        (
            f"- 完整 criteria/witness 链：已知候选 {full['candidate_count']} 条，"
            f"状态 `{full['candidate_count_status']}`；不可回放已知候选 "
            f"{full['not_replayable']} 条；候选数未知 case "
            f"{len(full['unknown_candidate_cases'])} 个。"
        ),
        (
            "- 旧 audit 缺结构化 base/head 引用、原始 sent bundle、counter-evidence "
            "和未过滤候选；历史建议已受旧流程筛选，不能用它估新 verifier 召回。"
        ),
        "- 当前只核验单行证据是否出现在 frozen diff 的 added line；",
        "  它不判断发现是否语义正确。gold 不参与候选回放。",
        "",
        "## 固定标签 Critic 过滤消融（历史产物复算）",
        "",
        f"- 状态：`{critic['status']}`；协议 `{critic['protocol']}`；基线/过滤后候选数 "
        f"{(critic.get('baseline') or {}).get('candidate_count')} / "
        f"{(critic.get('critic_fixed_labels') or {}).get('candidate_count')}。",
        (
            "- 该对比按候选文件精确 join 到基线已有 judge 标签，再只筛掉 Critic 未保留项；"
            "不重新 judge。评分：grounded P/R "
            f"{(critic.get('baseline') or {}).get('grounded_precision')} / "
            f"{(critic.get('baseline') or {}).get('grounded_recall')} → "
            f"{(critic.get('critic_fixed_labels') or {}).get('grounded_precision')} / "
            f"{(critic.get('critic_fixed_labels') or {}).get('grounded_recall')}。"
        ),
        (
            f"- 固定标签计数：TP/FP/novel TP "
            f"{critic_pre.get('matched_tp')}/{critic_pre.get('matched_fp')}/"
            f"{critic_pre.get('novel_tp')} → "
            f"{critic_post.get('matched_tp')}/{critic_post.get('matched_fp')}/"
            f"{critic_post.get('novel_tp')}；unique gold "
            f"{critic_pre.get('unique_gold_covered')}/{critic_pre.get('gold_count')} → "
            f"{critic_post.get('unique_gold_covered')}/{critic_post.get('gold_count')}。"
        ),
        (
            f"- 口径展开：baseline gold-linked TP "
            f"{critic_pre.get('matched_tp')}/{critic_pre.get('gold_linked_count')}，"
            f"含 novel 的 P {critic_pre.get('matched_tp', 0) + critic_pre.get('novel_tp', 0)}/"
            f"{critic_pre.get('candidate_count')}；Critic 后 gold-linked TP "
            f"{critic_post.get('matched_tp')}/{critic_post.get('gold_linked_count')}，"
            f"含 novel 的 P {critic_post.get('matched_tp', 0) + critic_post.get('novel_tp', 0)}/"
            f"{critic_post.get('candidate_count')}。"
        ),
        (
            f"- Critic audit usage {critic['critic_usage']['usage_records']} 条，"
            f"{critic['critic_usage']['total_tokens']:,} tokens；成本未记录。"
        ),
        "- 这是同家族旧 judge 标签下的过滤器回放，不代表当前 verifier 效果或独立质量裁决；",
        "  fresh Critic score 是另一种重新判分口径。",
        "",
        "## 正式比较前仍需准备",
        "",
        "固定 PR base/head、reviewer 源码和全部预算/重试协议；"
        "baseline/current 配对且每次 attempt 计入；"
        "review 与 judge usage/cost 分开。先对逐 finding 盲标 TP/FP/unknown，分歧独立裁决；"
        "只有独立语义标签覆盖足够时才报告 micro P/R/F1。未知项报上下界，locator 匹配只单列。"
        "Fresh test 仍未授权。",
        "",
        "标签 JSONL 字段：`case_id, arm, finding_sha256, blind_reviewer_id, "
        "label(tp|fp|unknown), matched_gold_ids[], reason_code, evidence_refs[], rationale`。"
        "hash 覆盖 case/arm/文件行/title/reason/evidence；双人盲标，分歧第三人裁决。"
        "多 claim 拆成原子 finding；同 case 同根因重复项仅首条计 TP，其余计 FP。"
        "行位置重合不自动算 TP。unknown 保留并报上下界；"
        "预测完成标注且 gold roster 完整后才出点估计。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument(
        "--prepared-dir", type=Path, default=Path(__file__).parents[3] / "eval-data" / "prepared"
    )
    parser.add_argument(
        "--mirrors-dir", type=Path, default=Path(__file__).parents[3] / "eval-data" / "mirrors"
    )
    parser.add_argument(
        "--split", type=Path, default=Path(__file__).parents[2] / "datasets/dev/split.json"
    )
    parser.add_argument(
        "--baseline-run",
        type=Path,
        default=Path(__file__).parents[2]
        / "reports/evaluation/reviewbench-baseline-v2-complete/run.json",
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=Path(__file__).parents[2]
        / "reports/evaluation/reviewbench-baseline-v2-complete/audits",
    )
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        default=Path(__file__).parents[2]
        / "reports/evaluation/reviewbench-baseline-v2-complete/candidates",
    )
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--markdown-out", type=Path, required=True)
    args = parser.parse_args()

    result = build_result(
        args.repo_root.resolve(),
        args.prepared_dir.resolve(),
        args.mirrors_dir.resolve(),
        args.split.resolve(),
        args.baseline_run.resolve(),
        args.audit_dir.resolve(),
        args.candidate_dir.resolve(),
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.write_text(render_markdown(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "mode": result["mode"],
                "network_or_provider_called": result["network_or_provider_called"],
                "ready": result["roster"]["local_snapshot_ready"],
                "not_ready": result["roster"]["local_snapshot_not_ready"],
                "component_replay": result["shadow_replay"]["component_replay"]["totals"],
                "full_chain_replay": result["shadow_replay"]["full_chain_replay"],
                "critic_fixed_labels": result["critic_fixed_label_replay"]["status"],
                "json_out": str(args.json_out),
                "markdown_out": str(args.markdown_out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
