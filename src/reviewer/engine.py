from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, ValidationError

from src.analyzer.context_builder import build_review_context
from src.analyzer.diff_parser import changed_line_map
from src.models import ChangedFile, PullRequest, ReviewReport, ReviewSuggestion, RiskFinding, Severity
from src.reviewer.prompt import SYSTEM_PROMPT, build_user_prompt
from src.reviewer.provider import ProviderError, ReviewModelProvider

logger = logging.getLogger(__name__)


class ModelReviewPayload(BaseModel):
    summary: str
    risk_level: Severity
    suggestions: list[ReviewSuggestion]


def build_rule_only_report(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    language: str = "en",
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
    return ReviewReport(
        pr=pr,
        files=files,
        summary=summary,
        risk_level=risk_level,
        rule_findings=findings,
        suggestions=suggestions,
        used_ai=False,
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
) -> ReviewReport:
    try:
        context, context_truncated = build_review_context(pr, files, findings)
        raw = provider.complete_json(
            SYSTEM_PROMPT, build_user_prompt(context, max_suggestions, language)
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
        if context_truncated:
            warnings.append(
                "Patch context was truncated to fit token budget; "
                "some files were not analyzed by AI."
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
            context_truncated=context_truncated,
        )
    except (ProviderError, ValueError) as exc:
        logger.warning("AI review failed, falling back to rule-only: %s", exc)
        report = build_rule_only_report(pr, files, findings)
        report.ai_failure_reason = str(exc)
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
        pass

    # Strategy 2: extract from ```json fenced code block
    match = re.search(r"```json\s*([\s\S]*?)\s*```", raw)
    if match:
        try:
            data = json.loads(match.group(1))
            return ModelReviewPayload.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

    # Strategy 3: extract from first { to last }
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            data = json.loads(match.group(0))
            return ModelReviewPayload.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

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
    seen: set[tuple[str, int | None, str]] = set()
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
        key = (suggestion.file_path, suggestion.line, suggestion.title.lower())
        if key in seen:
            continue
        # Enforce per-file cap
        if per_file_count.get(suggestion.file_path, 0) >= max_suggestions_per_file:
            continue
        seen.add(key)
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
