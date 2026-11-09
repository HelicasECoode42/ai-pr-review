"""Repository-grounded discovery and behavioral verification with auditable candidates."""
from __future__ import annotations

import json

from src.analyzer.context_builder import ReviewContext
from src.analyzer.repository_context import EvidenceBundle, Snapshot
from src.models import (
    Severity,
)
from src.reviewer.criteria import (
    CRITERIA_VERSION,
    blocked_by_witness,
    confirmation_verdicts,
    quote_was_shown,
    valid_citation,
)
from src.reviewer.grounded.critic import (  # noqa: F401
    _apply_grounded_critic,
)
from src.reviewer.grounded.discovery import (  # noqa: F401
    _collect_raw_findings,
    _excerpt,
    _raw_discovery_prompt,
    _raw_findings_payload,
)
from src.reviewer.grounded.evidence import (  # noqa: F401
    _CALL_NAME,
    _CAMEL_SYMBOL,
    _NOT_CALLEES,
    _SERIALIZATION_CLAIM,
    CALLEE_LIMIT,
    CALLER_EVIDENCE_LIMIT,
    SERIALIZATION_FIELDS_LIMIT,
    SERIALIZATION_SEARCH_LIMIT,
    VERIFY_EVIDENCE_EXTRA_TOKENS,
    _add_callee_contracts,
    _add_contract_callers,
    _add_serialization_contract,
    _fallback_missing_reads,
)
from src.reviewer.grounded.intent import (  # noqa: F401
    _INTENT_FOCUS_RANK,
    INTENT_FOCUS_ORDER,
    INTENT_HYPOTHESIS_SCHEMA_VERSION,
    INTENT_PLAN_SCHEMA_VERSION,
    MAX_INTENT_ERROR_KINDS,
    MAX_INTENT_HYPOTHESES,
    MAX_INTENT_OMISSION_RECORDS,
    MAX_INTENT_TASKS,
    MAX_MISSING_EVIDENCE,
    _bounded_python_index_search,
    _execute_intent_tasks,
    _intent_assessments,
    _intent_hypotheses,
    _intent_request_rejection,
    _intent_task_id,
    _missing_evidence_accounting,
    _new_intent_plan,
    _record_intent_error,
    _record_omitted_intent_task,
    _task_labels,
    _task_priority,
)
from src.reviewer.grounded.model_json import (  # noqa: F401
    _EXCERPT_CHARS,
    ModelResponseError,
    _json,
    _parse_model_json,
    _repair_closed_containers,
)
from src.reviewer.grounded.prompts import (  # noqa: F401
    DISCOVERY_SYSTEM,
    INDEX_ATTRIBUTION_CONTRACT,
    RAW_DISCOVERY_SYSTEM,
    VERIFY_SYSTEM,
)
from src.reviewer.grounded.reporting import (  # noqa: F401
    build_grounded_report,
)
from src.reviewer.grounded.result import (  # noqa: F401
    GroundedResult,
)
from src.reviewer.grounded.rule_candidates import (  # noqa: F401
    _AGREES_PATCH_MISSES,
    _MOCK_CLAIM,
    _contradicted_by_rule,
    _rule_candidate_key,
    _rule_rebuttals,
    _suggestion_rule_candidate_key,
)
from src.reviewer.grounded.trace import (  # noqa: F401
    _raw_candidate_inventory,
    _review_trace,
    _trace_hash,
    _trace_identity,
    _unit_coverage,
)
from src.reviewer.grounded.verification import (  # noqa: F401
    _DISMISSAL_BASES,
    _HEDGED_CONFIRMATION,
    _MALFORMED_ONLY,
    _STRONG_JUDGEMENT,
    _WEAK_JUDGEMENT,
    _dismissal_rests_on_judgement,
    _speculative_confirmation,
)
from src.reviewer.model_payload import parse_model_payload
from src.reviewer.prompt import build_user_prompt
from src.reviewer.provider import ProviderError

# Aliases kept for existing importers; the implementations live in criteria.py.
_quote_was_shown = quote_was_shown


_valid_citation = valid_citation


