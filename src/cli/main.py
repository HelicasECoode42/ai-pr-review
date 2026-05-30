from __future__ import annotations

from enum import Enum
from pathlib import Path

import typer
from rich.console import Console

from src.github.client import GitHubApiError, GitHubClient
from src.utils.config import get_settings

app = typer.Typer(help="AI assisted GitHub Pull Request review tool.")
console = Console()


class OutputLanguage(str, Enum):
    EN = "en"
    ZH = "zh"


@app.command()
def analyze(
    repo: str = typer.Argument(..., help="GitHub repository, for example owner/repo."),
    pr_number: int = typer.Argument(..., help="Pull Request number."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write report to file."),
    report_format: str = typer.Option("markdown", "--format", help="markdown or json."),
    use_ai: bool = typer.Option(True, "--ai/--no-ai", help="Call AI model for review."),
    reviewer_version: str = typer.Option(
        "pr-branch",
        "--reviewer-version",
        help="Reviewer runtime label, for example pr-branch or main-fallback.",
    ),
    execution_status: str = typer.Option(
        "success",
        "--execution-status",
        help="Execution status label: success or degraded.",
    ),
    degradation_reason: str | None = typer.Option(
        None,
        "--degradation-reason",
        help="Reason shown when the reviewer runs in degraded/fallback mode.",
    ),
    report_confidence: str = typer.Option(
        "normal",
        "--report-confidence",
        help="Report confidence label: normal, fallback, partial, or failed.",
    ),
    pr_syntax_ok: bool = typer.Option(
        True,
        "--pr-syntax-ok/--pr-syntax-fail",
        help="Whether the PR head branch passed syntax check.",
    ),
    reviewed_commit: str | None = typer.Option(
        None,
        "--reviewed-commit",
        help="Commit SHA that this review targets.",
    ),
    trigger_event: str | None = typer.Option(
        None,
        "--trigger-event",
        help="Event that triggered the review (push / issue_comment / workflow_dispatch).",
    ),
    workflow_run_url: str | None = typer.Option(
        None,
        "--workflow-run-url",
        help="URL to the GitHub Actions workflow run.",
    ),
    language: OutputLanguage = typer.Option(
        OutputLanguage.EN,
        "--language",
        help="Output language.",
    ),
) -> None:
    settings = get_settings()
    console.print(f"[bold]Fetching PR[/bold] {repo}#{pr_number}")

    pr = None
    files = []

    try:
        with GitHubClient(settings.github_token, timeout=settings.request_timeout_seconds) as github:
            pr = github.get_pull_request(repo, pr_number)
            files = github.get_changed_files(repo, pr_number)
    except GitHubApiError as exc:
        # Don't fail the whole process for CI: emit a minimal failure report
        console.print(f"[red]GitHub API error: {exc}[/red]")
        content = (
            f"# Analysis Failed\n\n"
            f"Failed to fetch PR {repo}#{pr_number} from GitHub API: {exc}\n\n"
            "No analysis was performed.\n"
        )
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
            console.print(f"[green]Failure report written to[/green] {output}")
        else:
            console.print(content)
        # Write short failure summary to GitHub Actions UI if available
        try:
            from src.utils.actions import write_step_summary
            summary = (
                f"**Reason:** GitHub API error\n\n```\n{exc}\n```\n"
                f"**PR:** {repo}#{pr_number}\n"
            )
            write_step_summary(summary)
        except Exception:
            # Do not raise during reporting
            pass
        # Return success exit so CI can still archive artifact produced
        raise typer.Exit(code=0)

    # Lazy imports and safe runtime handling: if analyzer/reviewer code is broken (e.g. PR modified tool),
    # catch errors and emit a diagnostics report instead of crashing the workflow.
    try:
        # Local imports to avoid import-time failures when modules are broken
        from src.analyzer.risk_rules import scan_risks
        from src.output.json_report import render_json
        from src.output.markdown import render_markdown
        from src.models import ReviewMeta
        from src.reviewer.engine import build_rule_only_report, review_with_ai
        from datetime import datetime, timezone
        from src.reviewer.provider import OpenAICompatibleProvider
    except Exception as exc:
        console.print(f"[red]Runtime import error in analyzer/reviewer modules: {exc}[/red]")
        # Build a minimal diagnostic report including PR metadata to help debugging
        parts = ["# Analysis Failed", "", "The review tool encountered an internal error during startup.", ""]
        parts.append(f"Error: {exc}")
        if pr:
            parts.append("")
            parts.append("## Pull Request")
            parts.append(f"Repo: {pr.repo}")
            parts.append(f"Number: {pr.number}")
            parts.append(f"Title: {pr.title}")
            parts.append(f"Author: {pr.author}")
            parts.append("")
            parts.append("## Changed files")
            for f in files:
                try:
                    parts.append(f"- {f.filename} +{f.additions}/-{f.deletions}")
                except Exception:
                    continue
        parts.append("")
        parts.append("Please check the repository changes that might have modified the review tool code.\n")
        content = "\n".join(parts)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
            console.print(f"[green]Diagnostic report written to[/green] {output}")
        else:
            console.print(content)
        # Write import failure summary to Actions step summary
        try:
            from src.utils.actions import write_step_summary
            summary = (
                f"**Reason:** Runtime import error in analyzer/reviewer modules\n\n```\n{exc}\n```\n"
                f"**PR:** {repo}#{pr_number if pr is None else pr.number}\n"
            )
            write_step_summary(summary)
        except Exception:
            pass
        raise typer.Exit(code=0)

    # Safe execution path
    try:
        findings = scan_risks(files)
        report = build_rule_only_report(
            pr,
            files,
            findings,
            language=language.value,
            reviewer_version=reviewer_version,
            execution_status=execution_status,
            degradation_reason=degradation_reason,
            report_confidence=report_confidence,
            pr_syntax_ok=pr_syntax_ok,
            review_meta=ReviewMeta(
                reviewed_commit=reviewed_commit,
                trigger_event=trigger_event,
                workflow_run_url=workflow_run_url,
                updated_at=datetime.now(timezone.utc).isoformat(),
            ),
        )

        if use_ai:
            if not settings.openai_api_key:
                console.print("[yellow]OPENAI_API_KEY is not set; falling back to rule-only report.[/yellow]")
                try:
                    from src.utils.actions import write_step_summary
                    write_step_summary(
                        "## AI Review - Skipped\n\n**Reason:** OPENAI_API_KEY not set; falling back to rule-only report.\n"
                    )
                except Exception:
                    pass
            else:
                provider = OpenAICompatibleProvider(
                    api_key=settings.openai_api_key,
                    model=settings.review_model,
                    base_url=settings.openai_base_url,
                    timeout=settings.request_timeout_seconds,
                )
                try:
                    report = review_with_ai(
                        pr=pr,
                        files=files,
                        findings=findings,
                        provider=provider,
                        max_suggestions=settings.max_suggestions,
                        min_confidence=settings.min_comment_confidence,
                        max_suggestions_per_file=settings.max_suggestions_per_file,
                        language=language.value,
                        reviewer_version=reviewer_version,
                        execution_status=execution_status,
                        degradation_reason=degradation_reason,
                        report_confidence=report_confidence,
                        pr_syntax_ok=pr_syntax_ok,
                        review_meta=ReviewMeta(
                            reviewed_commit=reviewed_commit,
                            trigger_event=trigger_event,
                            workflow_run_url=workflow_run_url,
                            updated_at=datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                finally:
                    provider.close()

        # After report generation, write an Actions step summary with any notable failures/warnings
        try:
            from src.utils.actions import write_step_summary
            summary_lines: list[str] = []
            if getattr(report, 'ai_failure_reason', None):
                summary_lines.append('## AI Review - Failure (fallback to rule-only)')
                summary_lines.append('**Reason:** ' + str(report.ai_failure_reason))
            if getattr(report, 'analysis_warnings', None):
                summary_lines.append('\n## Analysis Warnings')
                for w in (report.analysis_warnings or [])[:10]:
                    summary_lines.append('- ' + str(w))
            if getattr(report, 'context_truncated', False):
                summary_lines.append('\n**Context truncated:** yes')
            if getattr(report, 'skipped_context_files', None):
                skipped = getattr(report, 'skipped_context_files') or []
                if skipped:
                    names = ', '.join(s.file_path for s in skipped[:10])
                    summary_lines.append('\n**Skipped files:** ' + names)
            summary_lines.append('\n**AI used:** ' + ('yes' if getattr(report, 'used_ai', False) else 'no'))
            if summary_lines:
                write_step_summary('\n'.join(summary_lines) + '\n')
        except Exception:
            pass

        if report.ai_failure_reason:
            console.print(
                f"[yellow]AI review skipped: {report.ai_failure_reason}[/yellow]"
            )
        if report.analysis_warnings:
            for warning in report.analysis_warnings:
                console.print(f"[dim]{warning}[/dim]")

        content = (
            render_json(report)
            if report_format.lower() == "json"
            else render_markdown(report, language=language.value)
        )
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
            console.print(f"[green]Report written to[/green] {output}")
            # Stage 16: also write JSON sidecar for CI review-comment automation.
            json_path = output.with_suffix(".json")
            json_path.write_text(render_json(report), encoding="utf-8")
            console.print(f"[green]JSON sidecar written to[/green] {json_path}")
        else:
            console.print(content)
    except Exception as exc:
        # Catch unexpected runtime errors during analysis and produce a diagnostic report.
        console.print(f"[red]Analysis runtime error: {exc}[/red]")
        parts = ["# Analysis Failed", "", "An unexpected error occurred during analysis.", ""]
        parts.append(f"Error: {exc}")
        parts.append("")
        parts.append("This likely indicates the PR modified the review tool code. Please inspect changes.")
        content = "\n".join(parts)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
            console.print(f"[green]Diagnostic report written to[/green] {output}")
        else:
            console.print(content)
        # Write runtime analysis failure to Actions step summary
        try:
            from src.utils.actions import write_step_summary
            summary = (
                f"**Reason:** Unexpected runtime error during analysis\n\n```\n{exc}\n```\n"
                f"**PR:** {repo}#{pr_number}\n"
            )
            write_step_summary(summary)
        except Exception:
            pass
        # Do not fail CI.
        raise typer.Exit(code=0)


if __name__ == "__main__":
    app()
