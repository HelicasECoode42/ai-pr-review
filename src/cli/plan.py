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
    import subprocess

    from src.analyzer.repository_context import GitSnapshot
    from src.analyzer.review_plan import build_review_plan
    from src.models import ChangedFile, FileStatus

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "--literal-pathspecs", "-C", str(repo_path.resolve()), *args],
            text=True,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    try:
        base_sha = git("rev-parse", "--verify", "--end-of-options", base + "^{commit}").strip()
        head_sha = git("rev-parse", "--verify", "--end-of-options", head + "^{commit}").strip()
        snapshot = GitSnapshot(repo_path, base_sha, head_sha)
        raw = git("diff", "--no-renames", "--name-status", "-z", base_sha, head_sha, "--")
        fields = raw.rstrip("\0").split("\0") if raw else []
        statuses = {"A": FileStatus.ADDED, "D": FileStatus.REMOVED, "M": FileStatus.MODIFIED}
        files = [
            ChangedFile(
                filename=fields[i + 1],
                status=statuses.get(fields[i], FileStatus.UNKNOWN),
                patch=git(
                    "diff",
                    "--no-renames",
                    "--no-ext-diff",
                    "--no-textconv",
                    base_sha,
                    head_sha,
                    "--",
                    fields[i + 1],
                ),
            )
            for i in range(0, len(fields), 2)
        ]
        paths = git("ls-tree", "-r", "--name-only", "-z", head_sha).rstrip("\0").split("\0")
        plan = build_review_plan(snapshot, files, paths, token_budget=token_budget)
    except (subprocess.SubprocessError, ValueError) as exc:
        console.print(f"[red]Could not build frozen review plan: {exc}[/red]")
        raise typer.Exit(1) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    console.print(
        f"Plan: {len(plan.units)} units; {len(plan.relationships)} relationships. {output}"
    )
    console.print("Planning only: no model review or runtime reachability verification performed.")


if __name__ == "__main__":
    app()