def review_grounded(pr, files, provider, snapshot: Snapshot, context: ReviewContext,
                    max_suggestions=20, language="en",
                    evidence_tokens=6000, verify_behavior=True, max_suggestions_per_file=5,
                    contract_domains: dict | None = None,
                    discovery_rounds: int = 3, verify_rounds: int = 2,
                    prompt_addendum: str = "",
                    missing_evidence_followup: bool = False,
                    missing_evidence_followup_only: bool = False,
                    raw_discovery: bool = False,
                    repository_index_enabled: bool = False,
                    critic_provider=None, critic_batch_cap: int = 8,
                    critic_budget=None) -> GroundedResult:
    """Ground discovery and verification in a frozen snapshot.

    Publication here is decided by proof, not by the model's self-reported
    confidence: a candidate needs a validated behaviour chain and a HEAD source
    citation. `min_comment_confidence` therefore does not apply to this path,
    and it is no longer accepted as a parameter that suggested it did.
    """
    bundle = EvidenceBundle(snapshot, budget=evidence_tokens)
    changed_paths = {file.filename for file in files}
    bundle.follow(files)
    # Counts a confirmed candidate whose witness was rejected. Initialised before
    # any branch: a run with no eligible candidates must still report a number
    # rather than fail while building the audit.
    witness_blocked = 0
    discovery_calls = 0
    payload = None
    deferred_candidate_payload = None
    discovery_trace = []
    available_sources = {f"diff:{file.filename}" for file in files}
    if getattr(pr, "title", ""):
        available_sources.add("pr:title")
    if getattr(pr, "body", ""):
        available_sources.add("pr:description")
    intent_plan = None
    intent_query_keys = set()
    search_resolver = (
        _bounded_python_index_search(snapshot, files) if repository_index_enabled else None
    )
    force_final_reason = None
    # Round budgets are parameters because each round re-sends the whole prompt
    # and the evidence bundle: on a small unit one round each is enough, and the
    # saved re-sends are the difference between 62k and ~20k tokens per unit.
    # Raw discovery is the single-call lane: no hypotheses, no tool loop, one
    # minimal prompt. It is an experiment flag; verification/criteria still run.
    discovery_budget = 0 if raw_discovery else max(1, discovery_rounds)
    rule_rebuttals: dict[str, set[str]] = {}
    host_rule_candidate_keys = set()
    # The diff text the model sees. In the raw lane discovery, verification and
    # the citation check all use the same full diff, so a line discovery quoted
    # is never judged "not shown" because the budgeted context clipped it.
    is_unit_context = getattr(getattr(context, "budgets", None), "scope", None) == (
        "build_unit_context"
    )
    raw_discovery_scope = (
        "unit_context" if is_unit_context else "full_pr_diff"
    ) if raw_discovery else None
    if raw_discovery and is_unit_context:
        # Unit review has already selected exact bounded fragments. Rebuilding
        # prompts from ChangedFile.patch here would silently expand each unit
        # back to the full file diff and invalidate its coverage accounting.
        diff_text = "\n".join([
            f"# Pull request {getattr(pr, 'repo', '')}#{getattr(pr, 'number', '')}",
            f"Title: {getattr(pr, 'title', '') or ''}",
            "## Description",
            str(getattr(pr, "body", None) or "(empty)"),
            "## Unit diff",
            context.text,
        ])
    else:
        diff_text = _raw_discovery_prompt(pr, files) if raw_discovery else context.text
    if raw_discovery:
        # Two samples with different inputs, unioned: the diff-only prompt keeps
        # local defects undiluted, diff+evidence finds cross-file ones. Their
        # candidate sets overlap only partly, so the union raises discovery.
        # Deterministic test-quality candidates join the same pool; every one
        # still has to pass verification and criteria to be published.
        prefix = (prompt_addendum + "\n" if prompt_addendum else "") + diff_text
        from src.reviewer.discovery_lenses import LENS_DISCOVERY_SYSTEM, lens_facts
        evidence_text = "\n## Frozen repository evidence\n" + bundle.render()
        # The third sample is a checklist pass over the kinds of defect the two
        # generic samples missed on the dev set, with deterministic facts
        # (declared runtimes, untested changed symbols, callers, changed docs).
        samples = (("diff", RAW_DISCOVERY_SYSTEM, prefix),
                   ("diff+evidence", RAW_DISCOVERY_SYSTEM, prefix + evidence_text),
                   ("lenses", LENS_DISCOVERY_SYSTEM,
                    prefix + "\n" + lens_facts(snapshot, files) + evidence_text))
        pool = []
        sample_failures = []
        for origin, system, sample_prompt in samples:
            # One unparseable sample must not discard the others already collected.
            try:
                response, failure = _json(provider, system, sample_prompt), None
            except ValueError as exc:
                response, failure = None, exc
            discovery_calls += 1
            if failure is not None:
                sample_failures.append(failure)
                discovery_trace.append({
                    "round": discovery_calls - 1, "mode": "raw", "input": origin,
                    "status": "failed", "error_type": getattr(failure, "error_type", "ValueError"),
                    "error": str(failure)[:300],
                    "raw_excerpt": getattr(failure, "raw_excerpt", ""),
                })
                continue
            parse_errors = []
            returned = _collect_raw_findings(response, origin, parse_errors)
            pool.extend(returned)
            discovery_trace.append({
                "round": discovery_calls - 1, "mode": "raw", "input": origin, "status": "ok",
                "response_format_notice": response.get("response_format_notice"),
                "suggestions": len(returned), "parse_errors": parse_errors,
            })
        if len(sample_failures) == len(samples):
            # Nothing was collected: surface the failure as the lane always did.
            raise sample_failures[-1]
        from src.analyzer.test_quality_lane import collect_test_quality_candidates
        from src.reviewer.candidate_dedupe import dedupe_candidates
        rule_candidates = collect_test_quality_candidates(snapshot, files)
        host_rule_candidate_keys = {
            key for item in rule_candidates
            if (key := _rule_candidate_key(
                item.get("file"), item.get("line"), item.get("title"),
                item.get("reason"), item.get("evidence"),
            )) is not None
        }
        rule_rebuttals = _rule_rebuttals(rule_candidates)
        for rule_item in rule_candidates:
            target = rule_item.get("target")
            if target:
                # Show what the real (un-mocked) function does, so the
                # consequence of a missed patch or a wrong mock shape is visible.
                bundle.add(target[0], target[1], kind="candidate_rule_target", max_lines=40)
        pool.extend({**item, "origin": "test_quality_rules",
                     "raw_candidate_id": f"test_quality_rules:{index}"}
                    for index, item in enumerate(rule_candidates))
        union, merges = dedupe_candidates(pool)
        discovery_trace.append({
            "round": discovery_calls, "mode": "test_quality_rules",
            "suggestions": len(rule_candidates),
        })
        discovery_trace.append({
            "mode": "dedupe", "pool": len(pool), "kept": len(union), "merges": merges,
            # Pool size per origin, so model and rule candidates are counted separately.
            "pool_by_origin": {origin: sum(item.get("origin") == origin for item in pool)
                               for origin, _, _ in samples + (("test_quality_rules", "", ""),)},
            "parse_error_count": sum(len(entry.get("parse_errors", [])) for entry in discovery_trace
                                if entry.get("mode") == "raw"),
            "doubts": [{"file": item.get("file"), "line": item.get("line"),
                        "doubts": str(item.get("doubts"))[:300]}
                       for item in union if str(item.get("doubts") or "").strip()],
        })
        payload = parse_model_payload(json.dumps(
            _raw_findings_payload({"findings": union}, max(max_suggestions, len(union))),
            ensure_ascii=False,
        ))
    for round_index in range(discovery_budget):
        available_sources.update(item["evidence_ref"] for item in bundle.excerpts)
        # Cross-file evidence changes the constraints; the prompt module owns
        # that choice so this path never rewrites prompt text by matching strings.
        # Order matters for provider prefix caching: everything identical across
        # this run's calls (knowledge, invariants) goes first, then the diff, then
        # evidence. The addendum was previously dropped on this path entirely.
        prompt = (prompt_addendum + "\n" if prompt_addendum else "") + build_user_prompt(
            context.text, max_suggestions, language, cross_file_evidence=True
        )
        prompt += "\n## Available intent source references\n" + json.dumps(
            sorted(available_sources), ensure_ascii=False
        )
        prompt += "\n## Frozen repository evidence\n" + bundle.render()
        if intent_plan is not None:
            prompt += "\n## Current intent hypothesis plan\n" + json.dumps(
                intent_plan, ensure_ascii=False
            )
        if force_final_reason:
            prompt += (
                "\nPrevious tool requests added no new evidence (" + force_final_reason
                + "). Do not repeat them; return final candidates and hypothesis updates now."
            )
        else:
            prompt += (
                "\nIf context is insufficient, return bound context_requests "
                "before final candidates."
            )
        if round_index == discovery_budget - 1 or force_final_reason:
            prompt += "\nTool-round budget exhausted. Return your final candidates; do not assume missing evidence is safe."
        response = None
        requests: list = []
        payload = None
        attempt_prompt = prompt
        for attempt in (1, 2):
            try:
                response = _json(provider, DISCOVERY_SYSTEM, attempt_prompt)
                discovery_calls += 1
                requests = response.get("context_requests") or []
                if not isinstance(requests, list):
                    raise ValueError("context_requests must be a list")
                if requests and "suggestions" in response:
                    # Some discovery responses contain provisional candidates
                    # alongside tool requests. Keep them only as unverified
                    # fallback material in case a later, budgeted round cannot run.
                    try:
                        deferred_candidate_payload = parse_model_payload(
                            json.dumps(response, ensure_ascii=False)
                        )
                    except ValueError:
                        deferred_candidate_payload = None
                if "intent_hypotheses" in response:
                    hypotheses, errors = _intent_hypotheses(
                        response.get("intent_hypotheses"), available_sources
                    )
                    if intent_plan is None:
                        intent_plan = _new_intent_plan("planning")
                    prior = {item["id"]: item for item in intent_plan["hypotheses"]}
                    current = []
                    for hypothesis in hypotheses:
                        previous = prior.get(hypothesis["id"])
                        revision = previous.get("revision", 0) + 1 if previous else 1
                        hypothesis["revision"] = revision
                        if previous and any(previous.get(key) != hypothesis.get(key) for key in (
                            "goal", "constraints", "risk_hypotheses", "unknowns",
                            "source_refs", "focus_tags",
                        )):
                            intent_plan["hypothesis_history"].append(dict(previous))
                        current.append(hypothesis)
                    if current:
                        preserved = [item for item in intent_plan["hypotheses"]
                                     if item["id"] not in {hyp["id"] for hyp in current}]
                        intent_plan["hypotheses"] = preserved + current
                    for error in errors:
                        _record_intent_error(intent_plan, error)
                if not requests:
                    payload = parse_model_payload(json.dumps(response))
                break
            except ValueError:
                # A garbled answer must not discard the whole review: one
                # recorded retry is far cheaper than losing the run entirely.
                if attempt == 2:
                    raise
                discovery_trace.append({"round": round_index, "context_requests": [],
                                        "context_request_count": 0,
                                        "retry_after_unparseable_response": True})
                attempt_prompt = prompt + (
                    "\nYour previous answer could not be parsed. Return only the JSON "
                    "object described above, with no prose before or after it."
                )
            except ProviderError as exc:
                if deferred_candidate_payload is None:
                    raise
                payload = deferred_candidate_payload
                if intent_plan is None:
                    intent_plan = _new_intent_plan("partial")
                _record_intent_error(intent_plan, f"discovery_stopped:{exc.kind}")
                intent_plan["status"] = "partial"
                discovery_trace.append({
                    "round": round_index,
                    "context_request_count": 0,
                    "stopped_before_response": exc.kind,
                    "retained_provisional_candidates": len(
                        deferred_candidate_payload.suggestions
                    ),
                })
                response = {}
                requests = []
                break
        discovery_trace.append({"round": round_index,
                                "context_request_count": len(requests),
                                "response_format_notice": response.get("response_format_notice"),
                                "suggestions": response.get("suggestions", [])})
        if requests and round_index < discovery_budget - 1 and not force_final_reason:
            if intent_plan is None:
                intent_plan = _new_intent_plan("partial")
                _record_intent_error(intent_plan, "unbound_task")
            newly_added = _execute_intent_tasks(
                bundle, requests, intent_plan, snapshot, intent_query_keys, changed_paths,
                search_resolver=search_resolver,
            )
            if not newly_added:
                force_final_reason = "no_new_evidence"
            intent_plan["status"] = "evidence_collected" if newly_added else "partial"
            continue
        if requests:
            if intent_plan is None:
                intent_plan = _new_intent_plan("partial")
            request_status = (
                "not_executed_no_new_evidence" if force_final_reason
                else "not_executed_budget_exhausted"
            )
            _record_intent_error(intent_plan, request_status)
            overflow = max(0, len(requests) - MAX_INTENT_TASKS)
            if overflow:
                intent_plan["request_overflow_count"] = (
                    intent_plan.get("request_overflow_count", 0) + overflow
                )
            for request in requests[:MAX_INTENT_TASKS]:
                hypothesis_id, purpose = _task_labels(request)
                intent_plan["tasks"].append({
                    "hypothesis_id": hypothesis_id,
                    "purpose": purpose,
                    "task_id": _intent_task_id(request),
                    "status": request_status,
                    "evidence_refs": [],
                })
            if force_final_reason and round_index < discovery_budget - 1:
                continue
        if payload is None:
            payload = parse_model_payload(json.dumps(response))
        break
    if payload is None:
        raise ValueError("Discovery did not produce final candidates")
    if intent_plan is None:
        intent_plan = _new_intent_plan("not_reported")
        intent_plan["errors"] = ["intent_plan_not_reported"]
        intent_plan["error_counts"] = {"intent_plan_not_reported": 1}
    elif intent_plan["errors"]:
        intent_plan["status"] = "partial"

    candidates, pending, kept, decisions = [], [], [], []
    seen = set()
    eligible = []
    host_rule_candidate_ids: set[int] = set()
    paths = {f.filename: f for f in files}
    for index, suggestion in enumerate(payload.suggestions):
        if _suggestion_rule_candidate_key(suggestion) in host_rule_candidate_keys:
            host_rule_candidate_ids.add(index)
        identity = (suggestion.file_path, suggestion.line, suggestion.title.casefold())
        item = {"candidate_id": index, "suggestion": suggestion.model_dump(mode="json")}
        if identity in seen:
            item.update(status="filtered", reason="duplicate_identity")
        elif suggestion.file_path not in paths:
            item.update(status="needs_context", reason="anchor_not_in_changed_files")
            pending.append(suggestion.model_copy(update={"source": "ai_needs_context"}))
        elif len(eligible) >= max_suggestions:
            item.update(status="needs_context", reason="candidate_verification_budget")
            pending.append(suggestion.model_copy(update={"source": "ai_needs_context"}))
        else:
            source = snapshot.read(suggestion.file_path)
            if not source or not suggestion.line or not 1 <= suggestion.line <= len(source.splitlines()):
                item.update(status="needs_context", reason="invalid_head_anchor")
                pending.append(suggestion.model_copy(update={"source": "ai_needs_context"}))
            else:
                item.update(status="awaiting_verification")
                eligible.append((index, suggestion))
                # Candidate-specific complete definition before behavioral verification.
                bundle.add(suggestion.file_path, suggestion.line, kind="candidate_definition")
                # One hop along the contract: where else is this symbol used?
                _add_contract_callers(bundle, snapshot, files, suggestion)
                _add_serialization_contract(bundle, snapshot, suggestion)
                if raw_discovery:
                    _add_callee_contracts(bundle, snapshot, suggestion)
        seen.add(identity); candidates.append(item)

    verification_calls, failed = 0, False
    kept_ids: list[int] = []
    duplicate_of: dict[int, int | None] = {}
    # Verification gets its own headroom: candidate definitions, callers and
    # requested reads were being dropped as evidence_budget_exhausted because
    # discovery's initial windows had already filled the shared budget.
    if raw_discovery:
        bundle.budget += VERIFY_EVIDENCE_EXTRA_TOKENS
    if eligible and verify_behavior:
        verdicts = {}
        assessment_errors = []
        verify_force_final = False
        verify_budget = max(1, verify_rounds)
        for round_index in range(verify_budget):
            prompt = (prompt_addendum + "\n" if prompt_addendum else "")
            prompt += json.dumps({"candidates": [{"candidate_id": index,
                      "candidate": s.model_dump(mode="json")} for index, s in eligible]}, ensure_ascii=False)
            prompt += "\n## Changed diff\n" + diff_text + "\n## Frozen repository evidence\n" + bundle.render()
            prompt += (
                "\n## Intent hypotheses (untrusted, evidence-linked annotations only)\n"
                + json.dumps(intent_plan, ensure_ascii=False)
            )
            if round_index == verify_budget - 1:
                prompt += "\nFinal verification round: mark insufficient evidence needs_context."
            if verify_force_final:
                prompt += (
                    "\nPrevious context requests returned no new evidence; "
                    "return decisions now."
                )
            try:
                verify_system = VERIFY_SYSTEM + (
                    "\n\n" + INDEX_ATTRIBUTION_CONTRACT if repository_index_enabled else ""
                )
                response = _json(provider, verify_system, prompt)
                verification_calls += 1
                requested = response.get("context_requests") or []
                missing_requests = response.get("missing_evidence_requests") or []
                if (missing_evidence_followup and round_index == 0
                        and isinstance(missing_requests, list) and missing_requests):
                    prior_followup = any(
                        task.get("followup") for task in intent_plan.get("tasks", [])
                    )
                    if not prior_followup:
                        if len(missing_requests) > MAX_INTENT_TASKS:
                            overflow = len(missing_requests) - MAX_INTENT_TASKS
                            intent_plan["request_overflow_count"] = (
                                intent_plan.get("request_overflow_count", 0) + overflow
                            )
                            _record_intent_error(intent_plan, "request_limit_exceeded")
                        hypotheses_by_id = {
                            item["id"]: item for item in intent_plan["hypotheses"]
                        }
                        bounded_missing = []
                        seen_obligations = set()
                        for request in missing_requests[:MAX_INTENT_TASKS]:
                            if not isinstance(request, dict):
                                intent_plan["tasks"].append({
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
                                intent_plan["tasks"].append({
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
                            intent_plan["request_overflow_count"] = (
                                intent_plan.get("request_overflow_count", 0) + overflow
                            )
                            _record_intent_error(intent_plan, "request_limit_exceeded")
                        bounded_ordinary = ordinary_requests[:ordinary_capacity]
                        if missing_evidence_followup_only:
                            for request in bounded_ordinary:
                                hypothesis_id, purpose = _task_labels(request)
                                intent_plan["tasks"].append({
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
                                bundle, executable_requests, intent_plan, snapshot,
                                intent_query_keys, changed_paths,
                                search_resolver=search_resolver,
                            )
                            if newly_added:
                                intent_plan["status"] = "evidence_collected"
                            else:
                                _record_intent_error(
                                    intent_plan, "missing_evidence_followup_no_new_evidence"
                                )
                            # The final verification consumes the existing second
                            # round; no provider/request/evidence budget is added.
                            continue
                if (missing_evidence_followup and round_index > 0
                        and isinstance(missing_requests, list) and missing_requests):
                    if len(missing_requests) > MAX_INTENT_TASKS:
                        intent_plan["request_overflow_count"] = (
                            intent_plan.get("request_overflow_count", 0)
                            + len(missing_requests) - MAX_INTENT_TASKS
                        )
                        _record_intent_error(intent_plan, "request_limit_exceeded")
                    _record_intent_error(intent_plan, "followup_limit_exceeded")
                    for request in missing_requests[:MAX_INTENT_TASKS]:
                        hypothesis_id, purpose = _task_labels(request)
                        intent_plan["tasks"].append({
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
                    rendered_refs = {item["evidence_ref"] for item in bundle.excerpts}
                    assessed, errors = _intent_assessments(
                        response.get("intent_assessments"), intent_plan,
                        {
                            hypothesis["id"]: {
                                evidence_ref
                                for task in intent_plan["tasks"]
                                if task.get("hypothesis_id") == hypothesis["id"]
                                and task.get("status") == "executed_with_evidence"
                                for evidence_ref in task.get("evidence_refs", [])
                                if evidence_ref in rendered_refs
                            }
                            for hypothesis in intent_plan["hypotheses"]
                        },
                    )
                    if assessed:
                        intent_plan["assessments"] = assessed
                    assessment_errors.extend(errors)
                if (requested and round_index < verify_budget - 1
                        and not verify_force_final
                        and not (missing_evidence_followup_only
                                 and missing_evidence_followup and round_index == 0)):
                    if not isinstance(requested, list):
                        raise ValueError("Verification requests must be a list")
                    legacy_requests = (
                        intent_plan.get("status") == "not_reported"
                        and all(isinstance(request, dict)
                                and "hypothesis_id" not in request for request in requested)
                    )
                    if legacy_requests:
                        outcomes = bundle.execute(requested[:6])
                        _fallback_missing_reads(bundle, snapshot, outcomes)
                        for outcome in outcomes:
                            outcome["scope"] = "legacy_candidate_verification"
                        if len(requested) > 6:
                            bundle.omitted.append({
                                "reason": "request_limit_exceeded",
                                "count": len(requested) - 6,
                            })
                        continue
                    if intent_plan.get("status") == "not_reported":
                        intent_plan["status"] = "partial"
                    newly_added = _execute_intent_tasks(
                        bundle, requested, intent_plan, snapshot, intent_query_keys,
                        changed_paths, search_resolver=search_resolver,
                    )
                    if newly_added:
                        intent_plan["status"] = "evidence_collected"
                        continue
                    verify_force_final = True
                    _record_intent_error(intent_plan, "verification_no_new_evidence")
                    if round_index < verify_budget - 1:
                        continue
                elif requested:
                    request_status = (
                        "not_executed_no_new_evidence" if verify_force_final
                        else "not_executed_budget_exhausted"
                    )
                    _record_intent_error(intent_plan, request_status)
                    for request in requested[:MAX_INTENT_TASKS]:
                        hypothesis_id, purpose = _task_labels(request)
                        intent_plan["tasks"].append({
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
                    if index not in {i for i, _ in eligible}:
                        raise ValueError("Unknown verification candidate id")
                    verdicts[index] = item
            except (ProviderError, ValueError, TypeError, AttributeError) as exc:
                failed = True
                verdicts.clear()
                decisions.append({"decision": "unverified", "reason": str(exc), "stage": "behavior_verification"})
                break
            break
        if intent_plan["hypotheses"]:
            if assessment_errors:
                for error in assessment_errors:
                    _record_intent_error(intent_plan, error)
            assessments_by_id = {item["hypothesis_id"]: item for item in intent_plan["assessments"]}
            all_assessed = all(
                identifier in assessments_by_id
                and assessments_by_id[identifier]["status"] != "unknown"
                and assessments_by_id[identifier]["evidence_refs"]
                for identifier in (item["id"] for item in intent_plan["hypotheses"])
            )
            intent_plan["status"] = (
                "assessed" if all_assessed and not intent_plan["errors"] else "partial"
            )
        for index, suggestion in eligible:
            item = verdicts.get(index, {"candidate_id": index, "decision": "needs_context", "reason": "No verdict returned"})
            decision = item.get("decision")
            item = {**item, "verifier_decision": decision}
            evidence, counter = item.get("evidence") or [], item.get("counter_evidence") or []
            call_path = item.get("call_path")
            if isinstance(call_path, str) and call_path.strip():
                item = {**item, "call_path_original": call_path, "call_path": [call_path]}
            confidence = item.get("confidence", 0)
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                confidence = 0
            # The named filter chain replaces the old inline conjunction, so
            # every rejection carries a reason and can be reported (H7).
            filters = confirmation_verdicts(
                decision=decision, item=item, evidence=evidence, counter=counter,
                snapshot=snapshot, bundle=bundle, files=files, sent_text=diff_text,
                contract_domains=contract_domains,
                repository_index_enabled=(repository_index_enabled
                                          and index not in host_rule_candidate_ids),
                anchor_path=suggestion.file_path, anchor_line=suggestion.line,
            )
            by_name = {verdict.name: verdict for verdict in filters}
            validated = by_name["source_provenance"].passed
            counter_valid = by_name["counter_evidence"].passed
            witness_block = blocked_by_witness(filters)
            if witness_block is not None:
                witness_blocked += 1
            contradicted = (
                _contradicted_by_rule(
                    suggestion, rule_rebuttals,
                    host_rule_fact=index in host_rule_candidate_ids,
                ) if decision == "confirmed" else None
            )
            is_rule_fact = index in host_rule_candidate_ids
            if is_rule_fact:
                # A rule finding is an AST fact, not a runtime-failure claim. The
                # verifier judged these by "does the test fail?" and dismissed
                # true gold items (local import binding, magic indexes) as style.
                # The fact stands; the verifier's view is kept for the audit.
                item = {**item, "verifier_decision": decision, "decision": "confirmed",
                        "validation_reason": "deterministic_rule_fact"}
                decision = "confirmed"
            elif (raw_discovery and decision == "confirmed"
                  and (speculative := _speculative_confirmation(item)) is not None):
                item = {**item, "decision": "needs_context", "validation_reason": speculative}
                decision = "needs_context"
            elif contradicted is not None:
                # Deterministic fact beats model reasoning: the claimed mock does
                # not reach this local binding, so the stated mechanism is wrong.
                item = {**item, "decision": "needs_context",
                        "validation_reason": "contradicted_by_deterministic_rule",
                        "contradicted_name": contradicted}
                decision = "needs_context"
            elif decision == "confirmed" and witness_block is not None:
                item = {**item, "decision": "needs_context",
                        "validation_reason": f"witness_eval: {witness_block.reason}"}
                decision = "needs_context"
            elif decision == "confirmed" and not (validated and by_name["chain_completeness"].passed):
                item = {**item, "decision": "needs_context",
                        "validation_reason": "insufficient_behavior_chain_or_source_evidence"}
                decision = "needs_context"
            elif decision == "confirmed" and repository_index_enabled and not is_rule_fact:
                item = {**item, "decision": "needs_context", "attribution": "unknown",
                        "validation_reason": "causal_attribution_unproven"}
                decision = "needs_context"
            elif decision == "dismissed" and _dismissal_rests_on_judgement(item):
                # An "intended / minor / benign" verdict concedes the failure can
                # happen: it may lower severity, never remove the finding.
                item = {**item, "decision": "needs_context",
                        "validation_reason": "dismissal_rests_on_intent_or_impact"}
                decision = "needs_context"
            elif decision == "dismissed" and not counter_valid:
                item = {**item, "decision": "needs_context", "validation_reason": "dismissal_has_no_valid_counter_evidence"}
                decision = "needs_context"
            elif decision not in {"confirmed", "dismissed", "needs_context"}:
                item = {**item, "decision": "needs_context", "validation_reason": "invalid_decision"}
                decision = "needs_context"
            item = {**item, "verification_method": "source_reasoning", "tests_executed": False,
                    "source_evidence_valid": validated, "counter_evidence_valid": counter_valid,
                    "criteria_version": CRITERIA_VERSION,
                    "filter_verdicts": [verdict.as_dict() for verdict in filters]}
            if repository_index_enabled and not is_rule_fact:
                item["attribution"] = "unknown"
                difference = by_name.get("behavior_difference_support")
                item["behavior_difference_supported"] = bool(difference and difference.passed)
            decisions.append(item)
            candidates[index].update(
                status=decision,
                reason=item.get("reason", ""),
                verifier_decision=item.get("verifier_decision", decision),
                validation_reason=item.get("validation_reason"),
            )
            if decision == "confirmed" and is_rule_fact:
                kept_ids.append(index)
                duplicate_of[index] = None
                kept.append(suggestion.model_copy(update={
                    "source": "rule_test_quality", "severity": Severity.LOW,
                    "confidence": max(suggestion.confidence, 0.7),
                }))
            elif decision == "confirmed":
                reason = (suggestion.reason + "\nTrigger: " + item["trigger"] + "\nExpected: " + item["expected_behavior"]
                          + "\nActual: " + item["actual_behavior"] + "\nPR change: " + item["introduced_by"])
                kept_ids.append(index)
                duplicate_of[index] = item.get("duplicate_of")
                kept.append(suggestion.model_copy(update={"source": "ai_behavior_verified", "confidence": confidence,
                            "reason": reason, "evidence": [c["quote"] for c in evidence],
                            "failure_scenario": item["trigger"] + " -> " + item["actual_behavior"]}))
            elif decision == "needs_context":
                pending.append(suggestion.model_copy(update={"source": "ai_needs_context"}))
        # The verifier sees every candidate at once, so it can say which ones
        # share a root cause; a confirmed duplicate of another confirmed
        # candidate is folded into it instead of becoming a second comment.
        # Two guards against over-grouping, which folded distinct gold findings
        # into "the test mocks the function under test": a rule finding is a
        # separate structural fact and is never folded, and a folded model
        # candidate stays visible in the pending queue instead of disappearing.
        confirmed = set(kept_ids)
        folded = set()
        for index, suggestion in zip(kept_ids, kept):
            target = duplicate_of.get(index)
            if index in host_rule_candidate_ids:
                continue
            if (isinstance(target, int) and not isinstance(target, bool) and target != index
                    and target in confirmed and target not in folded):
                folded.add(index)
                candidates[index].update(status="needs_context",
                                         reason="duplicate_of_confirmed_candidate",
                                         duplicate_of=target)
                pending.append(suggestion.model_copy(update={"source": "ai_duplicate_of_confirmed"}))
        survivors = [(index, suggestion) for index, suggestion in zip(kept_ids, kept)
                     if index not in folded]
        kept_ids = [index for index, _ in survivors]
        kept = [suggestion for _, suggestion in survivors]
    else:
        kept = [s for _, s in eligible]
        kept_ids = [index for index, _ in eligible]
        for index, _ in eligible:
            candidates[index].update(status="unverified", reason="behavior_verification_disabled")
    published = []
    published_ids = []
    counts = {}
    for candidate_index, suggestion in zip(kept_ids, kept):
        # Deterministic rule findings are separate structural facts; they do
        # not compete with model findings for the per-file comment budget.
        if candidate_index in host_rule_candidate_ids:
            published.append(suggestion)
            published_ids.append(candidate_index)
            continue
        count = counts.get(suggestion.file_path, 0)
        if count >= max_suggestions_per_file:
            pending.append(suggestion.model_copy(update={"source": "ai_publication_deferred"}))
            for item in candidates:
                if item["suggestion"]["file_path"] == suggestion.file_path and item["suggestion"]["title"] == suggestion.title:
                    item.update(status="needs_context", reason="per_file_cap",
                                publication="deferred", publication_reason="per_file_cap")
            continue
        counts[suggestion.file_path] = count + 1
        published.append(suggestion)
        published_ids.append(candidate_index)
    kept = published
    kept_ids = published_ids
    critic_context = {
        "excerpts": bundle.excerpts,
        "host_rule_candidate_ids": sorted(host_rule_candidate_ids),
    }
    if critic_provider is not None:
        _apply_grounded_critic(
            (candidates, pending, kept_ids, kept, decisions, critic_context),
            critic_provider, batch_cap=critic_batch_cap,
            repository_index_enabled=repository_index_enabled,
            critic_budget=critic_budget,
        )
    audit = bundle.audit()
    if "grounded_critic" in critic_context:
        audit["grounded_critic"] = critic_context["grounded_critic"]
    audit["intent_plan"] = intent_plan
    audit["repository_index_enabled"] = bool(repository_index_enabled)
    audit["symbol_index_schema"] = (
        "python-binding-v1" if repository_index_enabled else None
    )
    audit["missing_evidence_followup_enabled"] = bool(missing_evidence_followup)
    if raw_discovery:
        audit["raw_discovery_scope"] = raw_discovery_scope
    audit["witness_blocked"] = witness_blocked
    # Impact closure: was the callers' side of every change actually visible? Hunk
    # counting says how many lines were shown; this says how much of the change's
    # influence surface was, which is what decides whether a defect is findable.
    try:
        from src.analyzer.diff_parser import parse_file_hunks
        from src.analyzer.impact_closure import build_impact_closure, impact_coverage

        hunks_by_file = {file.filename: parse_file_hunks(file) for file in files}
        closure = build_impact_closure(files, hunks_by_file, snapshot)
        audit["impact_closure"] = {**closure.summary(), **impact_coverage(closure, context.text)}
    except Exception as exc:  # a metric must never take a review down
        audit["impact_closure_error"] = f"{type(exc).__name__}: {exc}"
    audit["discovery_trace"] = discovery_trace
    audit["discovery_mode"] = "raw" if raw_discovery else "intent"
    if raw_discovery:
        audit["raw_discovery_candidates"] = _raw_candidate_inventory(candidates)
    return GroundedResult(payload.summary, kept, pending, candidates, decisions, audit,
                          discovery_calls, verification_calls, failed,
                          evidence_budget=bundle.budget_report,
                          unit_coverage=_unit_coverage(snapshot, files, context, audit))
