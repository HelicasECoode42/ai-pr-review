"""Merge candidates that describe the same issue.

Raw discovery unions two model samples and deterministic test-quality findings,
so the pool carries the same defect several times under different words. An
exact (file, line) key is wrong both ways: it keeps a restated issue on another
line, and it drops a different issue that happens to share a line.

Rules, all within one file:

* model vs model: similar titles merge anywhere in the file; somewhat similar
  titles merge only when the anchors are close;
* rule vs rule: the same rule id on nearby lines merges; different rule ids
  never merge, since each rule is a distinct structural fact;
* rule vs model: a nearby model candidate whose title uses the rule's own
  vocabulary is the same issue.

The first candidate of a group is kept (model order is most severe first), and
every merge is reported so nothing silently disappears from the audit.
"""

from __future__ import annotations

import re

_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "does", "for",
    "from", "has", "in", "into", "is", "it", "its", "may", "not", "of", "on",
    "only", "or", "so", "that", "the", "then", "this", "to", "when", "which",
    "while", "with", "without",
}

# Words a model title uses when it describes the same fact a rule reports.
_RULE_VOCABULARY = {
    "TQ001": ("import", "bound", "binding", "bypass", "local"),
    # Not "mock": in a test file nearly every title says mock.
    "TQ002": ("under test", "verif", "real code", "exercis", "calls the mock"),
    "TQ003": ("global", "teardown", "leak", "restor"),
    "TQ004": ("dependenc", "pytest-mock", "pytest-asyncio", "declared"),
    "TQ005": ("signature", "argument", "arity", "call shape", "parameter"),
    "TQ006": ("return", "shape", "tuple", "type"),
    "TQ007": ("magic", "index"),
    "TQ008": ("side_effect", "side effect", "mutat"),
}

# Tuned on the 2026-10-20 raw pools: 0.25 nearby merged distinct issues on the
# same line ("video mock never called" into "mocks the function under test").
# A missed duplicate costs a repeated comment; a wrong merge loses a finding.
SIMILAR_ANYWHERE = 0.4
SIMILAR_NEARBY = 0.34
NEARBY_LINES = 3
RULE_NEARBY_LINES = 5


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return {_stem(word) for word in words if word not in _STOP and len(word) > 1}


def title_similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _line(item) -> int | None:
    try:
        return int(item.get("line"))
    except (TypeError, ValueError):
        return None


def _near(left, right, window: int) -> bool:
    a, b = _line(left), _line(right)
    return a is not None and b is not None and abs(a - b) <= window


def _file(item):
    return item.get("file") or item.get("file_path")


def same_issue(left: dict, right: dict) -> bool:
    if _file(left) != _file(right):
        return False
    left_rule, right_rule = left.get("rule_id"), right.get("rule_id")
    if left_rule and right_rule:
        return left_rule == right_rule and _near(left, right, NEARBY_LINES)
    if left_rule or right_rule:
        rule = left_rule or right_rule
        model = right if left_rule else left
        title = str(model.get("title") or "").lower()
        return _near(left, right, RULE_NEARBY_LINES) and any(
            word in title for word in _RULE_VOCABULARY.get(rule, ())
        )
    similarity = title_similarity(str(left.get("title") or ""), str(right.get("title") or ""))
    return similarity >= SIMILAR_ANYWHERE or (
        similarity >= SIMILAR_NEARBY and _near(left, right, NEARBY_LINES)
    )


def dedupe_candidates(findings: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (kept, merges); each merge names the kept index it folded into."""
    kept: list[dict] = []
    merges: list[dict] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        match = next((index for index, other in enumerate(kept) if same_issue(other, item)), None)
        if match is None:
            kept.append(item)
            continue
        merges.append({
            "kept_index": match,
            "kept_title": kept[match].get("title"),
            "merged": {
                key: item.get(key) for key in ("file", "line", "title", "origin", "rule_id")
            },
        })
    return kept, merges
