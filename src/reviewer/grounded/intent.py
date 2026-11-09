"""Intent hypotheses and the bound read/search tasks they authorise."""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping

from src.analyzer.repository_context import safe_source_path

MAX_INTENT_HYPOTHESES = 4


MAX_INTENT_TASKS = 4


MAX_INTENT_ERROR_KINDS = 12


MAX_INTENT_OMISSION_RECORDS = 4


INTENT_PLAN_SCHEMA_VERSION = 2


INTENT_HYPOTHESIS_SCHEMA_VERSION = 1


MAX_MISSING_EVIDENCE = 4


INTENT_FOCUS_ORDER = (
    "security_boundary",
    "caller_contract",
    "error_propagation",
    "state_consistency",
    "resource_lifecycle",
    "input_validation",
)


_INTENT_FOCUS_RANK = {tag: rank for rank, tag in enumerate(INTENT_FOCUS_ORDER)}


def _record_intent_error(plan, error):
    """Keep a bounded set of error categories and aggregate repeated failures."""
    label = str(error)[:80] or "unknown_error"
    counts = plan.setdefault("error_counts", {})
    if label not in counts and len(counts) >= MAX_INTENT_ERROR_KINDS:
        label = "other_errors"
    counts[label] = counts.get(label, 0) + 1
    if label not in plan["errors"]:
        plan["errors"].append(label)


def _task_labels(request):
    if not isinstance(request, dict):
        return None, ""
    identifier = request.get("hypothesis_id")
    purpose = request.get("purpose")
    return (
        identifier[:32] if isinstance(identifier, str) else None,
        purpose.strip()[:240] if isinstance(purpose, str) else "",
    )


def _new_intent_plan(status):
    """Create the current bounded plan shape for every legacy-compatible branch."""
    return {
        "schema_version": INTENT_PLAN_SCHEMA_VERSION,
        "status": status,
        "hypotheses": [],
        "hypothesis_history": [],
        "tasks": [],
        "omitted_tasks": [],
        "omitted_task_overflow_count": 0,
        "assessments": [],
        "errors": [],
        "error_counts": {},
    }


def _task_priority(request, hypotheses, original_index):
    """Order focused source-backed tasks within the existing execution cap."""
    identifier, _ = _task_labels(request)
    hypothesis = next((item for item in hypotheses if item["id"] == identifier), None)
    if not hypothesis or not hypothesis.get("priority_eligible"):
        return (1, len(INTENT_FOCUS_ORDER), original_index)
    ranks = [_INTENT_FOCUS_RANK[tag] for tag in hypothesis.get("focus_tags", [])
             if tag in _INTENT_FOCUS_RANK]
    if not ranks:
        return (1, len(INTENT_FOCUS_ORDER), original_index)
    return (0, min(ranks), original_index)


def _intent_task_id(request):
    """Fingerprint bounded task coordinates without storing PR/source text in identity."""
    if not isinstance(request, dict):
        identity = {"invalid": type(request).__name__}
    else:
        identity = {
            key: request.get(key)
            for key in (
                "hypothesis_id", "missing_id", "tool", "side", "path", "line",
                "start_line", "symbol",
            )
            if isinstance(request.get(key), (str, int)) and not isinstance(request.get(key), bool)
        }
        purpose = request.get("purpose")
        if isinstance(purpose, str):
            identity["purpose_sha256"] = hashlib.sha256(purpose.encode("utf-8")).hexdigest()
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _intent_request_rejection(
    request: object, hypotheses: Mapping[str, object]
) -> str | None:
    """Share host-side validation between executable and budget-omitted tasks."""
    if not isinstance(request, dict):
        return "invalid_task"
    if not hypotheses:
        return "unknown_hypothesis_id" if request.get("hypothesis_id") else "unbound_task"
    if (not isinstance(request.get("hypothesis_id"), str)
            or request["hypothesis_id"] not in hypotheses):
        return "unknown_hypothesis_id"
    if (not isinstance(request.get("purpose"), str)
            or not request["purpose"].strip() or len(request["purpose"]) > 240):
        return "invalid_task_purpose"
    if not isinstance(request.get("tool"), str) or request["tool"] not in {"read", "search"}:
        return "unsupported_tool"
    if (not isinstance(request.get("side", "head"), str)
            or request.get("side", "head") not in {"head", "base"}):
        return "invalid_snapshot_side"
    if request["tool"] == "read":
        path = request.get("path")
        line = request.get("line", request.get("start_line"))
        if (not isinstance(path, str) or len(path) > 400 or not safe_source_path(path)
                or isinstance(line, bool) or not isinstance(line, int) or line < 1):
            return "invalid_read_query"
    elif (not isinstance(request.get("symbol"), str)
          or not re.fullmatch(r"[A-Za-z_]\w{2,79}", request.get("symbol", "").strip())):
        return "invalid_search_query"
    return None


