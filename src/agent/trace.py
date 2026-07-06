"""Trace utilities: summarise tool inputs/observations and redact secrets.

Security rules (hard requirements, do not relax):
- Never persist full API tokens  → replace with "<redacted>"
- Never persist full prompt text  → record character count only
- Never persist raw model output  → record parse_status, suggestion count, risk_level
- Safe to persist: file counts, finding counts, strategy, per-step duration, errors
"""

from __future__ import annotations

import re
from typing import Any

# ── per-type summarisers ──────────────────────────────────────


def _summarise_pr(pr: object) -> dict[str, Any]:
    """PullRequest → {title, author, additions, deletions, files_count}."""
    try:
        additions = getattr(pr, "additions", None)
        deletions = getattr(pr, "deletions", None)
        files_count = getattr(pr, "changed_files", None)
        return {
            "title": getattr(pr, "title", "?"),
            "author": getattr(pr, "author", "?"),
            "additions": additions if additions is not None else "?",
            "deletions": deletions if deletions is not None else "?",
            "files_count": files_count if files_count is not None else "?",
        }
    except Exception:
        return {"type": type(pr).__name__}


def _summarise_files(files: object) -> dict[str, Any]:
    """list[ChangedFile] → {count, filenames (first 10)}."""
    try:
        file_list = list(files)  # type: ignore[call-overload]
        return {
            "count": len(file_list),
            "filenames": [getattr(f, "filename", "?") for f in file_list[:10]],
        }
    except Exception:
        return {"count": "?", "filenames": []}


def _summarise_findings(findings: object) -> dict[str, Any]:
    """list[RiskFinding] → {count, top_titles (first 5)}."""
    try:
        finding_list = list(findings)  # type: ignore[call-overload]
        return {
            "count": len(finding_list),
            "top_titles": [getattr(f, "title", "?") for f in finding_list[:5]],
        }
    except Exception:
        return {"count": "?", "top_titles": []}


def _summarise_report(report: object) -> dict[str, Any]:
    """ReviewReport → {risk_level, suggestions_count, used_ai, confidence}."""
    try:
        return {
            "risk_level": str(getattr(report, "risk_level", "?")),
            "suggestions_count": len(getattr(report, "suggestions", [])),
            "used_ai": getattr(report, "used_ai", False),
            "report_confidence": getattr(report, "report_confidence", "?"),
        }
    except Exception:
        return {"type": type(report).__name__}


def _summarise_model_output(raw: str) -> dict[str, Any]:
    """Model raw output → {chars, parse_status, suggestions_count, risk_level}."""
    info: dict[str, Any] = {"chars": len(raw)}

    # Try to extract JSON for summary without persisting the full text
    try:
        import json

        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Try fenced code block
        match = re.search(r"```json\s*([\s\S]*?)\s*```", raw)
        if match:
            try:
                data = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                info["parse_status"] = "failed"
                return info
        else:
            info["parse_status"] = "failed"
            return info

    info["parse_status"] = "success"
    if isinstance(data, dict):
        suggestions = data.get("suggestions", [])
        info["suggestions_count"] = len(suggestions) if isinstance(suggestions, list) else 0
        info["risk_level"] = data.get("risk_level", "?")
    return info


def _summarise_generic(value: object) -> dict[str, Any]:
    """Fallback for unknown types."""
    if isinstance(value, str):
        return {"chars": len(value)}
    if isinstance(value, (list, tuple)):
        return {"count": len(value)}
    if isinstance(value, dict):
        return {"keys": list(value.keys())[:20]}
    try:
        return {"type": type(value).__name__}
    except Exception:
        return {}


# ── public API ────────────────────────────────────────────────


