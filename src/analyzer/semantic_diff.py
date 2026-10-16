"""Separate mechanical edits from semantic ones so the budget is spent on meaning.

Roughly a tenth of a large diff can be formatting, comment churn, import
reordering or a consistent rename. Showing those to a model costs tokens and
invites style findings. This module classifies a hunk deterministically, with no
model call, so the denominator of "what must be reviewed" is the semantic part
and the mechanical part is recorded rather than silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.models import ChangedFile

_TOKEN = re.compile(r"[A-Za-z_]\w*|\d+|\S")
_IMPORT = re.compile(r"^\s*(?:from\s+\S+\s+)?import\s")


def tokens(line: str) -> list[str]:
    return _TOKEN.findall(line)


def changed_lines(patch: str | None) -> tuple[list[str], list[str]]:
    removed, added = [], []
    for line in (patch or "").splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    return removed, added


def _whitespace_only(removed: list[str], added: list[str]) -> bool:
    """Formatting-only: identical once every space is removed, e.g. `x + 1` vs `x+1`."""
    return ["".join(line.split()) for line in removed] == [
        "".join(line.split()) for line in added
    ]


def _comment_only(removed: list[str], added: list[str]) -> bool:
    body = [line.strip() for line in removed + added if line.strip()]
    return bool(body) and all(line.startswith("#") for line in body)


def _import_reorder(removed: list[str], added: list[str]) -> bool:
    before = [line.strip() for line in removed if line.strip()]
    after = [line.strip() for line in added if line.strip()]
    if not before or not all(_IMPORT.match(line) for line in before + after):
        return False
    return sorted(before) == sorted(after)


def _rename_only(removed: list[str], added: list[str]) -> bool:
    """Same token structure with a consistent, bijective identifier mapping."""
    if len(removed) != len(added):
        return False
    mapping: dict[str, str] = {}
    for before, after in zip(removed, added, strict=True):
        left, right = tokens(before), tokens(after)
        if len(left) != len(right):
            return False
        for x, y in zip(left, right, strict=True):
            if x.isidentifier() and y.isidentifier():
                if mapping.setdefault(x, y) != y:
                    return False
            elif x != y:
                return False
    return bool(mapping) and any(k != v for k, v in mapping.items())


def classify(removed: list[str], added: list[str]) -> str:
    if not removed and not added:
        return "empty"
    if _whitespace_only(removed, added):
        return "whitespace_only"
    if _comment_only(removed, added):
        return "comment_only"
    if _import_reorder(removed, added):
        return "import_reorder"
    if _rename_only(removed, added):
        return "rename_only"
    return "semantic"


@dataclass
class SemanticPartition:
    counts: dict[str, int] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)

    def add(self, kind: str, size: int) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.tokens[kind] = self.tokens.get(kind, 0) + size

    @property
    def mechanical_tokens(self) -> int:
        return sum(v for k, v in self.tokens.items() if k != "semantic")

    @property
    def semantic_tokens(self) -> int:
        return self.tokens.get("semantic", 0)

    def summary(self) -> dict:
        total = self.mechanical_tokens + self.semantic_tokens
        return {
            "hunks": dict(sorted(self.counts.items())),
            "tokens": dict(sorted(self.tokens.items())),
            "mechanical_tokens": self.mechanical_tokens,
            "semantic_tokens": self.semantic_tokens,
            "mechanical_share": round(self.mechanical_tokens / total, 4) if total else 0.0,
        }


def partition(files: list[ChangedFile]) -> SemanticPartition:
    result = SemanticPartition()
    for file in files:
        removed, added = changed_lines(file.patch)
        kind = classify(removed, added)
        result.add(kind, len(removed) + len(added))
    return result
