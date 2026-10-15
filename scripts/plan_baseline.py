#!/usr/bin/env python
"""Record a reproducible offline chunk-planning baseline. No model calls.

Usage:
    .venv/bin/python scripts/plan_baseline.py \
        --base 89943fe --head 13e9a3e --token-budget 1000 \
        --output docs/evaluation/plan-baseline-89943fe-13e9a3e.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.analyzer.git_loader import GitPlanError, load_frozen_plan_inputs  # noqa: E402
from src.analyzer.plan_baseline import summarize_plan  # noqa: E402
from src.analyzer.review_plan import build_review_plan  # noqa: E402


def _git_revision() -> tuple[str, bool]:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), *args], text=True, stderr=subprocess.PIPE, timeout=30
        ).strip()

    revision = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain"))
    return revision, dirty


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-path", type=Path, default=REPO_ROOT)
    parser.add_argument("--base", required=True, help="Frozen comparison base commit")
    parser.add_argument("--head", required=True, help="Frozen comparison head commit")
    parser.add_argument("--token-budget", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        snapshot, files, paths = load_frozen_plan_inputs(args.repo_path, args.base, args.head)
        plan = build_review_plan(snapshot, files, paths, token_budget=args.token_budget)
    except (GitPlanError, ValueError) as exc:
        print(f"Could not build baseline plan: {exc}", file=sys.stderr)
        return 1

    revision, dirty = _git_revision()
    artifact = {
        "artifact": "chunk-planning-baseline",
        "recorded_on": date.today().isoformat(),
        "planner_revision": revision,
        "planner_working_tree_dirty": dirty,
        "summary": summarize_plan(plan),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary = artifact["summary"]
    coverage = summary["coverage"]
    print(
        f"{summary['base'][:7]}..{summary['head'][:7]} budget={summary['token_budget']}: "
        f"{summary['units']} units, {summary['slices']} slices, "
        f"{coverage['planned_hunks']} planned hunks, complete={coverage['complete']}"
    )
    if not coverage["complete"]:
        print(
            f"WARNING: unaccounted hunks={coverage['unaccounted_hunks']} "
            f"files={coverage['unaccounted_files']}",
            file=sys.stderr,
        )
    if summary["budget_over_limit"]:
        print(f"WARNING: budgets exceeded: {summary['budget_over_limit']}", file=sys.stderr)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
