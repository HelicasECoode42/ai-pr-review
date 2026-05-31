"""Two-stage AI review: triage first, then deep-dive on high-risk areas.

Stage 1 (Triage): Lightweight prompt with file overview → identify risk hotspots.
Stage 2 (Deep-dive): Focused prompts with full context for each hotspot → detailed suggestions.

Both stages use the same model, but the context budget is allocated
strategically — stage 1 gets a broad view, stage 2 gets deep context.
"""

from __future__ import annotations

import json
import logging

from src.analyzer.context_builder import build_review_context
from src.models import (
    ChangedFile,
    PullRequest,
    ReviewSuggestion,
    RiskFinding,
    Severity,
)
from src.reviewer.provider import ProviderError, ReviewModelProvider

logger = logging.getLogger(__name__)

# ── Stage 1: Triage prompt ────────────────────────────────

TRIAGE_SYSTEM = """You are a senior engineer doing a rapid first-pass review.
Your job: identify which files and line ranges in a PR need deeper scrutiny.
Focus on security, data integrity, concurrency, and crash risks.
Be precise — give specific file paths and line numbers.
Return valid JSON only."""


def _build_triage_prompt(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
) -> str:
    """Build a compact triage prompt focusing on file-level risk signals."""
    file_list = "\n".join(
        f"- {f.filename} ({f.status}): +{f.additions}/-{f.deletions}"
        for f in files[:50]
    )
    if len(files) > 50:
        file_list += f"\n  ... and {len(files) - 50} more files"

    rule_alerts = "\n".join(
        f"- [{f.severity.value}] {f.file_path}:{f.line or '?'} — {f.title}"
        for f in findings[:20]
    ) if findings else "No rule-based findings."

    return f"""PR #{pr.number}: {pr.title}

Changed files ({len(files)} total):
{file_list}

Rule-based alerts (first 20):
{rule_alerts}

Identify HIGH-RISK areas that need deep analysis. A high-risk area is:
- A file or function where a bug could cause data loss, security breach, or crash
- A change to auth, payment, CI/CD, or reviewer infrastructure
- A file with many additions (+100 lines) or complex logic
- Any file flagged by rule alerts above

Return JSON:
{{
  "hotspots": [
    {{
      "file_path": "path/to/file",
      "start_line": 10,
      "end_line": 50,
      "reason": "why this area needs closer review",
      "risk_category": "security|correctness|performance|infrastructure"
    }}
  ]
}}

Limit to the top 5 hotspots. If no significant risks, return empty array."""


# ── Stage 2: Deep-dive ─────────────────────────────────────

DEEP_SYSTEM = """You are an experienced software engineer doing a focused code review.
You are looking at a SPECIFIC risk hotspot identified during triage.
Only analyze the code in this hotspot — do not comment on unrelated files.
Every suggestion must cite specific evidence from the diff.
Return valid JSON only."""


def _build_deep_prompt(
    hotspot: dict,
    ctx_text: str,
    language: str,
) -> str:
    """Build a focused deep-dive prompt for one hotspot."""
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
      "recommendation": "concrete fix"
    }}
  ]
}}

Focus ONLY on this hotspot. Max 3 suggestions per hotspot. If no real issue, return empty array.

Diff context:
{ctx_text}
"""


# ── Main entry point ───────────────────────────────────────

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
        triage_raw = provider.complete_json(
            TRIAGE_SYSTEM,
            _build_triage_prompt(pr, files, findings),
        )
        triage = json.loads(triage_raw)
        hotspots: list[dict] = triage.get("hotspots", [])
    except (ProviderError, json.JSONDecodeError, KeyError) as exc:
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
        try:
            deep_raw = provider.complete_json(
                DEEP_SYSTEM,
                _build_deep_prompt(hotspot, ctx.text, language),
            )
            deep_result = json.loads(deep_raw)
            for s in deep_result.get("suggestions", []):
                all_suggestions.append(ReviewSuggestion(
                    file_path=s.get("file_path", hotspot["file_path"]),
                    line=s.get("line"),
                    severity=_safe_severity(s.get("severity", "medium")),
                    confidence=min(max(float(s.get("confidence", 0.5)), 0.0), 1.0),
                    title=str(s.get("title", "Issue")),
                    reason=str(s.get("reason", "")),
                    recommendation=str(s.get("recommendation", "")),
                ))
        except (ProviderError, json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Two-stage: deep-dive #%d failed: %s", i, exc)
            continue

    # ── Merge & deduplicate ────────────────────────────────
    from src.reviewer.engine import _filter_suggestions  # avoid circular import at module level
    final = _filter_suggestions(
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


def _safe_severity(s: str) -> Severity:
    try:
        return Severity(s.lower())
    except ValueError:
        return Severity.MEDIUM


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
    from src.reviewer.engine import _filter_suggestions, _parse_model_payload
    from src.reviewer.prompt import SYSTEM_PROMPT, build_user_prompt

    ctx = build_review_context(pr, files, findings)
    raw = provider.complete_json(
        SYSTEM_PROMPT, build_user_prompt(ctx.text, max_suggestions, language)
    )
    payload = _parse_model_payload(raw)
    suggestions = _filter_suggestions(
        payload.suggestions, files, max_suggestions,
        min_confidence, max_suggestions_per_file,
    )
    return payload.summary, payload.risk_level, suggestions
