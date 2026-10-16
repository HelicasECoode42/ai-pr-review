#!/usr/bin/env python
"""B7-b: evaluate the production path against ReviewBench's official real-PR gold.

Data sources are external and not vendored: clone the benchmark and point
--benchmark at it.

    git clone --depth 1 https://github.com/review-bench/ReviewBench.git /tmp/rb
    .venv/bin/python scripts/reviewbench_real_eval.py --benchmark /tmp/rb --limit 3

Matching is the benchmark's own "raw match": a published finding counts as a true
positive when it names a file the gold finding is in and its line falls inside the
gold line range. Anything else published is a false positive, and every tp gold
finding that no published finding matched is a miss. The numbers are therefore
grounded precision and grounded recall in ReviewBench's sense, computed from the
gold labels rather than from a judge, and the labels were never shown to the model.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from src.application.review import ReviewApplication  # noqa: E402
from src.evaluation.reviewbench import parse_diff  # noqa: E402
from src.evaluation.scoring import score  # noqa: E402
from src.models import PullRequest  # noqa: E402
from src.utils.config import get_settings  # noqa: E402


@dataclass
class Case:
    key: str
    repo: str
    number: int
    base: str
    head: str
    gold: list[dict]


def load_cases(benchmark: Path, dev_keys: set[str]) -> list[Case]:
    cases: list[Case] = []
    for path in sorted((benchmark / "golden").glob("*.json")):
        if dev_keys and path.stem not in dev_keys:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        pr = data["pr"]
        slug = pr["repo"].rstrip("/").split("github.com/")[-1]
        cases.append(Case(
            key=data.get("pr_key", path.stem), repo=slug, number=pr["pr_number"],
            base=pr["base"], head=pr["head"],
            gold=[f for f in data.get("findings", []) if str(f.get("tp_fp", "")).lower() == "tp"],
        ))
    return cases


def fetch_diff(case: Case, token: str) -> list:
    response = httpx.get(
        f"https://api.github.com/repos/{case.repo}/compare/{case.base}...{case.head}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.diff"},
        timeout=60,
    )
    response.raise_for_status()
    return parse_diff(response.text)


def match(published: list, gold: list[dict]) -> tuple[list[tuple[int, int]], list[str]]:
    """Raw match: same file and the published line inside the gold line range."""
    edges: list[tuple[int, int]] = []
    used: set[int] = set()
    for prediction, suggestion in enumerate(published):
        for index, finding in enumerate(gold):
            if index in used:
                continue
            same_file = str(suggestion.file_path).endswith(str(finding.get("file", "")))
            line = suggestion.line or 0
            within = finding.get("start_line", 0) <= line <= finding.get("end_line", 0)
            if same_file and within:
                edges.append((prediction, index))
                used.add(index)
                break
    missed = [str(gold[i].get("file")) for i in range(len(gold)) if i not in used]
    return edges, missed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--dev-split", type=Path, default=REPO_ROOT / "datasets/dev/split.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    dev_keys: set[str] = set()
    if args.dev_split.exists():
        split = json.loads(args.dev_split.read_text())
        dev_keys = set(split.get("dev", []))
    cases = load_cases(args.benchmark, dev_keys)
    # Smallest first: the point is a real-PR signal at a small cost.
    cases.sort(key=lambda case: (len(case.gold), case.key))
    results = []
    print(f"dev 集内且有 gold 的 case: {len(cases)}；本次取 {args.limit}\n")
    for case in cases[: args.limit]:
        try:
            files = fetch_diff(case, settings.github_token)
        except Exception as exc:
            print(f"SKIP {case.key}: diff fetch failed ({type(exc).__name__})")
            continue
        pr = PullRequest(repo=case.repo, number=case.number, title=case.key,
                         base_sha=case.base, head_sha=case.head)
        report = ReviewApplication(settings).review(pr, files, [])
        published = list(report.suggestions)
        edges, missed = match(published, case.gold)
        # Identification is separated from publication: a gold finding matched by a
        # *pending* candidate was found but withheld, which is a criteria question,
        # whereas one matched by nothing at all is a discovery question.
        held = list(report.pending_suggestions)
        still_open = [g for i, g in enumerate(case.gold) if i not in {e[1] for e in edges}]
        held_edges, _ = match(held, still_open)
        gold_ids = [str(index) for index in range(len(case.gold))]
        # score() keys gold by id, so the edges must carry the same string ids.
        case_score = score(len(published), gold_ids, [(prediction, str(index))
                                                     for prediction, index in edges])
        results.append({
            "key": case.key, "files": len(files), "gold_tp": len(case.gold),
            "published": len(published), "pending": len(report.pending_suggestions),
            "matched": len(edges), "matched_but_withheld": len(held_edges),
            "identified": len(edges) + len(held_edges), "missed_files": missed,
            "precision": case_score["precision"], "recall": case_score["recall"],
            "usage": report.metrics.get("usage_total_tokens"),
            "cache_hit_rate": report.metrics.get("cache_hit_rate"),
            "titles": [s.title for s in published[:6]],
        })
        usage = report.metrics.get("usage_total_tokens")
        print(f"{case.key:52s} files={len(files):3d} gold={len(case.gold):3d} "
              f"published={len(published):3d} matched={len(edges):3d} "
              f"P={case_score['precision']} R={case_score['recall']} tokens={usage}")

    if results:
        total_identified = sum(r["identified"] for r in results)
        total_tp = sum(r["matched"] for r in results)
        total_pub = sum(r["published"] for r in results)
        total_gold = sum(r["gold_tp"] for r in results)
        macro_p = sum(r["precision"] or 0 for r in results) / len(results)
        macro_r = sum(r["recall"] or 0 for r in results) / len(results)
        summary = {
            "cases": len(results), "published": total_pub, "gold_tp": total_gold,
            "matched": total_tp,
            "micro_precision": total_tp / total_pub if total_pub else None,
            "micro_recall": total_tp / total_gold if total_gold else None,
            "micro_identified_recall": total_identified / total_gold if total_gold else None,
            "matched_but_withheld": sum(r["matched_but_withheld"] for r in results),
            "macro_precision": round(macro_p, 4), "macro_recall": round(macro_r, 4),
            "tokens": sum(r["usage"] or 0 for r in results),
        }
        print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        summary = {"cases": 0}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "per_case": results},
                                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
