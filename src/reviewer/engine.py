from __future__ import annotations

import logging
import re

from pydantic import ValidationError

from src.analyzer.context_builder import (
    ReviewContext,
    build_review_context,
    partial_inclusion_warning,
    tokenizer_name,
)
from src.analyzer.signal_selection import select_verifiable_signals
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.github.client import GitHubClient

from src.models import (
    ReviewMeta,
    ChangedFile,
    CompletenessItem,
    FixTrackingItem,
    PullRequest,
    ReviewReport,
    ReviewSuggestion,
    RiskFinding,
    Severity,
    SkippedContextFile,
    StepStatus,
)
from src.reviewer.model_payload import ModelReviewPayload, parse_model_payload
from src.reviewer.evidence_validator import validate_ai_evidence
from src.reviewer.budget import budget_warnings, run_budget_report
from src.reviewer.critic import CriticDecision, apply_critic, critique_batch, select_critic_candidates
from src.reviewer.prompt import SYSTEM_PROMPT, build_user_prompt
from src.reviewer.provider import ProviderError, ReviewModelProvider
from src.reviewer.review_merger import merge_review_results
from src.reviewer.signal_verifier import ProviderSignalVerifier, build_signal_envelopes
from src.reviewer.suggestion_filter import filter_suggestions

logger = logging.getLogger(__name__)


def _build_completeness(
    pr: PullRequest,
    files: list[ChangedFile],
    skipped_ctx: list[SkippedContextFile],
    ctx_truncated: bool,
    used_ai: bool,
    ai_failed: bool,
    pr_syntax_ok: bool = True,
) -> list[CompletenessItem]:
    """Build analysis completeness items from review state."""
    items: list[CompletenessItem] = []

    # PR 元信息
    pr_ok = bool(pr.repo and pr.repo != "unknown" and pr.number > 0)
    items.append(CompletenessItem(
        item="PR 元信息获取",
        status=StepStatus.SUCCESS if pr_ok else StepStatus.FAILED,
        detail="成功" if pr_ok else "GitHub API 获取 PR 信息失败",
    ))

    # 变更文件
    files_ok = len(files) > 0
    items.append(CompletenessItem(
        item="变更文件获取",
        status=StepStatus.SUCCESS if files_ok else StepStatus.FAILED,
        detail=f"{len(files)} 个文件" if files_ok else "未获取到变更文件",
    ))

    # AI 上下文文件
    skipped_count = len(skipped_ctx)
    if ctx_truncated:
        items.append(CompletenessItem(
            item="AI 上下文文件", status=StepStatus.PARTIAL,
            detail="上下文预算不足，部分补丁被裁剪或未进入上下文",
        ))
    elif skipped_count == 0:
        items.append(CompletenessItem(
            item="AI 上下文文件",
            status=StepStatus.SUCCESS,
            detail="全部文件进入上下文",
        ))
    elif skipped_count < len(files):
        items.append(CompletenessItem(
            item="AI 上下文文件",
            status=StepStatus.PARTIAL,
            detail=f"{skipped_count} 个文件跳过（lockfile / 生成内容）",
        ))
    else:
        items.append(CompletenessItem(
            item="AI 上下文文件",
            status=StepStatus.FAILED,
            detail="全部文件被跳过",
        ))

    # AI 分析
    if used_ai and not ai_failed:
        items.append(CompletenessItem(
            item="AI 分析",
            status=StepStatus.SUCCESS,
            detail="成功",
        ))
    elif ai_failed:
        items.append(CompletenessItem(
            item="AI 分析",
            status=StepStatus.FAILED,
            detail="降级至规则扫描",
        ))
    else:
        items.append(CompletenessItem(
            item="AI 分析",
            status=StepStatus.SKIPPED,
            detail="未启用 AI",
        ))

    # 规则扫描
    items.append(CompletenessItem(
        item="规则扫描",
        status=StepStatus.SUCCESS,
        detail="成功",
    ))

    # Patch 上下文
    if ctx_truncated:
        items.append(CompletenessItem(
            item="Patch 上下文",
            status=StepStatus.PARTIAL,
            detail="裁剪 — 超出 token 预算",
        ))
    else:
        items.append(CompletenessItem(
            item="Patch 上下文",
            status=StepStatus.SUCCESS,
            detail="完整",
        ))

    # PR head 语法诊断
    items.append(CompletenessItem(
        item="PR head 语法诊断",
        status=StepStatus.SUCCESS if pr_syntax_ok else StepStatus.FAILED,
        detail="未检测到语法错误" if pr_syntax_ok else "PR 分支代码存在语法或编码错误，已生成降级报告",
    ))

    return items


