"""Offline graph planning CLI, separate from the stable PR review CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

app = typer.Typer(help="Build a frozen graph review plan without model calls.")
console = Console()


@app.command()
def plan_review(
    base: Annotated[str, typer.Option("--base", help="Explicit comparison base commit/ref")],
    repo_path: Annotated[Path, typer.Option("--repo-path", help="Local Git repository")] = Path(
        "."
    ),
    head: Annotated[str, typer.Option("--head", help="Comparison head commit/ref")] = "HEAD",
    token_budget: Annotated[int, typer.Option("--token-budget", min=128)] = 6000,
    output: Annotated[Path, typer.Option("--output")] = Path("reports/review-plan.json"),
) -> None:
    """Build an experimental frozen graph review plan, without model/API calls."""
    from src.analyzer.git_loader import GitPlanError, load_frozen_plan_inputs
    from src.analyzer.review_plan import build_review_plan

    try:
        snapshot, files, paths = load_frozen_plan_inputs(repo_path, base, head)
        plan = build_review_plan(snapshot, files, paths, token_budget=token_budget)
    except (GitPlanError, ValueError) as exc:
        console.print(f"[red]Could not build frozen review plan: {exc}[/red]")
        raise typer.Exit(1) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    console.print(
        f"Plan: {len(plan.units)} units; {len(plan.relationships)} relationships. {output}"
    )
    summary = plan.coverage_summary
    console.print(
        f"Coverage: {summary.planned_hunks} planned hunk(s), complete={summary.complete}."
    )
    console.print("Planning only: no model review or runtime reachability verification performed.")


if __name__ == "__main__":
    app()
