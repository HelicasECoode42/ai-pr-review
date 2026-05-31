from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, ValidationError

from src.analyzer.context_builder import build_review_context
from src.analyzer.diff_parser import changed_line_map
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
    completeness = _build_completeness(
        pr=pr, files=files,
        skipped_ctx=[], ctx_truncated=False,
        used_ai=False, ai_failed=False,
        pr_syntax_ok=pr_syntax_ok,
    )
    return ReviewReport(
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
    )


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
) -> ReviewReport:
    try:
        ctx = build_review_context(pr, files, findings)
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
        completeness = _build_completeness(
            pr=pr, files=files,
            skipped_ctx=skipped_ctx, ctx_truncated=ctx.truncated,
            used_ai=True, ai_failed=False,
            pr_syntax_ok=pr_syntax_ok,
        )
        return ReviewReport(
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
        )
    except (ProviderError, ValueError) as exc:
        logger.warning("AI review failed, falling back to rule-only: %s", exc)
        report = build_rule_only_report(pr, files, findings, language=language,
                                        execution_status="degraded",
                                        degradation_reason=f"AI 调用失败: {exc}",
                                        report_confidence="partial",
                                        pr_syntax_ok=pr_syntax_ok,
                                        review_meta=review_meta)
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
