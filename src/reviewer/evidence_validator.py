"""Ground AI suggestions in exact changed-line evidence.

This is an output-integrity check, not a static rule engine: it never decides
whether code is defective. It only rejects a model claim when the quoted code
is absent from the changed line or no concrete failure scenario is supplied.
"""

from __future__ import annotations

from src.analyzer.diff_parser import parse_file_hunks
from src.models import ChangedFile, ReviewSuggestion


def validate_ai_evidence(
    suggestions: list[ReviewSuggestion], files: list[ChangedFile]
) -> tuple[list[ReviewSuggestion], list[dict[str, object]]]:
    """Return evidence-grounded suggestions and audit records for rejected ones."""
    changed_content: dict[tuple[str, int], str] = {}
    for file in files:
        try:
            hunks = parse_file_hunks(file)
        except Exception:
            continue
        for hunk in hunks:
            for changed in hunk.added_lines:
                changed_content[(changed.file_path, changed.line)] = changed.content

    accepted: list[ReviewSuggestion] = []
    rejected: list[dict[str, object]] = []
    for suggestion in suggestions:
        reason = _rejection_reason(suggestion, changed_content)
        if reason is None:
            accepted.append(suggestion)
        else:
            rejected.append({
                "file_path": suggestion.file_path,
                "line": suggestion.line,
                "title": suggestion.title,
                "source": suggestion.source,
                "reason": reason,
            })
    return accepted, rejected


def _rejection_reason(
    suggestion: ReviewSuggestion, changed_content: dict[tuple[str, int], str]
) -> str | None:
    if suggestion.line is None:
        return "AI suggestion must cite one changed line."
    actual = changed_content.get((suggestion.file_path, suggestion.line))
    if actual is None:
        return "Cited location is not an added line in this diff."
    if not suggestion.evidence:
        return "AI suggestion did not provide an exact code evidence excerpt."
    normalized_line = _normalize(actual)
    if not any(_normalize(item) in normalized_line for item in suggestion.evidence if item.strip()):
        return "None of the AI evidence excerpts appears in the cited changed line."
    if not suggestion.failure_scenario.strip():
        return "AI suggestion did not provide a concrete failure scenario."
    return None


def _normalize(value: str) -> str:
    return "".join(value.split())
