from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from src.analyzer.context_builder import build_review_context
from src.analyzer.diff_parser import changed_line_map
from src.models import ChangedFile, PullRequest, ReviewReport, ReviewSuggestion, RiskFinding, Severity
from src.reviewer.prompt import SYSTEM_PROMPT, build_user_prompt
from src.reviewer.provider import ReviewModelProvider


class ModelReviewPayload(BaseModel):
    summary: str
    risk_level: Severity
    suggestions: list[ReviewSuggestion]


def build_rule_only_report(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
) -> ReviewReport:
    summary = (
        f"PR changes {len(files)} file(s), with "
        f"{sum(f.additions for f in files)} additions and {sum(f.deletions for f in files)} deletions. "
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
) -> ReviewReport:
    context = build_review_context(pr, files, findings)
    raw = provider.complete_json(SYSTEM_PROMPT, build_user_prompt(context, max_suggestions))
    payload = _parse_model_payload(raw)
    suggestions = _filter_suggestions(payload.suggestions, files, max_suggestions)
    return ReviewReport(
        pr=pr,
        files=files,
        summary=payload.summary,
        risk_level=payload.risk_level,
        rule_findings=findings,
        suggestions=suggestions,
        used_ai=True,
    )


def _parse_model_payload(raw: str) -> ModelReviewPayload:
    try:
        data = json.loads(raw)
        return ModelReviewPayload.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Model returned invalid review JSON: {exc}") from exc


def _filter_suggestions(
    suggestions: list[ReviewSuggestion],
    files: list[ChangedFile],
    max_suggestions: int,
) -> list[ReviewSuggestion]:
    changed_lines = changed_line_map(files)
    filtered: list[ReviewSuggestion] = []
    seen: set[tuple[str, int | None, str]] = set()

    for suggestion in suggestions:
        if suggestion.line is not None:
            if suggestion.line not in changed_lines.get(suggestion.file_path, set()):
                continue
        key = (suggestion.file_path, suggestion.line, suggestion.title.lower())
        if key in seen:
            continue
        seen.add(key)
        filtered.append(suggestion)

    severity_rank = {
        Severity.CRITICAL: 4,
        Severity.HIGH: 3,
        Severity.MEDIUM: 2,
        Severity.LOW: 1,
    }
    filtered.sort(key=lambda item: (severity_rank[item.severity], item.confidence), reverse=True)
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
