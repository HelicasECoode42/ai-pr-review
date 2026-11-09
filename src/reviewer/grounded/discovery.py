"""Raw discovery: prompts and per-item validation of model findings."""
from __future__ import annotations

import json

from src.reviewer.grounded import prompts
from src.reviewer.grounded.intent import (
    MAX_INTENT_TASKS,
    _execute_intent_tasks,
    _intent_hypotheses,
    _intent_task_id,
    _new_intent_plan,
    _record_intent_error,
    _task_labels,
)
from src.reviewer.grounded.model_json import _json
from src.reviewer.grounded.rule_candidates import _rule_candidate_key, _rule_rebuttals
from src.reviewer.grounded.state import GroundedRun
from src.reviewer.model_payload import parse_model_payload
from src.reviewer.prompt import build_user_prompt
from src.reviewer.provider import ProviderError


def _raw_discovery_prompt(pr, files):
    """PR metadata plus every file's full patch, matching the raw-model baseline.

    The raw lane deliberately bypasses `context.text`: its patch budget clips and
    reorders hunks, and on CrabTrap that clipped form cut candidates from 6-9 to
    1-5 with the same model and evidence. No budget is applied here.
    """
    parts = [
        f"# Pull request {getattr(pr, 'repo', '')}#{getattr(pr, 'number', '')}",
        f"Title: {getattr(pr, 'title', '') or ''}",
        "## Description",
        str(getattr(pr, "body", None) or "(empty)"),
        "## Diff",
    ]
    for file in files:
        if not file.patch:
            continue
        old = file.previous_filename or file.filename
        parts.append(f"--- a/{old}\n+++ b/{file.filename}\n{file.patch}")
    return "\n".join(parts)


def _excerpt(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)[:200]


def _collect_raw_findings(response, origin, parse_errors) -> list[dict]:
    """Keep the usable findings of one discovery sample; record each other one.

    Only what the pool and verifier read is required: a file path (the anchor
    the verifier starts from) and an integer line. A missing title or reason is
    defaulted downstream, so it is not a reason to drop a finding. The id is the
    sample name plus the item's position in the model's own list, so the same
    response always yields the same ids.
    """
    findings = response.get("findings", []) if isinstance(response, dict) else []
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        parse_errors.append({"origin": origin, "index": None,
                             "reason": "findings_not_a_list", "excerpt": _excerpt(findings)})
        return []
    items = []
    for index, item in enumerate(findings):
        line = item.get("line") if isinstance(item, dict) else None
        if not isinstance(item, dict):
            reason = "not_an_object"
        elif not isinstance(item.get("file") or item.get("file_path"), str):
            reason = "missing_file"
        elif isinstance(line, bool) or not (
                isinstance(line, int) or (isinstance(line, str) and line.strip().isdigit())):
            reason = "line_not_integer"
        else:
            reason = None
        if reason:
            parse_errors.append({"origin": origin, "index": index,
                                 "reason": reason, "excerpt": _excerpt(item)})
            continue
        line = item["line"]
        items.append({**item, "line": int(line) if isinstance(line, str) else line,
                      "origin": origin, "raw_candidate_id": f"{origin}:{index}"})
    return items


def _raw_findings_payload(response, max_suggestions):
    """Normalise the single-call discovery response into the review payload shape."""
    findings = response.get("findings") if isinstance(response, dict) else None
    suggestions = []
    for item in (findings or [])[:max_suggestions]:
        if not isinstance(item, dict):
            continue
        # Doubts stay out of the reason the verifier reads: it had dismissed
        # gold findings because "the candidate itself concedes" a hedge.
        reason = str(item.get("reason") or "").strip()
        suggestions.append({
            "file_path": item.get("file") or item.get("file_path"),
            "line": item.get("line"),
            "severity": "medium",
            "confidence": 0.6,
            "title": str(item.get("title") or "Possible defect"),
            "reason": reason or "Raw discovery candidate",
            "recommendation": str(item.get("recommendation") or ""),
            "evidence": [str(entry) for entry in (item.get("evidence") or [])],
            "failure_scenario": str(item.get("failure_scenario") or reason),
            "raw_candidate_id": item.get("raw_candidate_id"),
            "candidate_origin": item.get("origin"),
            "merged_candidates": list(item.get("merged_candidates") or []),
        })
    return {
        "summary": str(response.get("summary") or "Raw single-call discovery"),
        "risk_level": "medium",
        "suggestions": suggestions,
    }


