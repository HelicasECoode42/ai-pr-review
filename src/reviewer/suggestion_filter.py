"""Public suggestion filter — deduplicate, confidence-gate, per-file cap.

Extracted from src.reviewer.engine to eliminate private-function cross-module
imports (two_stage.py was importing _filter_suggestions from engine.py).
"""

from __future__ import annotations

from src.analyzer.diff_parser import changed_line_map
from src.models import ChangedFile, ReviewSuggestion, Severity


def filter_suggestions(
    suggestions: list[ReviewSuggestion],
    files: list[ChangedFile],
    max_suggestions: int,
    min_confidence: float = 0.0,
    max_suggestions_per_file: int = 5,
) -> list[ReviewSuggestion]:
    if not (0.0 <= min_confidence <= 1.0):
        raise ValueError(f"min_confidence must be in [0, 1], got {min_confidence}")
    changed_lines = changed_line_map(files)
    filtered: list[ReviewSuggestion] = []
    seen_exact: set[tuple[str, int | None, str]] = set()
    seen_reason_prefix: set[tuple[str, int | None, str]] = set()
    per_file_count: dict[str, int] = {}

    for suggestion in suggestions:
        if suggestion.confidence < min_confidence:
            continue
        if not suggestion.reason.strip() or not suggestion.recommendation.strip():
            continue
        if suggestion.line is not None:
            if suggestion.line not in changed_lines.get(suggestion.file_path, set()):
                continue
        exact_key = (suggestion.file_path, suggestion.line, suggestion.title.lower())
        if exact_key in seen_exact:
            continue
        reason_prefix = suggestion.reason.strip().lower()[:40]
        if suggestion.line is not None and len(reason_prefix) >= 15:
            reason_key = (suggestion.file_path, suggestion.line, reason_prefix)
            if reason_key in seen_reason_prefix:
                continue
            seen_reason_prefix.add(reason_key)
        if per_file_count.get(suggestion.file_path, 0) >= max_suggestions_per_file:
            continue
        seen_exact.add(exact_key)
        try:
            Severity(suggestion.severity)
        except ValueError:
            continue
        if not (0.0 <= suggestion.confidence <= 1.0):
            continue
        filtered.append(suggestion)
        per_file_count[suggestion.file_path] = (
            per_file_count.get(suggestion.file_path, 0) + 1
        )

    severity_rank = {
        Severity.CRITICAL: 4,
        Severity.HIGH: 3,
        Severity.MEDIUM: 2,
        Severity.LOW: 1,
    }
    filtered.sort(
        key=lambda item: (severity_rank.get(item.severity, 0), item.confidence), reverse=True
    )
    return filtered[:max_suggestions]
