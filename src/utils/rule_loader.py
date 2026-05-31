"""Rule loader — loads and merges rule definitions from YAML.

Supports:
- Default rules from src/utils/default_rules.yml
- User overrides from .ai-pr-review-rules.yml in repo root
- Rule disable (disabled_rules)
- Severity overrides (severity_overrides)
"""

from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from typing import Any

import yaml

from src.models import Severity

logger = logging.getLogger(__name__)

# ── Types ───────────────────────────────────────────────

PathRuleDef = tuple[str, Severity, re.Pattern[str], str, str, float]
LineRuleDef = tuple[str, re.Pattern[str], Severity, str, str]


def _parse_severity(s: str) -> Severity:
    """Parse a severity string to Severity enum."""
    try:
        return Severity(s.lower())
    except ValueError:
        logger.warning(f"Unknown severity '{s}', defaulting to MEDIUM")
        return Severity.MEDIUM


def _get_default_rules_path() -> str:
    """Get the path to the default rules YAML file."""
    return str(
        Path(__file__).resolve().parent / "default_rules.yml"
    )


def _get_user_rules_path() -> str | None:
    """Check for .ai-pr-review-rules.yml in project root or CWD."""
    for candidate in [
        os.path.join(os.getcwd(), ".ai-pr-review-rules.yml"),
        os.path.join(os.getcwd(), ".ai-pr-review-rules.yaml"),
    ]:
        if os.path.isfile(candidate):
            return candidate
    return None


def _load_yaml(path: str) -> dict[str, Any]:
    """Load a YAML rules file."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected dict in rules file, got {type(data).__name__}: {path}"
        )
    return data


def _parse_path_rules(data: list[dict]) -> list[PathRuleDef]:
    """Parse path rules from YAML data."""
    rules: list[PathRuleDef] = []
    for item in data:
        try:
            rule = (
                item["id"],
                _parse_severity(item.get("severity", "MEDIUM")),
                re.compile(item["pattern"], re.IGNORECASE),
                item.get("title", ""),
                item.get("recommendation", ""),
                float(item.get("confidence", 0.6)),
            )
            rules.append(rule)
        except (KeyError, re.error) as e:
            logger.warning(f"Skipping path rule '{item.get('id', '?')}': {e}")
            continue
    return rules


def _parse_line_rules(data: list[dict]) -> list[LineRuleDef]:
    """Parse line rules from YAML data."""
    rules: list[LineRuleDef] = []
    for item in data:
        try:
            rule = (
                item["id"],
                re.compile(item["pattern"], re.IGNORECASE),
                _parse_severity(item.get("severity", "MEDIUM")),
                item.get("title", ""),
                item.get("recommendation", ""),
            )
            rules.append(rule)
        except (KeyError, re.error) as e:
            logger.warning(f"Skipping line rule '{item.get('id', '?')}': {e}")
            continue
    return rules


def load_rules(
    user_rules_path: str | None = None,
) -> tuple[list[PathRuleDef], list[LineRuleDef], set[str]]:
    """Load and merge rules from default + optional user overrides.

    Returns:
        (path_rules, line_rules, disabled_rule_ids)
    """
    # Load defaults
    default_path = _get_default_rules_path()
    if os.path.isfile(default_path):
        default_data = _load_yaml(default_path)
    else:
        logger.warning(f"Default rules not found at {default_path}")
        default_data = {}

    path_rules = _parse_path_rules(default_data.get("path_rules", []))
    line_rules = _parse_line_rules(default_data.get("line_rules", []))
    disabled: set[str] = set()

    # Load user overrides if provided
    user_path = user_rules_path or _get_user_rules_path()
    if user_path and os.path.isfile(user_path):
        logger.info(f"Loading user rules from {user_path}")
        user_data = _load_yaml(user_path)

        # Disabled rules
        disabled = set(user_data.get("disabled_rules", []))

        # Severity overrides
        severity_overrides: dict[str, str] = user_data.get("severity_overrides", {})

        # Apply severity overrides to path rules
        for i, (rid, sev, pat, title, rec, conf) in enumerate(path_rules):
            if rid in severity_overrides:
                path_rules[i] = (rid, _parse_severity(severity_overrides[rid]), pat, title, rec, conf)

        # Apply severity overrides to line rules
        for i, (rid, pat, sev, title, rec) in enumerate(line_rules):
            if rid in severity_overrides:
                line_rules[i] = (rid, pat, _parse_severity(severity_overrides[rid]), title, rec)

        # User path rules (appended)
        if "path_rules" in user_data:
            path_rules.extend(_parse_path_rules(user_data["path_rules"]))

        # User line rules (appended)
        if "line_rules" in user_data:
            line_rules.extend(_parse_line_rules(user_data["line_rules"]))

    return path_rules, line_rules, disabled


def rules_to_dict(
    path_rules: list[PathRuleDef],
    line_rules: list[LineRuleDef],
) -> list[dict]:
    """Convert runtime rules back to serializable dicts (for API responses)."""
    result: list[dict] = []
    for rid, sev, pat, title, rec, conf in path_rules:
        result.append({
            "id": rid,
            "type": "path",
            "severity": sev.value,
            "pattern": pat.pattern,
            "title": title,
            "recommendation": rec,
            "confidence": conf,
        })
    for rid, pat, sev, title, rec in line_rules:
        result.append({
            "id": rid,
            "type": "line",
            "severity": sev.value,
            "pattern": pat.pattern,
            "title": title,
            "recommendation": rec,
        })
    return result
