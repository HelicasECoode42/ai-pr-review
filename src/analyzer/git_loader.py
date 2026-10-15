"""Read a frozen local Git revision range into planner inputs; no model calls.

Both the offline plan CLI and the baseline script use this module, so the two
cannot drift into reading different revisions or different changed-file sets.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.analyzer.repository_context import GitSnapshot
from src.models import ChangedFile, FileStatus

_STATUSES = {"A": FileStatus.ADDED, "D": FileStatus.REMOVED, "M": FileStatus.MODIFIED}


class GitPlanError(RuntimeError):
    """Raised when the requested local Git revision range cannot be read."""


def _git(repo_path: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "--literal-pathspecs", "-C", str(repo_path), *args],
            text=True,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitPlanError(f"git {' '.join(args[:2])} failed: {exc}") from exc


def _rev_parse(repo_path: Path, ref: str) -> str:
    return _git(repo_path, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").strip()


def load_frozen_plan_inputs(
    repo_path: Path, base: str, head: str
) -> tuple[GitSnapshot, list[ChangedFile], list[str]]:
    """Return the immutable snapshot, changed files and head path inventory."""
    root = Path(repo_path).resolve()
    base_sha = _rev_parse(root, base)
    head_sha = _rev_parse(root, head)
    snapshot = GitSnapshot(root, base_sha, head_sha)
    raw = _git(root, "diff", "--no-renames", "--name-status", "-z", base_sha, head_sha, "--")
    fields = raw.rstrip("\0").split("\0") if raw else []
    files = [
        ChangedFile(
            filename=fields[index + 1],
            status=_STATUSES.get(fields[index], FileStatus.UNKNOWN),
            patch=_git(
                root,
                "diff",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                base_sha,
                head_sha,
                "--",
                fields[index + 1],
            ),
        )
        for index in range(0, len(fields), 2)
    ]
    paths = _git(root, "ls-tree", "-r", "--name-only", "-z", head_sha).rstrip("\0").split("\0")
    return snapshot, files, [path for path in paths if path]
