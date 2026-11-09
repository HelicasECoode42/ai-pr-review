"""Repository-grounded discovery and behavioral verification with auditable candidates."""
from __future__ import annotations

from src.analyzer.context_builder import ReviewContext
from src.analyzer.repository_context import EvidenceBundle, Snapshot
from src.reviewer.criteria import (
    CRITERIA_VERSION,  # noqa: F401
    quote_was_shown,
    valid_citation,
)
from src.reviewer.grounded.candidates import admit_candidates
from src.reviewer.grounded.critic import (  # noqa: F401
    _apply_grounded_critic,
)
from src.reviewer.grounded.discovery import (  # noqa: F401
    _collect_raw_findings,
    _excerpt,
    _raw_discovery_prompt,
    _raw_findings_payload,
    finalize_intent_plan,
    run_intent_discovery,
    run_raw_discovery,
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
from src.reviewer.grounded.gates import apply_publication_caps, apply_verdicts, fold_duplicates
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
from src.reviewer.grounded.state import GroundedRun
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
    request_verdicts,
)

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
    run = _new_run(
        pr, files, provider, snapshot, context, evidence_tokens=evidence_tokens,
        max_suggestions=max_suggestions, language=language, verify_behavior=verify_behavior,
        max_suggestions_per_file=max_suggestions_per_file, contract_domains=contract_domains,
        discovery_rounds=discovery_rounds, verify_rounds=verify_rounds,
        prompt_addendum=prompt_addendum, missing_evidence_followup=missing_evidence_followup,
        missing_evidence_followup_only=missing_evidence_followup_only,
        raw_discovery=raw_discovery, repository_index_enabled=repository_index_enabled,
        critic_provider=critic_provider, critic_batch_cap=critic_batch_cap,
        critic_budget=critic_budget,
    )
    payload = run_raw_discovery(run) if raw_discovery else run_intent_discovery(run)
    if payload is None:
        raise ValueError("Discovery did not produce final candidates")
    finalize_intent_plan(run)
    admit_candidates(run, payload)
    # Verification gets its own headroom: candidate definitions, callers and
    # requested reads were being dropped as evidence_budget_exhausted because
    # discovery's initial windows had already filled the shared budget.
    if raw_discovery:
        run.bundle.budget += VERIFY_EVIDENCE_EXTRA_TOKENS
    if run.eligible and verify_behavior:
        apply_verdicts(run, request_verdicts(run))
        fold_duplicates(run)
    else:
        run.kept = [s for _, s in run.eligible]
        run.kept_ids = [index for index, _ in run.eligible]
        for index, _ in run.eligible:
            run.candidates[index].update(status="unverified", reason="behavior_verification_disabled")
    apply_publication_caps(run)
    return build_result(run, payload)


def _new_run(pr, files, provider, snapshot, context, *, evidence_tokens, **options) -> GroundedRun:
    bundle = EvidenceBundle(snapshot, budget=evidence_tokens)
    bundle.follow(files)
    available_sources = {f"diff:{file.filename}" for file in files}
    if getattr(pr, "title", ""):
        available_sources.add("pr:title")
    if getattr(pr, "body", ""):
        available_sources.add("pr:description")
    raw_discovery = options["raw_discovery"]
    is_unit_context = getattr(getattr(context, "budgets", None), "scope", None) == (
        "build_unit_context"
    )
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
    return GroundedRun(
        pr=pr, files=files, provider=provider, snapshot=snapshot, context=context, bundle=bundle,
        changed_paths={file.filename for file in files},
        available_sources=available_sources,
        search_resolver=(
            _bounded_python_index_search(snapshot, files)
            if options["repository_index_enabled"] else None
        ),
        # Round budgets are parameters because each round re-sends the whole prompt
        # and the evidence bundle: on a small unit one round each is enough, and the
        # saved re-sends are the difference between 62k and ~20k tokens per unit.
        discovery_budget=0 if raw_discovery else max(1, options["discovery_rounds"]),
        raw_discovery_scope=(
            "unit_context" if is_unit_context else "full_pr_diff"
        ) if raw_discovery else None,
        diff_text=diff_text,
        **options,
    )


def build_result(run: GroundedRun, payload):
    """Assemble the audit bundle and the final result."""
    critic_context = {
        "excerpts": run.bundle.excerpts,
        "host_rule_candidate_ids": sorted(run.host_rule_candidate_ids),
    }
    if run.critic_provider is not None:
        _apply_grounded_critic(
            (run.candidates, run.pending, run.kept_ids, run.kept, run.decisions, critic_context),
            run.critic_provider, batch_cap=run.critic_batch_cap,
            repository_index_enabled=run.repository_index_enabled,
            critic_budget=run.critic_budget,
        )
    audit = run.bundle.audit()
    if "grounded_critic" in critic_context:
        audit["grounded_critic"] = critic_context["grounded_critic"]
    audit["intent_plan"] = run.intent_plan
    audit["repository_index_enabled"] = bool(run.repository_index_enabled)
    audit["symbol_index_schema"] = (
        "python-binding-v1" if run.repository_index_enabled else None
    )
    audit["missing_evidence_followup_enabled"] = bool(run.missing_evidence_followup)
    if run.raw_discovery:
        audit["raw_discovery_scope"] = run.raw_discovery_scope
    audit["witness_blocked"] = run.witness_blocked
    # Impact closure: was the callers' side of every change actually visible? Hunk
    # counting says how many lines were shown; this says how much of the change's
    # influence surface was, which is what decides whether a defect is findable.
    try:
        from src.analyzer.diff_parser import parse_file_hunks
        from src.analyzer.impact_closure import build_impact_closure, impact_coverage

        hunks_by_file = {file.filename: parse_file_hunks(file) for file in run.files}
        closure = build_impact_closure(run.files, hunks_by_file, run.snapshot)
        audit["impact_closure"] = {**closure.summary(), **impact_coverage(closure, run.context.text)}
    except Exception as exc:  # a metric must never take a review down
        audit["impact_closure_error"] = f"{type(exc).__name__}: {exc}"
    audit["discovery_trace"] = run.discovery_trace
    audit["discovery_mode"] = "raw" if run.raw_discovery else "intent"
    if run.raw_discovery:
        audit["raw_discovery_candidates"] = _raw_candidate_inventory(run.candidates)
    return GroundedResult(payload.summary, run.kept, run.pending, run.candidates, run.decisions, audit,
                          run.discovery_calls, run.verification_calls, run.failed,
                          evidence_budget=run.bundle.budget_report,
                          unit_coverage=_unit_coverage(run.snapshot, run.files, run.context, audit))