def summarize_input(value: object, tool_name: str = "") -> dict[str, Any]:
    """Produce a compact, safe summary of a tool's input.

    Dispatch is based on *tool_name* because the same type (e.g. a list of
    ChangedFile) has different meaning depending on context.
    """
    type_name = type(value).__name__

    if tool_name in ("fetch_pull_request",):
        # value is (repo: str, pr_number: int) or similar
        if isinstance(value, tuple):
            return {"repo": str(value[0]), "pr_number": str(value[1]) if len(value) > 1 else "?"}
        return {"input": str(value)[:200]}

    if tool_name in ("fetch_changed_files",) and type_name != "PullRequest":
        return _summarise_files(value)

    if tool_name in ("scan_risks", "build_rule_only_report", "one_shot_ai_review"):
        # value is typically (pr, files, findings) tuple
        if isinstance(value, tuple) and len(value) >= 2:
            return {
                "pr": _summarise_pr(value[0]),
                "files": _summarise_files(value[1]),
                "findings": _summarise_findings(value[2]) if len(value) > 2 else {},
            }
        return _summarise_generic(value)

    if tool_name in ("render_markdown", "render_json"):
        return _summarise_report(value)

    if tool_name in ("triage_hotspots", "deep_review_hotspot"):
        if isinstance(value, str):
            return {"chars": len(value)}
        return _summarise_generic(value)

    # Generic fallback dispatch by type
    if "PullRequest" in type_name:
        return _summarise_pr(value)
    if "ChangedFile" in type_name or (isinstance(value, list) and value and hasattr(value[0], "filename")):
        return _summarise_files(value)
    if "RiskFinding" in type_name or (isinstance(value, list) and value and hasattr(value[0], "rule_id")):
        return _summarise_findings(value)
    if isinstance(value, str):
        return {"chars": len(value)}

    return _summarise_generic(value)


def summarize_observation(value: object, tool_name: str = "") -> dict[str, Any]:
    """Produce a compact, safe summary of a tool's return value."""
    type_name = type(value).__name__

    if tool_name in ("fetch_pull_request",):
        return _summarise_pr(value)

    if tool_name in ("fetch_changed_files",):
        return _summarise_files(value)

    if tool_name in ("scan_risks",):
        return _summarise_findings(value)

    if tool_name in ("build_rule_only_report", "one_shot_ai_review", "build_review_report"):
        return _summarise_report(value)

    if tool_name in ("render_markdown",):
        return {"chars": len(value) if isinstance(value, str) else "?"}

    if tool_name in ("render_json",):
        if isinstance(value, str):
            return {"chars": len(value)}
        if isinstance(value, dict):
            return {"keys": list(value.keys())[:20]}
        return {"type": type_name}

    if tool_name in ("triage_hotspots",):
        if isinstance(value, (list, tuple)):
            return {"hotspots_count": len(value)}
        return _summarise_generic(value)

    if tool_name in ("deep_review_hotspot",):
        if isinstance(value, (list, tuple)):
            return {"suggestions_count": len(value)}
        return _summarise_generic(value)

    # Generic fallback
    if isinstance(value, str):
        # Potentially model output → extra safe handling
        if len(value) > 500:
            return _summarise_model_output(value)
        return {"chars": len(value)}
    if isinstance(value, (list, tuple)):
        return {"count": len(value)}
    if isinstance(value, dict):
        return {"keys": list(value.keys())[:20]}

    return _summarise_generic(value)


# ── secret redaction ──────────────────────────────────────────

# Patterns that look like secrets / tokens
_SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"(?:github[_-]?token[=:]\s*)(gh[pous]_[A-Za-z0-9_]+)", "GITHUB_TOKEN"),
    (r"(?:OPENAI_API_KEY[=:]\s*)(sk-[A-Za-z0-9_-]+)", "OPENAI_API_KEY"),
    (r"(?:Authorization[=:]\s*Bearer\s+)([A-Za-z0-9_\-\.]+)", "Authorization"),
    (r"(?:api[_-]?key[=:]\s*)([A-Za-z0-9_\-]{20,})", "API_KEY"),
    (r"(?:token[=:]\s*)([A-Za-z0-9_\-]{20,})", "TOKEN"),
    (r"(?:secret[=:]\s*)([A-Za-z0-9_\-]{10,})", "SECRET"),
    (r"(gh[pous]_[A-Za-z0-9_]{20,})", "GITHUB_TOKEN"),
    (r"(sk-[A-Za-z0-9_-]{20,})", "OPENAI_API_KEY"),
]


def redact_secrets(value: object) -> object:
    """Replace secret-like patterns with '<redacted:NAME>' placeholders.

    Accepts str, dict, list or nested combinations.  Returns a copy with
    secrets replaced; the original is never mutated.
    """
    if isinstance(value, str):
        result = value
        for pattern, name in _SECRET_PATTERNS:
            result = re.sub(pattern, f"<redacted:{name}>", result)
        return result

    if isinstance(value, dict):
        return {
            str(k): redact_secrets(v)
            for k, v in value.items()  # type: ignore[union-attr]
        }

    if isinstance(value, (list, tuple)):
        return type(value)(redact_secrets(v) for v in value)  # type: ignore[call-arg,return-value]

    return value