def build_diagnostic_report(
    pr: PullRequest,
    files: list[ChangedFile] | None = None,
    error: str = "Unknown error during review",
    reviewer_version: str = "pr-branch",
    execution_status: str = "failed",
    language: str = "en",
    analyzer_warnings: list[str] | None = None,
) -> ReviewReport:
    """Build a minimal diagnostic report when all other paths fail (Level 3 fallback).

    This is the last resort — it always produces something readable.
    """
    if files is None:
        files = []
    if language == "zh":
        summary = (
            f"## 审查失败\n\n"
            f"审查过程遇到未预期的错误，无法生成完整报告。\n"
            f"**错误信息**：{error}"
        )
    else:
        summary = (
            f"## Review Failed\n\n"
            f"The review process encountered an unexpected error.\n"
            f"**Error**: {error}"
        )
    completeness = _build_completeness(
        pr=pr, files=files,
        skipped_ctx=[], ctx_truncated=False,
        used_ai=False, ai_failed=False,
        pr_syntax_ok=True,
    )
    report = ReviewReport(
        pr=pr,
        files=files,
        summary=summary,
        risk_level=Severity.LOW,
        used_ai=False,
        analysis_warnings=[
            *(analyzer_warnings or []),
            f"Diagnostic report generated due to: {error}",
        ],
        reviewer_version=reviewer_version,
        execution_status=execution_status,
        degradation_reason=error,
        report_confidence="failed",
        completeness=completeness,
    )
    validation_issues = validate_report(report)
    if validation_issues:
        report.analysis_warnings.extend(validation_issues)
    return report


def validate_report(report: ReviewReport) -> list[str]:
    """Run post-generation validation checks on a ReviewReport.

    Returns a list of issues found. Each issue is a human-readable string
    that should be appended to analysis_warnings.
    """
    issues: list[str] = []

    # 1. PR metadata must be present
    # Skip PR metadata check for diagnostic reports
    if report.report_confidence != "failed":
        if not report.pr or not report.pr.repo or report.pr.number == 0:
            issues.append("PR metadata missing — report may be incomplete")

    # 2. AI flag consistency
    if report.used_ai and report.ai_failure_reason:
        issues.append(
            "Inconsistency: report.used_ai=True but ai_failure_reason is set"
        )
    if not report.used_ai and report.ai_failure_reason:
        issues.append(
            "Inconsistency: report.used_ai=False but ai_failure_reason is set"
        )

    # 3. If suggestions exist, summary must be non-empty
    if report.suggestions and not report.summary.strip():
        issues.append(
            "Suggestions present but summary is empty — possible parse error"
        )

    # 4. report_confidence must be one of the known values
    valid_confidence = {"normal", "fallback", "partial", "failed"}
    if report.report_confidence not in valid_confidence:
        issues.append(
            f"Unknown report_confidence '{report.report_confidence}'"
        )

    # 5. execution_status consistency
    # Skip execution_status check for diagnostic reports
    if report.report_confidence != "failed":
        if report.execution_status == "success" and report.degradation_reason:
            issues.append(
                "Inconsistency: execution_status=success but degradation_reason is set"
            )
        if report.execution_status == "degraded" and not report.degradation_reason:
            issues.append(
                "execution_status=degraded but degradation_reason is missing"
            )

    # 6. ReviewMeta basic checks
    if report.review_meta:
        if report.review_meta.reviewed_commit and len(report.review_meta.reviewed_commit) < 6:
            issues.append(
                f"reviewed_commit looks truncated: '{report.review_meta.reviewed_commit}'"
            )

    # 7. Completeness table: should not be empty
    if not report.completeness:
        issues.append("Completeness table is empty — analysis status unknown")

    # 8. Pr_syntax_check_ok vs report_confidence
    if not report.pr_syntax_check_ok and report.report_confidence == "normal":
        issues.append(
            "pr_syntax_check failed but report_confidence=normal — should be partial or failed"
        )

    return issues


