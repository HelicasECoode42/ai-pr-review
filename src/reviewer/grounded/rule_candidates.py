"""Identity and rebuttals of deterministic rule candidates."""
from __future__ import annotations

import re

_MOCK_CLAIM = re.compile(r"\b(mock(ed|s)?|patch(ed|es)?)\b", re.IGNORECASE)


_AGREES_PATCH_MISSES = re.compile(
    r"\b(bypass(ed|es)?|real function|local (binding|import|name)|imported by name|"
    r"not (replaced|patched|mocked)|still (runs|calls|invokes)|direct import)\b",
    re.IGNORECASE,
)


def _rule_rebuttals(rule_candidates) -> dict[str, set[str]]:
    """Names TQ001 proved are NOT replaced by the patch, per file."""
    rebutted: dict[str, set[str]] = {}
    for item in rule_candidates:
        if item.get("rule_id") != "TQ001":
            continue
        match = re.search(r"`([^`]+)`", str(item.get("reason") or ""))
        if match:
            rebutted.setdefault(item.get("file"), set()).add(match.group(1))
    return rebutted


def _rule_candidate_key(file_path, line, title, reason, evidence) -> tuple | None:
    """Identity of a host-created deterministic candidate, never a model claim."""
    if (not isinstance(file_path, str) or isinstance(line, bool) or not isinstance(line, int)
            or not isinstance(title, str) or not isinstance(reason, str)):
        return None
    if not isinstance(evidence, (list, tuple)) or any(not isinstance(item, str) for item in evidence):
        return None
    return file_path, line, title, reason, tuple(evidence)


def _suggestion_rule_candidate_key(suggestion) -> tuple | None:
    return _rule_candidate_key(
        suggestion.file_path, suggestion.line, suggestion.title,
        suggestion.reason, suggestion.evidence,
    )


def _contradicted_by_rule(suggestion, rebutted, *, host_rule_fact=False) -> str | None:
    """A model claim that a name is mocked, where TQ001 proved the patch misses it."""
    if host_rule_fact:
        return None
    text = suggestion.title + "\n" + suggestion.reason
    for name in rebutted.get(suggestion.file_path, ()):
        if name in text and _MOCK_CLAIM.search(text) and not _AGREES_PATCH_MISSES.search(text):
            return name
    return None
