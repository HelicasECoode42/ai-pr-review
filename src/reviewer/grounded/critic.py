"""Optional critic pass over confirmed findings."""
from __future__ import annotations

from src.models import (
    ReviewSuggestion,
    Severity,
)


def _apply_grounded_critic(result_parts, provider, *, batch_cap, repository_index_enabled,
                           critic_budget=None):
    """Run one evidence-bound critic batch after the ordinary host gate."""
    candidates, pending, kept_ids, kept, decisions, bundle = result_parts
    trace = {"status": "not_triggered", "reason": "no_eligible_candidate", "items": []}
    def append_pending_once(suggestion):
        if not any(
            (item.file_path, item.line, item.title)
            == (suggestion.file_path, suggestion.line, suggestion.title)
            for item in pending
        ):
            pending.append(suggestion.model_copy(update={"source": "ai_needs_context"}))
    if provider is None:
        return
    from src.reviewer.critic import (
        GroundedCriticDecision,
        critique_grounded_batch,
    )

    excerpts = {item.get("evidence_ref"): item for item in bundle.get("excerpts", [])
                if isinstance(item.get("evidence_ref"), str)}
    decisions_by_id = {item.get("candidate_id"): item for item in decisions
                       if isinstance(item, dict)}
    host_rule_ids = set(bundle.get("host_rule_candidate_ids", []))
    triggered = []
    trigger_reasons = {}
    suggestions_by_id = dict(zip(kept_ids, kept))
    for candidate in candidates:
        index = candidate.get("candidate_id")
        source_suggestion = candidate.get("suggestion")
        if not isinstance(index, int) or isinstance(index, bool) or index in host_rule_ids:
            continue
        eligible_for_critic = (
            candidate.get("status") == "confirmed"
            or candidate.get("validation_reason") == "causal_attribution_unproven"
        )
        if not eligible_for_critic or not isinstance(source_suggestion, dict):
            continue
        suggestion = suggestions_by_id.get(index)
        if suggestion is None:
            try:
                suggestion = ReviewSuggestion.model_validate(source_suggestion)
            except Exception:
                continue
        if suggestion.severity not in {Severity.HIGH, Severity.CRITICAL}:
            continue
        decision = decisions_by_id.get(index, {})
        refs = []
        for citation in decision.get("evidence", []):
            if not isinstance(citation, dict):
                continue
            for ref_id, excerpt in excerpts.items():
                if (citation.get("path") == excerpt.get("path")
                        and citation.get("side") == excerpt.get("side")
                        and isinstance(citation.get("line"), int)
                        and excerpt.get("start_line", 0) <= citation["line"]
                        <= excerpt.get("end_line", 0)):
                    refs.append(ref_id)
        refs = list(dict.fromkeys(refs))
        if not refs:
            continue
        evidence = [{"evidence_ref_id": ref, "content": excerpts[ref].get("code", "")}
                    for ref in refs if ref in excerpts]
        if not evidence:
            continue
        triggered.append((index, suggestion, evidence))
        trigger_reasons[index] = "high_or_critical_verified_candidate"

    if not triggered:
        bundle["grounded_critic"] = trace
        return
    cap = min(8, max(1, int(batch_cap)))
    selected, skipped = triggered[:cap], triggered[cap:]
    trace = {
        "status": "triggered", "reason": "host_severity_trigger",
        "batch_cap": cap,
        "selected_candidate_ids": [item[0] for item in selected],
        "items": [],
    }
    for index, suggestion, _ in skipped:
        candidates[next(i for i, item in enumerate(candidates)
                        if item.get("candidate_id") == index)].update(
                            status="needs_context", reason="critic_skipped_budget"
                        )
        append_pending_once(suggestion)
        trace["items"].append({
            "candidate_id": index, "status": "skipped_budget",
            "reason": "critic_batch_cap_exceeded",
        })
    if critic_budget is not None and not critic_budget.claim_batch():
        trace.update(status="skipped", reason="critic_run_budget_exhausted")
        for index, suggestion, _ in selected:
            candidate = next(item for item in candidates if item.get("candidate_id") == index)
            candidate.update(status="needs_context", reason="critic_run_budget_exhausted")
            append_pending_once(suggestion)
            trace["items"].append({
                "candidate_id": index, "status": "skipped_budget",
                "reason": "critic_run_budget_exhausted",
            })
        held = {
            item.get("candidate_id") for item in candidates
            if item.get("reason") in {
                "critic_skipped_budget", "critic_run_budget_exhausted",
            }
        }
        survivors = [(index, suggestion) for index, suggestion in zip(kept_ids, kept)
                     if index not in held]
        kept_ids[:] = [index for index, _ in survivors]
        kept[:] = [suggestion for _, suggestion in survivors]
        bundle["grounded_critic"] = trace
        return
    batch = critique_grounded_batch(provider, selected, batch_cap=cap)
    trace["raw_response"] = batch.raw_response
    if batch.error:
        trace.update(status="unavailable", reason=batch.error)
        for index, suggestion, _ in selected:
            candidate = next(item for item in candidates if item.get("candidate_id") == index)
            candidate.update(status="needs_context", reason="critic_unavailable")
            append_pending_once(suggestion)
            trace["items"].append({
                "candidate_id": index, "status": "unavailable", "reason": batch.error,
            })
    else:
        verdicts = {item.candidate_id: item for item in batch.items}
        for index, suggestion, _ in selected:
            verdict = verdicts.get(index)
            if verdict is None:
                reason = "critic_missing_candidate_verdict"
                decision = "needs_context"
                refs = []
            else:
                reason = verdict.reason
                decision = verdict.decision.value
                refs = verdict.evidence_ref_ids
            hold = decision in {
                GroundedCriticDecision.REFUTE.value,
                GroundedCriticDecision.NEEDS_CONTEXT.value,
                GroundedCriticDecision.ABSTAIN.value,
            } or verdict is None or verdict.semantic_assessment.value != "supported"
            # The index can locate code but cannot establish PR causality.
            # A critic confirmation therefore cannot clear the host's unknown
            # attribution state, even when its semantic assessment is supported.
            if repository_index_enabled and decision == GroundedCriticDecision.CONFIRM.value:
                hold = True
            trace["items"].append({
                "candidate_id": index,
                "status": "shadow_refutation_pending" if decision == "refute" else decision,
                "decision": decision,
                "confidence_metadata": verdict.confidence if verdict else 0.0,
                "semantic_assessment": (
                    verdict.semantic_assessment.value if verdict else "insufficient"
                ),
                "semantic_consistent": bool(
                    verdict is not None
                    and (
                        (decision == "confirm"
                         and verdict.semantic_assessment.value == "supported")
                        or (decision == "refute"
                            and verdict.semantic_assessment.value == "contradicted")
                        or (decision in {"needs_context", "abstain"}
                            and verdict.semantic_assessment.value == "insufficient")
                    )
                ),
                "goal_coverage": verdict.goal_coverage if verdict else "unknown",
                "reason": reason,
                "evidence_ref_ids": refs,
                "trigger_reason": trigger_reasons[index],
            })
            if hold:
                candidate = next(item for item in candidates if item.get("candidate_id") == index)
                candidate.update(status="needs_context", reason=(
                    "critic_refutation_shadow_only" if decision == "refute"
                    else "critic_cannot_resolve_index_attribution"
                    if repository_index_enabled and decision == "confirm"
                    else "critic_needs_context"
                ))
                append_pending_once(suggestion)
        held = {item["candidate_id"] for item in trace["items"]
                if item["status"] in {"shadow_refutation_pending", "needs_context", "abstain"}}
    held = {
        item.get("candidate_id") for item in candidates
        if item.get("reason") in {
            "critic_skipped_budget", "critic_run_budget_exhausted", "critic_unavailable",
            "critic_refutation_shadow_only", "critic_needs_context",
        }
    }
    survivors = [(index, suggestion) for index, suggestion in zip(kept_ids, kept)
                 if index not in held]
    kept_ids[:] = [index for index, _ in survivors]
    kept[:] = [suggestion for _, suggestion in survivors]
    bundle["grounded_critic"] = trace
