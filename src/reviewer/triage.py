"""Stage 1 of two-stage review: triage — identify high-risk hotspots.

Returns a compact JSON with top-N hotspots for deep-dive analysis.
"""

from __future__ import annotations

import json
import logging

from src.models import ChangedFile, PullRequest, RiskFinding
from src.reviewer.provider import ReviewModelProvider

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM = """You are a senior engineer doing a rapid first-pass review.
Your job: identify which files and line ranges in a PR need deeper scrutiny.
Focus on security, data integrity, concurrency, and crash risks.
Be precise — give specific file paths and line numbers.
Return valid JSON only."""


def _build_triage_prompt(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    context_text: str = "",
) -> str:
    file_list = "\n".join(
        f"- {f.filename} ({f.status}): +{f.additions}/-{f.deletions}" for f in files[:50]
    )
    if len(files) > 50:
        file_list += f"\n  ... and {len(files) - 50} more files"

    rule_alerts = (
        "\n".join(
            f"- [{f.severity.value}] {f.file_path}:{f.line or '?'} — {f.title}"
            for f in findings[:20]
        )
        if findings
        else "No rule-based findings."
    )

    return f"""PR #{pr.number}: {pr.title}

Changed files ({len(files)} total):
{file_list}

Diff and repository context:
{context_text}

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


def run_triage(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    provider: ReviewModelProvider,
    context_text: str = "",
) -> list[dict]:
    """Run stage 1 triage, returning a list of hotspot dicts.

    Raises ProviderError or json.JSONDecodeError on failure — caller
    should fall back to one-shot review.
    """
    raw = provider.complete_json(
        TRIAGE_SYSTEM,
        _build_triage_prompt(pr, files, findings, context_text),
    )
    triage = json.loads(raw)
    if not isinstance(triage, dict):
        raise ValueError(f"Expected JSON object, got {type(triage).__name__}")
    hotspots = triage.get("hotspots")
    if not isinstance(hotspots, list):
        raise ValueError("Missing or invalid hotspots")
    paths = {f.filename for f in files}
    for hotspot in hotspots:
        if not isinstance(hotspot, dict) or hotspot.get("file_path") not in paths:
            raise ValueError("Invalid hotspot path")
        for field in ("start_line", "end_line"):
            if type(hotspot.get(field)) is not int or hotspot[field] < 1:
                raise ValueError("Invalid hotspot range")
        if hotspot["end_line"] < hotspot["start_line"] or not hotspot.get("reason"):
            raise ValueError("Invalid hotspot")
    return hotspots
