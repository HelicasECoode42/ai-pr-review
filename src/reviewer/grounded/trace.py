"""Audit trace and coverage derived from a finished review."""
from __future__ import annotations

import hashlib
import json

from src.analyzer.context_builder import ReviewContext
from src.reviewer.criteria import (
    CRITERIA_VERSION,
)
from src.reviewer.grounded.intent import (
    INTENT_HYPOTHESIS_SCHEMA_VERSION,
    INTENT_PLAN_SCHEMA_VERSION,
    _missing_evidence_accounting,
)
from src.reviewer.grounded.prompts import (
    DISCOVERY_SYSTEM,
    INDEX_ATTRIBUTION_CONTRACT,
    RAW_DISCOVERY_SYSTEM,
    VERIFY_SYSTEM,
)


def _unit_coverage(snapshot, files, context: ReviewContext, audit: dict) -> dict | None:
    """Account for which planned review units reached the prompt.

    Only runs when the prompt was truncated: that is exactly the case where a
    reader needs to know what was left out. Offline and model-free, but it does
    parse the changed files, so it is skipped entirely when it cannot matter.
    """
    if not context.truncated or not files:
        return None
    try:
        from src.analyzer.plan_baseline import summarize_unit_coverage
        from src.analyzer.review_plan import build_review_plan

        budget = max(128, context.patch_token_budget or 6000)
        # The inventory is the changed set: the grounded path has no head
        # listing, so relationship edges outside the diff are not resolved here.
        plan = build_review_plan(snapshot, files, [f.filename for f in files], token_budget=budget)
        return summarize_unit_coverage(plan, context)
    except Exception as exc:
        audit["unit_coverage_error"] = f"{type(exc).__name__}: {exc}"
        return None


def _raw_candidate_inventory(candidates) -> list[dict]:
    """Every raw pool item with where it ended up, for counting raw discoveries.

    A kept candidate gets its own row; each pool item merged into it gets a row
    pointing at the kept one (`merged_into`) and sharing its final status.
    """
    rows = []
    for item in candidates:
        suggestion = item.get("suggestion") or {}
        kept_id = suggestion.get("raw_candidate_id")
        if not isinstance(kept_id, str):
            continue
        final = {"candidate_id": item.get("candidate_id"), "status": item.get("status"),
                 "reason": item.get("reason")}
        rows.append({"raw_candidate_id": kept_id, "origin": suggestion.get("candidate_origin"),
                     "merged_into": None, **final})
        for member in suggestion.get("merged_candidates") or []:
            if isinstance(member, dict) and isinstance(member.get("raw_candidate_id"), str):
                rows.append({"raw_candidate_id": member["raw_candidate_id"],
                             "origin": member.get("origin"), "merged_into": kept_id, **final})
    return rows


