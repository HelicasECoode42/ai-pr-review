"""Tests for the dev/holdout prepared-input filter."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_dev_subset.py"
spec = importlib.util.spec_from_file_location("eval_dev_subset", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fake_prepared() -> dict:
    return {
        "benchmark_commit": "abc",
        "task_count": 3,
        "records": [
            {"case_id": "a_repo-1-aaaaaaaa", "status": "ready", "input_dir": "/x/a"},
            {"case_id": "b_repo-2-bbbbbbbb", "status": "ready", "input_dir": "/x/b"},
            {"case_id": "c_repo-3-cccccccc", "status": "ready", "input_dir": "/x/c"},
        ],
    }


SPLIT = {"dev": ["c_repo-3-cccccccc", "a_repo-1-aaaaaaaa"], "holdout": ["b_repo-2-bbbbbbbb"]}


def test_subset_keeps_split_order_and_records() -> None:
    result = module.subset(fake_prepared(), SPLIT, "dev")
    assert [r["case_id"] for r in result["records"]] == ["c_repo-3-cccccccc", "a_repo-1-aaaaaaaa"]
    assert result["records"][0]["input_dir"] == "/x/c"
    assert result["task_count"] == 2
    assert result["subset"] == "dev"
    assert result["benchmark_commit"] == "abc"


def test_subset_holdout() -> None:
    result = module.subset(fake_prepared(), SPLIT, "holdout")
    assert [r["case_id"] for r in result["records"]] == ["b_repo-2-bbbbbbbb"]


def test_missing_case_id_is_an_error() -> None:
    prepared = fake_prepared()
    prepared["records"] = prepared["records"][:1]
    with pytest.raises(KeyError, match="dev case ids missing.*c_repo-3-cccccccc"):
        module.subset(prepared, SPLIT, "dev")


def test_cli_writes_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepared_path = tmp_path / "prepared.json"
    split_path = tmp_path / "split.json"
    output = tmp_path / "out" / "dev.json"
    prepared_path.write_text(json.dumps(fake_prepared()))
    split_path.write_text(json.dumps(SPLIT))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eval_dev_subset.py",
            str(prepared_path),
            str(split_path),
            "--set",
            "dev",
            "--output",
            str(output),
        ],
    )
    module.main()
    assert len(json.loads(output.read_text())["records"]) == 2