def build_rule_only_report(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    language: str = "en",
    reviewer_version: str = "pr-branch",
    execution_status: str = "success",
    degradation_reason: str | None = None,
    report_confidence: str = "normal",
    pr_syntax_ok: bool = True,
    review_meta: ReviewMeta | None = None,
    gh_client: "GitHubClient | None" = None,
    analyzer_warnings: list[str] | None = None,
) -> ReviewReport:
    additions = sum(f.additions for f in files)
    deletions = sum(f.deletions for f in files)
    if language == "zh":
        summary = (
            f"本 PR 共变更 {len(files)} 个文件，新增 {additions} 行，删除 {deletions} 行。"
            f"规则扫描发现 {len(findings)} 个潜在风险项。"
        )
    else:
        summary = (
            f"PR changes {len(files)} file(s) with {additions} additions and {deletions} deletions. "
            f"Rule scan found {len(findings)} potential risk item(s)."
        )
    # Path-level and heuristic findings are deliberately allowed to stay in
    # rule_findings for auditability, but only sufficiently confident findings
    # with a concrete file location become user-facing suggestions.
    visible_findings = [
        finding
        for finding in findings
        if finding.confidence >= 0.65 and finding.file_path
    ]
    risk_level = _max_severity([finding.severity for finding in visible_findings])
    suggestions = [
        ReviewSuggestion(
            file_path=finding.file_path,
            line=finding.line,
            severity=finding.severity,
            confidence=finding.confidence,
            title=finding.title,
            reason=finding.evidence,
            recommendation=finding.recommendation,
            source="rule_unverified",
        )
        for finding in visible_findings
    ]
    # Build fix tracking from previous review comments
    fix_tracking = _build_fix_tracking(
        repo=pr.repo,
        pr_number=pr.number,
        current_suggestions=suggestions,
        gh_client=gh_client,
    )

    completeness = _build_completeness(
        pr=pr, files=files,
        skipped_ctx=[], ctx_truncated=False,
        used_ai=False, ai_failed=False,
        pr_syntax_ok=pr_syntax_ok,
    )
    report = ReviewReport(
        pr=pr,
        files=files,
        summary=summary,
        risk_level=risk_level,
        rule_findings=findings,
        suggestions=suggestions,
        used_ai=False,
        analysis_warnings=list(analyzer_warnings or []),
        reviewer_version=reviewer_version,
        execution_status=execution_status,
        degradation_reason=degradation_reason,
        report_confidence=report_confidence,
        completeness=completeness,
        review_meta=review_meta or ReviewMeta(),
        fix_tracking=fix_tracking,
        confirmed_signals=[],
        dismissed_signals=[],
        hidden_rule_findings_count=len(findings) - len(visible_findings),
        unverified_signals=[
            {
                "rule_id": finding.rule_id,
                "file_path": finding.file_path,
                "line": finding.line,
                "reason": "AI semantic verification was not available.",
            }
            for finding in findings
        ],
        metrics={
            "raw_signal_count": len(findings),
            "visible_rule_signal_count": len(visible_findings),
            "main_review_prompt_signal_count": 0,
            "signal_verification_request_count": 0,
        },
    )
    # Post-generation validation
    validation_issues = validate_report(report)
    if validation_issues:
        report.analysis_warnings.extend(validation_issues)
        logger.warning(f"Report validation issues: {validation_issues}")
    return report