def _record_omitted_intent_task(plan, request, reason, changed_paths, omission_reason=None):
    """Record only valid, code-sourced cross-file work the host actually skipped."""
    hypotheses = {item["id"]: item for item in plan["hypotheses"]}
    if _intent_request_rejection(request, hypotheses):
        return
    hypothesis = hypotheses[request["hypothesis_id"]]
    if not hypothesis.get("priority_eligible"):
        return
    is_cross_file = (
        request["tool"] == "search"
        or request.get("path") not in changed_paths
    )
    if not is_cross_file:
        return
    omissions = plan.setdefault("omitted_tasks", [])
    if len(omissions) >= MAX_INTENT_OMISSION_RECORDS:
        plan["omitted_task_overflow_count"] = plan.get("omitted_task_overflow_count", 0) + 1
        return
    omissions.append({
        "task_id": _intent_task_id(request),
        "hypothesis_id": request["hypothesis_id"],
        "status": "evidence_omitted" if reason == "evidence_budget" else "not_executed_budget",
        "reason": reason,
        "omission_reason": omission_reason,
        "request_kind": request["tool"],
        "valid_source_refs": [
            ref for ref in hypothesis["source_refs"]
            if ref not in {"pr:title", "pr:description"}
        ],
        "query": ({
            "tool": "search", "symbol": request["symbol"].strip(),
            "side": request.get("side", "head"),
        } if request["tool"] == "search" else {
            "tool": "read", "path": request["path"],
            "line": request.get("line", request.get("start_line")),
            "side": request.get("side", "head"),
        }),
    })


