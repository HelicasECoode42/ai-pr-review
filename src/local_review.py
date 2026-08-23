from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.models import ChangedFile, FileStatus


class LocalReviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalDiff:
    root: Path
    mode: str
    base: str | None
    head_sha: str
    files: list[ChangedFile]


def _git(root: Path, *args: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "git command failed"
        raise LocalReviewError(detail)
    return process.stdout


def find_git_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    output = _git(candidate, "rev-parse", "--show-toplevel")
    return Path(output.strip()).resolve()


def _has_staged_changes(root: Path) -> bool:
    process = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--quiet", "--exit-code"],
        check=False,
        capture_output=True,
    )
    if process.returncode not in (0, 1):
        raise LocalReviewError(process.stderr.decode(errors="replace").strip())
    return process.returncode == 1


def _ref_exists(root: Path, ref: str) -> bool:
    process = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", ref],
        check=False,
        capture_output=True,
    )
    return process.returncode == 0


def detect_base(root: Path) -> str:
    for ref in ("origin/main", "main", "origin/master", "master"):
        if _ref_exists(root, ref):
            return ref
    raise LocalReviewError("Cannot detect base branch; pass --base explicitly")


def collect_local_diff(
    start: Path | None = None,
    *,
    force_staged: bool = False,
    base: str | None = None,
) -> LocalDiff:
    root = find_git_root(start)
    staged = force_staged or (base is None and _has_staged_changes(root))
    if staged:
        diff_args = ["--cached"]
        mode = "staged"
        selected_base = None
    else:
        selected_base = base or detect_base(root)
        diff_args = [f"{selected_base}...HEAD"]
        mode = "branch"

    names = _git(root, "diff", *diff_args, "--name-status", "--find-renames")
    if not names.strip() and not staged:
        working_names = _git(root, "diff", "--name-status", "--find-renames")
        if working_names.strip():
            names = working_names
            diff_args = []
            mode = "working"
            selected_base = None
    files: list[ChangedFile] = []
    for raw in names.splitlines():
        if not raw.strip():
            continue
        columns = raw.split("\t")
        code = columns[0]
        status_code = code[0]
        if status_code == "R" and len(columns) >= 3:
            previous, filename = columns[1], columns[2]
            status = FileStatus.RENAMED
        else:
            previous = None
            filename = columns[-1]
            status = {
                "A": FileStatus.ADDED,
                "M": FileStatus.MODIFIED,
                "D": FileStatus.REMOVED,
            }.get(status_code, FileStatus.UNKNOWN)

        patch = _git(root, "diff", *diff_args, "--no-ext-diff", "--unified=3", "--", filename)
        numstat = _git(root, "diff", *diff_args, "--numstat", "--", filename).strip()
        additions = deletions = 0
        if numstat:
            first = numstat.splitlines()[0].split("\t")
            if len(first) >= 2:
                additions = int(first[0]) if first[0].isdigit() else 0
                deletions = int(first[1]) if first[1].isdigit() else 0
        files.append(ChangedFile(
            filename=filename,
            previous_filename=previous,
            status=status,
            additions=additions,
            deletions=deletions,
            changes=additions + deletions,
            patch=patch,
        ))

    return LocalDiff(
        root=root,
        mode=mode,
        base=selected_base,
        head_sha=_git(root, "rev-parse", "HEAD").strip(),
        files=files,
    )


_PR_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:[/?#].*)?$"
)


def parse_pr_url(url: str) -> tuple[str, int]:
    match = _PR_URL_RE.match(url.strip())
    if not match:
        raise LocalReviewError("Expected a GitHub PR URL like https://github.com/OWNER/REPO/pull/123")
    return f"{match.group('owner')}/{match.group('repo')}", int(match.group("number"))


def load_gh_token() -> str | None:
    try:
        process = subprocess.run(
            ["gh", "auth", "token"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    token = process.stdout.strip()
    return token if process.returncode == 0 and token else None
