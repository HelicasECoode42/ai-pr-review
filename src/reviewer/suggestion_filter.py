"""Public suggestion filter — deduplicate, confidence-gate, per-file cap, locate.

Extracted from src.reviewer.engine to eliminate private-function cross-module
imports (two_stage.py was importing _filter_suggestions from engine.py).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from src.analyzer.diff_parser import changed_line_map, parse_file_hunks
from src.models import ChangedFile, DiffHunk, ReviewSuggestion, Severity

# Documentation carries no runtime behaviour, so "which function" is the wrong
# question for it: the location is labelled as not applicable, not as a failure.
DOCUMENTATION_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc"})
_DEF_RE = re.compile(r"(?:async\s+)?def\s+([A-Za-z_]\w*)")
_CLASS_RE = re.compile(r"class\s+([A-Za-z_]\w*)")


def _symbol_from_hunks(hunks: list[DiffHunk], line: int) -> tuple[str | None, str, str | None]:
    for hunk in hunks:
        if hunk.new_start <= line <= hunk.new_start + max(hunk.new_count, 1) - 1:
            context = hunk.header.split("@@", 2)[-1].strip()
            if not context:
                return None, "unavailable", "hunk_header_has_no_context"
            match = _DEF_RE.search(context) or _CLASS_RE.search(context)
            if match:
                return match.group(1), "resolved", "diff_hunk_header"
            if re.fullmatch(r"[\w.]+", context):
                return context, "resolved", "diff_hunk_header"
            return None, "unavailable", "unrecognized_hunk_context"
    return None, "unavailable", "line_not_in_any_hunk"


def locate_symbol(
    files: list[ChangedFile],
    file_path: str,
    line: int | None,
    hunks_by_file: dict[str, list[DiffHunk]] | None = None,
) -> tuple[str | None, str, str | None]:
    """Return ``(symbol, status, reason)`` for one suggestion location.

    The enclosing name comes from the hunk header git already writes
    (``@@ -10,3 +10,4 @@ def foo(x):``): no extra parse, and it cannot disagree
    with the diff the suggestion was anchored to. Every negative answer names a
    reason, so "we could not tell" never reads as "there is nothing here".
    """
    if PurePosixPath(file_path).suffix.lower() in DOCUMENTATION_SUFFIXES:
        return None, "not_applicable", "documentation"
    if line is None:
        return None, "unavailable", "no_line"
    changed = next((f for f in files if f.filename == file_path), None)
    if changed is None:
        return None, "unavailable", "file_not_in_changed_set"
    if hunks_by_file is not None and file_path not in hunks_by_file:
        hunks_by_file[file_path] = parse_file_hunks(changed)
    hunks = (hunks_by_file or {}).get(file_path) or parse_file_hunks(changed)
    if not hunks:
        return None, "unavailable", "no_parseable_hunk"
    return _symbol_from_hunks(hunks, line)


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
    hunks_by_file: dict[str, list[DiffHunk]] = {}
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
        symbol, symbol_status, symbol_reason = locate_symbol(
            files, suggestion.file_path, suggestion.line, hunks_by_file
        )
        filtered.append(
            suggestion.model_copy(
                update={
                    "symbol": symbol,
                    "symbol_status": symbol_status,
                    "symbol_reason": symbol_reason,
                }
            )
        )
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