def _intent_hypotheses(raw, available_sources):
    """Bound the model's plan and keep only source references present in this run."""
    if not isinstance(raw, list) or len(raw) > MAX_INTENT_HYPOTHESES:
        return [], ["invalid_hypothesis_list"]
    hypotheses, errors, seen = [], [], set()
    for item in raw:
        if not isinstance(item, dict):
            errors.append("invalid_hypothesis")
            continue
        identifier, goal = item.get("id"), item.get("goal")
        valid_id = isinstance(identifier, str) and re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,31}", identifier
        )
        if (not valid_id or identifier in seen or not isinstance(goal, str)
                or not goal.strip() or len(goal) > 320):
            errors.append("invalid_hypothesis_identity_or_goal")
            continue
        fields = {}
        valid = True
        for name in ("constraints", "risk_hypotheses", "unknowns"):
            values = item.get(name, [])
            if (not isinstance(values, list) or len(values) > 5
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 240
                           for value in values)):
                valid = False
                break
            fields[name] = [value.strip() for value in values]
        raw_missing = item.get("missing_evidence", [])
        missing_evidence = []
        if not isinstance(raw_missing, list) or len(raw_missing) > MAX_MISSING_EVIDENCE:
            errors.append("invalid_missing_evidence_list")
            raw_missing = []
        missing_ids = set()
        for missing in raw_missing:
            if not isinstance(missing, dict):
                errors.append("invalid_missing_evidence")
                continue
            missing_id = missing.get("id")
            description = missing.get("description")
            if (not isinstance(missing_id, str)
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", missing_id)
                    or missing_id in missing_ids
                    or not isinstance(description, str) or not description.strip()
                    or len(description) > 240):
                errors.append("invalid_missing_evidence_identity_or_description")
                continue
            missing_ids.add(missing_id)
            missing_evidence.append({"id": missing_id, "description": description.strip()})
        source_refs = item.get("source_refs", [])
        if not isinstance(source_refs, list) or len(source_refs) > 6 or any(
            not isinstance(ref, str) or len(ref) > 200 for ref in source_refs
        ):
            valid = False
            source_refs = []
        if not valid:
            errors.append("invalid_hypothesis_fields")
            continue
        raw_focus = item.get("focus_tags", [])
        if (not isinstance(raw_focus, list) or len(raw_focus) > 3
                or any(not isinstance(tag, str) for tag in raw_focus)):
            focus_tags = []
            errors.append("invalid_hypothesis_focus_tags")
        else:
            focus_tags = list(dict.fromkeys(
                tag for tag in raw_focus if tag in _INTENT_FOCUS_RANK
            ))
            if len(focus_tags) != len(raw_focus):
                errors.append("unsupported_hypothesis_focus_tag")
        accepted = [ref for ref in source_refs if ref in available_sources]
        unknown = [ref for ref in source_refs if ref not in available_sources]
        hypothesis = {
            "schema_version": INTENT_HYPOTHESIS_SCHEMA_VERSION,
            "id": identifier,
            "goal": goal.strip(),
            **fields,
            "focus_tags": focus_tags,
            "source_refs": accepted,
            "unknown_source_refs": unknown,
            "missing_evidence": missing_evidence,
            "source_status": "known" if accepted and not unknown else "unknown",
            # PR metadata can seed a question but cannot make it outrank source-backed work.
            "priority_eligible": bool(accepted and not unknown) and any(
                ref not in {"pr:title", "pr:description"} for ref in accepted
            ),
            "record_type": "intent_hypothesis_not_contract",
        }
        hypotheses.append(hypothesis)
        seen.add(identifier)
    if not hypotheses and not errors:
        errors.append("no_hypotheses")
    return hypotheses, errors


