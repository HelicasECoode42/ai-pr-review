"""Deterministic checks: free, factual, and honest about what they skipped."""

from __future__ import annotations

from pathlib import Path

from src.analyzer.deterministic_tools import run_linters
from src.models import ChangedFile

BAD = "import os\n\n\ndef f():\n    x = 1\n    return x\n"
GOOD = "def f():\n    return 1\n"


def changed(name: str) -> ChangedFile:
    return ChangedFile(filename=name, patch=f"@@ -1,1 +1,1 @@\n+{name}\n")


def test_a_real_defect_is_reported_as_a_fact(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "bad.py").write_text(BAD)
    result = run_linters([changed("bad.py")], root=root)
    assert result.checked_paths == ["bad.py"]
    assert result.findings, "unused import must be reported"
    finding = result.findings[0]
    assert finding.rule_id.startswith("lint-")
    assert finding.confidence == 1.0
    assert finding.line


def test_a_clean_file_produces_nothing(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "good.py").write_text(GOOD)
    result = run_linters([changed("good.py")], root=root)
    assert result.findings == []
    assert result.unavailable_reason is None


def test_a_path_absent_from_the_tree_is_skipped_and_recorded(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    result = run_linters([changed("gone.py")], root=root)
    assert result.findings == []
    assert result.skipped_paths == ["gone.py"]
    assert result.checked_paths == []


def test_only_python_files_are_checked(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.md").write_text("# hi\n")
    result = run_linters([changed("notes.md")], root=root)
    assert result.checked_paths == [] and result.skipped_paths == []


def test_the_rule_selection_excludes_style(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    # Two blank lines before a top-level def is style (E301/E303) and must not be
    # reported: this tool must not become a style bot.
    (root / "style.py").write_text("x = 1\n\n\n\ndef f():\n    return x\n")
    assert run_linters([changed("style.py")], root=root).findings == []
