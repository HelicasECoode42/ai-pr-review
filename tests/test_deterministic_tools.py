"""Frozen-snapshot Ruff diagnostics are bounded, attributable candidates."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from src.analyzer import deterministic_tools
from src.analyzer.deterministic_tools import run_linters
from src.models import ChangedFile, FileStatus


class Snapshot:
    base = "a" * 40
    head = "b" * 40

    def __init__(self, contents: dict[tuple[str, str], str | None]):
        self.contents = contents
        self.reads: list[tuple[str, str]] = []

    def read(self, path: str, side: str = "head") -> str | None:
        self.reads.append((path, side))
        return self.contents.get((path, side))


def changed(name: str, patch: str, *, status=FileStatus.MODIFIED) -> ChangedFile:
    return ChangedFile(filename=name, status=status, patch=patch)


def test_lints_frozen_head_not_cwd_and_marks_added_line_as_candidate(tmp_path: Path, monkeypatch):
    cwd = tmp_path / "checkout"
    cwd.mkdir()
    (cwd / "bad.py").write_text("def f():\n    return 1\n")
    monkeypatch.chdir(cwd)
    snapshot = Snapshot({
        ("bad.py", "base"): "def f():\n    return 1\n",
        ("bad.py", "head"): "import os\ndef f():\n    return 1\n",
    })
    file = changed("bad.py", "@@ -1,0 +1,1 @@\n+import os\n")

    result = run_linters([file], snapshot=snapshot)

    assert result.checked_paths == ["bad.py"]
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.line == 1
    assert finding.rule_id == "lint-candidate-f401"
    assert "candidate" in finding.title.lower()
    assert finding.confidence < 0.65
    assert result.tool_version and result.head_sha == snapshot.head


def test_same_base_diagnostic_is_not_reported_as_a_new_candidate():
    source = "import os\ndef f():\n    return 1\n"
    snapshot = Snapshot({("bad.py", "base"): source, ("bad.py", "head"): source})
    result = run_linters(
        [changed("bad.py", "@@ -1,1 +1,1 @@\n import os\n")], snapshot=snapshot
    )
    assert result.findings == []
    assert result.baseline_diagnostics == 1
    assert result.status == "complete"


def test_missing_snapshot_is_explicit_and_never_reads_cwd(tmp_path: Path, monkeypatch):
    (tmp_path / "bad.py").write_text("import os\n")
    monkeypatch.chdir(tmp_path)
    result = run_linters([changed("bad.py", "@@ -0,0 +1,1 @@\n+import os\n")])
    assert result.status == "unavailable"
    assert "snapshot" in result.unavailable_reason
    assert result.findings == []


def test_missing_base_text_keeps_head_diagnostic_but_never_calls_it_new():
    snapshot = Snapshot({("bad.py", "head"): "import os\n"})
    result = run_linters(
        [changed("bad.py", "@@ -0,0 +1,1 @@\n+import os\n")], snapshot=snapshot
    )
    assert result.findings == []
    assert any(note.startswith("base_unavailable_unattributed:") for note in result.notes)
    assert result.unattributed_diagnostics == 1


def test_non_python_and_removed_files_are_not_materialized():
    snapshot = Snapshot({})
    result = run_linters([
        changed("notes.md", "@@ -1,1 +1,1 @@\n # note\n"),
        changed("gone.py", "", status=FileStatus.REMOVED),
    ], snapshot=snapshot)
    assert result.checked_paths == []
    assert "gone.py" in result.skipped_paths
    assert snapshot.reads == []


def test_unsafe_path_is_skipped_before_snapshot_read():
    snapshot = Snapshot({})
    result = run_linters(
        [changed("../escape.py", "@@ -0,0 +1,1 @@\n+import os\n")], snapshot=snapshot
    )
    assert result.checked_paths == []
    assert "../escape.py" in result.skipped_paths
    assert snapshot.reads == []


def test_added_file_uses_empty_base_and_can_only_yield_a_candidate():
    snapshot = Snapshot({("new.py", "base"): None, ("new.py", "head"): "import os\n"})
    result = run_linters([
        changed("new.py", "@@ -0,0 +1,1 @@\n+import os\n", status=FileStatus.ADDED)
    ], snapshot=snapshot)
    assert len(result.findings) == 1
    assert "candidate" in result.findings[0].title.lower()
    assert not any(note.startswith("unattributed:") for note in result.notes)


def test_file_size_limit_is_explicit_and_does_not_run_ruff():
    source = "x = 1\n" * 20_000
    snapshot = Snapshot({("large.py", "base"): "", ("large.py", "head"): source})
    result = run_linters([
        changed("large.py", "@@ -0,0 +1,1 @@\n+x = 1\n")
    ], snapshot=snapshot)
    assert result.findings == []
    assert "large.py" in result.skipped_paths
    assert any(note.startswith("file_size_limit:") for note in result.notes)


def test_total_snapshot_bytes_are_bounded():
    source = "x = 1\n" * 13_000
    contents = {}
    files = []
    for name in ("a.py", "b.py", "c.py"):
        contents[(name, "base")] = source
        contents[(name, "head")] = source
        files.append(changed(name, "@@ -1,1 +1,1 @@\n x = 1\n"))
    result = run_linters(files, snapshot=Snapshot(contents))
    assert "total_size_limit:400000" in result.notes
    assert result.status == "partial"


def test_snapshot_fetch_budget_stops_after_included_rejected_file_bytes():
    large = "x = 1\n" * 65_000  # below the snapshot reader's 500KB per-read ceiling
    contents = {}
    files = []
    for name in ("a.py", "b.py", "c.py"):
        contents[(name, "base")] = large
        contents[(name, "head")] = large
        files.append(changed(name, "@@ -1,1 +1,1 @@\n x = 1\n"))
    snapshot = Snapshot(contents)
    result = run_linters(files, snapshot=snapshot)
    assert result.snapshot_read_calls == 3
    assert result.snapshot_bytes_read > deterministic_tools.MAX_SNAPSHOT_FETCH_BYTES
    assert result.snapshot_bytes_read <= deterministic_tools.MAX_SNAPSHOT_FETCH_BYTES + len(large)
    assert any(note.startswith("snapshot_fetch_bytes_limit:") for note in result.notes)


def test_output_limit_kills_noisy_tool(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(deterministic_tools, "MAX_OUTPUT_BYTES", 64)
    started = time.monotonic()
    _, _, _, error = deterministic_tools._bounded_process(
        [sys.executable, "-c", "import sys,time; print('x'*10000,flush=True); time.sleep(3)"],
        cwd=tmp_path, timeout=2,
    )
    assert error and "output exceeded" in error
    assert time.monotonic() - started < 2


def test_closed_pipes_do_not_make_process_wait_unbounded(tmp_path: Path):
    started = time.monotonic()
    _, _, _, error = deterministic_tools._bounded_process(
        [sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(2)"],
        cwd=tmp_path, timeout=0.1,
    )
    assert error and "timed out" in error
    assert time.monotonic() - started < 1
