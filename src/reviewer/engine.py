from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, ValidationError

from src.analyzer.context_builder import build_review_context
from src.analyzer.diff_parser import changed_line_map
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.github.client import GitHubClient

from src.models import (
    ReviewMeta,
    ChangedFile,
    CompletenessItem,
    PullRequest,
    ReviewReport,
    ReviewSuggestion,
    RiskFinding,
    Severity,
    SkippedContextFile,
    StepStatus,
)
from src.reviewer.prompt import SYSTEM_PROMPT, build_user_prompt
from src.reviewer.provider import ProviderError, ReviewModelProvider

logger = logging.getLogger(__name__)


class ModelReviewPayload(BaseModel):
    summary: str
    risk_level: Severity
    suggestions: list[ReviewSuggestion]


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
    if skipped_count == 0:
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
        analysis_warnings=[f"Diagnostic report generated due to: {error}"],
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
    risk_level = _max_severity([finding.severity for finding in findings])
    suggestions = [
        ReviewSuggestion(
            file_path=finding.file_path,
            line=finding.line,
            severity=finding.severity,
            confidence=finding.confidence,
            title=finding.title,
            reason=finding.evidence,
            recommendation=finding.recommendation,
        )
        for finding in findings
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
        reviewer_version=reviewer_version,
        execution_status=execution_status,
        degradation_reason=degradation_reason,
        report_confidence=report_confidence,
        completeness=completeness,
        review_meta=review_meta or ReviewMeta(),
        fix_tracking=fix_tracking,
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
    gh_client: "GitHubClient | None" = None,
) -> ReviewReport:
    try:
        ctx = build_review_context(pr, files, findings)
        if two_stage:
            from src.reviewer.two_stage import two_stage_review
            summary, risk_level, suggestions = two_stage_review(
                pr, files, findings, provider,
                max_suggestions=max_suggestions,
                min_confidence=min_confidence,
                max_suggestions_per_file=max_suggestions_per_file,
                language=language,
            )
            payload = ModelReviewPayload(
                summary=summary,
                risk_level=risk_level,
                suggestions=suggestions,
            )
            total_from_model = len(suggestions)
        else:
            raw = provider.complete_json(
                SYSTEM_PROMPT, build_user_prompt(ctx.text, max_suggestions, language)
            )
            payload = _parse_model_payload(raw)
            total_from_model = len(payload.suggestions)
        suggestions = _filter_suggestions(
            payload.suggestions, files, max_suggestions, min_confidence,
            max_suggestions_per_file,
        )
        warnings: list[str] = []
        hidden = total_from_model - len(suggestions)
        if hidden > 0:
            warnings.append(
                f"{hidden} suggestion(s) filtered out (low confidence, "
                f"unchanged line, or duplicate)"
            )
        if ctx.truncated:
            warnings.append(
                "Patch context was truncated to fit token budget; "
                "some files were not analyzed by AI."
            )
        skipped_ctx = [
            SkippedContextFile(file_path=fpath, reason=reason)
            for fpath, reason in ctx.skipped_files
        ]
        if skipped_ctx:
            skipped_names = ", ".join(s.file_path for s in skipped_ctx)
            warnings.append(
                f"{len(skipped_ctx)} file(s) excluded from AI patch context: {skipped_names}"
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
            used_ai=True, ai_failed=False,
            pr_syntax_ok=pr_syntax_ok,
        )
        report = ReviewReport(
            pr=pr,
            files=files,
            summary=payload.summary,
            risk_level=payload.risk_level,
            rule_findings=findings,
            suggestions=suggestions,
            used_ai=True,
            analysis_warnings=warnings,
            hidden_suggestions_count=hidden,
            context_truncated=ctx.truncated,
            skipped_context_files=skipped_ctx,
            reviewer_version=reviewer_version,
            execution_status=execution_status,
            degradation_reason=degradation_reason,
            report_confidence=report_confidence,
            completeness=completeness,
            review_meta=review_meta or ReviewMeta(),
            fix_tracking=fix_tracking,
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
                                            gh_client=gh_client)
        except Exception as rule_exc:
            # Level 3: diagnostic report — rules also failed
            logger.warning("Rule-only fallback also failed: %s", rule_exc)
            return build_diagnostic_report(
                pr=pr, files=files,
                error=f"AI failed: {exc}; rules also failed: {rule_exc}",
                reviewer_version=reviewer_version,
                execution_status="failed",
                language=language,
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
            f"AI review unavailable: {exc}. Showing rule-based analysis only."
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


def _parse_model_payload(raw: str) -> ModelReviewPayload:
    # Strategy 1: direct JSON parse
    try:
        data = json.loads(raw)
        return ModelReviewPayload.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        logger.warning("JSON fallback: direct parse failed, trying fenced code block")

    # Strategy 2: extract from ```json fenced code block
    match = re.search(r"```json\s*([\s\S]*?)\s*```", raw)
    if match:
        try:
            data = json.loads(match.group(1))
            return ModelReviewPayload.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            logger.warning("JSON fallback: fenced code block parse failed, trying brace extraction")

    # Strategy 3: extract from first { to last }
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            data = json.loads(match.group(0))
            return ModelReviewPayload.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            logger.warning("JSON fallback: brace extraction also failed")

    raise ValueError(
        f"Model returned invalid review JSON. Raw output prefix: {raw[:300]}"
    )


def _filter_suggestions(
    suggestions: list[ReviewSuggestion],
    files: list[ChangedFile],
    max_suggestions: int,
    min_confidence: float = 0.0,
    max_suggestions_per_file: int = 5,
) -> list[ReviewSuggestion]:
    changed_lines = changed_line_map(files)
    filtered: list[ReviewSuggestion] = []
    seen_exact: set[tuple[str, int | None, str]] = set()
    seen_reason_prefix: set[tuple[str, int | None, str]] = set()
    per_file_count: dict[str, int] = {}

    for suggestion in suggestions:
        if suggestion.confidence < min_confidence:
            continue
        # Drop suggestions with empty reason or recommendation
        if not suggestion.reason.strip() or not suggestion.recommendation.strip():
            continue
        if suggestion.line is not None:
            if suggestion.line not in changed_lines.get(suggestion.file_path, set()):
                continue
        # Exact dedup: same file + line + title
        exact_key = (suggestion.file_path, suggestion.line, suggestion.title.lower())
        if exact_key in seen_exact:
            continue
        # Fuzzy dedup: same file + line + similar reason prefix (>20 chars)
        reason_prefix = suggestion.reason.strip().lower()[:40]
        if suggestion.line is not None and len(reason_prefix) >= 15:
            reason_key = (suggestion.file_path, suggestion.line, reason_prefix)
            if reason_key in seen_reason_prefix:
                continue
            seen_reason_prefix.add(reason_key)
        # Enforce per-file cap
        if per_file_count.get(suggestion.file_path, 0) >= max_suggestions_per_file:
            continue
        seen_exact.add(exact_key)
        try:
            Severity(suggestion.severity)
        except ValueError:
            continue
        if not (0.0 <= suggestion.confidence <= 1.0):
            continue
        filtered.append(suggestion)
        per_file_count[suggestion.file_path] = (
            per_file_count.get(suggestion.file_path, 0) + 1
        )

    severity_rank = {
        Severity.CRITICAL: 4,
        Severity.HIGH: 3,
        Severity.MEDIUM: 2,
        Severity.LOW: 1,
    }
    filtered.sort(
        key=lambda item: (severity_rank[item.severity], item.confidence), reverse=True
    )
    return filtered[:max_suggestions]


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
