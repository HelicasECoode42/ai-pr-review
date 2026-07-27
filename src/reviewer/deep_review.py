"""Stage 2 of two-stage review: deep-dive on a single hotspot.

Takes one hotspot from the triage stage and produces detailed suggestions.
"""

from __future__ import annotations

import json
import logging

from src.models import ReviewSuggestion, Severity
from src.reviewer.provider import ReviewModelProvider

logger = logging.getLogger(__name__)

DEEP_SYSTEM = """You are an experienced software engineer doing a focused code review.
You are looking at a SPECIFIC risk hotspot identified during triage.
Only analyze the code in this hotspot — do not comment on unrelated files.
Every suggestion must quote an exact changed-line excerpt and state a concrete
failure scenario.
Return valid JSON only."""


def _build_deep_prompt(hotspot: dict, ctx_text: str, language: str) -> str:
    lang_note = "Output in Chinese (Simplified)." if language == "zh" else "Output in English."
    return f"""Review this risk hotspot in detail.

Hotspot: {hotspot['file_path']} (lines ~{hotspot['start_line']}-{hotspot['end_line']})
Risk category: {hotspot.get('risk_category', 'unknown')}
Triage reason: {hotspot['reason']}

{lang_note}

Return JSON:
{{
  "suggestions": [
    {{
      "file_path": "path/to/file",
      "line": 123,
      "severity": "low|medium|high|critical",
      "confidence": 0.0,
      "title": "short actionable title",
      "reason": "why this is risky; must reference specific changed lines",
      "recommendation": "concrete fix",
      "evidence": ["exact excerpt from the cited changed line"],
      "failure_scenario": "execution path and observable failure"
    }}
  ]
}}

Focus ONLY on this hotspot. Max 3 suggestions per hotspot. If no real issue, return empty array.

Diff context:
{ctx_text}
"""


def _safe_severity(s: object) -> Severity:
    try:
        return Severity(str(s).lower())
    except (ValueError, AttributeError):
        return Severity.MEDIUM


def run_deep_review(
    hotspot: dict,
    ctx_text: str,
    provider: ReviewModelProvider,
    language: str = "en",
) -> list[ReviewSuggestion]:
    """Deep-dive a single hotspot, returning 0-3 suggestions.

    Does NOT raise on failure — returns empty list instead,
    so one failed hotspot doesn't block others.
    """
    hotspot_path = hotspot.get("file_path", "?") if isinstance(hotspot, dict) else "?"
    try:
        raw = provider.complete_json(
            DEEP_SYSTEM,
            _build_deep_prompt(hotspot, ctx_text, language),
        )
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise TypeError(f"Expected JSON object, got {type(result).__name__}")
    except Exception as exc:
        logger.warning("Deep-dive on %s failed: %s", hotspot_path, exc)
        return []

    suggestions: list[ReviewSuggestion] = []
    raw_suggestions = result.get("suggestions", [])
    if not isinstance(raw_suggestions, list):
        return suggestions
    for s in raw_suggestions:
        try:
            if not isinstance(s, dict):
                continue
            confidence = s.get("confidence", 0.5)
            try:
                confidence = min(max(float(confidence), 0.0), 1.0)
            except (TypeError, ValueError):
                confidence = 0.5
            evidence = s.get("evidence")
            if not isinstance(evidence, list):
                evidence = []
            suggestions.append(ReviewSuggestion(
                file_path=str(s.get("file_path", hotspot["file_path"])),
                line=s.get("line"),
                severity=_safe_severity(s.get("severity", "medium")),
                confidence=confidence,
                title=str(s.get("title", "Issue")),
                reason=str(s.get("reason", "")),
                recommendation=str(s.get("recommendation", "")),
                evidence=[str(item) for item in evidence],
                failure_scenario=str(s.get("failure_scenario", "")),
            ))
        except Exception:
            logger.warning("Skipping malformed suggestion in %s", hotspot_path)
    return suggestions
