"""Bounded triage/deep review with explicit coverage and failure states."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.analyzer.context_builder import ReviewContext, build_review_context
from src.models import ChangedFile, PullRequest, ReviewSuggestion, RiskFinding, Severity
from src.reviewer.deep_review import run_deep_review
from src.reviewer.model_payload import parse_model_payload
from src.reviewer.prompt import SYSTEM_PROMPT, build_user_prompt
from src.reviewer.provider import ReviewModelProvider
from src.reviewer.suggestion_filter import filter_suggestions
from src.reviewer.triage import run_triage


@dataclass
class TwoStageResult:
    summary: str
    risk_level: Severity
    suggestions: list[ReviewSuggestion]
    status: str = "success"
    stages: list[dict] = field(default_factory=list)


def two_stage_review(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    provider: ReviewModelProvider,
    max_suggestions: int = 15,
    min_confidence: float = 0.0,
    max_suggestions_per_file: int = 5,
    language: str = "en",
    review_context: ReviewContext | None = None,
    prompt_addendum: str = "",
) -> TwoStageResult:
    # Rule conclusions never enter primary review, including triage or fallback.
    ctx = review_context if review_context is not None else build_review_context(pr, files)
    stages = []
    try:
        hotspots = run_triage(pr, files, [], provider, ctx.text)
        stages.append({"stage": "triage", "status": "success"})
    except Exception as exc:
        hotspots = []
        stages.append({"stage": "triage", "status": "failed", "error_type": type(exc).__name__})
    if not hotspots:
        # No high-risk hotspot does not imply no medium/low issues. Perform
        # the general review; recovery from a failed triage is traceable.
        raw = provider.complete_json(
            SYSTEM_PROMPT + ("\n" + prompt_addendum if prompt_addendum else ""),
            build_user_prompt(ctx.text, max_suggestions, language),
        )
        payload = parse_model_payload(raw)
        suggestions = filter_suggestions(
            payload.suggestions, files, max_suggestions, min_confidence, max_suggestions_per_file
        )
        stages.append({"stage": "one_shot_fallback", "status": "success"})
        return TwoStageResult(payload.summary, payload.risk_level, suggestions, stages=stages)
    suggestions = []
    for hotspot in hotspots[:5]:
        result = run_deep_review(hotspot, ctx.text, provider, language, prompt_addendum)
        suggestions.extend(result.suggestions)
        stages.append(
            {
                "stage": "deep_review",
                "file_path": hotspot["file_path"],
                "status": result.status,
                "error_type": result.error_type,
            }
        )
    final = filter_suggestions(
        suggestions, files, max_suggestions, min_confidence, max_suggestions_per_file
    )
    deep_stages = stages[1:]
    failed = sum(s["status"] != "success" for s in deep_stages)
    status = (
        "failed"
        if all(s["status"] == "failed" for s in deep_stages)
        else ("partial" if failed else "success")
    )
    risk = max(
        (s.severity for s in final), key=lambda s: list(Severity).index(s), default=Severity.LOW
    )
    summary = (
        f"Two-stage review of PR #{pr.number}: {len(deep_stages)} hotspots reviewed; "
        f"{failed} incomplete; {len(final)} suggestions. Coverage status: {status}."
    )
    return TwoStageResult(summary, risk, final, status, stages)
