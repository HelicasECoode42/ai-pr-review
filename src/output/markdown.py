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
        "",
        "## Summary",
        "",
        report.summary,
        "",
        "## Changed Files",
        "",
    ]

    for file in report.files:
        lines.append(
            f"- `{file.filename}` `{file.status.value}` +{file.additions}/-{file.deletions}"
        )

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
                    f"### {index}. {suggestion.title}",
                    "",
                    f"- Location: `{location}`",
                    f"- Severity: `{suggestion.severity.value}`",
                    f"- Confidence: `{suggestion.confidence:.2f}`",
                    f"- Reason: {suggestion.reason}",
                    f"- Recommendation: {suggestion.recommendation}",
                    "",
                ]
            )

    lines.extend(["", "## Rule Findings", ""])
    if not report.rule_findings:
        lines.append("No rule findings.")
    else:
        for finding in report.rule_findings:
            location = finding.file_path
            if finding.line is not None:
                location = f"{location}:{finding.line}"
            lines.extend(
                [
                    f"- `{finding.severity.value}` `{finding.rule_id}` at `{location}`",
                    f"  - {finding.title}",
                    f"  - Evidence: {finding.evidence}",
                ]
            )

    return "\n".join(lines).rstrip() + "\n"
