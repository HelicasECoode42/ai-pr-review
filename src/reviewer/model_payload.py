"""Model payload parsing — validate, repair, and extract structured review output.

Public API:
    ModelReviewPayload   — Pydantic schema for AI review output
    parse_model_payload  — parse raw LLM output with 4-strategy fallback
    repair_truncated_json — heuristic repair for truncated JSON
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, ValidationError

from src.models import ReviewSuggestion, Severity

logger = logging.getLogger(__name__)


class ModelReviewPayload(BaseModel):
    summary: str
    risk_level: Severity
    suggestions: list[ReviewSuggestion]
    dismissed_rule_alerts: list[dict] = []  # AI-confirmed false positives


# ── public parsing ──────────────────────────────────────────


def parse_model_payload(raw: str) -> ModelReviewPayload:
    """Parse raw LLM output with cascading fallback strategies.

    Strategy 1: direct JSON parse
    Strategy 2: extract from ```json fenced code block
    Strategy 3: extract from first { to last }
    Strategy 4: repair truncated JSON by closing open braces/brackets
    """
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

    # Strategy 4: repair truncated JSON by closing open braces/brackets
    repaired = repair_truncated_json(raw)
    if repaired:
        try:
            data = json.loads(repaired)
            return ModelReviewPayload.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            logger.warning("JSON fallback: repair also failed")

    raise ValueError(
        f"Model returned invalid review JSON. Raw output prefix: {raw[:300]}"
    )


# ── truncated JSON repair ───────────────────────────────────


def repair_truncated_json(raw: str) -> str | None:
    """Attempt to repair truncated/incomplete JSON by closing open structures.

    Returns repaired JSON string, or None if repair is not possible.
    """
    start = raw.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i, ch in enumerate(raw[start:], start=start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1

    if depth <= 0:
        return None

    candidate = raw.rstrip()
    if candidate.endswith(","):
        candidate = candidate[:-1]

    d = 0
    in_s = False
    esc = False
    for ch in candidate:
        if esc:
            esc = False
            continue
        if ch == "\\" and in_s:
            esc = True
            continue
        if ch == '"':
            in_s = not in_s
            continue
        if in_s:
            continue
        if ch in "{[":
            d += 1
        elif ch in "}]":
            d -= 1

    if d <= 0:
        return None

    closers = "}" * d

    if in_s:
        closers = '"]' + closers
        if not candidate.endswith('"'):
            candidate += '"'

    return candidate + closers
