"""Judgement guards applied to verifier decisions."""
from __future__ import annotations

import json
import re

from src.reviewer.grounded import prompts
from src.reviewer.grounded.evidence import _fallback_missing_reads
from src.reviewer.grounded.intent import (
    MAX_INTENT_TASKS,
    _execute_intent_tasks,
    _intent_assessments,
    _intent_request_rejection,
    _intent_task_id,
    _record_intent_error,
    _task_labels,
)
from src.reviewer.grounded.model_json import _json
from src.reviewer.grounded.state import GroundedRun
from src.reviewer.provider import ProviderError

_DISMISSAL_BASES = {"unreachable", "incorrect", "not_introduced"}


# Wording that concedes the failure happens and only argues it is acceptable.
# The verifier has paired these with a false factual basis ("not_introduced"
# for a documented default), so they override any declared basis.
_STRONG_JUDGEMENT = re.compile(
    r"\b(intended|intentional(ly)?|by design|design choice|documented|self[- ]correcting|benign|"
    r"concedes?|candidate itself|inefficien\w*|performance[- ]only|not a correctness|"
    r"not (a )?(correctness |functional |real )?(bug|defect)( per se)?)\b",
    re.IGNORECASE,
)


# Weaker wording: a judgement only when no factual basis is declared.
_WEAK_JUDGEMENT = re.compile(
    r"\b(minor|harmless|cosmetic|low[- ](impact|severity|risk)|expected behaviou?r|"
    r"acceptable|edge case only|nitpick|style)\b",
    re.IGNORECASE,
)


def _dismissal_rests_on_judgement(item) -> bool:
    """A dismissal needs a factual basis; intent or impact is not one."""
    reason = str(item.get("reason") or "")
    if _STRONG_JUDGEMENT.search(reason):
        return True
    basis = str(item.get("dismissal_basis") or "").strip().lower()
    if basis in _DISMISSAL_BASES:
        return False
    if basis:
        return True  # any other declared basis (intended, low_impact ...) is a judgement
    return bool(_WEAK_JUDGEMENT.search(reason))


# A confirmation that hedges its own causal claim, or whose only failure is on
# malformed input, was a false positive in every case on the six-case dev set
# (12 of 12): these stay in the review queue instead of being published.
_HEDGED_CONFIRMATION = re.compile(
    r"\b(may|might|could|potentially|possibly|theoretically)\b", re.IGNORECASE)


_MALFORMED_ONLY = re.compile(
    r"\b(malformed|truncated|corrupt\w*|short (file|stream|read|segment)|EOF|"
    r"end of (file|input|stream))\b", re.IGNORECASE)


def _speculative_confirmation(item) -> str | None:
    if _HEDGED_CONFIRMATION.search(str(item.get("reason") or "")):
        return "hedged_confirmation"
    if _MALFORMED_ONLY.search(str(item.get("actual_behavior") or "")):
        return "malformed_input_only"
    return None