def _intent_assessments(raw, plan, evidence_by_hypothesis):
    """Record evidence-linked model assessments without changing finding verdicts."""
    if not isinstance(raw, list):
        return [], ["intent_assessments_missing"]
    hypotheses = {item["id"] for item in plan.get("hypotheses", [])}
    assessments, errors, seen = [], [], set()
    allowed = {"supported", "refuted", "revised", "unknown"}
    for item in raw[:MAX_INTENT_HYPOTHESES]:
        if not isinstance(item, dict):
            errors.append("invalid_intent_assessment")
            continue
        identifier = item.get("hypothesis_id")
        if not isinstance(identifier, str) or identifier not in hypotheses or identifier in seen:
            errors.append("unknown_or_duplicate_hypothesis_assessment")
            continue
        status = item.get("status")
        refs = item.get("evidence_refs", [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            refs = []
        permitted_refs = set(evidence_by_hypothesis.get(identifier, ()))
        accepted_refs = list(dict.fromkeys(ref for ref in refs if ref in permitted_refs))
        unknown_refs = [ref for ref in refs if ref not in permitted_refs]
        rationale = item.get("rationale", "")
        unknowns = item.get("unknowns", [])
        valid_unknowns = (isinstance(unknowns, list) and len(unknowns) <= 5
                    and all(isinstance(value, str) and len(value) <= 240
                            for value in unknowns))
        revision = item.get("revision", "")
        valid_revision = isinstance(revision, str) and len(revision) <= 320
        if (not isinstance(status, str) or status not in allowed or not accepted_refs
                or unknown_refs
                or not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 500
                or not valid_unknowns or not valid_revision
                or (status == "revised" and not revision.strip())):
            status = "unknown"
            errors.append("assessment_missing_valid_evidence_or_fields")
        assessments.append({
            "hypothesis_id": identifier,
            "status": status,
            "evidence_refs": accepted_refs,
            "unknown_evidence_refs": unknown_refs,
            "rationale": rationale.strip() if isinstance(rationale, str) else "",
            "unknowns": unknowns if valid_unknowns else [],
            "revision": revision if valid_revision else "",
            "record_type": "model_hypothesis_assessment_not_finding_verdict",
        })
        seen.add(identifier)
    if set(seen) != hypotheses:
        errors.append("hypotheses_without_assessment")
    return assessments, errors


def _missing_evidence_accounting(plan, bundle):
    """Mechanically report each declared obligation against executed task refs."""
    excerpt_items = bundle.excerpts if hasattr(bundle, "excerpts") else bundle.get("excerpts", [])
    excerpts = {item["evidence_ref"]: item for item in excerpt_items}
    tasks = plan.get("tasks", [])
    result = []
    for hypothesis in plan.get("hypotheses", []):
        for obligation in hypothesis.get("missing_evidence", []):
            matching = [task for task in tasks
                        if task.get("hypothesis_id") == hypothesis["id"]
                        and task.get("missing_id") == obligation["id"]]
            refs = list(dict.fromkeys(
                ref for task in matching for ref in task.get("evidence_refs", [])
                if ref in excerpts
            ))
            ranges = [{
                "evidence_ref": ref,
                "path": excerpts[ref]["path"],
                "side": excerpts[ref]["side"],
                "start_line": excerpts[ref]["start_line"],
                "end_line": excerpts[ref]["end_line"],
                "truncated": bool(excerpts[ref].get("truncated")),
            } for ref in refs]
            statuses = [task.get("status") for task in matching]
            if not matching:
                status = "not_requested"
            elif any(value == "not_executed_followup_limit" for value in statuses):
                status = "followup_limit_rejected"
            elif any(value in {"not_executed_budget_exhausted", "task_limit_exceeded",
                               "request_limit_exceeded"} for value in statuses):
                status = "budget_rejected"
            elif refs and all(item["truncated"] for item in ranges):
                status = "snippet_truncated"
            elif refs:
                status = "evidence_visible"
            elif any(task.get("omission_reason") == "evidence_budget_exhausted"
                     for task in matching):
                status = "budget_rejected"
            elif any(value in {"executed_no_evidence", "search_unavailable", "no_evidence",
                               "unavailable", "failed"} for value in statuses):
                status = "request_failed"
            else:
                status = "request_failed"
            result.append({
                "hypothesis_id": hypothesis["id"],
                "missing_id": obligation["id"],
                "description_sha256": hashlib.sha256(
                    obligation["description"].encode("utf-8")
                ).hexdigest(),
                "status": status,
                "request_count": len(matching),
                "evidence_ref_ids": refs,
                "code_ranges": ranges,
            })
    return result


def _bounded_python_index_search(snapshot, files):
    """Resolve an existing search request inside changed + lexical-hit files only."""
    from src.analyzer.symbol_index import (
        PythonSymbolIndex,
        parse_python,
        resolve_python_relationships,
    )

    parsed_by_side: dict[tuple[str, str], object] = {}
    total_source_bytes = 0
    max_index_files = 12
    max_file_bytes = 100_000
    max_total_bytes = 400_000

    def resolve(symbol, side, limit):
        nonlocal total_source_bytes
        started = time.monotonic()
        errors_before = len(snapshot.audit().get("errors", []))
        lexical_hits = snapshot.search(symbol, side=side, limit=limit)
        search_errors = snapshot.audit().get("errors", [])[errors_before:]
        side_changed_paths = sorted({
            file.filename if side == "head" else (file.previous_filename or file.filename)
            for file in files
        })
        ordered_paths = list(dict.fromkeys(
            side_changed_paths + [path for path, _ in lexical_hits]
        ))
        # The index is shared across all existing search requests in this review.
        # Parsing must not silently multiply the repository read budget by the
        # number of hypotheses or discovery rounds.
        scope_paths = list(dict.fromkeys(
            [path for (indexed_side, path) in parsed_by_side if indexed_side == side]
            + ordered_paths
        ))
        omissions = ["scope_limited_to_changed_and_search_hits"]
        if len(scope_paths) > max_index_files:
            omissions.append("index_file_limit")
            scope_paths = scope_paths[:max_index_files]
        parsed_bytes = 0
        for path in scope_paths:
            key = (side, path)
            if key in parsed_by_side:
                continue
            if len(parsed_by_side) >= max_index_files:
                omissions.append("index_file_limit")
                break
            try:
                text = snapshot.read(path, side)
            except Exception:
                text = None
            if text is None:
                omissions.append(f"source_unavailable:{path}")
                continue
            size = len(text.encode("utf-8"))
            if size > max_file_bytes or total_source_bytes + size > max_total_bytes:
                omissions.append(f"index_byte_limit:{path}")
                continue
            total_source_bytes += size
            parsed_bytes += size
            parsed_by_side[key] = parse_python(path, text, side, snapshot.base, snapshot.head)
        parsed = [
            parsed_by_side[(indexed_side, path)]
            for indexed_side, path in parsed_by_side
            if indexed_side == side
        ]
        python_files = tuple(source for source in parsed if source.parser == "python-ast")
        if len(python_files) != len(parsed):
            omissions.append("non_python_or_parse_failed")
        if any("parse_failed" in source.limitations for source in python_files):
            omissions.append("parse_failed")
        relationships, unresolved, edge_omissions = resolve_python_relationships(
            python_files, set(scope_paths), side=side,
        )
        omissions.extend(edge_omissions)
        if unresolved:
            omissions.append("unresolved_or_ambiguous_bindings")
        index = PythonSymbolIndex(
            python_files, relationships, side=side,
            index_omissions=tuple(dict.fromkeys(omissions)),
            unresolved_references=unresolved,
        )
        symbols = [symbol_ref for source in python_files for symbol_ref in source.symbols
                   if symbol_ref.name == symbol]
        source_ranges = {
            symbol_ref.symbol_id: (source.path, side, symbol_ref.start, symbol_ref.end)
            for source in python_files for symbol_ref in source.symbols
        }
        relation_rows = []
        caller_hits = []
        if len(symbols) == 1:
            query = index.query(symbols[0].symbol_id, direction="callers", depth=1,
                                limit=limit)
            omissions.extend(query.omissions)
            for edge in query.relationships:
                relation_rows.append({
                    "source": edge.source, "target": edge.target, "kind": edge.kind,
                    "strength": edge.strength, "provenance": edge.provenance,
                    "evidence_level": edge.evidence_level,
                    "source_symbol_id": edge.source_symbol_id,
                    "target_symbol_id": edge.target_symbol_id,
                    "source_start": edge.source_start, "source_end": edge.source_end,
                    "target_start": edge.target_start, "target_end": edge.target_end,
                })
                if edge.source_start:
                    caller_hits.append((edge.source, edge.source_start, edge.source_symbol_id))
        elif len(symbols) > 1:
            omissions.append("ambiguous_symbol_target")
        else:
            omissions.append("symbol_definition_not_in_index_scope")

        # Preserve the established lexical search as the request's baseline.
        # Indexed callers may use only slots left after those hits, so weak or
        # repeated edges cannot reorder away evidence the non-indexed path found.
        baseline_hits = list(dict.fromkeys(lexical_hits))[:limit]
        baseline_ranges = set()
        for path, line in baseline_hits:
            source_range = next((value for symbol_id, value in source_ranges.items()
                                 if value[0] == path and value[2] <= line <= value[3]), None)
            baseline_ranges.add(source_range or (path, side, line, line))
        indexed_candidates = []
        seen_index_ranges = set(baseline_ranges)
        for path, line, source_symbol_id in caller_hits:
            key = source_ranges.get(source_symbol_id) or (path, side, line, line)
            if key not in seen_index_ranges:
                seen_index_ranges.add(key)
                indexed_candidates.append((path, line))
        remaining_slots = max(0, limit - len(baseline_hits))
        index_extra_hits = indexed_candidates[:remaining_slots]
        extra_omitted = len(indexed_candidates) - len(index_extra_hits)
        return {
            "hits": baseline_hits + index_extra_hits,
            "baseline_hits": baseline_hits,
            "index_extra_hits": index_extra_hits,
            "index_extra_omitted": ({
                "count": extra_omitted, "reason": "no_return_slot",
            } if extra_omitted else {}),
            "search_errors": search_errors,
            "index": {
                "schema": "python-binding-v1", "status": "partial",
                "side": side, "base_sha": snapshot.base, "head_sha": snapshot.head,
                "searched_scope": sorted({source.path for source in parsed}),
                "completeness": "partial",
                "omissions": list(dict.fromkeys(omissions)),
                "files_parsed": len(parsed), "source_bytes_parsed": parsed_bytes,
                "source_bytes_run_total": total_source_bytes,
                "scan_ms": round((time.monotonic() - started) * 1000, 3),
                "relationships": relation_rows,
            },
        }

    return resolve


def _execute_intent_tasks(bundle, requests, plan, snapshot, query_keys, changed_paths,
                          search_resolver=None):
    """Validate and execute one bounded batch of hypothesis-bound source queries."""
    hypotheses = {item["id"]: item for item in plan["hypotheses"]}
    ranked_requests = sorted(
        enumerate(requests),
        key=lambda pair: _task_priority(pair[1], plan["hypotheses"], pair[0]),
    )
    if len(ranked_requests) > MAX_INTENT_TASKS:
        plan["request_overflow_count"] = (
            plan.get("request_overflow_count", 0) + len(ranked_requests) - MAX_INTENT_TASKS
        )
        _record_intent_error(plan, "request_limit_exceeded")
        for _, request in ranked_requests[MAX_INTENT_TASKS:]:
            _record_omitted_intent_task(
                plan, request, "task_limit", changed_paths
            )
    batch = ranked_requests[:MAX_INTENT_TASKS]
    executable = []
    for original_index, request in batch:
        priority = _task_priority(request, plan["hypotheses"], original_index)
        rejection = _intent_request_rejection(request, hypotheses)
        if not rejection and request.get("followup") and not request.get("missing_id"):
            rejection = "missing_evidence_id_required"
        if not rejection and request.get("missing_id") is not None:
            hypothesis = hypotheses[request["hypothesis_id"]]
            known_missing = {item["id"] for item in hypothesis.get("missing_evidence", [])}
            if request["missing_id"] not in known_missing:
                rejection = "unknown_missing_evidence_id"
        if rejection:
            identifier, purpose = _task_labels(request)
            plan["tasks"].append({
                "hypothesis_id": identifier,
                "purpose": purpose,
                "task_id": _intent_task_id(request),
                "missing_id": request.get("missing_id"),
                "followup": bool(request.get("followup")),
                "status": rejection, "evidence_refs": [],
                "priority_rank": priority[1],
                "priority_source": "focus_tag" if priority[0] == 0 else "model_order",
            })
            _record_intent_error(plan, rejection)
            continue
        if request["tool"] == "read":
            line = request.get("line", request.get("start_line"))
            query_key = (snapshot.base, snapshot.head, "read", request.get("side", "head"),
                         request["path"], line)
        else:
            symbol = request["symbol"].strip()
            query_key = (snapshot.base, snapshot.head, "search", request.get("side", "head"),
                         symbol, 4)
            request = {**request, "symbol": symbol}
        prior_task = None
        for completed in plan["tasks"]:
            query = completed.get("query")
            if not isinstance(query, dict):
                continue
            if query.get("tool") == "read":
                prior_key = (snapshot.base, snapshot.head, "read", query.get("side", "head"),
                             query.get("path"), query.get("line"))
            elif query.get("tool") == "search":
                prior_key = (snapshot.base, snapshot.head, "search", query.get("side", "head"),
                             query.get("symbol"), query.get("limit", 4))
            else:
                continue
            if prior_key == query_key:
                prior_task = completed
                break
        if prior_task is not None:
            visible_refs = {item["evidence_ref"] for item in bundle.excerpts}
            shared_refs = [ref for ref in prior_task.get("evidence_refs", [])
                           if ref in visible_refs]
            prior_status = prior_task.get("status", "unknown")
            prior_had_no_evidence = prior_status != "executed_with_evidence"
            prior_reused_result = prior_had_no_evidence or bool(shared_refs)
            plan["tasks"].append({
                "hypothesis_id": request["hypothesis_id"],
                "purpose": request["purpose"],
                "task_id": _intent_task_id(request),
                "query": prior_task.get("query"),
                "missing_id": request.get("missing_id"),
                "followup": bool(request.get("followup")),
                "status": (prior_status if prior_reused_result
                           else "evidence_not_visible_after_prior_request"),
                "reused": prior_reused_result,
                "evidence_refs": shared_refs,
                "omission_reason": (
                    prior_task.get("omission_reason") if prior_had_no_evidence
                    else None if shared_refs else "prior_evidence_not_rendered"
                ),
                "priority_rank": priority[1],
                "priority_source": "focus_tag" if priority[0] == 0 else "model_order",
            })
            continue
        if query_key not in query_keys and len(query_keys) >= MAX_INTENT_TASKS:
            plan["tasks"].append({
                "hypothesis_id": request["hypothesis_id"], "purpose": request["purpose"],
                "task_id": _intent_task_id(request),
                "missing_id": request.get("missing_id"),
                "followup": bool(request.get("followup")),
                "status": "task_limit_exceeded", "evidence_refs": [],
                "priority_rank": priority[1],
                "priority_source": "focus_tag" if priority[0] == 0 else "model_order",
            })
            _record_intent_error(plan, "task_limit_exceeded")
            continue
        query_keys.add(query_key)
        executable.append((request, priority))

    before_refs = {item["evidence_ref"] for item in bundle.excerpts}
    outcomes = bundle.execute(
        [request for request, _ in executable], search_resolver=search_resolver
    )
    for (request, priority), outcome in zip(executable, outcomes, strict=True):
        task_evidence_refs = outcome.get("evidence_refs", [])
        omission_reason = outcome.get("omission_reason")
        if not task_evidence_refs and omission_reason == "evidence_budget_exhausted":
            _record_omitted_intent_task(
                plan, request, "evidence_budget", changed_paths,
                omission_reason="evidence_budget_exhausted",
            )
        plan["tasks"].append({
            "hypothesis_id": request["hypothesis_id"],
            "purpose": request["purpose"],
            "task_id": _intent_task_id(request),
            "query": outcome.get("normalized_query"),
            "status": outcome.get("status", "unavailable"),
            "reused": bool(outcome.get("reused")),
            "evidence_refs": task_evidence_refs,
            "omission_reason": omission_reason,
            "missing_id": request.get("missing_id"),
            "followup": bool(request.get("followup")),
            "priority_rank": priority[1],
            "priority_source": "focus_tag" if priority[0] == 0 else "model_order",
        })
        if outcome.get("status") not in {"executed_with_evidence", "executed_no_evidence"}:
            _record_intent_error(plan, outcome.get("status", "unavailable"))
    after_refs = {item["evidence_ref"] for item in bundle.excerpts}
    return after_refs - before_refs
