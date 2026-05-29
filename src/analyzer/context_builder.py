from __future__ import annotations

from dataclasses import dataclass, field

from src.analyzer.diff_parser import parse_file_hunks
from src.models import ChangedFile, PullRequest, RiskFinding

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
    skipped_files: list[tuple[str, str]] = field(default_factory=list)
    # (file_path, reason)


def _skip_patch_reason(filename: str) -> str | None:
    name = filename.split("/")[-1]
    if name in LOCKFILE_NAMES:
        return "lockfile"
    for prefix, reason in SKIP_PATCH_REASONS.items():
        if filename.startswith(prefix):
            return reason
    return None


def build_review_context(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    max_patch_chars: int = 24_000,
) -> ReviewContext:
    parts: list[str] = [
        "# Pull Request",
        f"Repo: {pr.repo}",
        f"Number: {pr.number}",
        f"Title: {pr.title}",
        f"Author: {pr.author or 'unknown'}",
        f"Base: {pr.base_ref or 'unknown'}",
        f"Head: {pr.head_ref or 'unknown'}",
        "",
        "## Description",
        pr.body or "(empty)",
        "",
        "## Changed files",
    ]

    for file in files:
        parts.append(
            f"- {file.filename} [{file.status.value}] +{file.additions}/-{file.deletions}"
        )

    if findings:
        parts.extend(["", "## Rule findings"])
        for finding in findings:
            line = f":{finding.line}" if finding.line else ""
            parts.append(
                f"- {finding.severity.value} {finding.file_path}{line} "
                f"{finding.rule_id}: {finding.title}. Evidence: {finding.evidence}"
            )

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
    for file in ordered_files:
        reason = _skip_patch_reason(file.filename)
        if reason is not None:
            skipped_files.append((file.filename, reason))
            if reason == "lockfile":
                lockfiles_skipped += 1
            continue
        hunks = parse_file_hunks(file)
        if not hunks:
            continue
        file_text = "\n".join(h.raw for h in hunks)
        if len(file_text) > patch_budget:
            file_text = file_text[:patch_budget] + "\n...[truncated]"
        parts.extend(["", f"### {file.filename}", "```diff", file_text, "```"])
        patch_budget -= len(file_text)
        if patch_budget <= 0:
            parts.append("\nPatch budget exhausted. Remaining files omitted.")
            truncated = True
            break

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

    return ReviewContext(
        text="\n".join(parts),
        truncated=truncated,
        skipped_files=skipped_files,
    )