def request_verdicts(run: GroundedRun):
    """Ask the verifier for decisions, serving its bounded evidence requests."""
    verdicts = {}
    assessment_errors = []
    verify_force_final = False
    verify_budget = max(1, run.verify_rounds)
    for round_index in range(verify_budget):
        prompt = (run.prompt_addendum + "\n" if run.prompt_addendum else "")
        prompt += json.dumps({"candidates": [{"candidate_id": index,
                  "candidate": s.model_dump(mode="json")} for index, s in run.eligible]}, ensure_ascii=False)
        prompt += "\n## Changed diff\n" + run.diff_text + "\n## Frozen repository evidence\n" + run.bundle.render()
        prompt += (
            "\n## Intent hypotheses (untrusted, evidence-linked annotations only)\n"
            + json.dumps(run.intent_plan, ensure_ascii=False)
        )
        if round_index == verify_budget - 1:
            prompt += "\nFinal verification round: mark insufficient evidence needs_context."
        if verify_force_final:
            prompt += (
                "\nPrevious context requests returned no new evidence; "
                "return decisions now."
            )
        try:
            verify_system = prompts.VERIFY_SYSTEM + (
                "\n\n" + prompts.INDEX_ATTRIBUTION_CONTRACT if run.repository_index_enabled else ""
            )
            response = _json(run.provider, verify_system, prompt)
            run.verification_calls += 1
            requested = response.get("context_requests") or []
            missing_requests = response.get("missing_evidence_requests") or []
            if (run.missing_evidence_followup and round_index == 0
                    and isinstance(missing_requests, list) and missing_requests):
                prior_followup = any(
                    task.get("followup") for task in run.intent_plan.get("tasks", [])
                )
                if not prior_followup:
                    if len(missing_requests) > MAX_INTENT_TASKS:
                        overflow = len(missing_requests) - MAX_INTENT_TASKS
                        run.intent_plan["request_overflow_count"] = (
                            run.intent_plan.get("request_overflow_count", 0) + overflow
                        )
                        _record_intent_error(run.intent_plan, "request_limit_exceeded")
                    hypotheses_by_id = {
                        item["id"]: item for item in run.intent_plan["hypotheses"]
                    }
                    bounded_missing = []
                    seen_obligations = set()
                    for request in missing_requests[:MAX_INTENT_TASKS]:
                        if not isinstance(request, dict):
                            run.intent_plan["tasks"].append({
                                "task_id": _intent_task_id(request),
                                "status": "invalid_missing_evidence_request",
                                "evidence_refs": [],
                            })
                            continue
                        hypothesis = hypotheses_by_id.get(request.get("hypothesis_id"))
                        known_ids = {item["id"] for item in hypothesis.get(
                            "missing_evidence", [])} if hypothesis else set()
                        rejection = _intent_request_rejection(
                            request, hypotheses_by_id
                        )
                        missing_id = request.get("missing_id")
                        if not isinstance(missing_id, str) or not missing_id:
                            rejection = rejection or "missing_evidence_id_required"
                        elif missing_id not in known_ids:
                            rejection = rejection or "unknown_missing_evidence_id"
                        obligation_key = (request.get("hypothesis_id"), missing_id)
                        if not rejection and obligation_key in seen_obligations:
                            rejection = "duplicate_missing_evidence_request"
                        if rejection:
                            hypothesis_id, purpose = _task_labels(request)
                            run.intent_plan["tasks"].append({
                                "hypothesis_id": hypothesis_id,
                                "purpose": purpose,
                                "task_id": _intent_task_id(request),
                                "missing_id": request.get("missing_id"),
                                "followup": True,
                                "status": rejection,
                                "evidence_refs": [],
                            })
                        else:
                            seen_obligations.add(obligation_key)
                            bounded_missing.append({**request, "followup": True})
                    ordinary_requests = requested if isinstance(requested, list) else []
                    ordinary_capacity = max(0, MAX_INTENT_TASKS - len(bounded_missing))
                    if len(ordinary_requests) > ordinary_capacity:
                        overflow = len(ordinary_requests) - ordinary_capacity
                        run.intent_plan["request_overflow_count"] = (
                            run.intent_plan.get("request_overflow_count", 0) + overflow
                        )
                        _record_intent_error(run.intent_plan, "request_limit_exceeded")
                    bounded_ordinary = ordinary_requests[:ordinary_capacity]
                    if run.missing_evidence_followup_only:
                        for request in bounded_ordinary:
                            hypothesis_id, purpose = _task_labels(request)
                            run.intent_plan["tasks"].append({
                                "hypothesis_id": hypothesis_id,
                                "purpose": purpose,
                                "task_id": _intent_task_id(request),
                                "status": "not_executed_missing_only_policy",
                                "evidence_refs": [],
                            })
                        executable_requests = bounded_missing
                    else:
                        executable_requests = bounded_missing + bounded_ordinary
                    if executable_requests:
                        newly_added = _execute_intent_tasks(
                            run.bundle, executable_requests, run.intent_plan, run.snapshot,
                            run.intent_query_keys, run.changed_paths,
                            search_resolver=run.search_resolver,
                        )
                        if newly_added:
                            run.intent_plan["status"] = "evidence_collected"
                        else:
                            _record_intent_error(
                                run.intent_plan, "missing_evidence_followup_no_new_evidence"
                            )
                        # The final verification consumes the existing second
                        # round; no provider/request/evidence budget is added.
                        continue
            if (run.missing_evidence_followup and round_index > 0
                    and isinstance(missing_requests, list) and missing_requests):
                if len(missing_requests) > MAX_INTENT_TASKS:
                    run.intent_plan["request_overflow_count"] = (
                        run.intent_plan.get("request_overflow_count", 0)
                        + len(missing_requests) - MAX_INTENT_TASKS
                    )
                    _record_intent_error(run.intent_plan, "request_limit_exceeded")
                _record_intent_error(run.intent_plan, "followup_limit_exceeded")
                for request in missing_requests[:MAX_INTENT_TASKS]:
                    hypothesis_id, purpose = _task_labels(request)
                    run.intent_plan["tasks"].append({
                        "hypothesis_id": hypothesis_id,
                        "purpose": purpose,
                        "task_id": _intent_task_id(request),
                        "missing_id": request.get("missing_id")
                        if isinstance(request, dict) else None,
                        "followup": True,
                        "status": "not_executed_followup_limit",
                        "evidence_refs": [],
                    })
            if not requested or round_index == verify_budget - 1:
                rendered_refs = {item["evidence_ref"] for item in run.bundle.excerpts}
                assessed, errors = _intent_assessments(
                    response.get("intent_assessments"), run.intent_plan,
                    {
                        hypothesis["id"]: {
                            evidence_ref
                            for task in run.intent_plan["tasks"]
                            if task.get("hypothesis_id") == hypothesis["id"]
                            and task.get("status") == "executed_with_evidence"
                            for evidence_ref in task.get("evidence_refs", [])
                            if evidence_ref in rendered_refs
                        }
                        for hypothesis in run.intent_plan["hypotheses"]
                    },
                )
                if assessed:
                    run.intent_plan["assessments"] = assessed
                assessment_errors.extend(errors)
            if (requested and round_index < verify_budget - 1
                    and not verify_force_final
                    and not (run.missing_evidence_followup_only
                             and run.missing_evidence_followup and round_index == 0)):
                if not isinstance(requested, list):
                    raise ValueError("Verification requests must be a list")
                legacy_requests = (
                    run.intent_plan.get("status") == "not_reported"
                    and all(isinstance(request, dict)
                            and "hypothesis_id" not in request for request in requested)
                )
                if legacy_requests:
                    outcomes = run.bundle.execute(requested[:6])
                    _fallback_missing_reads(run.bundle, run.snapshot, outcomes)
                    for outcome in outcomes:
                        outcome["scope"] = "legacy_candidate_verification"
                    if len(requested) > 6:
                        run.bundle.omitted.append({
                            "reason": "request_limit_exceeded",
                            "count": len(requested) - 6,
                        })
                    continue
                if run.intent_plan.get("status") == "not_reported":
                    run.intent_plan["status"] = "partial"
                newly_added = _execute_intent_tasks(
                    run.bundle, requested, run.intent_plan, run.snapshot, run.intent_query_keys,
                    run.changed_paths, search_resolver=run.search_resolver,
                )
                if newly_added:
                    run.intent_plan["status"] = "evidence_collected"
                    continue
                verify_force_final = True
                _record_intent_error(run.intent_plan, "verification_no_new_evidence")
                if round_index < verify_budget - 1:
                    continue
            elif requested:
                request_status = (
                    "not_executed_no_new_evidence" if verify_force_final
                    else "not_executed_budget_exhausted"
                )
                _record_intent_error(run.intent_plan, request_status)
                for request in requested[:MAX_INTENT_TASKS]:
                    hypothesis_id, purpose = _task_labels(request)
                    run.intent_plan["tasks"].append({
                        "hypothesis_id": hypothesis_id,
                        "purpose": purpose,
                        "task_id": _intent_task_id(request),
                        "status": request_status,
                        "evidence_refs": [],
                    })
            items = response.get("items")
            if not isinstance(items, list):
                raise ValueError("Verification response has no items list")
            for item in items:
                index = item.get("candidate_id")
                if isinstance(index, bool) or not isinstance(index, int) or index in verdicts:
                    raise ValueError("Ambiguous verification candidate id")
                if index not in {i for i, _ in run.eligible}:
                    raise ValueError("Unknown verification candidate id")
                verdicts[index] = item
        except (ProviderError, ValueError, TypeError, AttributeError) as exc:
            run.failed = True
            verdicts.clear()
            run.decisions.append({"decision": "unverified", "reason": str(exc), "stage": "behavior_verification"})
            break
        break
    if run.intent_plan["hypotheses"]:
        if assessment_errors:
            for error in assessment_errors:
                _record_intent_error(run.intent_plan, error)
        assessments_by_id = {item["hypothesis_id"]: item for item in run.intent_plan["assessments"]}
        all_assessed = all(
            identifier in assessments_by_id
            and assessments_by_id[identifier]["status"] != "unknown"
            and assessments_by_id[identifier]["evidence_refs"]
            for identifier in (item["id"] for item in run.intent_plan["hypotheses"])
        )
        run.intent_plan["status"] = (
            "assessed" if all_assessed and not run.intent_plan["errors"] else "partial"
        )
    return verdicts