def _trace_hash(value):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _trace_identity(pr, files, reviewer_version, supplied, *, audit=None):
    """Hash review inputs and use only stable identities in the offline trace."""
    if supplied:
        return dict(supplied)
    audit = audit or {}
    raw_discovery = audit.get("discovery_mode") == "raw"
    repository_index_enabled = bool(audit.get("repository_index_enabled"))
    metadata = {
        "repository": pr.repo,
        "pr_number": pr.number,
        "base_sha": pr.base_sha,
        "head_sha": pr.head_sha,
        "title": pr.title,
        "body": pr.body,
    }
    file_manifest = [{
        "path": file.filename,
        "status": getattr(file.status, "value", file.status),
        "additions": file.additions,
        "deletions": file.deletions,
        "patch_sha256": hashlib.sha256((file.patch or "").encode("utf-8")).hexdigest(),
    } for file in files]
    metadata_sha = _trace_hash(metadata)
    diff_sha = _trace_hash(file_manifest)
    effective_policy = {"prompt_addendum": "", "playbook_recall_k": 5}
    from pathlib import Path

    from src.reviewer.discovery_lenses import LENS_DISCOVERY_SYSTEM

    source_root = Path(__file__).parents[2]
    reviewer_root = source_root / "reviewer"
    runtime_files = (
        reviewer_root / "grounded_review.py",
        *sorted(Path(__file__).parent.glob("*.py")),
        reviewer_root / "engine.py",
        reviewer_root / "discovery_lenses.py",
        reviewer_root / "criteria.py",
        reviewer_root / "critic.py",
        reviewer_root / "unit_review.py",
        source_root / "analyzer" / "repository_context.py",
        source_root / "analyzer" / "symbol_index.py",
        source_root / "application" / "review.py",
        source_root / "application" / "strategy_policy.py",
        reviewer_root / "provider.py",
    )
    runtime_hash = hashlib.sha256()
    for path in runtime_files:
        runtime_hash.update(path.relative_to(source_root).as_posix().encode("utf-8"))
        runtime_hash.update(path.read_bytes())
    return {
        "identity": {
            "case_id": f"{pr.repo}#{pr.number}@{pr.head_sha or 'unknown'}",
            "repository": pr.repo,
            "pr_number": pr.number,
            "base_sha": pr.base_sha,
            "head_sha": pr.head_sha,
            "metadata_sha256": metadata_sha,
            "diff_sha256": diff_sha,
            "review_input_set_sha256": _trace_hash({
                "metadata_sha256": metadata_sha, "diff_sha256": diff_sha,
            }),
            "benchmark_case_id": None,
            "prepared_metadata_sha256": None,
            "prepared_diff_sha256": None,
            "input_set_sha256": None,
        },
        "policy": {
            "policy_id": "baseline",
            "status": "baseline",
            "effective_policy_sha256": _trace_hash(effective_policy),
            "raw_discovery": raw_discovery,
            "raw_discovery_scope": audit.get(
                "raw_discovery_scope", "full_pr_diff" if raw_discovery else "intent"
            ),
            "repository_index_enabled": repository_index_enabled,
        },
        "criteria_sha256": hashlib.sha256(CRITERIA_VERSION.encode("utf-8")).hexdigest(),
        "runtime_sha256": runtime_hash.hexdigest(),
        "prompt_sha256": hashlib.sha256(
            ((RAW_DISCOVERY_SYSTEM + LENS_DISCOVERY_SYSTEM if raw_discovery
              else DISCOVERY_SYSTEM) + VERIFY_SYSTEM
             + (INDEX_ATTRIBUTION_CONTRACT if repository_index_enabled else "")).encode("utf-8")
        ).hexdigest(),
        # Direct callers do not supply the effective addendum. This fingerprint
        # identifies the known system prompts, not the complete sent prompt.
        "prompt_identity_status": "partial",
        "reviewer_version": reviewer_version,
    }


