from __future__ import annotations

from src.analyzer.diff_parser import parse_file_hunks
from src.models import ChangedFile, PullRequest, RiskFinding


def build_review_context(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding],
    max_patch_chars: int = 24_000,
) -> tuple[str, bool]:
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
    for file in ordered_files:
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

    return "\n".join(parts), truncated
