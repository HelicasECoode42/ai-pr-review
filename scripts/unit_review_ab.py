#!/usr/bin/env python
"""A/B one review budget: file-order truncation vs per-unit iteration.

Both arms are given the *same* diff-token budget and the same model, so the only
variable is how the budget is spent. Arm A builds one prompt by truncating files
in the production order. Arm B reviews whole ReviewUnits one at a time.

The Critic is not involved in either arm: this script calls review_grounded
directly, and the Critic stage belongs to the engine wrapper.

This is a mechanism comparison on one frozen range, not a quality measurement:
there is no ground truth here, so it reports coverage, findings and cost.

    .venv/bin/python scripts/unit_review_ab.py \
        --base d99524db81 --head HEAD --units 8 --critic off \
        --output reports/real-run/ab-units.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.analyzer.context_builder import build_review_context  # noqa: E402
from src.analyzer.git_loader import load_frozen_plan_inputs  # noqa: E402
from src.analyzer.review_plan import build_review_plan  # noqa: E402
from src.analyzer.risk_rules import scan_signals  # noqa: E402
from src.models import PullRequest  # noqa: E402
from src.reviewer.grounded_review import review_grounded  # noqa: E402
from src.reviewer.provider import OpenAICompatibleProvider  # noqa: E402
from src.reviewer.unit_review import merge_grounded_results, review_by_units  # noqa: E402
from src.reviewer.usage import summarize_usage  # noqa: E402
from src.utils.config import get_settings  # noqa: E402


def make_provider(settings, model: str):
    return OpenAICompatibleProvider(
        api_key=settings.openai_api_key, model=model, base_url=settings.openai_base_url,
        timeout=settings.request_timeout_seconds, stream=True, total_timeout=300,
        max_output_tokens=32768, reasoning_effort="low",
    )


def arm_summary(name, ctx, result, usage, seconds, plan=None, units=None):
    coverage = ctx.coverage_summary
    out = {
        "arm": name,
        "seconds": round(seconds, 1),
        "context_tokens": ctx.token_count,
        "hunks_total": coverage.hunks_total,
        "hunks_complete": coverage.hunks_complete,
        "hunks_omitted": coverage.hunks_omitted,
        "files_total": coverage.changed_files,
        "files_excluded": coverage.files_excluded,
        "suggestions": len(result.suggestions),
        "pending": len(result.pending),
        "candidates": len(result.candidates),
        "confirmed": sum(d.get("decision") == "confirmed" for d in result.decisions),
        "usage": usage,
    }
    if units:
        out["units"] = units
    if plan is not None:
        out["units_planned_full_diff"] = len(plan.units)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--units", type=int, default=8,
                        help="How many ReviewUnits arm B reviews")
    parser.add_argument("--model", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    model = args.model or settings.review_model
    snapshot, files, paths = load_frozen_plan_inputs(Path(REPO_ROOT), args.base, args.head)
    scan = scan_signals(files)
    pr = PullRequest(repo="HelicasECoode42/ai-pr-review", number=0,
                     title=f"A/B {args.base[:7]}..{args.head[:7]}",
                     head_sha=snapshot.head, base_sha=snapshot.base)

    plan = build_review_plan(snapshot, files, paths, token_budget=6000)
    units = list(plan.units)[: args.units]
    budget = sum(u.estimated_tokens for u in units)
    print(f"matched diff budget = {budget} tokens over {len(units)} units "
          f"(plan has {len(plan.units)} units, {len(files)} files)")

    report: dict = {
        "base": snapshot.base, "head": snapshot.head, "model": model,
        "critic": "not used: this script calls review_grounded directly",
        "units_requested": args.units,
        "matched_diff_token_budget": budget,
        "plan_units_total": len(plan.units),
        "changed_files": len(files),
        "rule_findings": len(scan.findings),
    }

    # ── Arm A: one prompt, production truncation, same budget ──
    ctx_a = build_review_context(pr, files, include_context_pack=False,
                                 allow_remote_file_fetch=False, max_patch_tokens=budget)
    provider = make_provider(settings, model)
    t0 = time.perf_counter()
    result_a = review_grounded(pr, files, provider, snapshot, ctx_a,
                               max_suggestions=settings.max_suggestions,
                               evidence_tokens=settings.repository_evidence_tokens,
                               verify_behavior=True)
    seconds_a = time.perf_counter() - t0
    usage_a = summarize_usage(provider.usage_records)
    provider.close()
    report["arm_a_file_order_truncation"] = arm_summary(
        "file_order_truncation", ctx_a, result_a, usage_a, seconds_a, plan=plan
    )

    # ── Arm B: whole units, one review per unit, same total budget ──
    providers: list = []

    def run_unit(unit_ctx, unit_files):
        p = make_provider(settings, model)
        providers.append(p)
        return review_grounded(pr, unit_files, p, snapshot, unit_ctx,
                               max_suggestions=settings.max_suggestions,
                               evidence_tokens=settings.repository_evidence_tokens,
                               verify_behavior=True)

    t0 = time.perf_counter()
    unit_summary = review_by_units(plan, files, run_unit, max_units=args.units)
    seconds_b = time.perf_counter() - t0
    merged = merge_grounded_results([o.result for o in unit_summary.reviewed])
    usage_b = summarize_usage([r for p in providers for r in p.usage_records])
    for p in providers:
        p.close()
    # Coverage for arm B is exact by construction: every slice of every reviewed
    # unit was inside that unit's prompt. Count hunks, not slices, so the two
    # arms are comparable.
    reviewed_ids = {o.unit_id for o in unit_summary.outcomes if o.status == "reviewed"}
    reviewed_units = [u for u in units if u.unit_id in reviewed_ids]
    reviewed_hunks = {s.hunk_id for u in reviewed_units for s in u.slices}
    reviewed_paths = sorted({s.path for u in reviewed_units for s in u.slices})
    report["arm_b_per_unit"] = {
        "arm": "per_unit",
        "seconds": round(seconds_b, 1),
        "units": unit_summary.counts(),
        "hunks_in_reviewed_units": len(reviewed_hunks),
        "diff_tokens_in_reviewed_units": sum(u.estimated_tokens for u in reviewed_units),
        "paths_in_reviewed_units": reviewed_paths[:20],
        "suggestions": len(merged.suggestions),
        "pending": len(merged.pending),
        "candidates": len(merged.candidates),
        "confirmed": sum(d.get("decision") == "confirmed" for d in merged.decisions),
        "discovery_calls": merged.discovery_calls,
        "verification_calls": merged.verification_calls,
        "usage": usage_b,
        "unit_outcomes": [
            {"unit_id": o.unit_id, "status": o.status, "tokens": o.estimated_tokens,
             "error": o.error}
            for o in unit_summary.outcomes
        ],
    }
    report["comparison"] = {
        "hunks_seen_arm_a": report["arm_a_file_order_truncation"]["hunks_complete"],
        "hunks_seen_arm_b": len(reviewed_hunks),
        "total_tokens_arm_a": usage_a.get("usage_total_tokens", 0),
        "total_tokens_arm_b": usage_b.get("usage_total_tokens", 0),
        "note": ("Same diff-token budget in both arms. Arm B cannot lose a hunk inside a "
                 "reviewed unit; arm A may truncate anywhere. Mechanism comparison on one "
                 "frozen range with no ground truth: coverage and cost only, no quality claim."),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["comparison"], indent=2, ensure_ascii=False))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
