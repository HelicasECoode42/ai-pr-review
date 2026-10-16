"""Mechanical vs semantic partition: the token-denominator reduction."""

from __future__ import annotations

from src.analyzer.semantic_diff import changed_lines, classify, partition
from src.models import ChangedFile

WS = "@@ -1,1 +1,1 @@\n-    return x + 1\n+    return x+1\n"
COMMENT = "@@ -1,1 +1,1 @@\n-# old note\n+# new note\n"
IMPORTS = "@@ -1,2 +1,2 @@\n-import b\n-import a\n+import a\n+import b\n"
RENAME = "@@ -1,2 +1,2 @@\n-    total = 0\n+    acc = 0\n"
REAL = "@@ -1,2 +1,2 @@\n-    return total / count if count else 0\n+    return total / count\n"


def kinds(patch: str) -> str:
    removed, added = changed_lines(patch)
    return classify(removed, added)


def test_each_mechanical_family_is_recognised():
    assert kinds(WS) == "whitespace_only"
    assert kinds(COMMENT) == "comment_only"
    assert kinds(IMPORTS) == "import_reorder"
    assert kinds(RENAME) == "rename_only"


def test_a_real_behaviour_change_is_not_mechanical():
    assert kinds(REAL) == "semantic"


def test_partition_reports_the_share_and_keeps_the_semantic_part():
    files = [ChangedFile(filename="a.py", patch=WS), ChangedFile(filename="b.py", patch=REAL)]
    summary = partition(files).summary()
    assert summary["hunks"] == {"semantic": 1, "whitespace_only": 1}
    assert summary["semantic_tokens"] == 2
    assert 0 < summary["mechanical_share"] < 1


def test_an_inconsistent_rename_is_semantic():
    patch = "@@ -1,2 +1,2 @@\n-    total = 0\n-    return total\n+    acc = 0\n+    return other\n"
    assert kinds(patch) == "semantic"
