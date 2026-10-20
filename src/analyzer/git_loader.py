"""Read a frozen local Git revision range into planner inputs; no model calls.

Both the offline plan CLI and the baseline script use this module, so the two
cannot drift into reading different revisions or different changed-file sets.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.analyzer.repository_context import (
    GIT_TREE_MAX_BYTES,
    GitSnapshot,
    _BoundedIOUnavailable,
    _run_bounded_command,
)
from src.models import ChangedFile, FileStatus

_STATUSES = {"A": FileStatus.ADDED, "D": FileStatus.REMOVED, "M": FileStatus.MODIFIED}
_PLAN_PATCH_MAX_BYTES = 2_000_000
_PLAN_PATCH_TOTAL_MAX_BYTES = 8_000_000


class GitPlanError(RuntimeError):
    """Raised when the requested local Git revision range cannot be read."""


def _git(repo_path: Path, *args: str, max_bytes: int = GIT_TREE_MAX_BYTES) -> str:
    try:
        output, limited = _run_bounded_command(
            ["git", "--literal-pathspecs", "-C", str(repo_path), *args],
            max_bytes=max_bytes,
            timeout=30,
        )
        if limited:
            raise GitPlanError(f"git {' '.join(args[:2])} exceeded its output byte limit")
        return output.decode("utf-8")
    except UnicodeError as exc:
        raise GitPlanError(f"git {' '.join(args[:2])} returned unsupported text encoding") from exc
    except _BoundedIOUnavailable as exc:
        raise GitPlanError(f"git {' '.join(args[:2])} has no bounded output support") from exc
    except GitPlanError:
        raise
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
    files = []
    patch_bytes = 0
    for index in range(0, len(fields), 2):
        path = fields[index + 1]
        remaining_bytes = _PLAN_PATCH_TOTAL_MAX_BYTES - patch_bytes
        if remaining_bytes <= 0:
            raise GitPlanError("frozen diff exceeded the total patch byte limit")
        patch = _git(
            root,
            "diff",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            base_sha,
            head_sha,
            "--",
            path,
            max_bytes=min(_PLAN_PATCH_MAX_BYTES, remaining_bytes),
        )
        patch_bytes += len(patch.encode("utf-8"))
        if patch_bytes > _PLAN_PATCH_TOTAL_MAX_BYTES:
            raise GitPlanError("frozen diff exceeded the total patch byte limit")
        files.append(ChangedFile(
            filename=path,
            status=_STATUSES.get(fields[index], FileStatus.UNKNOWN),
            patch=patch,
        ))
    paths = _git(
        root, "ls-tree", "-r", "--name-only", "-z", head_sha,
        max_bytes=GIT_TREE_MAX_BYTES,
    ).rstrip("\0").split("\0")
    return snapshot, files, [path for path in paths if path]
