"""Offline, auditable strategy-candidate lifecycle.

Candidate generation is deterministic and development-only. Replay consumes fixed
prepared inputs; promotion requires independently supplied semantic adjudication,
coverage, and measured-cost evidence. This module never loads credentials or calls a
provider by itself.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from src.application.strategy_policy import (
    BASELINE_CONTENT_SHA256,
    BASELINE_POLICY,
    DEFAULT_PLAYBOOK_RECALL_K,
    PROMPT_TEMPLATES,
    LoadedPolicy,
    RuntimePolicy,
    load_active_policy,
)
from src.application.strategy_policy import (
    current_criteria_fingerprint as active_policy_criteria_fingerprint,
)
from src.evaluation.reviewbench import run_review
from src.evaluation.runtime import source_identity
from src.reviewer.provider import ReviewRequestBudget

SCHEMA_VERSION = 1
CASE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,180}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_FAILURE_TAGS = frozenset(
    {"missing_cross_file_evidence", "cross_file_task_budget_omitted"}
)


class ImprovementError(ValueError):
    """Input or lifecycle state is invalid; no policy change was applied."""


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    failure_tag: str
    run_artifact_sha256: str
    trace_sha256: str
    failure_id: str | None = None
    source: str
    adjudicator: str | None = None
    evidence_ref: str | None = None


def _assert_provider_budget(provider: object, expected_output: int, request_budget) -> None:
    configured = getattr(provider, "max_output_tokens", None)
    if configured is None:
        configured = getattr(provider, "_max_output_tokens", None)
    if configured != expected_output:
        raise ImprovementError("provider factory did not configure the required output-token cap")
    if getattr(provider, "_request_budget", None) is not request_budget:
        raise ImprovementError("provider factory did not attach the required shared request budget")


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def protocol_fingerprint(protocol: dict) -> str:
    """Fingerprint the complete declared arm configuration, excluding itself."""
    body = {key: value for key, value in protocol.items() if key != "protocol_fingerprint"}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def budget_fingerprint(protocol: dict) -> str:
    keys = (
        "max_output_tokens",
        "max_http_attempts_per_arm",
        "max_total_http_attempts",
        "max_input_utf8_bytes_per_arm",
        "timeout_seconds_per_arm",
        "retry_policy",
    )
    return hashlib.sha256(canonical_bytes({key: protocol.get(key) for key in keys})).hexdigest()


def current_criteria_fingerprint() -> str:
    from src.reviewer.criteria import CRITERIA_VERSION

    return hashlib.sha256(CRITERIA_VERSION.encode("utf-8")).hexdigest()


def _candidate_identity(policy: dict, provenance: dict) -> str:
    return hashlib.sha256(canonical_bytes({"policy": policy, "provenance": provenance})).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(raw, encoding="utf-8")
    if exclusive:
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            temporary.unlink(missing_ok=True)
            raise ImprovementError("holdout input set has already been consumed") from exc
        finally:
            temporary.unlink(missing_ok=True)
    else:
        temporary.replace(path)


def _append_event(store_dir: Path, value: dict) -> None:
    """Append a human-readable lifecycle record for the local single-writer store."""
    path = store_dir / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")


@contextmanager
def _store_lock(store_dir: Path):
    """Serialize local holdout and policy mutations on one filesystem."""
    store_dir.mkdir(parents=True, exist_ok=True)
    lock_path = store_dir / ".lifecycle.lock"
    with lock_path.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImprovementError(f"cannot read JSON artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise ImprovementError(f"expected JSON object: {path.name}")
    return value


def _contained(root: Path, path: Path) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ImprovementError("artifact path escapes its root")
    return resolved


def _default_split_registry_path() -> Path:
    return Path(__file__).resolve().parents[2] / "datasets" / "dev" / "split.json"


def _read_split_registry(path: Path | None = None) -> tuple[dict, str]:
    registry_path = (path or _default_split_registry_path()).resolve()
    registry = _read_json(registry_path)
    if registry.get("split_rule") != "by repository":
        raise ImprovementError("split registry must declare repository-level isolation")
    if not SHA256.fullmatch(str(registry.get("prepared_sha256", ""))):
        raise ImprovementError("split registry must pin the trusted prepared input manifest")
    for split_name in ("dev", "holdout", "test"):
        values = registry.get(split_name, [])
        if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
            raise ImprovementError("split registry contains invalid case ids")
        if len(values) != len(set(values)):
            raise ImprovementError("split registry contains duplicate case ids")
    dev = set(registry.get("dev", []))
    holdout = set(registry.get("holdout", []))
    test = set(registry.get("test", []))
    if dev & holdout or dev & test or holdout & test:
        raise ImprovementError("split registry case ids overlap")
    return registry, sha256_file(registry_path)


def build_input_manifest(
    prepared_path: Path,
    *,
    split: str,
    case_ids: list[str] | None = None,
    split_registry_path: Path | None = None,
) -> dict:
    """Make a no-label manifest from persisted prepared inputs."""
    if split not in {"dev", "holdout", "test"}:
        raise ImprovementError("split must be dev, holdout, or test")
    registry, registry_sha256 = _read_split_registry(split_registry_path)
    allowed_case_ids = set(registry.get(split, []))
    if not allowed_case_ids:
        raise ImprovementError(f"split registry has no {split} cases")
    prepared_path = prepared_path.resolve()
    prepared = _read_json(prepared_path)
    requested = set(case_ids or [])
    records = []
    for task in prepared.get("records", []):
        case_id = task.get("case_id")
        if requested and case_id not in requested:
            continue
        if case_id not in allowed_case_ids:
            continue
        if task.get("status") != "ready":
            continue
        if not isinstance(case_id, str) or not CASE_ID.fullmatch(case_id):
            raise ImprovementError("invalid prepared case id")
        input_dir = Path(task["input_dir"]).resolve()
        # Prepared records may point outside the prepared.json parent (the official
        # corpus stores inputs beside its index); only fixed diff/pr inputs are read.
        pr_path, diff_path = input_dir / "pr.json", input_dir / "diff.patch"
        metadata = _read_json(pr_path)
        if metadata != task.get("metadata"):
            raise ImprovementError(f"metadata mismatch for {case_id}")
        diff_sha = sha256_file(diff_path)
        if diff_sha != task.get("diff_sha256"):
            raise ImprovementError(f"diff hash mismatch for {case_id}")
        project_root = input_dir / "project"
        context_hashes = {}
        for relative in ("AGENTS.md", "docs/review-guide.md"):
            context_path = project_root / relative
            if context_path.is_file():
                _contained(project_root, context_path)
                context_hashes[relative] = sha256_file(context_path)
        records.append(
            {
                "case_id": case_id,
                "group": metadata.get("nwo"),
                "base": metadata.get("base"),
                "head": metadata.get("head"),
                "metadata_sha256": sha256_file(pr_path),
                "diff_sha256": diff_sha,
                "context_sha256": context_hashes,
                "input_dir": str(input_dir),
            }
        )
    if requested and requested != {x["case_id"] for x in records}:
        raise ImprovementError(
            "requested cases are absent, not ready, or outside the trusted split"
        )
    if not records:
        raise ImprovementError("manifest contains no ready fixed inputs")
    groups = [x["group"] for x in records]
    if any(not isinstance(group, str) or not group.strip() for group in groups):
        raise ImprovementError("prepared records require a non-empty repository identity")
    if any(
        not re.fullmatch(r"[0-9a-f]{40}", str(record.get(key, "")))
        for record in records
        for key in ("base", "head")
    ):
        raise ImprovementError("prepared records require fixed 40-character base/head SHAs")
    if len(groups) != len(set(groups)):
        raise ImprovementError("manifest must use at most one case per repository group")
    body = {
        "schema_version": SCHEMA_VERSION,
        "split": split,
        "split_registry_sha256": registry_sha256,
        "prepared_sha256": sha256_file(prepared_path),
        "prepared_path": str(prepared_path),
        "benchmark_commit": prepared.get("benchmark_commit"),
        "case_count": len(records),
        "records": records,
    }
    body["input_set_sha256"] = hashlib.sha256(canonical_bytes(body)).hexdigest()
    return body


def _validate_manifest(
    manifest: dict,
    *,
    required_split: str | None = None,
    split_registry_path: Path | None = None,
) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(
        manifest.get("records"), list
    ):
        raise ImprovementError("unsupported input manifest")
    if required_split and manifest.get("split") != required_split:
        raise ImprovementError(f"operation requires {required_split} inputs")
    registry, registry_sha256 = _read_split_registry(split_registry_path)
    split = manifest.get("split")
    if (
        split not in {"dev", "holdout", "test"}
        or manifest.get("split_registry_sha256") != registry_sha256
        or manifest.get("prepared_sha256") != registry.get("prepared_sha256")
        or any(r.get("case_id") not in set(registry.get(split, [])) for r in manifest["records"])
    ):
        raise ImprovementError("manifest case assignment does not match trusted split registry")
    ids = [r.get("case_id") for r in manifest["records"]]
    groups = [r.get("group") for r in manifest["records"]]
    if not ids or len(ids) != len(set(ids)) or len(groups) != len(set(groups)):
        raise ImprovementError("manifest case/repository groups are empty or duplicated")
    for record in manifest["records"]:
        if not CASE_ID.fullmatch(str(record.get("case_id", ""))):
            raise ImprovementError("invalid case id in manifest")
        for key in ("metadata_sha256", "diff_sha256"):
            if not SHA256.fullmatch(str(record.get(key, ""))):
                raise ImprovementError("invalid input hash in manifest")
    unsigned = {k: v for k, v in manifest.items() if k != "input_set_sha256"}
    expected = hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
    if manifest.get("input_set_sha256") != expected:
        raise ImprovementError("input manifest hash mismatch")
    prepared_path = manifest.get("prepared_path")
    if not isinstance(prepared_path, str) or not Path(prepared_path).is_file():
        raise ImprovementError("trusted prepared input manifest is unavailable")
    if sha256_file(Path(prepared_path)) != registry.get("prepared_sha256"):
        raise ImprovementError("prepared input manifest does not match trusted split registry")
    rebuilt = build_input_manifest(
        Path(prepared_path), split=split, case_ids=ids, split_registry_path=split_registry_path
    )
    if any(
        manifest.get(key) != rebuilt.get(key)
        for key in (
            "prepared_sha256",
            "benchmark_commit",
            "case_count",
            "records",
        )
    ):
        raise ImprovementError("manifest records do not match trusted prepared input records")


def _load_verified_trace(
    run_path: Path, run_record: dict, manifest: dict, manifest_record: dict
) -> tuple[dict, str]:
    relative = run_record.get("review_trace_path")
    linked_sha = run_record.get("review_trace_sha256")
    if not isinstance(relative, str) or not SHA256.fullmatch(str(linked_sha or "")):
        raise ImprovementError("run record has no valid review trace linkage")
    trace_path = _contained(run_path.parent, run_path.parent / relative)
    if not trace_path.is_file():
        raise ImprovementError("review trace sidecar is missing")
    file_sha = sha256_file(trace_path)
    if file_sha != linked_sha:
        raise ImprovementError("review trace sidecar hash does not match run linkage")
    trace = _read_json(trace_path)
    if trace.get("schema_version") != 1 or not SHA256.fullmatch(
        str(trace.get("trace_sha256", ""))
    ):
        raise ImprovementError("review trace schema/hash is invalid")
    unsigned_trace = {key: value for key, value in trace.items() if key != "trace_sha256"}
    if hashlib.sha256(canonical_bytes(unsigned_trace)).hexdigest() != trace["trace_sha256"]:
        raise ImprovementError("review trace content hash mismatch")
    identity = trace.get("identity", {})
    metadata, _, _ = _verify_case_input(manifest_record)
    expected_identity = {
        "benchmark_case_id": manifest_record["case_id"],
        "repository": metadata.get("nwo"),
        "pr_number": metadata.get("pr_number"),
        "base_sha": metadata.get("base"),
        "head_sha": metadata.get("head"),
        "prepared_metadata_sha256": manifest_record["metadata_sha256"],
        "prepared_diff_sha256": manifest_record["diff_sha256"],
        "input_set_sha256": manifest.get("input_set_sha256"),
    }
    if any(identity.get(key) != value for key, value in expected_identity.items()):
        raise ImprovementError("review trace identity does not match fixed prepared input")
    for key in ("criteria_sha256", "runtime_sha256", "prompt_sha256"):
        if not SHA256.fullmatch(str(trace.get(key, ""))):
            raise ImprovementError(f"review trace {key} is missing or invalid")
    policy = trace.get("policy", {})
    if not isinstance(policy, dict) or not SHA256.fullmatch(
        str(policy.get("effective_policy_sha256", ""))
    ):
        raise ImprovementError("review trace effective policy identity is invalid")
    if policy.get("grounded_missing_evidence_followup") is True:
        raise ImprovementError(
            "unsupported_source_strategy: missing-evidence follow-up traces cannot seed RSI"
        )
    run_identity = run_record.get("review_trace_identity", {})
    if run_identity and any(
        run_identity.get(key) != value
        for key, value in {
            "policy_id": policy.get("policy_id"),
            "effective_policy_sha256": policy.get("effective_policy_sha256"),
            "criteria_sha256": trace.get("criteria_sha256"),
            "runtime_sha256": trace.get("runtime_sha256"),
            "prompt_sha256": trace.get("prompt_sha256"),
        }.items()
    ):
        raise ImprovementError("review trace policy/runtime identity differs from run record")
    return trace, file_sha


def derive_trace_diagnoses(
    *, run_path: Path, manifest_path: Path, split_registry_path: Path | None = None
) -> dict:
    """Derive only host-recorded, allowlisted engineering omissions from saved traces."""
    run = _read_json(run_path)
    manifest = _read_json(manifest_path)
    _validate_manifest(manifest, required_split="dev", split_registry_path=split_registry_path)
    run_sha = sha256_file(run_path)
    records = {item.get("case_id"): item for item in run.get("records", []) if isinstance(item, dict)}
    diagnoses = []
    unavailable = []
    for manifest_record in manifest["records"]:
        record = records.get(manifest_record["case_id"])
        if not record or record.get("status") != "success":
            unavailable.append({"case_id": manifest_record["case_id"], "reason": "run_not_successful"})
            continue
        try:
            trace, trace_file_sha = _load_verified_trace(
                run_path, record, manifest, manifest_record
            )
        except ImprovementError as exc:
            unavailable.append({"case_id": manifest_record["case_id"], "reason": str(exc)})
            continue
        failures = trace.get("mechanical_failures", [])
        if not isinstance(failures, list):
            unavailable.append({"case_id": manifest_record["case_id"], "reason": "invalid_mechanical_failures"})
            continue
        for failure in failures:
            if not isinstance(failure, dict):
                continue
            if failure.get("failure_tag") != "cross_file_task_budget_omitted":
                continue
            task_id = failure.get("failure_id")
            hypothesis_id = failure.get("hypothesis_id")
            reason = failure.get("reason")
            source_refs = failure.get("valid_source_refs")
            if (
                not isinstance(task_id, str)
                or not task_id
                or not isinstance(hypothesis_id, str)
                or reason not in {"task_limit", "evidence_budget"}
                or not isinstance(source_refs, list)
                or not source_refs
                or any(not isinstance(ref, str) or ref.startswith("pr:") for ref in source_refs)
            ):
                continue
            intent = trace.get("intent", {})
            hypotheses = intent.get("hypotheses", []) if isinstance(intent, dict) else []
            hypothesis = next(
                (item for item in hypotheses if isinstance(item, dict) and item.get("id") == hypothesis_id),
                None,
            )
            omitted = intent.get("omitted_tasks", []) if isinstance(intent, dict) else []
            task = next(
                (item for item in omitted if isinstance(item, dict) and item.get("task_id") == task_id),
                None,
            ) if isinstance(omitted, list) else None
            if (
                not hypothesis
                or hypothesis.get("source_status") != "known"
                or not task
                or task.get("hypothesis_id") != hypothesis_id
                or task.get("reason") != reason
                or task.get("omission_reason") != failure.get("omission_reason")
                or task.get("request_kind") not in {"read", "search"}
                or task.get("status") not in {"evidence_omitted", "not_executed_budget"}
                or not set(source_refs).issubset(set(hypothesis.get("source_refs", [])))
                or source_refs != task.get("valid_source_refs")
            ):
                continue
            diagnoses.append(
                {
                    "case_id": manifest_record["case_id"],
                    "failure_tag": "cross_file_task_budget_omitted",
                    "run_artifact_sha256": run_sha,
                    "trace_sha256": trace_file_sha,
                    "failure_id": task_id,
                    "source": "trace_mechanical",
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "split": "dev",
        "run_artifact_sha256": run_sha,
        "input_set_sha256": manifest["input_set_sha256"],
        "diagnoses": diagnoses,
        "unavailable": unavailable,
        "status": "ready" if diagnoses else "blocked_no_mechanical_diagnosis",
    }


def generate_candidate(
    store_dir: Path,
    *,
    manifest_path: Path,
    run_path: Path,
    diagnoses_path: Path,
    split_registry_path: Path | None = None,
) -> dict:
    """Generate one fixed, allow-listed prompt candidate from dev failure tags."""
    manifest = _read_json(manifest_path)
    _validate_manifest(
        manifest, required_split="dev", split_registry_path=split_registry_path
    )
    run = _read_json(run_path)
    run_sha = sha256_file(run_path)
    diagnosis_data = _read_json(diagnoses_path)
    diagnoses = diagnosis_data.get("diagnoses")
    if diagnosis_data.get("split") != "dev" or not isinstance(diagnoses, list):
        raise ImprovementError("only explicitly development-scoped diagnoses are accepted")
    parsed = [Diagnosis.model_validate(d) for d in diagnoses]
    if not parsed or any(d.run_artifact_sha256 != run_sha for d in parsed):
        raise ImprovementError("diagnoses must bind to the exact run artifact")
    manifest_ids = {r["case_id"] for r in manifest["records"]}
    run_ids = {r.get("case_id") for r in run.get("records", [])}
    if any(d.case_id not in manifest_ids or d.case_id not in run_ids for d in parsed):
        raise ImprovementError("diagnosis case is outside fixed dev run")
    if any(d.failure_tag not in ALLOWED_FAILURE_TAGS for d in parsed):
        raise ImprovementError("unsupported failure tag; no free-form prompt generation")
    for record in manifest["records"]:
        if record["case_id"] in {d.case_id for d in parsed}:
            run_record = next(r for r in run["records"] if r.get("case_id") == record["case_id"])
            if run_record.get("status") != "success":
                raise ImprovementError("diagnosis must reference a successful review artifact")
            candidates_path = run_path.parent / "candidates" / f"{record['case_id']}.json"
            audits_path = run_path.parent / "audits" / f"{record['case_id']}.json"
            if not candidates_path.is_file() or not audits_path.is_file():
                raise ImprovementError(
                    "diagnosis run is missing its persisted candidate/audit artifacts"
                )
            _verify_case_input(record)
    manifest_by_case = {record["case_id"]: record for record in manifest["records"]}
    run_by_case = {
        record.get("case_id"): record
        for record in run.get("records", [])
        if isinstance(record, dict)
    }
    verified_traces = {}
    mechanically_derived = derive_trace_diagnoses(
        run_path=run_path,
        manifest_path=manifest_path,
        split_registry_path=split_registry_path,
    )
    mechanical_keys = {
        (item["case_id"], item["failure_tag"], item["failure_id"], item["trace_sha256"])
        for item in mechanically_derived["diagnoses"]
    }
    for diagnosis in parsed:
        run_record = run_by_case[diagnosis.case_id]
        trace, trace_file_sha = _load_verified_trace(
            run_path, run_record, manifest, manifest_by_case[diagnosis.case_id]
        )
        if diagnosis.trace_sha256 != trace_file_sha:
            raise ImprovementError("diagnosis does not bind to the exact persisted trace")
        if diagnosis.source == "trace_mechanical":
            if diagnosis.failure_tag != "cross_file_task_budget_omitted" or (
                diagnosis.case_id,
                diagnosis.failure_tag,
                diagnosis.failure_id,
                diagnosis.trace_sha256,
            ) not in mechanical_keys:
                raise ImprovementError("mechanical diagnosis is not derivable from the host trace")
        elif diagnosis.source == "human_adjudication":
            if (
                diagnosis.failure_tag != "missing_cross_file_evidence"
                or not diagnosis.adjudicator
                or not diagnosis.evidence_ref
            ):
                raise ImprovementError("semantic diagnosis requires a named human and evidence ref")
            evidence_refs = trace.get("evidence_refs", [])
            evidence = next(
                (item for item in evidence_refs if item.get("evidence_ref_id") == diagnosis.evidence_ref),
                None,
            ) if isinstance(evidence_refs, list) else None
            if not evidence or evidence.get("repo_sha") != trace.get("identity", {}).get("head_sha"):
                raise ImprovementError("human semantic diagnosis ref is not available at frozen head SHA")
        else:
            raise ImprovementError("unsupported diagnosis source")
        verified_traces[diagnosis.case_id] = trace_file_sha
    failure_tags = {diagnosis.failure_tag for diagnosis in parsed}
    template_id = (
        "bounded_cross_file_evidence_v1"
        if "cross_file_task_budget_omitted" in failure_tags
        else "cross_file_contract_check_v1"
    )
    policy = RuntimePolicy(prompt_addendum=PROMPT_TEMPLATES[template_id])
    active = load_active_policy(store_dir)
    if policy == active.policy:
        raise ImprovementError("candidate is identical to active policy")
    provenance = {
        "run_sha256": run_sha,
        "diagnoses_sha256": sha256_file(diagnoses_path),
        "manifest_sha256": hashlib.sha256(canonical_bytes(manifest)).hexdigest(),
        "input_set_sha256": manifest["input_set_sha256"],
        "case_ids": sorted({d.case_id for d in parsed}),
        "groups": sorted(
            {r["group"] for r in manifest["records"] if r["case_id"] in {d.case_id for d in parsed}}
        ),
        "failure_tags": sorted({d.failure_tag for d in parsed}),
        "review_trace_sha256": verified_traces,
        "diagnosis_sources": sorted({d.source for d in parsed}),
        "review_artifacts": {
            d.case_id: {
                "candidate_sha256": sha256_file(
                    run_path.parent / "candidates" / f"{d.case_id}.json"
                ),
                "audit_sha256": sha256_file(run_path.parent / "audits" / f"{d.case_id}.json"),
            }
            for d in parsed
        },
        "source_runtime_identity": {
            k: run.get(k)
            for k in ("reviewer_commit", "reviewer_source_sha256", "model")
            if k in run
        },
    }
    record = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": _candidate_identity(policy.model_dump(), provenance),
        "status": "candidate",
        "policy": policy.model_dump(),
        "provenance": provenance,
        "created_at_epoch": int(time.time()),
    }
    path = store_dir / "candidates" / f"{record['candidate_id']}.json"
    if path.exists():
        existing = _read_json(path)
        if existing.get("candidate_id") != record["candidate_id"] or existing.get("status") != "candidate":
            raise ImprovementError("candidate id already exists with different lifecycle state")
        return existing
    _write_json(path, record, exclusive=True)
    return record


def _verify_case_input(record: dict) -> tuple[dict, str, Path]:
    input_dir = Path(record["input_dir"]).resolve()
    metadata_path, diff_path = input_dir / "pr.json", input_dir / "diff.patch"
    if (
        sha256_file(metadata_path) != record["metadata_sha256"]
        or sha256_file(diff_path) != record["diff_sha256"]
    ):
        raise ImprovementError("fixed input changed after manifest creation")
    project_root = input_dir / "project"
    expected_context = record.get("context_sha256", {})
    if not isinstance(expected_context, dict):
        raise ImprovementError("invalid frozen context manifest")
    for relative, expected in expected_context.items():
        context_path = _contained(project_root, project_root / relative)
        if not context_path.is_file() or sha256_file(context_path) != expected:
            raise ImprovementError("frozen repository guidance changed after manifest creation")
    return _read_json(metadata_path), diff_path.read_text(encoding="utf-8"), input_dir


def _scope_receipt_path(store_dir: Path, kind: str, value: str) -> Path:
    digest = hashlib.sha256(f"{kind}:{value}".encode("utf-8")).hexdigest()
    return store_dir / "consumed-holdouts" / f"scope-{digest}.json"


def _consume_evaluation_scopes(
    store_dir: Path, manifest: dict, candidate_id: str, attested_by: str
) -> None:
    """Consume every case and repo scope before any provider is constructed."""
    with _store_lock(store_dir):
        for record in manifest["records"]:
            for kind, value in (("case", record["case_id"]), ("group", record["group"])):
                _write_json(
                    _scope_receipt_path(store_dir, kind, value),
                    {
                        "schema_version": SCHEMA_VERSION,
                        "scope_kind": kind,
                        "scope_value": value,
                        "input_set_sha256": manifest["input_set_sha256"],
                        "candidate_id": candidate_id,
                        "attested_by": attested_by,
                        "consumed_at_epoch": int(time.time()),
                        "status": "consumed_before_replay",
                    },
                    exclusive=True,
                )
        _write_json(
            store_dir / "consumed-holdouts" / f"{manifest['input_set_sha256']}.json",
            {
                "schema_version": SCHEMA_VERSION,
                "input_set_sha256": manifest["input_set_sha256"],
                "candidate_id": candidate_id,
                "attested_by": attested_by,
                "consumed_at_epoch": int(time.time()),
                "status": "consumed_before_replay",
            },
            exclusive=True,
        )


def replay_candidate(
    store_dir: Path,
    *,
    candidate_id: str,
    manifest_path: Path,
    provider_factory: Callable[..., object],
    protocol: dict,
    provider_mode: str,
    holdout_attestation: dict | None = None,
    split_registry_path: Path | None = None,
) -> dict:
    """Paired replay. Provider construction happens only after holdout is consumed."""
    if not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ImprovementError("invalid candidate id")
    candidate_path = _contained(store_dir, store_dir / "candidates" / f"{candidate_id}.json")
    candidate = _read_json(candidate_path)
    if candidate.get("candidate_id") != candidate_id or candidate.get("status") != "candidate":
        raise ImprovementError("candidate record invalid")
    if candidate_id != _candidate_identity(
        candidate.get("policy", {}), candidate.get("provenance", {})
    ):
        raise ImprovementError("candidate id does not match canonical policy/provenance")
    if provider_mode not in {"fixture", "live"}:
        raise ImprovementError("provider_mode must be fixture or live")
    policy = RuntimePolicy.model_validate(candidate.get("policy"))
    if policy.playbook_recall_k != DEFAULT_PLAYBOOK_RECALL_K:
        raise ImprovementError("unsupported_policy_change: retrieval k cannot be replayed")
    manifest = _read_json(manifest_path)
    _validate_manifest(manifest, split_registry_path=split_registry_path)
    if manifest["split"] not in {"dev", "holdout", "test"}:
        raise ImprovementError("unsupported split")
    if provider_mode == "live" and len(manifest["records"]) != 1:
        raise ImprovementError("live pilot is limited to one fixed PR per paired experiment")
    if protocol.get("protocol_fingerprint") != protocol_fingerprint(protocol):
        raise ImprovementError("protocol fingerprint does not match declared configuration")
    if protocol.get("budget_fingerprint") != budget_fingerprint(protocol):
        raise ImprovementError("budget fingerprint does not match enforced request limits")
    if protocol.get("criteria_fingerprint") != current_criteria_fingerprint():
        raise ImprovementError("criteria fingerprint does not match current review criteria")
    if (
        protocol.get("max_output_tokens") != 4096
        or protocol.get("max_http_attempts_per_arm") != 2
        or protocol.get("max_total_http_attempts") != 4
        or protocol.get("max_input_utf8_bytes_per_arm") != 12000
        or not isinstance(protocol.get("timeout_seconds_per_arm"), (int, float))
        or not 1 <= protocol["timeout_seconds_per_arm"] <= 180
    ):
        raise ImprovementError(
            "replay requires explicit 12k-byte/4096-output/two-attempt arm budgets"
        )
    runtime_before = source_identity()
    if protocol.get("runtime_fingerprint") != runtime_before["reviewer_source_sha256"]:
        raise ImprovementError("runtime fingerprint does not match current source tree")
    if manifest["split"] in {"holdout", "test"}:
        registry, _ = _read_split_registry(split_registry_path)
        if provider_mode == "live" and manifest["split"] == "holdout":
            raise ImprovementError("the configured holdout is regression-only, not promotable")
        if provider_mode == "live" and (
            manifest["split"] != "test" or registry.get("test_status") != "fresh_authorized"
        ):
            raise ImprovementError("live promotion requires an explicitly authorized fresh test split")
        att = holdout_attestation or {}
        if (
            att.get("previously_exposed_to_candidate_design") is not False
            or att.get("split_by_repository") is not True
            or not att.get("attested_by")
        ):
            raise ImprovementError(
                "holdout requires explicit non-exposure and repository-split attestation"
            )
        candidate_cases = set(candidate["provenance"].get("case_ids", []))
        candidate_groups = set(candidate["provenance"].get("groups", []))
        eval_cases = {r["case_id"] for r in manifest["records"]}
        eval_groups = {r["group"] for r in manifest["records"]}
        if candidate_cases & eval_cases or candidate_groups & eval_groups:
            raise ImprovementError("candidate development data overlaps holdout")
        _consume_evaluation_scopes(
            store_dir, manifest, candidate_id, str(att["attested_by"])
        )
    # The exact same protocol identity is recorded for both arms; no CLI path here
    # constructs a live provider or reads settings/.env.
    run_id = hashlib.sha256(
        canonical_bytes(
            {
                "candidate": candidate_id,
                "manifest": manifest["input_set_sha256"],
                "protocol": protocol,
                "time": time.time_ns(),
            }
        )
    ).hexdigest()
    result = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": run_id,
        "candidate_id": candidate_id,
        "candidate_file_sha256": sha256_file(candidate_path),
        "policy_sha256": hashlib.sha256(canonical_bytes(policy.model_dump())).hexdigest(),
        "split": manifest["split"],
        "provider_mode": provider_mode,
        "input_set_sha256": manifest["input_set_sha256"],
        "protocol": protocol,
        "runtime_identity": runtime_before,
        "status": "running",
        "quality_status": "unadjudicated",
        "arms": {"baseline": [], "candidate": []},
    }
    output_dir = store_dir / "evaluations" / run_id
    _write_json(output_dir / "input_manifest.json", manifest, exclusive=True)
    _write_json(output_dir / "replay.json", result, exclusive=True)
    for item in manifest["records"]:
        try:
            metadata, diff, project_root = _verify_case_input(item)
            for arm, arm_policy in (("baseline", BASELINE_POLICY), ("candidate", policy)):
                request_budget = ReviewRequestBudget(
                    max_input_token_upper_bound=12000,
                    max_http_attempts=2,
                    total_timeout_seconds=float(protocol["timeout_seconds_per_arm"]),
                )
                provider = provider_factory(
                    arm,
                    item["case_id"],
                    arm_policy,
                    request_budget=request_budget,
                    max_output_tokens=4096,
                )
                with closing(provider):
                    _assert_provider_budget(provider, 4096, request_budget)
                    findings, audit = run_review(
                        metadata,
                        diff,
                        provider,
                        model=str(protocol.get("model", "fake-provider")),
                        evidence=bool(protocol.get("evidence", True)),
                        critic=bool(protocol.get("critic", False)),
                        context_pack=bool(protocol.get("context_pack", False)),
                        two_stage=bool(protocol.get("two_stage", False)),
                        verify_signals=bool(protocol.get("verify_signals", False)),
                        follow_changes=bool(protocol.get("follow_changes", False)),
                        project_root=project_root,
                        prompt_addendum=arm_policy.prompt_addendum,
                        patch_and_repository_tokens=int(
                            protocol.get("patch_and_repository_tokens", 12000)
                        ),
                        repository_evidence_tokens=int(
                            protocol.get("repository_evidence_tokens", 6000)
                        ),
                        strategy_policy_id=("baseline" if arm == "baseline" else candidate_id),
                        strategy_policy_status=("baseline" if arm == "baseline" else "candidate"),
                        strategy_policy_content_sha256=hashlib.sha256(
                            canonical_bytes(arm_policy.model_dump())
                        ).hexdigest(),
                    )
                arm_record = {
                    "case_id": item["case_id"],
                    "status": "success",
                    "findings_sha256": hashlib.sha256(canonical_bytes(findings)).hexdigest(),
                    "audit_sha256": hashlib.sha256(canonical_bytes(audit)).hexdigest(),
                    "request_count": audit["request_count"],
                    "http_attempts": getattr(provider, "http_attempts", None),
                    "request_budget": request_budget.snapshot(),
                    "token_usage": audit["token_usage"],
                    "cost_usd": audit["cost_usd"],
                    "input_coverage": audit["input_coverage"],
                    "prompt_sha256": audit["strategy"]["prompt_sha256"],
                }
                execution = {
                    "http_attempts": getattr(provider, "http_attempts", None),
                    "request_budget": request_budget.snapshot(),
                }
                detail_path = output_dir / arm / f"{item['case_id']}.json"
                _write_json(
                    detail_path,
                    {
                        "case_id": item["case_id"],
                        "arm": arm,
                        "findings": findings,
                        "audit": audit,
                        "execution": execution,
                    },
                )
                arm_record["detail_sha256"] = sha256_file(detail_path)
                result["arms"][arm].append(arm_record)
        except Exception as exc:
            result["arms"]["baseline"].append(
                {"case_id": item["case_id"], "status": "failed", "error": type(exc).__name__}
            )
            result["status"] = "failed_partial"
            _write_json(output_dir / "replay.json", result)
            return result
        _write_json(output_dir / "replay.json", result)
    result["status"] = "complete"
    runtime_after = source_identity()
    if runtime_after["reviewer_source_sha256"] != runtime_before["reviewer_source_sha256"]:
        result["status"] = "failed_runtime_changed_during_replay"
    _write_json(output_dir / "replay.json", result)
    result["replay_sha256"] = sha256_file(output_dir / "replay.json")
    return result


def evaluate_gate(
    store_dir: Path,
    *,
    evaluation_id: str,
    adjudication: dict,
    quality: dict,
    coverage: dict,
    costs: dict,
    allow_fixture: bool = False,
    minimum_precision: float = 0.8,
    split_registry_path: Path | None = None,
) -> dict:
    """Fail-closed paired gate; all semantic and resource metrics are external evidence."""
    if not re.fullmatch(r"[0-9a-f]{64}", evaluation_id):
        raise ImprovementError("invalid evaluation id")
    replay_path = _contained(store_dir, store_dir / "evaluations" / evaluation_id / "replay.json")
    replay = _read_json(replay_path)
    digest = sha256_file(replay_path)
    input_manifest_path = _contained(replay_path.parent, replay_path.parent / "input_manifest.json")
    input_manifest = _read_json(input_manifest_path)
    try:
        _validate_manifest(input_manifest, split_registry_path=split_registry_path)
    except ImprovementError:
        input_manifest = {}
    reasons: list[str] = []
    protocol = replay.get("protocol", {})
    protocol_fingerprint = protocol.get("protocol_fingerprint")
    evaluation_identity = {
        "evaluation_id": evaluation_id,
        "replay_sha256": digest,
        "input_set_sha256": replay.get("input_set_sha256"),
        "protocol_fingerprint": protocol_fingerprint,
    }

    def bound_to_replay(report: dict, label: str) -> bool:
        mismatch = [key for key, value in evaluation_identity.items() if report.get(key) != value]
        if mismatch:
            reasons.append(f"{label}_identity_mismatch:" + ",".join(mismatch))
            return False
        return True

    if replay.get("status") != "complete" or replay.get("split") not in {"holdout", "test"}:
        reasons.append("replay_not_complete_independent_split")
    if replay.get("provider_mode") == "live" and replay.get("split") != "test":
        reasons.append("live_replay_is_not_on_fresh_test_split")
    if replay.get("provider_mode") == "live":
        registry, _ = _read_split_registry(split_registry_path)
        if registry.get("test_status") != "fresh_authorized":
            reasons.append("fresh_test_split_not_authorized")
    if replay.get("provider_mode") != "live" and not allow_fixture:
        reasons.append("fixture_provider_cannot_pass_production_gate")
    if not isinstance(protocol_fingerprint, str) or not protocol_fingerprint:
        reasons.append("protocol_fingerprint_missing")
    for field in (
        "model",
        "budget_fingerprint",
        "criteria_fingerprint",
        "runtime_fingerprint",
        "retry_policy",
    ):
        if not protocol.get(field):
            reasons.append(f"protocol_{field}_missing")
    if replay.get("runtime_identity", {}).get("reviewer_source_sha256") != protocol.get(
        "runtime_fingerprint"
    ):
        reasons.append("runtime_fingerprint_mismatch")
    if not input_manifest or input_manifest.get("input_set_sha256") != replay.get(
        "input_set_sha256"
    ):
        reasons.append("replay_input_manifest_missing_or_mismatched")
    receipt_path = store_dir / "consumed-holdouts" / f"{replay.get('input_set_sha256')}.json"
    if not receipt_path.is_file():
        reasons.append("holdout_not_consumed")
    elif input_manifest:
        for record in input_manifest.get("records", []):
            if any(
                not _scope_receipt_path(store_dir, kind, value).is_file()
                for kind, value in (("case", record.get("case_id")), ("group", record.get("group")))
            ):
                reasons.append("holdout_scope_not_consumed")
                break
    bound_to_replay(adjudication, "adjudication")
    if (
        adjudication.get("method") != "independent_semantic"
        or adjudication.get("raw_line_matching_proxy") is not False
    ):
        reasons.append("semantic_adjudication_not_independent")
    if (
        adjudication.get("complete") is not True
        or adjudication.get("unadjudicated", 1) != 0
        or not adjudication.get("adjudicator")
        or not adjudication.get("evidence_ref")
    ):
        reasons.append("adjudication_incomplete")
    bound_to_replay(quality, "quality")
    bound_to_replay(coverage, "coverage")
    bound_to_replay(costs, "costs")
    if (
        quality.get("source_adjudication_sha256")
        != hashlib.sha256(canonical_bytes(adjudication)).hexdigest()
    ):
        reasons.append("quality_not_bound_to_adjudication")
    expected_cases = (
        {r["case_id"] for r in input_manifest.get("records", [])}
        if input_manifest
        else set()
    )
    if not expected_cases:
        reasons.append("replay_input_case_set_unknown")
    for arm in ("baseline", "candidate"):
        arm_cases = replay.get("arms", {}).get(arm, [])
        arm_case_ids = [item.get("case_id") for item in arm_cases if isinstance(item, dict)]
        if len(arm_case_ids) != len(set(arm_case_ids)):
            reasons.append(f"{arm}_replay_duplicate_case")
        if set(arm_case_ids) != expected_cases:
            reasons.append(f"{arm}_replay_case_set_mismatch")
        if len(arm_cases) != len(expected_cases) or any(
            r.get("status") != "success" for r in arm_cases
        ):
            reasons.append(f"{arm}_replay_incomplete")
        for item in arm_cases:
            attempts = item.get("http_attempts")
            budget = item.get("request_budget", {})
            if (
                type(attempts) is not int
                or not 0 <= attempts <= 2
                or budget.get("request_http_attempts") != attempts
                or budget.get("request_http_attempt_cap") != 2
                or budget.get("request_input_upper_bound_cap_utf8_bytes") != 12000
            ):
                reasons.append(f"{arm}_request_budget_unverified")
            try:
                detail_path = _contained(
                    replay_path.parent,
                    replay_path.parent / arm / f"{item['case_id']}.json",
                )
                detail = _read_json(detail_path)
                audit = detail.get("audit", {})
                if (
                    detail.get("case_id") != item["case_id"]
                    or detail.get("arm") != arm
                    or sha256_file(detail_path) != item.get("detail_sha256")
                    or hashlib.sha256(canonical_bytes(detail.get("findings"))).hexdigest()
                    != item.get("findings_sha256")
                    or hashlib.sha256(canonical_bytes(audit)).hexdigest()
                    != item.get("audit_sha256")
                    or detail.get("execution", {}).get("http_attempts") != attempts
                    or detail.get("execution", {}).get("request_budget") != budget
                    or audit.get("request_count") != item.get("request_count")
                    or audit.get("token_usage") != item.get("token_usage")
                    or audit.get("cost_usd") != item.get("cost_usd")
                ):
                    reasons.append(f"{arm}_persisted_replay_artifact_mismatch")
            except (ImprovementError, OSError, KeyError, TypeError):
                reasons.append(f"{arm}_persisted_replay_artifact_missing")
    total_attempts = sum(
        item.get("http_attempts", 0)
        for arm in ("baseline", "candidate")
        for item in replay.get("arms", {}).get(arm, [])
    )
    if total_attempts > 4:
        reasons.append("experiment_request_budget_exceeded")

    derived: dict[str, dict[str, float | int]] = {}
    for arm in ("baseline", "candidate"):
        metrics = quality.get("arms", {}).get(arm, {})
        counts = [metrics.get(k) for k in ("tp", "fp", "fn")]
        if any(type(x) is not int or x < 0 for x in counts):
            reasons.append(f"{arm}_adjudicated_counts_unknown")
        else:
            tp, fp, fn = counts
            precision = tp / (tp + fp) if tp + fp else None
            recall = tp / (tp + fn) if tp + fn else None
            f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
            if None in (precision, recall, f1):
                reasons.append(f"{arm}_quality_denominator_unknown")
            else:
                derived[arm] = {"precision": precision, "recall": recall, "f1": f1}
        if metrics.get("failed_cases") != 0:
            reasons.append(f"{arm}_failed_cases")
    if all(arm in derived for arm in ("baseline", "candidate")):
        bp, br, bf = (derived["baseline"][k] for k in ("precision", "recall", "f1"))
        cp, cr, cf = (derived["candidate"][k] for k in ("precision", "recall", "f1"))
        if cp < minimum_precision:
            reasons.append("candidate_precision_below_threshold")
        if cr < br:
            reasons.append("candidate_recall_regressed")
        if cf <= bf:
            reasons.append("candidate_f1_not_improved")
    if coverage.get("method") != "measured_input_coverage" or coverage.get("evidence_ref") is None:
        reasons.append("coverage_not_measured")
    for arm in ("baseline", "candidate"):
        entry = coverage.get(arm, {})
        rows = entry.get("cases")
        n, d = entry.get("covered"), entry.get("total")
        if type(n) is not int or type(d) is not int or d <= 0 or not 0 <= n <= d:
            reasons.append(f"{arm}_coverage_unknown")
        if (
            not isinstance(rows, list)
            or {row.get("case_id") for row in rows if isinstance(row, dict)} != expected_cases
        ):
            reasons.append(f"{arm}_coverage_case_breakdown_missing")
        elif all(type(row.get(k)) is int for row in rows for k in ("covered", "total")):
            row_covered = sum(row["covered"] for row in rows)
            row_total = sum(row["total"] for row in rows)
            if row_covered != n or row_total != d:
                reasons.append(f"{arm}_coverage_total_mismatch")
            if arm in quality.get("arms", {}) and row_total != quality["arms"][arm].get(
                "tp", 0
            ) + quality["arms"][arm].get("fn", 0):
                reasons.append(f"{arm}_coverage_gold_denominator_mismatch")
    if all(
        type(coverage.get(a, {}).get(k)) is int
        for a in ("baseline", "candidate")
        for k in ("covered", "total")
    ):
        b, c = coverage["baseline"], coverage["candidate"]
        if (
            b["total"] <= 0
            or c["total"] <= 0
            or c["covered"] / c["total"] < b["covered"] / b["total"]
        ):
            reasons.append("candidate_coverage_regressed")
    values = [costs.get("arms", {}).get(a, {}).get("cost_usd") for a in ("baseline", "candidate")]
    if (
        costs.get("source") != "provider_measured"
        or not costs.get("evidence_ref")
        or any(not isinstance(x, (float, int)) or not math.isfinite(x) or x < 0 for x in values)
    ):
        reasons.append("measured_cost_unknown")
    elif values[1] > values[0]:
        reasons.append("candidate_cost_increased")
    if costs.get("same_model_budget_retry_policy") is not True or costs.get(
        "pricing_basis"
    ) not in {"provider_invoice", "provider_reported_unit_rates"}:
        reasons.append("cost_protocol_not_comparable")
    if (
        costs.get("model") != protocol.get("model")
        or costs.get("budget_fingerprint") != protocol.get("budget_fingerprint")
        or costs.get("retry_policy") != protocol.get("retry_policy")
    ):
        reasons.append("cost_protocol_identity_mismatch")
    for arm in ("baseline", "candidate"):
        evidence = costs.get("arms", {}).get(arm, {})
        actual_records = replay.get("arms", {}).get(arm, [])
        expected_prompt = sum(
            (r.get("token_usage") or {}).get("prompt_tokens", 0) for r in actual_records
        )
        expected_completion = sum(
            (r.get("token_usage") or {}).get("completion_tokens", 0) for r in actual_records
        )
        expected_attempts = sum(r.get("http_attempts", 0) for r in actual_records)
        if evidence.get("request_count") != expected_attempts:
            reasons.append(f"{arm}_cost_request_count_mismatch")
        if (
            any(r.get("token_usage") is None for r in actual_records)
            or evidence.get("prompt_tokens") != expected_prompt
            or evidence.get("completion_tokens") != expected_completion
        ):
            reasons.append(f"{arm}_cost_usage_unverified")
        if (
            not isinstance(evidence.get("cost_usd"), (int, float))
            or not math.isfinite(evidence["cost_usd"])
            or evidence["cost_usd"] < 0
        ):
            reasons.append(f"{arm}_cost_usd_invalid")
    gate = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": evaluation_id,
        "replay_sha256": digest,
        "input_set_sha256": replay.get("input_set_sha256"),
        "candidate_id": replay.get("candidate_id"),
        "candidate_file_sha256": replay.get("candidate_file_sha256"),
        "policy_sha256": replay.get("policy_sha256"),
        "decision": "reject" if reasons else "eligible_for_human_promotion",
        "reasons": sorted(set(reasons)),
        "derived_quality": derived,
        "evidence_sha256": {
            "adjudication": hashlib.sha256(canonical_bytes(adjudication)).hexdigest(),
            "quality": hashlib.sha256(canonical_bytes(quality)).hexdigest(),
            "coverage": hashlib.sha256(canonical_bytes(coverage)).hexdigest(),
            "costs": hashlib.sha256(canonical_bytes(costs)).hexdigest(),
        },
    }
    _write_json(store_dir / "evaluations" / evaluation_id / "gate.json", gate)
    return gate


def _promote_candidate_unlocked(
    store_dir: Path, candidate_id: str, evaluation_id: str, *, allow_fixture: bool = False
) -> str:
    """Explicit human CLI action after a successful holdout gate."""
    if not re.fullmatch(r"[0-9a-f]{64}", candidate_id) or not re.fullmatch(
        r"[0-9a-f]{64}", evaluation_id
    ):
        raise ImprovementError("invalid candidate/evaluation id")
    candidate_path = _contained(store_dir, store_dir / "candidates" / f"{candidate_id}.json")
    candidate = _read_json(candidate_path)
    if candidate_id != _candidate_identity(
        candidate.get("policy", {}), candidate.get("provenance", {})
    ):
        raise ImprovementError("candidate id does not match canonical policy/provenance")
    gate_path = _contained(store_dir, store_dir / "evaluations" / evaluation_id / "gate.json")
    gate = _read_json(gate_path)
    if (
        gate.get("decision") != "eligible_for_human_promotion"
        or gate.get("candidate_id") != candidate_id
    ):
        raise ImprovementError("candidate has no matching passing gate")
    replay_path = _contained(store_dir, store_dir / "evaluations" / evaluation_id / "replay.json")
    replay = _read_json(replay_path)
    if (
        replay.get("evaluation_id") != evaluation_id
        or replay.get("candidate_id") != candidate_id
        or replay.get("split") not in {"holdout", "test"}
        or gate.get("evaluation_id") != evaluation_id
        or gate.get("replay_sha256") != sha256_file(replay_path)
        or gate.get("candidate_file_sha256") != sha256_file(candidate_path)
        or gate.get("candidate_file_sha256") != replay.get("candidate_file_sha256")
        or gate.get("policy_sha256")
        != hashlib.sha256(canonical_bytes(candidate["policy"])).hexdigest()
        or gate.get("policy_sha256") != replay.get("policy_sha256")
        or gate.get("input_set_sha256") != replay.get("input_set_sha256")
    ):
        raise ImprovementError(
            "candidate, replay, gate, or input identity changed after evaluation"
        )
    if replay.get("provider_mode") != "live" and not allow_fixture:
        raise ImprovementError(
            "fixture replay is regression-only and cannot promote production policy"
        )
    if replay.get("provider_mode") == "live":
        manifest_path = _contained(
            store_dir, store_dir / "evaluations" / evaluation_id / "input_manifest.json"
        )
        manifest = _read_json(manifest_path)
        _validate_manifest(manifest)
        registry, _ = _read_split_registry()
        if manifest.get("split") != "test" or registry.get("test_status") != "fresh_authorized":
            raise ImprovementError("production promotion requires an authorized fresh test split")
        for record in manifest["records"]:
            for kind, value in (("case", record["case_id"]), ("group", record["group"])):
                scope_receipt = _read_json(_scope_receipt_path(store_dir, kind, value))
                if (
                    scope_receipt.get("input_set_sha256") != replay.get("input_set_sha256")
                    or scope_receipt.get("candidate_id") != candidate_id
                ):
                    raise ImprovementError("matching case/repository consumption receipt is missing")
    receipt_path = _contained(
        store_dir,
        store_dir / "consumed-holdouts" / f"{replay.get('input_set_sha256')}.json",
    )
    receipt = _read_json(receipt_path)
    if (
        receipt.get("input_set_sha256") != replay.get("input_set_sha256")
        or receipt.get("candidate_id") != candidate_id
        or receipt.get("status") != "consumed_before_replay"
    ):
        raise ImprovementError("matching consumed holdout receipt is missing")
    policy = RuntimePolicy.model_validate(candidate["policy"])
    version = {
        "schema_version": SCHEMA_VERSION,
        "version_id": "",
        "status": "accepted",
        "policy": policy.model_dump(),
        "criteria_fingerprint": active_policy_criteria_fingerprint(),
        "candidate_id": candidate_id,
        "evaluation_id": evaluation_id,
        "gate_sha256": sha256_file(gate_path),
        "accepted_at_epoch": int(time.time()),
    }
    version_id = hashlib.sha256(
        canonical_bytes({k: v for k, v in version.items() if k != "version_id"})
    ).hexdigest()
    version["version_id"] = version_id
    version_path = _contained(store_dir, store_dir / "versions" / f"{version_id}.json")
    _write_json(version_path, version, exclusive=True)
    pointer = {
        "schema_version": SCHEMA_VERSION,
        "version_id": version_id,
        "content_sha256": sha256_file(version_path),
    }
    _write_json(store_dir / "active.json", pointer)
    try:
        _append_event(
            store_dir,
            {
                "event": "promoted",
                "candidate_id": candidate_id,
                "evaluation_id": evaluation_id,
                "version_id": version_id,
                "at_epoch": int(time.time()),
            },
        )
    except OSError as exc:
        raise ImprovementError(
            f"policy {version_id} is active, but the promotion event was not recorded; reconcile active.json and events.jsonl"
        ) from exc
    return version_id


def promote_candidate(
    store_dir: Path, candidate_id: str, evaluation_id: str, *, allow_fixture: bool = False
) -> str:
    with _store_lock(store_dir):
        return _promote_candidate_unlocked(
            store_dir, candidate_id, evaluation_id, allow_fixture=allow_fixture
        )


def _rollback_policy_unlocked(store_dir: Path, version_id: str) -> LoadedPolicy:
    """Point to baseline or an existing accepted immutable local version."""
    if version_id == "baseline":
        pointer = {
            "schema_version": SCHEMA_VERSION,
            "version_id": "baseline",
            "content_sha256": BASELINE_CONTENT_SHA256,
        }
        _write_json(store_dir / "active.json", pointer)
        try:
            _append_event(
                store_dir, {"event": "rollback", "version_id": "baseline", "at_epoch": int(time.time())}
            )
        except OSError as exc:
            raise ImprovementError(
                "baseline is active, but the rollback event was not recorded; reconcile active.json and events.jsonl"
            ) from exc
        return load_active_policy(store_dir)
    if not re.fullmatch(r"[0-9a-f]{64}", version_id):
        raise ImprovementError("rollback target must be baseline or a known version id")
    path = _contained(store_dir, store_dir / "versions" / f"{version_id}.json")
    record = _read_json(path)
    if record.get("version_id") != version_id or record.get("status") != "accepted":
        raise ImprovementError("rollback target is not an accepted version")
    if record.get("criteria_fingerprint") != active_policy_criteria_fingerprint():
        raise ImprovementError("rollback target is incompatible with current review criteria")
    _write_json(
        store_dir / "active.json",
        {
            "schema_version": SCHEMA_VERSION,
            "version_id": version_id,
            "content_sha256": sha256_file(path),
        },
    )
    try:
        _append_event(
            store_dir, {"event": "rollback", "version_id": version_id, "at_epoch": int(time.time())}
        )
    except OSError as exc:
        raise ImprovementError(
            f"policy {version_id} is active, but the rollback event was not recorded; reconcile active.json and events.jsonl"
        ) from exc
    return load_active_policy(store_dir)


def rollback_policy(store_dir: Path, version_id: str) -> LoadedPolicy:
    with _store_lock(store_dir):
        return _rollback_policy_unlocked(store_dir, version_id)


__all__ = [
    "ImprovementError",
    "build_input_manifest",
    "evaluate_gate",
    "generate_candidate",
    "promote_candidate",
    "replay_candidate",
    "rollback_policy",
]
