"""Host decisions that turn verifier output into candidate statuses."""
from __future__ import annotations

from src.models import (
    Severity,
)
from src.reviewer.criteria import (
    CRITERIA_VERSION,
    blocked_by_witness,
    confirmation_verdicts,
    quote_was_shown,
)
from src.reviewer.grounded.rule_candidates import (
    _contradicted_by_rule,
)
from src.reviewer.grounded.verification import (
    _dismissal_rests_on_judgement,
    _speculative_confirmation,
)

# Aliases kept for existing importers; the implementations live in criteria.py.
_quote_was_shown = quote_was_shown
from src.reviewer.grounded.state import GroundedRun


def apply_verdicts(run: GroundedRun, verdicts):
    """Turn verifier decisions into statuses; only the host criteria can confirm."""
    for index, suggestion in run.eligible:
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
            snapshot=run.snapshot, bundle=run.bundle, files=run.files, sent_text=run.diff_text,
            contract_domains=run.contract_domains,
            repository_index_enabled=(run.repository_index_enabled
                                      and index not in run.host_rule_candidate_ids),
            anchor_path=suggestion.file_path, anchor_line=suggestion.line,
        )
        by_name = {verdict.name: verdict for verdict in filters}
        validated = by_name["source_provenance"].passed
        counter_valid = by_name["counter_evidence"].passed
        witness_block = blocked_by_witness(filters)
        if witness_block is not None:
            run.witness_blocked += 1
        contradicted = (
            _contradicted_by_rule(
                suggestion, run.rule_rebuttals,
                host_rule_fact=index in run.host_rule_candidate_ids,
            ) if decision == "confirmed" else None
        )
        is_rule_fact = index in run.host_rule_candidate_ids
        if is_rule_fact:
            # A rule finding is an AST fact, not a runtime-failure claim. The
            # verifier judged these by "does the test fail?" and dismissed
            # true gold items (local import binding, magic indexes) as style.
            # The fact stands; the verifier's view is kept for the audit.
            item = {**item, "verifier_decision": decision, "decision": "confirmed",
                    "validation_reason": "deterministic_rule_fact"}
            decision = "confirmed"
        elif (run.raw_discovery and decision == "confirmed"
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
        elif decision == "confirmed" and run.repository_index_enabled and not is_rule_fact:
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
        if run.repository_index_enabled and not is_rule_fact:
            item["attribution"] = "unknown"
            difference = by_name.get("behavior_difference_support")
            item["behavior_difference_supported"] = bool(difference and difference.passed)
        run.decisions.append(item)
        run.candidates[index].update(
            status=decision,
            reason=item.get("reason", ""),
            verifier_decision=item.get("verifier_decision", decision),
            validation_reason=item.get("validation_reason"),
        )
        if decision == "confirmed" and is_rule_fact:
            run.kept_ids.append(index)
            run.duplicate_of[index] = None
            run.kept.append(suggestion.model_copy(update={
                "source": "rule_test_quality", "severity": Severity.LOW,
                "confidence": max(suggestion.confidence, 0.7),
            }))
        elif decision == "confirmed":
            reason = (suggestion.reason + "\nTrigger: " + item["trigger"] + "\nExpected: " + item["expected_behavior"]
                      + "\nActual: " + item["actual_behavior"] + "\nPR change: " + item["introduced_by"])
            run.kept_ids.append(index)
            run.duplicate_of[index] = item.get("duplicate_of")
            run.kept.append(suggestion.model_copy(update={"source": "ai_behavior_verified", "confidence": confidence,
                        "reason": reason, "evidence": [c["quote"] for c in evidence],
                        "failure_scenario": item["trigger"] + " -> " + item["actual_behavior"]}))
        elif decision == "needs_context":
            run.pending.append(suggestion.model_copy(update={"source": "ai_needs_context"}))


def fold_duplicates(run: GroundedRun):
    """Fold confirmed candidates the verifier attributes to one root cause."""
    # The verifier sees every candidate at once, so it can say which ones
    # share a root cause; a confirmed duplicate of another confirmed
    # candidate is folded into it instead of becoming a second comment.
    # Two guards against over-grouping, which folded distinct gold findings
    # into "the test mocks the function under test": a rule finding is a
    # separate structural fact and is never folded, and a folded model
    # candidate stays visible in the pending queue instead of disappearing.
    confirmed = set(run.kept_ids)
    folded = set()
    for index, suggestion in zip(run.kept_ids, run.kept):
        target = run.duplicate_of.get(index)
        if index in run.host_rule_candidate_ids:
            continue
        if (isinstance(target, int) and not isinstance(target, bool) and target != index
                and target in confirmed and target not in folded):
            folded.add(index)
            run.candidates[index].update(status="needs_context",
                                     reason="duplicate_of_confirmed_candidate",
                                     duplicate_of=target)
            run.pending.append(suggestion.model_copy(update={"source": "ai_duplicate_of_confirmed"}))
    survivors = [(index, suggestion) for index, suggestion in zip(run.kept_ids, run.kept)
                 if index not in folded]
    run.kept_ids = [index for index, _ in survivors]
    run.kept = [suggestion for _, suggestion in survivors]


def apply_publication_caps(run: GroundedRun):
    """Hold back model findings beyond the per-file comment budget."""
    published = []
    published_ids = []
    counts = {}
    for candidate_index, suggestion in zip(run.kept_ids, run.kept):
        # Deterministic rule findings are separate structural facts; they do
        # not compete with model findings for the per-file comment budget.
        if candidate_index in run.host_rule_candidate_ids:
            published.append(suggestion)
            published_ids.append(candidate_index)
            continue
        count = counts.get(suggestion.file_path, 0)
        if count >= run.max_suggestions_per_file:
            run.pending.append(suggestion.model_copy(update={"source": "ai_publication_deferred"}))
            for item in run.candidates:
                if item["suggestion"]["file_path"] == suggestion.file_path and item["suggestion"]["title"] == suggestion.title:
                    item.update(status="needs_context", reason="per_file_cap",
                                publication="deferred", publication_reason="per_file_cap")
            continue
        counts[suggestion.file_path] = count + 1
        published.append(suggestion)
        published_ids.append(candidate_index)
    run.kept = published
    run.kept_ids = published_ids