def _review_trace(pr, files, result, context, reviewer_version):
    """Build a compact host-auditable trace; never copy source or prompt bodies."""
    identity = _trace_identity(
        pr, files, reviewer_version, result.bundle.get("trace_identity"), audit=result.bundle
    )
    plan = result.bundle.get("intent_plan", {})
    excerpt_by_ref = {
        excerpt["evidence_ref"]: excerpt for excerpt in result.bundle.get("excerpts", [])
        if isinstance(excerpt.get("evidence_ref"), str)
    }
    hypotheses = [{
        "schema_version": item.get("schema_version", INTENT_HYPOTHESIS_SCHEMA_VERSION),
        "id": item.get("id"),
        "focus_tags": list(item.get("focus_tags", [])),
        "source_status": item.get("source_status", "unknown"),
        "source_refs": list(item.get("source_refs", [])),
        "goal_sha256": _trace_hash(item.get("goal", "")),
        "constraints_sha256": _trace_hash(item.get("constraints", [])),
        "risk_sha256": _trace_hash(item.get("risk_hypotheses", [])),
        "unknown_count": len(item.get("unknowns", [])),
        "missing_evidence": [obligation for obligation in _missing_evidence_accounting(
            plan, result.bundle
        ) if obligation["hypothesis_id"] == item.get("id")],
        "revision": item.get("revision", 0),
    } for item in plan.get("hypotheses", []) if isinstance(item, dict)]
    tasks = []
    for index, item in enumerate(plan.get("tasks", [])):
        if not isinstance(item, dict):
            continue
        query = item.get("query")
        if isinstance(query, dict):
            query = {key: query[key] for key in ("tool", "path", "side", "line", "symbol", "limit")
                     if key in query}
        indexed = next((request.get("host_index") for request in result.bundle.get("requests", [])
                        if request.get("normalized_query") == item.get("query")
                        and isinstance(request.get("host_index"), dict)), None)
        tasks.append({
            "task_id": item.get("task_id") or _trace_hash({
                "index": index, "hypothesis_id": item.get("hypothesis_id")
            }),
            "hypothesis_id": item.get("hypothesis_id"),
            "purpose_sha256": _trace_hash(item.get("purpose", "")),
            "status": item.get("status", "unknown"),
            "omission_reason": item.get("omission_reason"),
            "missing_id": item.get("missing_id"),
            "followup": bool(item.get("followup")),
            "query": query,
            "priority_rank": item.get("priority_rank"),
            "priority_source": item.get("priority_source", "model_order"),
            "reused": bool(item.get("reused")),
            "evidence_ref_ids": [
                ref for ref in item.get("evidence_refs", []) if ref in excerpt_by_ref
            ],
            **({"host_index": indexed} if indexed else {}),
        })
    omitted_tasks = [
        {
            "task_id": item.get("task_id"),
            "hypothesis_id": item.get("hypothesis_id"),
            "status": item.get("status"),
            "reason": item.get("reason"),
            "omission_reason": item.get("omission_reason"),
            "request_kind": item.get("request_kind"),
            "query": item.get("query"),
            "valid_source_refs": item.get("valid_source_refs", []),
        }
        for item in plan.get("omitted_tasks", [])
        if isinstance(item, dict)
    ]
    evidence_refs = [{
        "evidence_ref_id": ref,
        "status": "available_in_report",
        "repo_sha": item.get("commit"),
        "path": item.get("path"),
        "start_line": item.get("start_line"),
        "end_line": item.get("end_line"),
        "content_sha256": item.get("sha256"),
    } for ref, item in sorted(excerpt_by_ref.items())]
    candidate_evidence = []
    for candidate in result.candidates:
        candidate_id = candidate.get("candidate_id")
        decision = next((item for item in result.decisions
                         if item.get("candidate_id") == candidate_id), {})
        refs = []
        for citation in (decision.get("evidence", []) + decision.get("counter_evidence", [])):
            if not isinstance(citation, dict):
                continue
            for ref, excerpt in excerpt_by_ref.items():
                if (citation.get("path") == excerpt.get("path")
                        and citation.get("side") == excerpt.get("side")
                        and isinstance(citation.get("line"), int)
                        and excerpt.get("start_line", 0) <= citation["line"]
                        <= excerpt.get("end_line", 0)):
                    refs.append(ref)
        refs = list(dict.fromkeys(refs))
        candidate_evidence.append({
            "candidate_id": candidate_id,
            "status": candidate.get("status", "unknown"),
            "evidence_ref_ids": refs,
            "code_ranges": [{
                "evidence_ref_id": ref, "path": excerpt_by_ref[ref].get("path"),
                "side": excerpt_by_ref[ref].get("side"),
                "start_line": excerpt_by_ref[ref].get("start_line"),
                "end_line": excerpt_by_ref[ref].get("end_line"),
                "truncated": bool(excerpt_by_ref[ref].get("truncated")),
            } for ref in refs],
        })
    filters = [
        verdict.get("name") + ":" + ("passed" if verdict.get("passed") else "failed")
        for decision in result.decisions if isinstance(decision, dict)
        for verdict in decision.get("filter_verdicts", []) if isinstance(verdict, dict)
        and isinstance(verdict.get("name"), str)
    ]
    statuses = [item.get("status", "unknown") for item in result.candidates]
    trace = {
        "schema_version": 1,
        "identity": identity.get("identity", {}),
        "policy": identity.get("policy", {}),
        "criteria_sha256": identity.get("criteria_sha256"),
        "runtime_sha256": identity.get("runtime_sha256"),
        "prompt_sha256": identity.get("prompt_sha256"),
        "prompt_identity_status": identity.get("prompt_identity_status", "complete"),
        "intent": {
            "schema_version": plan.get("schema_version", INTENT_PLAN_SCHEMA_VERSION),
            "status": plan.get("status", "not_reported"),
            "hypotheses": hypotheses,
            "candidate_evidence": candidate_evidence,
            "tasks": tasks,
            "omitted_tasks": omitted_tasks,
            "omitted_task_overflow_count": plan.get("omitted_task_overflow_count", 0),
            "request_overflow_count": plan.get("request_overflow_count", 0),
            "assessments": [{
                "hypothesis_id": item.get("hypothesis_id"),
                "status": item.get("status", "unknown"),
                "evidence_ref_ids": [ref for ref in item.get("evidence_refs", [])
                                     if ref in excerpt_by_ref],
                "unknown_ref_count": len(item.get("unknown_evidence_refs", [])),
            } for item in plan.get("assessments", []) if isinstance(item, dict)],
            "error_counts": dict(plan.get("error_counts", {})),
        },
        "evidence_refs": evidence_refs,
        "missing_evidence_followup_enabled": bool(
            result.bundle.get("missing_evidence_followup_enabled", False)
        ),
        "discovery_mode": result.bundle.get("discovery_mode", "intent"),
        "grounded_critic": (lambda critic: {
            "status": critic.get("status"),
            "reason": critic.get("reason"),
            "batch_cap": critic.get("batch_cap"),
            "selected_candidate_ids": critic.get("selected_candidate_ids", []),
            "items": [{key: item.get(key) for key in (
                "candidate_id", "status", "decision", "reason", "evidence_ref_ids",
                "trigger_reason", "semantic_assessment", "semantic_consistent",
                "goal_coverage",
            )} for item in critic.get("items", []) if isinstance(item, dict)],
            "raw_response_sha256": (
                hashlib.sha256(critic["raw_response"].encode("utf-8")).hexdigest()
                if isinstance(critic.get("raw_response"), str) else None
            ),
        } if isinstance(critic, dict) else None)(result.bundle.get("grounded_critic")),
        "candidate_counts": {
            "raw": len(result.candidates),
            "confirmed": sum(status == "confirmed" for status in statuses),
            "pending": sum(status == "needs_context" for status in statuses),
            "filtered": sum(status == "filtered" for status in statuses),
            "unverified": sum(status == "unverified" for status in statuses),
        },
        "filter_counts": {label: filters.count(label) for label in sorted(set(filters))},
        "mechanical_failures": [{
            "failure_id": item["task_id"],
            "failure_tag": "cross_file_task_budget_omitted",
            "reason": item["reason"],
            "hypothesis_id": item["hypothesis_id"],
            "task_id": item["task_id"],
            "valid_source_refs": item["valid_source_refs"],
            "omission_reason": item["omission_reason"],
        } for item in omitted_tasks
            if item.get("reason") in {"task_limit", "evidence_budget"}
            and item.get("hypothesis_id")
            and item.get("valid_source_refs")],
        "coverage": {
            "context_truncated": context.truncated,
            "files_total": len(files),
            "files_skipped": len(context.skipped_files),
            "hunks": context.coverage_summary.model_dump(mode="json"),
            "unit": result.unit_coverage,
            "evidence_budget_used": result.bundle.get("tokens", 0),
            "evidence_budget_limit": result.bundle.get("token_budget", 0),
            "omitted_count": len(result.bundle.get("omitted", [])),
            "evicted_count": len(result.bundle.get("evicted", [])),
            # Head lines that reached the prompt patch section. Ranges only, no
            # source text, so a saved report can answer "was the gold line shown?"
            "rendered_head_ranges": [
                {"path": path, "ranges": [[start, end] for start, end in ranges]}
                for path, ranges in sorted(
                    getattr(context, "rendered_head_ranges", {}).items()
                )
                if ranges
            ],
        },
        "unknown_counts": {
            "hypotheses_with_unknown_source": sum(
                item["source_status"] != "known" for item in hypotheses
            ),
            "unknown_assessments": sum(
                item["status"] == "unknown" for item in plan.get("assessments", [])
            ),
            "tasks_without_evidence": sum(not item["evidence_ref_ids"] for item in tasks),
        },
        "usage": {
            "discovery_requests": result.discovery_calls,
            "verification_requests": result.verification_calls,
            "provider_usage": "not_observed_at_engine_boundary",
        },
        "usage_status": "unknown",
    }
    trace["trace_sha256"] = _trace_hash(trace)
    return trace
