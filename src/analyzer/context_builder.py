from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.analyzer.diff_parser import parse_file_hunks
from src.models import ChangedFile, PullRequest, RiskFinding, FileStatus
from src.github.client import GitHubClient, GitHubApiError
from src.utils.config import get_settings

logger = logging.getLogger(__name__)


class AnalysisStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


LOCKFILE_NAMES = {
    "uv.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
}

SKIP_PATCH_PREFIXES = (
    "docs/demo/",
    "reports/",
)

SKIP_PATCH_REASONS: dict[str, str] = {
    "docs/demo/": "demo report (generated artifact)",
    "reports/": "generated report",
}


@dataclass
class ReviewContext:
    text: str
    truncated: bool
    status: AnalysisStatus = AnalysisStatus.SUCCESS
    skipped_files: list[tuple[str, str]] = field(default_factory=list)


def _skip_patch_reason(filename: str) -> str | None:
    try:
        name = filename.split("/")[-1]
        if name in LOCKFILE_NAMES:
            return "lockfile"
        for prefix, reason in SKIP_PATCH_REASONS.items():
            if filename.startswith(prefix):
                return reason
        return None
    except Exception:
        return None


def _fetch_repo_file_text(repo: str, path: str, ref: str | None) -> str | None:
    """Try to fetch file text from the given ref; if ref fetch fails, fall back to 'main'.

    Returns None if the file cannot be fetched.
    """
    settings = get_settings()
    try:
        with GitHubClient(settings.github_token, timeout=settings.request_timeout_seconds) as gh:
            try:
                text = gh.get_file_contents(repo, path, ref=ref) if ref else gh.get_file_contents(repo, path)
                if text is not None:
                    return text
            except GitHubApiError as e:
                logger.debug(f"Failed to fetch {repo}/{path}@{ref}: {e}")
            # fallback to main branch
            try:
                main_text = gh.get_file_contents(repo, path, ref="main")
                return main_text
            except GitHubApiError:
                return None
    except Exception as e:
        logger.warning(f"Error while fetching file contents for {repo}/{path}: {e}")
        return None


