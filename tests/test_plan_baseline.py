"""The offline baseline records planning accounting, never review quality."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

from src.analyzer.plan_baseline import summarize_plan
from src.analyzer.review_plan import build_review_plan
from src.models import ChangedFile

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


class MemorySnapshot:
    base = "1" * 40
    head = "2" * 40

    def __init__(self, head_sources):
        self.head_sources = head_sources

    def read(self, path, side="head"):
        return self.head_sources.get(path) if side == "head" else None


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "plan_baseline_script", SCRIPTS_DIR / "plan_baseline.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_reports_coverage_mapping_and_omissions():
    files = [
        ChangedFile(filename="src/a.py", patch="@@ -1,1 +1,1 @@\n-old\n+new\n"),
        ChangedFile(filename="nopatch.py", patch="not a diff\n"),
    ]
    plan = build_review_plan(
        MemorySnapshot({"src/a.py": "value = 1\n"}), files, ["src/a.py"]
    )
    summary = summarize_plan(plan)
    assert summary["coverage"]["complete"] is True
    assert summary["coverage"]["planned_hunks"] == 1
    assert summary["fragment_line_mapping"] == {
        "slices": 1,
        "slices_with_a_line_span": 1,
        "slices_cutting_a_diff_line": 0,
    }
    assert summary["omission_reasons"] == {
        "base_definition_unavailable": 1,
        "head_unavailable": 1,
        "missing_or_invalid_patch": 1,
    }
    assert summary["budget_over_limit"] == []
    json.dumps(summary)  # the artifact must stay JSON-serializable


def test_baseline_script_records_a_frozen_range_only(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    git("add", "a.py")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "a.py").write_text("def a():\n    return 2\n")
    git("add", "a.py")
    git("commit", "-qm", "head")
    head = git("rev-parse", "HEAD")
    (tmp_path / "a.py").write_text("UNCOMMITTED_SECRET_SENTINEL")

    output = tmp_path / "baseline.json"
    exit_code = _load_script().main(
        [
            "--repo-path",
            str(tmp_path),
            "--base",
            base,
            "--head",
            head,
            "--token-budget",
            "1000",
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    text = output.read_text()
    assert "UNCOMMITTED_SECRET_SENTINEL" not in text
    record = json.loads(text)
    assert record["artifact"] == "chunk-planning-baseline"
    assert record["summary"]["base"] == base
    assert record["summary"]["head"] == head
    assert record["summary"]["coverage"]["complete"] is True
    assert record["summary"]["coverage"]["planned_hunks"] == 1
    assert record["planner_revision"]