def run_raw_discovery(run: GroundedRun):
    """Pool every discovery sample and rule candidate into one review payload."""
    # Two samples with different inputs, unioned: the diff-only prompt keeps
    # local defects undiluted, diff+evidence finds cross-file ones. Their
    # candidate sets overlap only partly, so the union raises discovery.
    # Deterministic test-quality candidates join the same pool; every one
    # still has to pass verification and criteria to be published.
    prefix = (run.prompt_addendum + "\n" if run.prompt_addendum else "") + run.diff_text
    from src.reviewer.discovery_lenses import LENS_DISCOVERY_SYSTEM, lens_facts
    evidence_text = "\n## Frozen repository evidence\n" + run.bundle.render()
    # The third sample is a checklist pass over the kinds of defect the two
    # generic samples missed on the dev set, with deterministic facts
    # (declared runtimes, untested changed symbols, callers, changed docs).
    samples = (("diff", prompts.RAW_DISCOVERY_SYSTEM, prefix),
               ("diff+evidence", prompts.RAW_DISCOVERY_SYSTEM, prefix + evidence_text),
               ("lenses", LENS_DISCOVERY_SYSTEM,
                prefix + "\n" + lens_facts(run.snapshot, run.files) + evidence_text))
    pool = []
    sample_failures = []
    for origin, system, sample_prompt in samples:
        # One unparseable sample must not discard the others already collected.
        try:
            response, failure = _json(run.provider, system, sample_prompt), None
        except ValueError as exc:
            response, failure = None, exc
        run.discovery_calls += 1
        if failure is not None:
            sample_failures.append(failure)
            run.discovery_trace.append({
                "round": run.discovery_calls - 1, "mode": "raw", "input": origin,
                "status": "failed", "error_type": getattr(failure, "error_type", "ValueError"),
                "error": str(failure)[:300],
                "raw_excerpt": getattr(failure, "raw_excerpt", ""),
            })
            continue
        parse_errors = []
        returned = _collect_raw_findings(response, origin, parse_errors)
        pool.extend(returned)
        run.discovery_trace.append({
            "round": run.discovery_calls - 1, "mode": "raw", "input": origin, "status": "ok",
            "response_format_notice": response.get("response_format_notice"),
            "suggestions": len(returned), "parse_errors": parse_errors,
        })
    if len(sample_failures) == len(samples):
        # Nothing was collected: surface the failure as the lane always did.
        raise sample_failures[-1]
    from src.analyzer.test_quality_lane import collect_test_quality_candidates
    from src.reviewer.candidate_dedupe import dedupe_candidates
    rule_candidates = collect_test_quality_candidates(run.snapshot, run.files)
    run.host_rule_candidate_keys = {
        key for item in rule_candidates
        if (key := _rule_candidate_key(
            item.get("file"), item.get("line"), item.get("title"),
            item.get("reason"), item.get("evidence"),
        )) is not None
    }
    run.rule_rebuttals = _rule_rebuttals(rule_candidates)
    for rule_item in rule_candidates:
        target = rule_item.get("target")
        if target:
            # Show what the real (un-mocked) function does, so the
            # consequence of a missed patch or a wrong mock shape is visible.
            run.bundle.add(target[0], target[1], kind="candidate_rule_target", max_lines=40)
    pool.extend({**item, "origin": "test_quality_rules",
                 "raw_candidate_id": f"test_quality_rules:{index}"}
                for index, item in enumerate(rule_candidates))
    union, merges = dedupe_candidates(pool)
    run.discovery_trace.append({
        "round": run.discovery_calls, "mode": "test_quality_rules",
        "suggestions": len(rule_candidates),
    })
    run.discovery_trace.append({
        "mode": "dedupe", "pool": len(pool), "kept": len(union), "merges": merges,
        # Pool size per origin, so model and rule candidates are counted separately.
        "pool_by_origin": {origin: sum(item.get("origin") == origin for item in pool)
                           for origin, _, _ in samples + (("test_quality_rules", "", ""),)},
        "parse_error_count": sum(len(entry.get("parse_errors", [])) for entry in run.discovery_trace
                            if entry.get("mode") == "raw"),
        "doubts": [{"file": item.get("file"), "line": item.get("line"),
                    "doubts": str(item.get("doubts"))[:300]}
                   for item in union if str(item.get("doubts") or "").strip()],
    })
    payload = parse_model_payload(json.dumps(
        _raw_findings_payload({"findings": union}, max(run.max_suggestions, len(union))),
        ensure_ascii=False,
    ))
    return payload