def review_with_ai(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    provider: ReviewModelProvider,
    max_suggestions: int,
    min_confidence: float = 0.0,
    max_suggestions_per_file: int = 5,
    language: str = "en",
    reviewer_version: str = "pr-branch",
    execution_status: str = "success",
    degradation_reason: str | None = None,
    report_confidence: str = "normal",
    pr_syntax_ok: bool = True,
    review_meta: ReviewMeta | None = None,
    two_stage: bool = False,
    verify_rule_signals: bool = False,
    enable_critic: bool = False,
    critic_provider: ReviewModelProvider | None = None,
    gh_client: "GitHubClient | None" = None,
    enable_evidence_gate: bool = True,
    review_context: "ReviewContext | None" = None,
    prompt_addendum: str = "",
    contract_domains: dict | None = None,
    unit_review_max_units: int = 0,
    unit_review_workers: int = 1,
    unit_review_cost_ceiling_tokens: int = 0,
    unit_evidence_tokens: int = 3000,
    unit_provider_factory=None,
    repository_snapshot=None,
    follow_changes: bool = False,
    verify_behavior: bool = True,
    repository_evidence_tokens: int = 6000,
    max_input_tokens: int | None = None,
    analyzer_warnings: list[str] | None = None,
) -> ReviewReport:
    try:
        # The main review must start from the PR text and diff alone. Rule
        # results are retained as audit signals, not injected as conclusions.
        ctx = review_context if review_context is not None else build_review_context(
            pr, files, include_context_pack=not follow_changes, allow_remote_file_fetch=not follow_changes
        )
        if follow_changes and not two_stage and (repository_snapshot is not None or (pr.head_sha and pr.base_sha)):
            from src.reviewer.grounded_review import review_grounded, build_grounded_report
            def run_one_unit(unit_ctx, unit_files, snapshot):
                unit_provider = unit_provider_factory() if unit_provider_factory else provider
                try:
                    return review_grounded(
                        pr, unit_files, unit_provider, snapshot, unit_ctx, max_suggestions,
                        language, min(repository_evidence_tokens, unit_evidence_tokens),
                        verify_behavior,
                        max_suggestions_per_file=max_suggestions_per_file,
                        contract_domains=contract_domains,
                        # A unit is small: one discovery round and one verification
                        # round. Each extra round re-sends the prompt and evidence.
                        discovery_rounds=1, verify_rounds=1,
                    )
                finally:
                    if unit_provider_factory is not None:
                        unit_provider.close()

            def grounded(snapshot):
                # Large diffs are reviewed one semantic unit at a time. The gate is
                # deliberate: this path costs one review per unit, so it only runs
                # when the prompt was truncated and the unit count is capped.
                skip_details: dict = {}
                warnings = list(analyzer_warnings or [])
                if unit_review_max_units and ctx.truncated and len(files) > 2:
                    from src.reviewer.unit_review import run_units_for_review

                    merged, unit_details = run_units_for_review(
                        lambda unit_ctx, unit_files: run_one_unit(unit_ctx, unit_files, snapshot),
                        snapshot, files,
                        token_budget=ctx.patch_token_budget or repository_evidence_tokens,
                        max_units=unit_review_max_units, max_workers=unit_review_workers,
                        cost_ceiling_tokens=unit_review_cost_ceiling_tokens,
                    )
                    if merged is not None and unit_details.get("units_reviewed"):
                        merged.bundle["unit_details"] = unit_details
                        return build_grounded_report(
                            pr, files, findings, merged, ctx, reviewer_version=reviewer_version,
                            execution_status=execution_status, review_meta=review_meta,
                            pr_syntax_ok=pr_syntax_ok, report_confidence=report_confidence,
                            degradation_reason=degradation_reason,
                            max_input_tokens=max_input_tokens,
                            analyzer_warnings=warnings,
                        )
                    if unit_details.get("unit_review_skipped_cost"):
                        # Refused before spending anything; the reason is recorded
                        # rather than the run silently costing 73 CNY.
                        skip_details = unit_details
                        warnings.append(
                            "Per-unit review refused before dispatch: estimated "
                            f"{unit_details['estimated_tokens']:,} tokens for "
                            f"{unit_details['units_where_attempted']} of "
                            f"{unit_details['units_planned']} units exceeds the ceiling "
                            f"{unit_details['cost_ceiling_tokens']:,}."
                        )
                result = review_grounded(pr, files, provider, snapshot, ctx, max_suggestions,
                                         language, repository_evidence_tokens, verify_behavior,
                                         max_suggestions_per_file=max_suggestions_per_file,
                                         contract_domains=contract_domains)
                report = build_grounded_report(
                    pr, files, findings, result, ctx, reviewer_version=reviewer_version,
                    execution_status=execution_status, review_meta=review_meta, pr_syntax_ok=pr_syntax_ok,
                    report_confidence=report_confidence, degradation_reason=degradation_reason,
                    max_input_tokens=max_input_tokens,
                    analyzer_warnings=warnings,
                )
                if skip_details:
                    report.metrics["unit_review_skipped_cost"] = 1
                    report.metrics["unit_review_estimated_tokens"] = skip_details["estimated_tokens"]
                return report
            if repository_snapshot is not None:
                return grounded(repository_snapshot)
            from src.analyzer.repository_context import GitHubSnapshot
            from src.github.client import GitHubClient
            from src.utils.config import get_settings
            settings = get_settings()
            # Earlier legacy callers have already closed their fetch client.
            # Use a fresh scoped reader so HEAD evidence remains available.
            token = gh_client.token if gh_client is not None else settings.github_token
            with GitHubClient(token, timeout=settings.request_timeout_seconds) as reader:
                snapshot = GitHubSnapshot(reader, pr.repo, pr.base_sha, pr.head_sha, [f.filename for f in files])
                return grounded(snapshot)
        stage_audit = []
        if two_stage:
            from src.reviewer.two_stage import two_stage_review
            stage_result = two_stage_review(
                pr, files, [], provider,
                max_suggestions=max_suggestions,
                min_confidence=min_confidence,
                max_suggestions_per_file=max_suggestions_per_file,
                language=language,
                review_context=ctx,
                prompt_addendum=prompt_addendum,
            )
            stage_audit = stage_result.stages
            if stage_result.status != "success":
                execution_status = stage_result.status
                report_confidence = "partial" if execution_status == "partial" else "failed"
                degradation_reason = "Two-stage review contains incomplete hotspots"
            suggestions = stage_result.suggestions
            payload = ModelReviewPayload(
                summary=stage_result.summary,
                risk_level=stage_result.risk_level,
                suggestions=suggestions,
            )
            total_from_model = len(suggestions)
        else:
            raw = provider.complete_json(
                SYSTEM_PROMPT + ("\n" + prompt_addendum if prompt_addendum else ""),
                build_user_prompt(ctx.text, max_suggestions, language)
            )
            payload = parse_model_payload(raw)
            total_from_model = len(payload.suggestions)
        suggestions = filter_suggestions(
            payload.suggestions, files, max_suggestions, min_confidence,
            max_suggestions_per_file,
        )
        independent_suggestions = list(suggestions)
        verifiable_signals = select_verifiable_signals(findings) if verify_rule_signals else []
        verification = ProviderSignalVerifier(provider).verify_batch(
            build_signal_envelopes(verifiable_signals, files)
        )
        merged = merge_review_results(suggestions, verifiable_signals, verification)
        suggestions = filter_suggestions(
            merged.suggestions, files, max_suggestions, min_confidence, max_suggestions_per_file
        )
        # Every origin passes the same output-integrity and Critic policy.
        evidence_rejections = []
        if enable_evidence_gate:
            suggestions, evidence_rejections = validate_ai_evidence(suggestions, files)
        pre_critic_suggestions = list(suggestions)
        critic_candidates = select_critic_candidates(suggestions) if enable_critic else []
        head_cache = {}
        def read_frozen_head(path):
            if path not in head_cache:
                head_cache[path] = gh_client.get_file_contents(pr.repo, path, pr.head_sha)
            return head_cache[path]
        read_head_file = read_frozen_head if gh_client is not None and pr.head_sha else None
        critic_batch = critique_batch(
            critic_provider or provider, critic_candidates, files, read_head_file=read_head_file
        )
        if enable_critic:
            suggestions, critic_decisions = apply_critic(suggestions, critic_candidates, critic_batch)
        else:
            critic_decisions = []
        pending_indexes = {
            item["suggestion_index"] for item in critic_decisions
            if item["decision"] in {"uncertain", "unverified"}
        }
        pending_suggestions = [
            suggestion.model_copy(update={"source": "ai_needs_context"})
            for index, suggestion in enumerate(pre_critic_suggestions)
            if index in pending_indexes
        ]
        # Provider outages and insufficient evidence remain visible for follow-up,
        # but must not be counted or published as verified findings.
        suggestions = [s for s in suggestions if s.source != "ai_critic_unverified"]
        independent_suggestion_count = sum(
            any((s.file_path, s.line, s.title) == (i.file_path, i.line, i.title)
                for i in independent_suggestions) for s in suggestions
        )
        # The visible risk level must describe the final, evidence-gated
        # suggestions rather than an unfiltered model draft.
        risk_level = _max_severity([suggestion.severity for suggestion in suggestions])
        warnings: list[str] = []
        # Scans that could not finish must reach the reader, not just the log.
        if analyzer_warnings:
            warnings.extend(analyzer_warnings)
        if stage_audit and any(s["status"] != "success" for s in stage_audit):
            warnings.append("Two-stage failures recorded; inspect stage_results for coverage.")
        hidden = total_from_model - independent_suggestion_count
        if hidden > 0:
            warnings.append(
                f"{hidden} suggestion(s) filtered out (low confidence, "
                f"unchanged line, or duplicate)"
            )
        if ctx.truncated:
            if report_confidence in {"normal", "fallback"}:
                report_confidence = "partial"
            warnings.append(
                "Patch context was truncated to fit token budget; "
                "some files were not analyzed by AI."
            )
        partial_warning = partial_inclusion_warning(ctx)
        if partial_warning:
            warnings.append(partial_warning)
        # One run-level view of every budget that shaped this prompt, with an
        # optional cap. Measured always; enforced only when a cap is configured.
        run_budget = run_budget_report((ctx.budgets,), cap=max_input_tokens)
        warnings.extend(budget_warnings(run_budget))
        skipped_ctx = [
            SkippedContextFile(file_path=fpath, reason=reason)
            for fpath, reason in ctx.skipped_files
        ]
        if pending_suggestions:
            warnings.append(f"{len(pending_suggestions)} candidate(s) need additional context; not confirmed defects.")
        if skipped_ctx:
            skipped_names = ", ".join(s.file_path for s in skipped_ctx)
            warnings.append(
                f"{len(skipped_ctx)} file(s) excluded from AI patch context: {skipped_names}"
            )

        # Track dismissed rule alerts
        confirmed = [item.model_dump(mode="json") for item in merged.confirmed]
        dismissed = [item.model_dump(mode="json") for item in merged.dismissed]
        if dismissed:
            warnings.append(
                f"{len(dismissed)} rule alert(s) dismissed by AI as false positives"
            )

        # Build fix tracking from previous review comments
        fix_tracking = _build_fix_tracking(
            repo=pr.repo,
            pr_number=pr.number,
            current_suggestions=suggestions,
            gh_client=gh_client,
        )

        completeness = _build_completeness(
            pr=pr, files=files,
            skipped_ctx=skipped_ctx, ctx_truncated=ctx.truncated,
            used_ai=True, ai_failed=(execution_status == "failed"),
            pr_syntax_ok=pr_syntax_ok,
        )
        report = ReviewReport(
            pr=pr,
            files=files,
            summary=payload.summary,
            risk_level=risk_level,
            rule_findings=findings,
            suggestions=suggestions,
            used_ai=True,
            analysis_warnings=warnings,
            hidden_suggestions_count=hidden,
            context_truncated=ctx.truncated,
            context_token_count=ctx.token_count,
            patch_token_budget=ctx.patch_token_budget,
            context_tokenizer=tokenizer_name(),
            skipped_context_files=skipped_ctx,
            reviewer_version=reviewer_version,
            execution_status=execution_status,
            degradation_reason=degradation_reason,
            report_confidence=report_confidence,
            completeness=completeness,
            review_meta=review_meta or ReviewMeta(),
            fix_tracking=fix_tracking,
            confirmed_signals=confirmed,
            dismissed_signals=dismissed,
            unresolved_signals=[item.model_dump(mode="json") for item in merged.unresolved],
            unverified_signals=[item.model_dump(mode="json") for item in merged.unverified],
            evidence_rejections=evidence_rejections,
            critic_decisions=critic_decisions,
            pending_suggestions=pending_suggestions,
            stage_results=stage_audit,
            budget_report=run_budget,
            metrics={
                "raw_signal_count": len(findings),
                "main_review_prompt_signal_count": 0,
                "independent_suggestion_count": independent_suggestion_count,
                "evidence_rejected_suggestion_count": len(evidence_rejections),
                "critic_request_count": int(bool(critic_candidates)),
                "critic_confirmed_count": sum(
                    item["decision"] == CriticDecision.CONFIRMED.value for item in critic_decisions
                ),
                "critic_dismissed_count": sum(
                    item["decision"] == CriticDecision.DISMISSED.value for item in critic_decisions
                ),
                "critic_uncertain_count": sum(
                    item["decision"] == CriticDecision.UNCERTAIN.value for item in critic_decisions
                ),
                "critic_unverified_count": sum(
                    item["decision"] == CriticDecision.UNVERIFIED.value for item in critic_decisions
                ),
                "context_token_count": ctx.token_count,
                "signal_verification_request_count": int(bool(verifiable_signals)),
                "telemetry_only_signal_count": len(findings) - len(verifiable_signals),
                "confirmed_signal_count": len(merged.confirmed),
                "dismissed_signal_count": len(merged.dismissed),
                "unresolved_signal_count": len(merged.unresolved),
                "unverified_signal_count": len(merged.unverified),
            },
        )
        # Post-generation validation
        validation_issues = validate_report(report)
        if validation_issues:
            report.analysis_warnings.extend(validation_issues)
            logger.warning(f"Report validation issues: {validation_issues}")
        return report
    except (ProviderError, ValueError) as exc:
        logger.warning("AI review failed, falling back to rule-only: %s", exc)
        # Level 2: rule-only fallback
        try:
            report = build_rule_only_report(pr, files, findings, language=language,
                                            execution_status="degraded",
                                            degradation_reason=f"AI 调用失败: {exc}",
                                            report_confidence="partial",
                                            pr_syntax_ok=pr_syntax_ok,
                                            review_meta=review_meta,
                                            gh_client=gh_client,
                                            analyzer_warnings=analyzer_warnings)
        except Exception as rule_exc:
            # Level 3: diagnostic report — rules also failed
            logger.warning("Rule-only fallback also failed: %s", rule_exc)
            return build_diagnostic_report(
                pr=pr, files=files,
                error=f"AI failed: {exc}; rules also failed: {rule_exc}",
                reviewer_version=reviewer_version,
                execution_status="failed",
                language=language,
                analyzer_warnings=analyzer_warnings,
            )
        report.ai_failure_reason = str(exc)
        # Re-build completeness to reflect AI-attempted-but-failed state
        report.completeness = _build_completeness(
            pr=pr, files=files,
            skipped_ctx=report.skipped_context_files,
            ctx_truncated=report.context_truncated,
            used_ai=True, ai_failed=True,
            pr_syntax_ok=pr_syntax_ok,
        )
        report.analysis_warnings = [
            f"AI review unavailable: {exc}. Showing rule-based analysis only.",
            *(analyzer_warnings or []),
        ]
        return report
    except Exception as exc:
        # Catch-all: Level 3 diagnostic for any unexpected error
        logger.warning("Unexpected error in review_with_ai, generating diagnostic: %s", exc)
        return build_diagnostic_report(
            pr=pr, files=files,
            error=f"Unexpected review error: {exc}",
            reviewer_version=reviewer_version,
            execution_status="failed",
            language=language,
            analyzer_warnings=analyzer_warnings,
        )


