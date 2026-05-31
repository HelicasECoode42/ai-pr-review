"""FixTracking: compare current review suggestions against previous review.

Matches previous suggestions to current ones by file_path + line + title
similarity, then marks each as fixed, still_present, or unknown.
"""

from __future__ import annotations

import difflib
import re

from src.models import FixTrackingItem, ReviewSuggestion

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _strip_markdown_link(text: str) -> str:
    """Extract display text from a markdown link: [text](url) → text."""
    return _MD_LINK_RE.sub(r"\1", text)


def _similarity(a: str, b: str) -> float:
    """Return a 0..1 string similarity score for non-empty strings."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _parse_previous_suggestions(
    summary_markdown: str | None,
) -> list[dict[str, object]]:
    """Parse previous review suggestions from the bot summary comment markdown.

    The markdown contains a table like:
    | # | Severity | Location | Confidence | Title |
    | 1 | high | file.py:42 | 90% | SQL injection |
    """
    if not summary_markdown:
        return []

    suggestions: list[dict[str, object]] = []
    in_table = False

    for line in summary_markdown.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        # Detect suggestions table: header row or numbered row
        if "| # |" in stripped or "| #  |" in stripped:
            in_table = True
            continue
        # First numbered row also indicates table start
        if not in_table:
            cells = stripped.split("|")[1:-1]
            if cells and cells[0].strip().isdigit():
                in_table = True
            else:
                continue

        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        if len(cells) < 5:
            continue
        # Skip separator rows like |---|---|---|---|
        if all(c.replace("-", "").replace(" ", "") == "" for c in cells):
            continue
        # Try to parse location: file_path:line
        # Strip markdown link syntax: [text](url) → text, and backticks
        location = cells[2] if len(cells) > 2 else ""
        location = _strip_markdown_link(location)
        location = location.strip("`")
        loc_match = location.rsplit(":", 1) if ":" in location else (location, "")
        file_path = loc_match[0].strip()
        try:
            line = int(loc_match[1].strip()) if len(loc_match) > 1 else None
        except (ValueError, IndexError):
            line = None

        # Parse confidence: "90%" or similar
        conf_str = cells[3] if len(cells) > 3 else ""
        try:
            confidence = float(conf_str.rstrip("%")) / 100
        except (ValueError, TypeError):
            confidence = 0.0

        suggestions.append({
            "title": cells[4] if len(cells) > 4 else "",
            "file_path": file_path,
            "line": line,
            "confidence": confidence,
        })

    return suggestions


def build_fix_tracking(
    current_suggestions: list[ReviewSuggestion],
    previous_summary_md: str | None,
    title_similarity_threshold: float = 0.6,
) -> list[FixTrackingItem]:
    """Build FixTracking items by comparing current vs previous suggestions.

    For each previous suggestion, tries to find a matching current suggestion
    (same file + line and similar title). If found → still_present.
    If not found → fixed.
    If previous data is unavailable → unknown.

    Args:
        current_suggestions: This review's suggestions.
        previous_summary_md: The previous bot summary comment markdown.
        title_similarity_threshold: Minimum title similarity for a match.
    """
    prev = _parse_previous_suggestions(previous_summary_md)

    if not prev:
        return []

    current_by_file: dict[str, list[ReviewSuggestion]] = {}
    for s in current_suggestions:
        current_by_file.setdefault(s.file_path, []).append(s)

    items: list[FixTrackingItem] = []

    for prev_s in prev:
        title = str(prev_s.get("title", ""))
        file_path = str(prev_s.get("file_path", ""))
        prev_line = prev_s.get("line")
        line_key: int | None = int(prev_line) if isinstance(prev_line, (int, float)) else None

        # Look for a matching current suggestion
        candidates = current_by_file.get(file_path, [])
        matched = None
        best_sim = 0.0

        for curr in candidates:
            # Same line is a strong signal
            same_line = line_key is not None and curr.line == line_key
            sim = _similarity(title, curr.title)

            # Weight: same line boosts similarity
            weighted = sim + (0.3 if same_line else 0.0)
            if weighted > best_sim and weighted > title_similarity_threshold:
                best_sim = weighted
                matched = curr

        if matched:
            items.append(FixTrackingItem(
                previous_title=title,
                file_path=file_path,
                previous_line=line_key,
                status="still_present",
                detail=f"Matched current suggestion: {matched.title} "
                       f"(similarity {best_sim:.0%})",
            ))
        else:
            items.append(FixTrackingItem(
                previous_title=title,
                file_path=file_path,
                previous_line=line_key,
                status="fixed",
                detail="No matching suggestion in current review.",
            ))

    return items
