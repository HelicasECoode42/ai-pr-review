from __future__ import annotations

from enum import Enum
from pathlib import Path

import typer
from rich.console import Console

from src.analyzer.risk_rules import scan_risks
from src.github.client import GitHubApiError, GitHubClient
from src.output.json_report import render_json
from src.output.markdown import render_markdown
from src.reviewer.engine import build_rule_only_report, review_with_ai
from src.reviewer.provider import OpenAICompatibleProvider
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
    language: OutputLanguage = typer.Option(
        OutputLanguage.EN,
        "--language",
        help="Output language.",
    ),
) -> None:
    settings = get_settings()
    console.print(f"[bold]Fetching PR[/bold] {repo}#{pr_number}")

    try:
        with GitHubClient(settings.github_token, timeout=settings.request_timeout_seconds) as github:
            pr = github.get_pull_request(repo, pr_number)
            files = github.get_changed_files(repo, pr_number)
    except GitHubApiError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    findings = scan_risks(files)
    report = build_rule_only_report(pr, files, findings, language=language.value)

    if use_ai:
        if not settings.openai_api_key:
            console.print("[yellow]OPENAI_API_KEY is not set; falling back to rule-only report.[/yellow]")
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
                )
            finally:
                provider.close()

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
        # Stage 16: also write JSON sidecar for CI review-comment automation
        json_path = output.with_suffix(".json")
        json_path.write_text(render_json(report), encoding="utf-8")
        console.print(f"[green]JSON sidecar written to[/green] {json_path}")
    else:
        console.print(content)


if __name__ == "__main__":
    app()