def run_intent_discovery(run: GroundedRun):
    """Run bounded discovery rounds until the model returns final candidates."""
    payload = None
    deferred_candidate_payload = None
    force_final_reason = None
    for round_index in range(run.discovery_budget):
        run.available_sources.update(item["evidence_ref"] for item in run.bundle.excerpts)
        # Cross-file evidence changes the constraints; the prompt module owns
        # that choice so this path never rewrites prompt text by matching strings.
        # Order matters for provider prefix caching: everything identical across
        # this run's calls (knowledge, invariants) goes first, then the diff, then
        # evidence. The addendum was previously dropped on this path entirely.
        prompt = (run.prompt_addendum + "\n" if run.prompt_addendum else "") + build_user_prompt(
            run.context.text, run.max_suggestions, run.language, cross_file_evidence=True
        )
        prompt += "\n## Available intent source references\n" + json.dumps(
            sorted(run.available_sources), ensure_ascii=False
        )
        prompt += "\n## Frozen repository evidence\n" + run.bundle.render()
        if run.intent_plan is not None:
            prompt += "\n## Current intent hypothesis plan\n" + json.dumps(
                run.intent_plan, ensure_ascii=False
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
        if round_index == run.discovery_budget - 1 or force_final_reason:
            prompt += "\nTool-round budget exhausted. Return your final candidates; do not assume missing evidence is safe."
        response = None
        requests: list = []
        payload = None
        attempt_prompt = prompt
        for attempt in (1, 2):
            try:
                response = _json(run.provider, prompts.DISCOVERY_SYSTEM, attempt_prompt)
                run.discovery_calls += 1
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
                        response.get("intent_hypotheses"), run.available_sources
                    )
                    if run.intent_plan is None:
                        run.intent_plan = _new_intent_plan("planning")
                    prior = {item["id"]: item for item in run.intent_plan["hypotheses"]}
                    current = []
                    for hypothesis in hypotheses:
                        previous = prior.get(hypothesis["id"])
                        revision = previous.get("revision", 0) + 1 if previous else 1
                        hypothesis["revision"] = revision
                        if previous and any(previous.get(key) != hypothesis.get(key) for key in (
                            "goal", "constraints", "risk_hypotheses", "unknowns",
                            "source_refs", "focus_tags",
                        )):
                            run.intent_plan["hypothesis_history"].append(dict(previous))
                        current.append(hypothesis)
                    if current:
                        preserved = [item for item in run.intent_plan["hypotheses"]
                                     if item["id"] not in {hyp["id"] for hyp in current}]
                        run.intent_plan["hypotheses"] = preserved + current
                    for error in errors:
                        _record_intent_error(run.intent_plan, error)
                if not requests:
                    payload = parse_model_payload(json.dumps(response))
                break
            except ValueError:
                # A garbled answer must not discard the whole review: one
                # recorded retry is far cheaper than losing the run entirely.
                if attempt == 2:
                    raise
                run.discovery_trace.append({"round": round_index, "context_requests": [],
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
                if run.intent_plan is None:
                    run.intent_plan = _new_intent_plan("partial")
                _record_intent_error(run.intent_plan, f"discovery_stopped:{exc.kind}")
                run.intent_plan["status"] = "partial"
                run.discovery_trace.append({
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
        run.discovery_trace.append({"round": round_index,
                                "context_request_count": len(requests),
                                "response_format_notice": response.get("response_format_notice"),
                                "suggestions": response.get("suggestions", [])})
        if requests and round_index < run.discovery_budget - 1 and not force_final_reason:
            if run.intent_plan is None:
                run.intent_plan = _new_intent_plan("partial")
                _record_intent_error(run.intent_plan, "unbound_task")
            newly_added = _execute_intent_tasks(
                run.bundle, requests, run.intent_plan, run.snapshot, run.intent_query_keys, run.changed_paths,
                search_resolver=run.search_resolver,
            )
            if not newly_added:
                force_final_reason = "no_new_evidence"
            run.intent_plan["status"] = "evidence_collected" if newly_added else "partial"
            continue
        if requests:
            if run.intent_plan is None:
                run.intent_plan = _new_intent_plan("partial")
            request_status = (
                "not_executed_no_new_evidence" if force_final_reason
                else "not_executed_budget_exhausted"
            )
            _record_intent_error(run.intent_plan, request_status)
            overflow = max(0, len(requests) - MAX_INTENT_TASKS)
            if overflow:
                run.intent_plan["request_overflow_count"] = (
                    run.intent_plan.get("request_overflow_count", 0) + overflow
                )
            for request in requests[:MAX_INTENT_TASKS]:
                hypothesis_id, purpose = _task_labels(request)
                run.intent_plan["tasks"].append({
                    "hypothesis_id": hypothesis_id,
                    "purpose": purpose,
                    "task_id": _intent_task_id(request),
                    "status": request_status,
                    "evidence_refs": [],
                })
            if force_final_reason and round_index < run.discovery_budget - 1:
                continue
        if payload is None:
            payload = parse_model_payload(json.dumps(response))
        break
    return payload


def finalize_intent_plan(run: GroundedRun):
    """Give the run a definite intent plan status once discovery has ended."""
    if run.intent_plan is None:
        run.intent_plan = _new_intent_plan("not_reported")
        run.intent_plan["errors"] = ["intent_plan_not_reported"]
        run.intent_plan["error_counts"] = {"intent_plan_not_reported": 1}
    elif run.intent_plan["errors"]:
        run.intent_plan["status"] = "partial"
