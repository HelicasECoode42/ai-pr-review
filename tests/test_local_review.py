from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.local_review import LocalReviewError, collect_local_diff, load_gh_token, parse_pr_url


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_parse_pr_url() -> None:
    assert parse_pr_url("https://github.com/acme/widget/pull/42") == ("acme/widget", 42)
    with pytest.raises(LocalReviewError):
        parse_pr_url("https://example.com/acme/widget/pull/42")


@patch("src.local_review.subprocess.run")
def test_load_gh_token_uses_authenticated_cli(mock_run: Mock) -> None:
    mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="ghp_test\n", stderr="")

    assert load_gh_token() == "ghp_test"
    mock_run.assert_called_once_with(
        ["gh", "auth", "token"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


@patch("src.local_review.subprocess.run", side_effect=FileNotFoundError)
def test_load_gh_token_is_optional_when_cli_is_missing(_mock_run: Mock) -> None:
    assert load_gh_token() is None


def test_default_prefers_staged_diff(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    source = tmp_path / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "initial")
    source.write_text("value = 2\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")

    diff = collect_local_diff(tmp_path)

    assert diff.mode == "staged"
    assert [item.filename for item in diff.files] == ["app.py"]
    assert diff.files[0].additions == 1
    assert diff.files[0].deletions == 1


def test_branch_diff_uses_explicit_base(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    source = tmp_path / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "initial")
    git(tmp_path, "switch", "-c", "feature")
    source.write_text("value = 2\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "change")

    diff = collect_local_diff(tmp_path, base="main")

    assert diff.mode == "branch"
    assert diff.base == "main"
    assert len(diff.files) == 1


def test_falls_back_to_working_tree_when_branch_diff_is_empty(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    source = tmp_path / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-m", "initial")
    source.write_text("value = 2\n", encoding="utf-8")

    diff = collect_local_diff(tmp_path)

    assert diff.mode == "working"
    assert len(diff.files) == 1
