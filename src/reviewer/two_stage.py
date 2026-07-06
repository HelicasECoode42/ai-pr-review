"""Two-stage AI review: triage → deep-dive on hotspots.

This module is the entry point that orchestrates stage 1 (triage) and
stage 2 (deep-dive).  Individual stages are implemented in triage.py
and deep_review.py for testability and reuse.
"""

from __future__ import annotations

import logging

from src.analyzer.context_builder import build_review_context
from src.models import ChangedFile, PullRequest, ReviewSuggestion, RiskFinding, Severity
from src.reviewer.provider import ProviderError, ReviewModelProvider
from src.reviewer.suggestion_filter import filter_suggestions
from src.reviewer.triage import run_triage
from src.reviewer.deep_review import run_deep_review

logger = logging.getLogger(__name__)


def two_stage_review(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    provider: ReviewModelProvider,
    max_suggestions: int = 15,
    min_confidence: float = 0.0,
    max_suggestions_per_file: int = 5,
    language: str = "en",
) -> tuple[str, Severity, list[ReviewSuggestion]]:
    """Run two-stage AI review: triage → deep-dive on hotspots.

    Returns (summary, risk_level, suggestions).
    Falls back to one-shot if stage 1 returns no hotspots.
    """
    # ── Stage 1: Triage ───────────────────────────────────
    try:
        hotspots = run_triage(pr, files, findings, provider)
    except (ProviderError, Exception) as exc:
        logger.warning("Two-stage: triage failed (%s), fallback to one-shot", exc)
        return _fallback_one_shot(pr, files, findings, provider,
                                  max_suggestions, min_confidence,
                                  max_suggestions_per_file, language)

    if not hotspots:
        logger.info("Two-stage: triage found no hotspots — clean PR")
        return (
            f"PR #{pr.number}: {pr.title} — no high-risk areas identified. "
            f"{len(findings)} rule-based finding(s).",
            _highest_severity(findings),
            [],
        )

    logger.info("Two-stage: %d hotspot(s) identified for deep review", len(hotspots))

    # ── Stage 2: Deep-dive per hotspot ────────────────────
    all_suggestions: list[ReviewSuggestion] = []
    ctx = build_review_context(pr, files, findings)

    for i, hotspot in enumerate(hotspots[:5]):
        suggestions = run_deep_review(hotspot, ctx.text, provider, language)
        all_suggestions.extend(suggestions)
        if not suggestions:
            logger.info("Deep-dive #%d on %s returned no suggestions",
                        i, hotspot.get("file_path", "?"))

    # ── Merge & deduplicate ────────────────────────────────
    final = filter_suggestions(
        all_suggestions, files, max_suggestions, min_confidence,
        max_suggestions_per_file,
    )

    summary = (
        f"Two-stage review of PR #{pr.number}: {pr.title}. "
        f"Stage 1 identified {len(hotspots)} hotspot(s); "
        f"stage 2 produced {len(all_suggestions)} suggestion(s) "
        f"({len(final)} after filtering). "
        f"{len(findings)} rule-based finding(s) also present."
    )

    risk = _compute_risk(final, findings)
    return summary, risk, final


# ── Helpers ────────────────────────────────────────────────

def _highest_severity(findings: list[RiskFinding]) -> Severity:
    sevs = {f.severity for f in findings}
    for s in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM):
        if s in sevs:
            return s
    return Severity.LOW


def _compute_risk(
    suggestions: list[ReviewSuggestion],
    findings: list[RiskFinding],
) -> Severity:
    sevs = set()
    for s in suggestions:
        sevs.add(s.severity)
    for f in findings:
        sevs.add(f.severity)
    for s in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM):
        if s in sevs:
            return s
    return Severity.LOW


def _fallback_one_shot(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    provider: ReviewModelProvider,
    max_suggestions: int,
    min_confidence: float,
    max_suggestions_per_file: int,
    language: str,
) -> tuple[str, Severity, list[ReviewSuggestion]]:
    """Fallback to one-shot review when triage fails."""
    from src.reviewer.model_payload import parse_model_payload
    from src.reviewer.prompt import SYSTEM_PROMPT, build_user_prompt

    ctx = build_review_context(pr, files, findings)
    raw = provider.complete_json(
        SYSTEM_PROMPT, build_user_prompt(ctx.text, max_suggestions, language)
    )
    payload = parse_model_payload(raw)
    suggestions = filter_suggestions(
        payload.suggestions, files, max_suggestions,
        min_confidence, max_suggestions_per_file,
    )
    return payload.summary, payload.risk_level, suggestions
