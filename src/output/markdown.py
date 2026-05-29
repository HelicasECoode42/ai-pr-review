from __future__ import annotations

from src.models import ReviewReport


def render_markdown(report: ReviewReport) -> str:
    lines = [
        f"# AI PR Review: {report.pr.repo}#{report.pr.number}",
        "",
        f"- Title: {report.pr.title}",
        f"- Author: {report.pr.author or 'unknown'}",
        f"- Base: {report.pr.base_ref or 'unknown'}",
        f"- Head: {report.pr.head_ref or 'unknown'}",
        f"- URL: {report.pr.html_url or 'n/a'}",
        f"- AI used: {'yes' if report.used_ai else 'no'}",
        f"- Overall risk: {report.risk_level.value}",
    ]
    if report.ai_failure_reason:
        lines.append(f"- AI failure reason: {report.ai_failure_reason}")

    lines.extend([
        "",
        "## Summary",
        "",
        report.summary,
        "",
        "## Changed Files",
        "",
    ])

    additions = sum(f.additions for f in report.files)
    deletions = sum(f.deletions for f in report.files)
    lines.append(f"| File | Status | +/− |")
    lines.append(f"|---|---|---|")
    for file in report.files:
        lines.append(
            f"| `{file.filename}` | `{file.status.value}` | +{file.additions}/-{file.deletions} |"
        )
    lines.append(f"| **Total** ({len(report.files)} files) | | **+{additions}/-{deletions}** |")

    lines.extend(["", "## Review Suggestions", ""])
    if not report.suggestions:
        lines.append("No high-signal suggestions generated.")
    else:
        for index, suggestion in enumerate(report.suggestions, start=1):
            location = suggestion.file_path
            if suggestion.line is not None:
                location = f"{location}:{suggestion.line}"
            lines.extend(
                [
                    f"### {index}. [{suggestion.severity.value.upper()}] {suggestion.title}",
                    "",
                    f"- **Location**: `{location}`",
                    f"- **Confidence**: {suggestion.confidence:.0%}",
                    f"- **Reason**: {suggestion.reason}",
                    f"- **Recommendation**: {suggestion.recommendation}",
                    "",
                    "<details>",
                    "<summary>Suggested GitHub comment</summary>",
                    "",
                    f"**{suggestion.severity.value}**: {suggestion.title}",
                    "",
                    f"> {suggestion.reason}",
                    "",
                    f"Suggestion: {suggestion.recommendation}",
                    "",
                    "</details>",
                    "",
                ]
            )

    lines.extend(["", "## Rule Findings", ""])
    if not report.rule_findings:
        lines.append("No rule findings.")
    else:
        lines.append("| Severity | Rule | Location | Finding |")
        lines.append("|---|---|---|---|")
        for finding in report.rule_findings:
            location = finding.file_path
            if finding.line is not None:
                location = f"{location}:{finding.line}"
            lines.append(
                f"| `{finding.severity.value}` | `{finding.rule_id}` "
                f"| `{location}` | {finding.title} |"
            )

    if report.analysis_warnings or report.hidden_suggestions_count > 0:
        lines.extend(["", "## Analysis Notes", ""])
        if report.hidden_suggestions_count > 0:
            lines.append(
                f"- {report.hidden_suggestions_count} low-confidence or "
                f"duplicate suggestion(s) hidden"
            )
        for warning in report.analysis_warnings:
            lines.append(f"- {warning}")

    return "\n".join(lines).rstrip() + "\n"
