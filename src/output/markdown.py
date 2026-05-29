from __future__ import annotations

from src.models import ReviewReport, ReviewSuggestion, Severity


def render_markdown(report: ReviewReport) -> str:
    lines = [
        f"# AI PR Review: {report.pr.repo}#{report.pr.number}",
        "",
        "## PR Overview",
        "",
    ]

    additions = sum(f.additions for f in report.files)
    deletions = sum(f.deletions for f in report.files)

    lines.extend(
        [
            "| Field | Value |",
            "|---|---|",
            f"| Repository | `{report.pr.repo}` |",
            f"| PR | [#{report.pr.number}]({report.pr.html_url or '#'}) |",
            f"| Title | {report.pr.title} |",
            f"| Author | {report.pr.author or 'unknown'} |",
            f"| Base | `{report.pr.base_ref or 'unknown'}` |",
            f"| Head | `{report.pr.head_ref or 'unknown'}` |",
            f"| Files changed | {len(report.files)} |",
            f"| Additions / Deletions | +{additions} / -{deletions} |",
            f"| Overall risk | **`{report.risk_level.value.upper()}`** |",
            f"| AI used | {'yes' if report.used_ai else 'no'} |",
        ]
    )

    if report.ai_failure_reason:
        lines.append(f"| AI failure | {report.ai_failure_reason} |")
    if report.context_truncated:
        lines.append(
            "| Context | Patch context was truncated; some files not analyzed by AI |"
        )

    # Risk summary
    severity_counts = _count_by_severity(report.suggestions)
    if severity_counts:
        lines.extend(["", "### Risk Summary", ""])
        for sev in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW):
            count = severity_counts.get(sev, 0)
            if count > 0:
                lines.append(f"- **{sev.value.upper()}**: {count} suggestion(s)")
        if not any(severity_counts.values()):
            lines.append("No risk items detected.")

    # Summary
    lines.extend(["", "## Executive Summary", "", report.summary])

    # Changed files
    lines.extend(["", "## Changed Files", ""])
    lines.append("| File | Status | +/- |")
    lines.append("|---|---|---|")
    for file in report.files:
        lines.append(
            f"| `{file.filename}` | `{file.status.value}` "
            f"| +{file.additions}/-{file.deletions} |"
        )
    lines.append(
        f"| **Total** ({len(report.files)} files) | | **+{additions}/-{deletions}** |"
    )

    # Suggestions
    lines.extend(["", "## Review Suggestions", ""])
    if not report.suggestions:
        lines.append("No high-signal suggestions generated.")
    else:
        lines.append("| # | Severity | Location | Confidence | Title |")
        lines.append("|---|---|---|---|---|")
        for index, s in enumerate(report.suggestions, start=1):
            location = s.file_path
            if s.line is not None:
                location = f"{location}:{s.line}"
            lines.append(
                f"| {index} | `{s.severity.value.upper()}` "
                f"| `{location}` | {s.confidence:.0%} | {s.title} |"
            )

        lines.extend(["", "---", ""])
        for index, s in enumerate(report.suggestions, start=1):
            location = s.file_path
            if s.line is not None:
                location = f"{location}:{s.line}"
            lines.extend(
                [
                    f"### {index}. [{s.severity.value.upper()}] {s.title}",
                    "",
                    f"- **Location**: `{location}`",
                    f"- **Confidence**: {s.confidence:.0%}",
                    f"- **Reason**: {s.reason}",
                    f"- **Recommendation**: {s.recommendation}",
                    "",
                    "<details>",
                    "<summary>Suggested GitHub comment</summary>",
                    "",
                    f"**{s.severity.value}**: {s.title}",
                    "",
                    f"> {s.reason}",
                    "",
                    f"Suggestion: {s.recommendation}",
                    "",
                    "</details>",
                    "",
                ]
            )

    # Rule findings
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

    # Analysis notes
    if (
        report.analysis_warnings
        or report.hidden_suggestions_count > 0
        or report.context_truncated
    ):
        lines.extend(["", "## Analysis Notes", ""])
        if report.hidden_suggestions_count > 0:
            lines.append(
                f"- {report.hidden_suggestions_count} low-confidence or "
                f"duplicate suggestion(s) hidden from main results"
            )
        if report.context_truncated:
            lines.append(
                "- Patch context was truncated to fit token budget; "
                "some files were not included in AI analysis."
            )
        for warning in report.analysis_warnings:
            lines.append(f"- {warning}")

    return "\n".join(lines).rstrip() + "\n"


def _count_by_severity(suggestions: list[ReviewSuggestion]) -> dict[Severity, int]:
    counts: dict[Severity, int] = {}
    for s in suggestions:
        counts[s.severity] = counts.get(s.severity, 0) + 1
    return counts
