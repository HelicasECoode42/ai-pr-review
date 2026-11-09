"""Turns a grounded result into a ReviewReport."""
from __future__ import annotations

from src.analyzer.context_builder import tokenizer_name
from src.models import (
    BudgetReport,
    ReviewReport,
    Severity,
    SkippedContextFile,
)
from src.reviewer.grounded.trace import _review_trace


def build_grounded_report(pr, files, findings, result, context, *, reviewer_version="pr-branch",
                          execution_status="success", review_meta=None, pr_syntax_ok=True,
                          report_confidence="normal", degradation_reason=None,
                          max_input_tokens: int | None = None,
                          analyzer_warnings: list[str] | None = None,
                          trace_identity: dict | None = None):
    if trace_identity is not None:
        result.bundle["trace_identity"] = trace_identity
    result.bundle["review_trace"] = _review_trace(
        pr, files, result, context, reviewer_version
    )
    severity = max((s.severity for s in result.suggestions),
                   key=lambda s: {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}[s],
                   default=Severity.LOW)
    incomplete = bool(context.truncated or result.bundle["omitted"] or result.bundle.get("evicted")
                      or any(e.get("truncated") for e in result.bundle["excerpts"])
                      or result.bundle["snapshot"].get("search_limited")
                      or result.bundle["snapshot"].get("errors"))
    warnings = ["Behavior verification uses cited source reasoning; no target tests were executed."]
    if result.bundle.get("provider_cleanup_failed"):
        warnings.append(
            "Unit provider cleanup failed; completed model results were retained without retry."
        )
        report_confidence = "partial"
    intent_status = result.bundle.get("intent_plan", {}).get("status")
    if intent_status == "not_reported":
        warnings.append(
            "The model did not report an intent hypothesis plan; findings were "
            "reviewed independently."
        )
    elif intent_status in {"partial", "evidence_collected"}:
        warnings.append(
            "Intent hypotheses remain partially assessed; they do not change finding verdicts."
        )
    if incomplete:
        warnings.append("Repository evidence coverage is bounded/incomplete; no-findings does not establish correctness.")
    if result.verification_failed:
        execution_status, report_confidence = "partial", "partial"
        warnings.append("Behavior verification provider failed; candidates retained as pending.")
    elif incomplete:
        report_confidence = "partial"
    # A failed discovery sample means fewer candidates were looked for; the
    # surviving samples still count, but the report must not read as complete.
    raw_trace = [entry for entry in result.bundle.get("discovery_trace", [])
                 if isinstance(entry, dict) and entry.get("mode") == "raw"]
    failed_samples = [str(entry.get("input")) for entry in raw_trace
                      if entry.get("status") == "failed"]
    if failed_samples:
        report_confidence = "partial"
        warnings.append(
            f"Discovery sample(s) failed to parse: {', '.join(failed_samples)}; "
            "candidates from the remaining samples were reviewed."
        )
    parse_error_count = sum(len(entry.get("parse_errors") or []) for entry in raw_trace)
    if parse_error_count:
        warnings.append(
            f"{parse_error_count} discovery item(s) were malformed and not reviewed; "
            "see discovery_trace parse_errors."
        )
    if result.pending:
        warnings.append(f"{len(result.pending)} candidate(s) need additional context; excluded from confirmed findings.")
    from src.analyzer.context_builder import partial_inclusion_warning
    partial_warning = partial_inclusion_warning(context)
    if partial_warning:
        warnings.append(partial_warning)
    # Scans that could not finish must reach the reader, not just the log.
    warnings.extend(analyzer_warnings or [])
    from src.reviewer.budget import budget_warnings, run_budget_report
    evidence_budget = result.evidence_budget or BudgetReport(scope="repository_evidence")
    run_budget = run_budget_report((context.budgets, evidence_budget), cap=max_input_tokens)
    warnings.extend(budget_warnings(run_budget))
    run_metrics: dict[str, int | float] = {
        "raw_candidate_count": len(result.candidates),
        "pending_candidate_count": len(result.pending),
        "behavior_verified_count": sum(d.get("decision") == "confirmed" for d in result.decisions),
        "discovery_request_count": result.discovery_calls,
        "behavior_verification_request_count": result.verification_calls,
        "main_review_prompt_signal_count": 0,
        "witness_blocked_count": int(result.bundle.get("witness_blocked", 0) or 0),
    }
    critic_trace = result.bundle.get("grounded_critic")
    if isinstance(critic_trace, dict):
        critic_items = critic_trace.get("items", [])
        run_metrics["critic_request_count"] = int(
            critic_trace.get("status") in {"triggered", "unavailable"}
        )
        run_metrics["critic_selected_candidate_count"] = len(
            critic_trace.get("selected_candidate_ids", [])
        )
        run_metrics["critic_pending_candidate_count"] = sum(
            item.get("status") in {
                "shadow_refutation_pending", "needs_context", "abstain",
                "unavailable", "skipped_budget",
            } for item in critic_items if isinstance(item, dict)
        )
    closure = result.bundle.get("impact_closure")
    if isinstance(closure, dict):
        run_metrics["impact_closure_nodes"] = int(closure.get("closure_nodes", 0))
        run_metrics["impact_closure_seen"] = int(closure.get("seen", 0))
        run_metrics["impact_coverage"] = float(closure.get("coverage", 0.0))
    unit_details = result.bundle.get("unit_details")
    if isinstance(unit_details, dict):
        for key in ("units_planned", "units_reviewed", "units_failed", "units_skipped_by_budget",
                    "unit_review", "units_resumed", "units_cancelled", "unit_attempts",
                    "unit_checkpoint_failed", "discovery_calls_restored",
                    "verification_calls_restored"):
            if isinstance(unit_details.get(key), int):
                run_metrics[key] = unit_details[key]
        # GroundedResult counters remain cumulative audit data after restoring
        # checkpoints; request_count is specifically the work done this run.
        for key in ("discovery_calls_this_run", "verification_calls_this_run"):
            if isinstance(unit_details.get(key), int):
                metric_key = key.removesuffix("_this_run")
                request_key = (
                    "discovery_request_count" if metric_key == "discovery_calls"
                    else "behavior_verification_request_count"
                )
                run_metrics[request_key] = unit_details[key]
        issues = unit_details.get("unit_checkpoint_issues")
        if isinstance(issues, list):
            run_metrics["unit_checkpoint_issue_count"] = len(issues)
    # What the reader must know about a truncated prompt: how much of the change
    # never reached the model at all.
    if result.unit_coverage:
        coverage = result.unit_coverage
        for key in (
            "units_planned",
            "units_with_evidence_in_prompt",
            "units_fully_in_prompt",
            "units_not_reviewed",
        ):
            if isinstance(coverage.get(key), int):
                run_metrics[key] = coverage[key]
        unreviewed = coverage.get("units_not_reviewed") or 0
        if unreviewed:
            sample = ", ".join((coverage.get("unreviewed_paths_sample") or [])[:5])
            warnings.append(
                f"{unreviewed} of {coverage.get('units_planned', 0)} semantic review unit(s) were "
                f"not reviewed in this run; the report is partial."
                + (f" Examples: {sample}." if sample else "")
            )
    from src.reviewer.engine import _build_completeness
    skipped = [SkippedContextFile(file_path=p, reason=r) for p, r in context.skipped_files]
    return ReviewReport(pr=pr, files=files, summary=result.summary, risk_level=severity,
                        rule_findings=findings, suggestions=result.suggestions, pending_suggestions=result.pending,
                        used_ai=True, execution_status=execution_status, reviewer_version=reviewer_version,
                        report_confidence=report_confidence, degradation_reason=degradation_reason,
                        analysis_warnings=warnings, context_truncated=context.truncated,
                        context_token_count=context.token_count + result.bundle["tokens"],
                        patch_token_budget=context.patch_token_budget,
                        skipped_context_files=skipped, pr_syntax_check_ok=pr_syntax_ok,
                        review_meta=review_meta or {},
                        completeness=_build_completeness(pr, files, skipped, incomplete, True, False, pr_syntax_ok),
                        candidate_audit=result.candidates, behavior_verifications=result.decisions,
                        repository_context=result.bundle,
                        context_tokenizer=tokenizer_name(),
                        budget_report=run_budget,
                        hidden_suggestions_count=len(result.candidates) - len(result.suggestions),
                        metrics=run_metrics)