def _build_fix_tracking(
    repo: str,
    pr_number: int,
    current_suggestions: list[ReviewSuggestion],
    gh_client: GitHubClient | None,
) -> list[FixTrackingItem]:
    """Compare current suggestions with previous review comments to build fix tracking.

    Fetches previous review comments from GitHub, matches them against current
    suggestions. If a previous comment's file+line doesn't appear in current
    suggestions, it's marked as 'fixed'. If it still appears, 'still_present'.
    """
    items: list[FixTrackingItem] = []

    if not gh_client or not current_suggestions:
        return items

    # Build set of (file_path, line) for current suggestions for fast lookup
    current_set: set[tuple[str, int | None]] = {
        (s.file_path, s.line) for s in current_suggestions if s.file_path
    }

    try:
        prev_comments = gh_client.get_review_comments(repo, pr_number)
    except Exception as exc:
        logger.warning("Failed to fetch previous review comments for fix tracking: %s", exc)
        return items

    for comment in prev_comments:
        body = comment.get("body", "") or ""
        file_path = comment.get("path", "") or ""
        line = comment.get("line")

        if not file_path:
            continue

        # Extract title from comment body (format: **severity** (confidence%): title)
        title = ""
        header_match = re.search(
            r"\*\*(\w+)\*\*\s*\(\d+%\):\s*(.+)$", body, re.MULTILINE
        )
        if header_match:
            title = header_match.group(2).strip()
        else:
            # Fallback: use first line
            title = body.split("\n")[0].strip()[:80]

        key = (file_path, line)
        if key in current_set:
            items.append(FixTrackingItem(
                previous_title=title,
                file_path=file_path,
                previous_line=line,
                status="still_present",
                detail="建议在当前审查结果中仍存在",
            ))
        else:
            items.append(FixTrackingItem(
                previous_title=title,
                file_path=file_path,
                previous_line=line,
                status="fixed",
                detail="该行未出现在本次审查结果中，可能已修复",
            ))

    return items


def _max_severity(values: list[Severity]) -> Severity:
    if not values:
        return Severity.LOW
    rank = {
        Severity.LOW: 1,
        Severity.MEDIUM: 2,
        Severity.HIGH: 3,
        Severity.CRITICAL: 4,
    }
    return max(values, key=lambda value: rank[value])
