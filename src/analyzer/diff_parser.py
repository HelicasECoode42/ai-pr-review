from __future__ import annotations

import re
import logging

from src.models import ChangedFile, ChangedLine, DiffHunk

logger = logging.getLogger(__name__)

HUNK_RE = re.compile(r"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")


def parse_file_hunks(file: ChangedFile) -> list[DiffHunk]:
    try:
        if not file or not file.patch:
            return []
        return _parse_patch(file.filename, file.patch)
    except Exception as e:
        logger.warning(f"Failed to parse patch for {file.filename}: {e}")
        return []


def _parse_patch(file_path: str, patch_text: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    current_header: str | None = None
    current_lines: list[str] = []
    old_start = old_count = new_start = new_count = 0

    for line in patch_text.splitlines():
        match = HUNK_RE.match(line)
        if match:
            if current_header is not None:
                hunks.append(
                    _build_hunk(
                        file_path,
                        current_header,
                        old_start,
                        old_count,
                        new_start,
                        new_count,
                        current_lines,
                    )
                )
            current_header = line
            current_lines = []
            old_start = int(match.group("old_start"))
            old_count = int(match.group("old_count") or "1")
            new_start = int(match.group("new_start"))
            new_count = int(match.group("new_count") or "1")
            continue

        if current_header is not None:
            current_lines.append(line)

    if current_header is not None:
        hunks.append(
            _build_hunk(
                file_path,
                current_header,
                old_start,
                old_count,
                new_start,
                new_count,
                current_lines,
            )
        )
    return hunks


def _build_hunk(
    file_path: str,
    header: str,
    old_start: int,
    old_count: int,
    new_start: int,
    new_count: int,
    lines: list[str],
) -> DiffHunk:
    old_line = old_start
    new_line = new_start
    added_lines: list[ChangedLine] = []
    removed_lines: list[str] = []

    for raw_line in lines:
        try:
            if raw_line.startswith("+") and not raw_line.startswith("+++"):
                added_lines.append(
                    ChangedLine(file_path=file_path, line=new_line, content=raw_line[1:])
                )
                new_line += 1
            elif raw_line.startswith("-") and not raw_line.startswith("---"):
                removed_lines.append(raw_line[1:])
                old_line += 1
            else:
                old_line += 1
                new_line += 1
        except Exception as e:
            logger.warning(f"Skipping malformed line in hunk: {e}")
            continue

    return DiffHunk(
        file_path=file_path,
        header=header,
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        added_lines=added_lines,
        removed_lines=removed_lines,
        raw="\n".join([header, *lines]),
    )


def changed_line_map(files: list[ChangedFile]) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    if not files:
        return result
    for file in files:
        try:
            for hunk in parse_file_hunks(file):
                result.setdefault(file.filename, set()).update(line.line for line in hunk.added_lines)
        except Exception as e:
            logger.warning(f"Failed to process line map for {file.filename}: {e}")
            continue
    return result