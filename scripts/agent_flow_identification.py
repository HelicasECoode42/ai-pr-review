#!/usr/bin/env python
"""Does the agent flow identify known errors that truncation loses?

Builds a synthetic large PR whose ground truth is known by construction: a few
files carry the seeded defects from datasets/golden/synthetic-dev.json, the rest
are behaviour-preserving local-variable renames. Then it runs two arms over the
*same* corpus:

  arm A  one prompt built by the production file-order truncation at a budget
  arm B  one review per ReviewUnit, every unit

and scores both against the seeded truth with the repository's own scorer, and
separately reports what was identified but withheld as pending. Unlike a cost
comparison, this measures what the reviewer actually found.

    .venv/bin/python scripts/agent_flow_identification.py \
        --files 40 --budget 6000 --output reports/real-run/agent-flow.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.analyzer.context_builder import build_review_context  # noqa: E402
from src.analyzer.review_plan import build_review_plan  # noqa: E402
from src.evaluation.reviewbench import parse_diff  # noqa: E402
from src.evaluation.scoring import score  # noqa: E402
from src.models import PullRequest  # noqa: E402
from src.reviewer.grounded_review import review_grounded  # noqa: E402
from src.reviewer.provider import OpenAICompatibleProvider  # noqa: E402
from src.reviewer.unit_review import merge_grounded_results, review_by_units  # noqa: E402
from src.reviewer.usage import summarize_usage  # noqa: E402
from src.utils.config import get_settings  # noqa: E402

# Which seeded case covers which gold id, and the words that prove the reviewer
# found it. Stated here so adjudication is inspectable rather than hidden.
SEEDED_GOLD = {
    "division": ("division-bug", ("zerodivision", "guard", "count")),
    "negative-index": ("negative-index-bug", ("negative", "index")),
}
SAFE_CASES = ("safe-division", "safe-index")


def seeded_sides(diff: str) -> tuple[str, str]:
    """Base and head text of a two-line synthetic case, rebuilt from its diff."""
    context = [line[1:] for line in diff.splitlines() if line.startswith(" ")]
    removed = [line[1:] for line in diff.splitlines()
               if line.startswith("-") and not line.startswith("---")]
    added = [line[1:] for line in diff.splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    body = context or [""]
    return "\n".join(body + removed) + "\n", "\n".join(body + added) + "\n"


def noise_pair(index: int) -> tuple[str, str]:
    """A behaviour-preserving rename of one local variable, with chunkier bodies
    so the corpus is genuinely larger than the arm-A budget."""
    old = [f"def helper_{index}(value):", "    total = 0"]
    old += [f"    total += value + {i}" for i in range(16)]
    old += ["    return total"]
    new = [old[0], "    accumulated = 0"]
    new += [f"    accumulated += value + {i}" for i in range(16)]
    new += ["    return accumulated"]
    return "\n".join(old) + "\n", "\n".join(new) + "\n"


def noise_diff(path: str, old: str, new: str) -> str:
    old_lines, new_lines = old.splitlines(), new.splitlines()
    header = f"@@ -1,{len(old_lines)} +1,{len(new_lines)} @@"
    lines = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", header]
    for index, (before, after) in enumerate(zip(old_lines, new_lines, strict=True)):
        if index == 0:
            lines.append(f" {before}")
        else:
            lines.append(f"-{before}")
            lines.append(f"+{after}")
    return "\n".join(lines)


def build_corpus(count: int, seeded_at: dict[int, str], cases: dict[str, dict]) -> tuple[str, dict]:
    diff_parts: list[str] = []
    sources: dict[str, dict[str, str]] = {}
    for index in range(count):
        path = f"mod_{index:02d}.py"
        if index in seeded_at:
            case_id = seeded_at[index]
            body = cases[case_id]["diff"].replace("sample.py", path)
            base_body, head_body = cases[case_id]["_base"], cases[case_id]["_head"]
        else:
            base_body, head_body = noise_pair(index)
            body = noise_diff(path, base_body, head_body)
        diff_parts.append(body.rstrip("\n"))
        sources[path] = {"base": base_body, "head": head_body}
    return "\n".join(diff_parts) + "\n", sources


class CorpusSnapshot:
    def __init__(self, sources, base, head):
        self.sources = sources
        self.base = base
        self.head = head

    def read(self, path, side="head"):
        return self.sources.get(path, {}).get(side)

    def search(self, symbol, side="head", limit=6):
        return []

    def audit(self):
        return {"kind": "synthetic_corpus", "search_limited": False, "errors": []}


def matches(text: str, gold_id: str) -> bool:
    keywords = next(k for _c, (g, k) in SEEDED_GOLD.items() if g == gold_id)
    return any(keyword in text for keyword in keywords)


def gold_edges(predictions: list[dict], gold_ids: list[str]) -> list[tuple[int, str]]:
    edges: list[tuple[int, str]] = []
    for index, prediction in enumerate(predictions):
        text = f"{prediction.get('title','')} {prediction.get('reason','')}".lower()
        edges.extend((index, gold_id) for gold_id in gold_ids if matches(text, gold_id))
    return edges


def identified(items: list[dict], gold_ids: list[str]) -> set[str]:
    found = set()
    for item in items:
        text = f"{item.get('title','')} {item.get('reason','')}".lower()
        found.update(gold_id for gold_id in gold_ids if matches(text, gold_id))
    return found


def make_provider(settings, model):
    return OpenAICompatibleProvider(
        api_key=settings.openai_api_key, model=model, base_url=settings.openai_base_url,
        timeout=settings.request_timeout_seconds, stream=True, total_timeout=300,
        max_output_tokens=32768, reasoning_effort="low",
    )


def run_arm(name, result, usage, gold_ids, extra) -> dict:
    predictions = [{"title": s.title, "reason": s.reason, "file_path": s.file_path,
                    "line": s.line, "confidence": s.confidence, "source": s.source}
                   for s in result.suggestions]
    held = [{"title": s.title, "reason": s.reason, "file_path": s.file_path, "line": s.line}
            for s in result.pending]
    published_found = identified(predictions, gold_ids)
    held_found = identified(held, gold_ids)
    arm = {
        **extra,
        "identified_published": sorted(published_found),
        "identified_but_withheld": sorted(held_found - published_found),
        "gold_found_anywhere": sorted(published_found | held_found),
        "recall_on_seeded_bugs": len(
            [g for g in gold_ids if g in (published_found | held_found)]
        ) / len(gold_ids),
        "published": len(predictions),
        "withheld": len(held),
        "usage": usage,
        "score": score(len(predictions), gold_ids, gold_edges(predictions, gold_ids)),
        "predictions": predictions,
        "withheld_predictions": held,
    }
    print(f"\n=== {name} ===")
    print(f"  published-found: {sorted(published_found)}  withheld-found: {sorted(held_found)}")
    print(f"  published={len(predictions)} withheld={len(held)} "
          f"tokens={usage.get('usage_total_tokens')} calls={usage.get('usage_responses_known')}")
    for prediction in predictions[:10]:
        print(f"   - {prediction['file_path']}:{prediction['line']} | {prediction['title'][:78]}")
    if held:
        print("   (withheld)")
        for item in held[:6]:
            print(f"   - {item['file_path']}:{item['line']} | {item['title'][:78]}")
    return arm


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=40)
    parser.add_argument("--budget", type=int, default=6000)
    parser.add_argument("--units", type=int, default=0,
                        help="0 means every unit the planner produces")
    parser.add_argument("--model", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    model = args.model or settings.review_model
    cases = {c["case_id"]: c for c in
             json.loads((REPO_ROOT / "datasets/golden/synthetic-dev.json").read_text())}
    for case in cases.values():
        case["_base"], case["_head"] = seeded_sides(case["diff"])

    # Deterministic placement: seeded files are spread through the diff rather
    # than parked at the end, so neither arm wins by where the bugs sit.
    order = ["division", "safe-division", "negative-index", "safe-index"]
    step = max(2, args.files // (len(order) + 1))
    seeded_at = {min(args.files - 1, step * (i + 1) - 1): case_id
                 for i, case_id in enumerate(order)}

    diff, sources = build_corpus(args.files, seeded_at, cases)
    files = parse_diff(diff)
    base, head = "a" * 40, "b" * 40
    snapshot = CorpusSnapshot(sources, base, head)
    pr = PullRequest(repo="synthetic/agent-flow", number=1, title="Synthetic large PR",
                     base_sha=base, head_sha=head)
    gold_ids = [v[0] for v in SEEDED_GOLD.values()]
    report: dict = {"files": len(files), "budget": args.budget, "model": model,
                    "seeded_at": {str(k): v for k, v in sorted(seeded_at.items())},
                    "gold_ids": gold_ids}
    print(f"corpus: {len(files)} files, seeded at {sorted(seeded_at)}, gold={gold_ids}")

    context_a = build_review_context(pr, files, include_context_pack=False,
                                     allow_remote_file_fetch=False,
                                     max_patch_tokens=args.budget)
    provider_a = make_provider(settings, model)
    result_a = review_grounded(pr, files, provider_a, snapshot, context_a,
                               max_suggestions=settings.max_suggestions,
                               evidence_tokens=settings.repository_evidence_tokens,
                               verify_behavior=True)
    usage_a = summarize_usage(provider_a.usage_records)
    provider_a.close()
    report["corpus_total_diff_tokens"] = context_a.token_count
    report["arm_a_truncation"] = run_arm(
        "arm_a_truncation", result_a, usage_a, gold_ids,
        {"context_tokens": context_a.token_count,
         "hunks_complete": context_a.coverage_summary.hunks_complete,
         "hunks_total": context_a.coverage_summary.hunks_total},
    )

    plan = build_review_plan(snapshot, files, [f.filename for f in files],
                             token_budget=args.budget)
    providers: list = []

    def run_unit(unit_context, unit_files):
        provider = make_provider(settings, model)
        providers.append(provider)
        return review_grounded(pr, unit_files, provider, snapshot, unit_context,
                               max_suggestions=settings.max_suggestions,
                               evidence_tokens=settings.repository_evidence_tokens,
                               verify_behavior=True)

    units = args.units or len(plan.units)
    summary_b = review_by_units(plan, files, run_unit, max_units=units)
    merged_b = merge_grounded_results([o.result for o in summary_b.reviewed])
    usage_b = summarize_usage([r for p in providers for r in p.usage_records])
    for provider in providers:
        provider.close()
    report["arm_b_per_unit"] = run_arm(
        "arm_b_per_unit", merged_b, usage_b, gold_ids,
        {"units": summary_b.counts(),
         "unit_outcomes": [{"unit_id": o.unit_id[:12], "status": o.status,
                            "tokens": o.estimated_tokens, "error": o.error}
                           for o in summary_b.outcomes]},
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    arms = ("arm_a_truncation", "arm_b_per_unit")
    summary = {}
    for arm in arms:
        entry = report[arm]
        summary[arm] = {
            "gold_found_anywhere": entry["gold_found_anywhere"],
            "published": entry["published"],
            "withheld": entry["withheld"],
            "tokens": entry["usage"].get("usage_total_tokens"),
            "score": {key: entry["score"][key] for key in ("tp", "fp", "precision", "recall")},
        }
    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
