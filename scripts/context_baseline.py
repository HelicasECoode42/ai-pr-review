#!/usr/bin/env python
"""Record a reproducible offline baseline for the production patch context.

No model calls and no network: the snapshot is read from frozen Git objects and
remote file fetching is disabled, so the artifact depends only on the commits
plus the code revision that produced it.

Usage:
    .venv/bin/python scripts/context_baseline.py \
        --base 89943fe --head 13e9a3e --patch-token-budget 6000 \
        --output docs/evaluation/context-baseline-89943fe-13e9a3e.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.analyzer.context_builder import build_review_context  # noqa: E402
from src.analyzer.git_loader import GitPlanError, load_frozen_plan_inputs  # noqa: E402
from src.analyzer.plan_baseline import (  # noqa: E402
    compare_planner_and_context,
    summarize_context,
    summarize_plan,
)
from src.analyzer.review_plan import build_review_plan  # noqa: E402
from src.models import PullRequest  # noqa: E402


def _git(repo_path: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo_path), *args], text=True, stderr=subprocess.PIPE, timeout=30
    ).strip()


def _origin_repo(repo_path: Path) -> str:
    """Best-effort owner/repo from origin, so the artifact stays reproducible."""
    try:
        url = _git(repo_path, "config", "--get", "remote.origin.url")
    except (OSError, subprocess.SubprocessError):
        return "unknown/unknown"
    match = re.search(r"github\.com[:/]+([^/]+/[^/\s]+?)(?:\.git)?$", url)
    return match.group(1) if match else "unknown/unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-path", type=Path, default=REPO_ROOT)
    parser.add_argument("--base", required=True, help="Frozen comparison base commit")
    parser.add_argument("--head", required=True, help="Frozen comparison head commit")
    parser.add_argument("--patch-token-budget", type=int, default=6000)
    parser.add_argument("--plan-token-budget", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        snapshot, files, paths = load_frozen_plan_inputs(args.repo_path, args.base, args.head)
    except GitPlanError as exc:
        print(f"Could not read frozen range: {exc}", file=sys.stderr)
        return 1

    pr = PullRequest(
        repo=_origin_repo(Path(args.repo_path)),
        number=0,
        title=f"Frozen range {snapshot.base[:7]}..{snapshot.head[:7]}",
        head_sha=snapshot.head,
        base_sha=snapshot.base,
    )
    context = build_review_context(
        pr,
        files,
        include_context_pack=False,
        allow_remote_file_fetch=False,
        max_patch_tokens=args.patch_token_budget,
    )
    plan = build_review_plan(snapshot, files, paths, token_budget=args.plan_token_budget)

    context_summary = summarize_context(context)
    plan_summary = summarize_plan(plan)
    revision = _git(REPO_ROOT, "rev-parse", "HEAD")
    dirty = bool(_git(REPO_ROOT, "status", "--porcelain"))
    artifact = {
        "artifact": "chunk-context-baseline",
        "recorded_on": date.today().isoformat(),
        "planner_revision": revision,
        "planner_working_tree_dirty": dirty,
        "inputs": {
            "base": snapshot.base,
            "head": snapshot.head,
            "patch_token_budget": args.patch_token_budget,
            "plan_token_budget": args.plan_token_budget,
            "remote_fetch": False,
            "context_pack": False,
        },
        "production_context": context_summary,
        "planner_plan": plan_summary,
        "planner_vs_production": compare_planner_and_context(plan_summary, context_summary),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    hunks = context_summary["hunks"]
    files_summary = context_summary["files"]
    print(
        f"{snapshot.base[:7]}..{snapshot.head[:7]} patch_budget={args.patch_token_budget}: "
        f"files included={files_summary['included']} partial={files_summary['partial']} "
        f"excluded={files_summary['excluded']}; "
        f"hunks complete={hunks['complete']} partial={hunks['partial']} omitted={hunks['omitted']} "
        f"of {hunks['total']}"
    )
    gap = artifact["planner_vs_production"]
    print(
        f"planner planned {gap['planned_hunks']} hunks; context has "
        f"{gap['context_hunks_complete']} complete; hunk totals match={gap['hunk_totals_match']}"
    )
    if not context_summary["complete"]:
        print(f"WARNING: unaccounted files: {files_summary['unaccounted']}", file=sys.stderr)
    if context_summary["budget_over_limit"]:
        print(
            f"WARNING: budgets exceeded: {context_summary['budget_over_limit']}", file=sys.stderr
        )
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