def build_review_context(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    max_patch_chars: int = 24_000,
) -> ReviewContext:
    # Input validation: require a valid PullRequest object
    if not isinstance(pr, PullRequest):
        raise TypeError("pr must be a PullRequest instance")
    if files is None:
        files = []
    if findings is None:
        findings = []

    # Detect empty/invalid PR (e.g., from failed API call)
    if (hasattr(pr, 'title') and not pr.title and pr.number == 0) or not pr.repo:
        error_text = (
            "# Analysis Failed\n\n"
            "The pull request information could not be retrieved from GitHub API.\n"
            "Possible reasons: invalid token, network issue, or the PR does not exist.\n"
            "No analysis was performed."
        )
        return ReviewContext(text=error_text, truncated=False, status=AnalysisStatus.FAILED)

    def safe_attr(obj, attr, default):
        try:
            val = getattr(obj, attr, default)
            return val if val is not None else default
        except Exception:
            return default

    parts: list[str] = [
        "# Pull Request",
        f"Repo: {safe_attr(pr, 'repo', 'unknown')}",
        f"Number: {safe_attr(pr, 'number', 0)}",
        f"Title: {safe_attr(pr, 'title', '')}",
        f"Author: {safe_attr(pr, 'author', 'unknown')}",
        f"Base: {safe_attr(pr, 'base_ref', 'unknown')}",
        f"Head: {safe_attr(pr, 'head_ref', 'unknown')}",
        "",
        "## Description",
        safe_attr(pr, 'body', '(empty)') or '(empty)',
        "",
        "## Changed files",
    ]

    for file in files:
        try:
            status = getattr(file, 'status', 'unknown')
            if hasattr(status, 'value'):
                status = status.value
            parts.append(
                f"- {file.filename} [{status}] +{file.additions}/-{file.deletions}"
            )
        except Exception as e:
            logger.warning(f"Failed to add file info for {getattr(file, 'filename', '?')}: {e}")
            continue

    if findings:
        parts.extend(["", "## Rule findings"])
        for finding in findings:
            try:
                line = f":{finding.line}" if finding.line else ""
                parts.append(
                    f"- {finding.severity.value} {finding.file_path}{line} "
                    f"{finding.rule_id}: {finding.title}. Evidence: {finding.evidence}"
                )
            except Exception as e:
                logger.warning(f"Failed to add finding: {e}")
                continue

    parts.extend(["", "## Patches"])
    patch_budget = max_patch_chars
    truncated = False
    ordered_files = sorted(
        files,
        key=lambda f: (
            not any(r.file_path == f.filename for r in findings),
            -(f.additions + f.deletions),
        ),
    )
    skipped_files: list[tuple[str, str]] = []
    lockfiles_skipped = 0
    partial_flag = False

    for file in ordered_files:
        try:
            reason = _skip_patch_reason(file.filename)
            if reason is not None:
                skipped_files.append((file.filename, reason))
                if reason == "lockfile":
                    lockfiles_skipped += 1
                continue

            parse_failed = False
            try:
                hunks = parse_file_hunks(file)
            except Exception as e:
                logger.warning(f"Failed to parse hunks for {file.filename}: {e}")
                hunks = []
                parse_failed = True
                partial_flag = True

            # If hunks empty and either parsing failed, patch missing, or file added/removed, try fallback to full file content
            if not hunks and (parse_failed or file.patch is None or file.patch == "" or file.status in (FileStatus.ADDED, FileStatus.REMOVED)):
                # For added files, include full content from head_ref if available
                # For removed files, try base_ref
                ref_try = None
                if file.status == FileStatus.ADDED:
                    ref_try = pr.head_ref
                elif file.status == FileStatus.REMOVED:
                    ref_try = pr.base_ref
                else:
                    # try both head then base
                    ref_try = pr.head_ref or pr.base_ref

                file_contents = None
                try:
                    file_contents = _fetch_repo_file_text(pr.repo, file.filename, ref_try)
                except Exception as e:
                    logger.debug(f"Fetching file contents fallback failed for {file.filename}: {e}")

                if file_contents:
                    file_text = file_contents
                    if len(file_text) > patch_budget:
                        file_text = file_text[:patch_budget] + "\n...[truncated]"
                    parts.extend(["", f"### {file.filename}", "```", file_text, "```"])
                    patch_budget -= len(file_text)
                    if patch_budget <= 0:
                        parts.append("\nPatch budget exhausted. Remaining files omitted.")
                        truncated = True
                        partial_flag = True
                        break
                    continue
                else:
                    # Couldn't fetch full file; mark partial and continue
                    logger.warning(f"Could not retrieve full contents for {file.filename} during fallback")
                    partial_flag = True
                    continue

            if not hunks:
                continue

            file_text = "\n".join(h.raw for h in hunks)
            # If hunks seem incomplete (e.g., new_count differs from added lines), attempt to fetch full file
            total_added = sum(len(h.added_lines) for h in hunks)
            total_hunk_lines = sum(h.new_count for h in hunks)
            if total_hunk_lines > 0 and total_added < total_hunk_lines // 2:
                # Hunk likely truncated; fetch full file from base/main
                file_contents = _fetch_repo_file_text(pr.repo, file.filename, pr.base_ref)
                if file_contents:
                    file_text = file_contents

            if len(file_text) > patch_budget:
                file_text = file_text[:patch_budget] + "\n...[truncated]"
            parts.extend(["", f"### {file.filename}", "```diff", file_text, "```"])
            patch_budget -= len(file_text)
            if patch_budget <= 0:
                parts.append("\nPatch budget exhausted. Remaining files omitted.")
                truncated = True
                partial_flag = True
                break
        except Exception as e:
            logger.warning(f"Failed to process patch for {getattr(file, 'filename', '?')}: {e}")
            partial_flag = True
            continue

    if lockfiles_skipped > 0 or skipped_files:
        parts.append("")
        if lockfiles_skipped > 0:
            parts.append(
                f"({lockfiles_skipped} lockfile(s) excluded from patch context: "
                f"only change statistics are shown.)"
            )
        for fpath, freason in skipped_files:
            if freason != "lockfile":
                parts.append(
                    f"(`{fpath}` excluded from patch context "
                    f"({freason}): only change statistics are shown.)"
                )

    # Determine final status
    if partial_flag:
        status = AnalysisStatus.PARTIAL
    else:
        status = AnalysisStatus.SUCCESS

    # Additional check: if there are no files, it's partial
    if not files:
        status = AnalysisStatus.PARTIAL

    return ReviewContext(
        text="\n".join(parts),
        truncated=truncated,
        status=status,
        skipped_files=skipped_files,
    )