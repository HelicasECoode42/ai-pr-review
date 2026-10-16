#!/usr/bin/env python
"""Does the code we send the model even contain the defects? Measured for free.

Every paid evaluation so far confounded two questions: whether the right code
reached the prompt, and whether the model noticed it. This separates the first one
and costs nothing, because "is the gold finding's file and line inside a hunk we
kept" needs no model call. It runs over the whole ReviewBench corpus rather than
five PRs, which is what the earlier comparison lacked.

Two selection policies are simulated over the real diffs, with the production
budget and the production per-hunk cost:

  file_order      what the clipper does today: files in diff order
  evidence_order  A: files whose hunks the free rule/AST sweep flagged first

    .venv/bin/python scripts/selection_recall.py --benchmark /tmp/reviewbench-probe --limit 30
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from src.analyzer.context_builder import count_tokens  # noqa: E402
from src.analyzer.diff_parser import parse_file_hunks  # noqa: E402
from src.analyzer.risk_rules import scan_signals  # noqa: E402
from src.analyzer.tiered_scan import triage  # noqa: E402
from src.evaluation.reviewbench import parse_diff  # noqa: E402
from src.utils.config import get_settings  # noqa: E402

CACHE = REPO_ROOT / "reports/real-run/diff-cache"


def fetch_diff(repo: str, base: str, head: str, token: str) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{repo.replace('/', '_')}_{head[:12]}.diff"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    response = httpx.get(
        f"https://api.github.com/repos/{repo}/compare/{base}...{head}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.diff"},
        timeout=90,
    )
    response.raise_for_status()
    cached.write_text(response.text, encoding="utf-8")
    time.sleep(0.3)
    return response.text


def keep_hunks(files, hunks_by_file, order, budget: int) -> tuple[set[tuple[str, int]], int]:
    """Greedy fill in the given file order, exactly as the clipper does."""
    kept: set[tuple[str, int]] = set()
    spent = 0
    for file in order:
        for hunk in hunks_by_file.get(file.filename, []):
            cost = max(1, count_tokens(hunk.raw or ""))
            if spent + cost > budget:
                continue
            kept.add((file.filename, hunk.new_start))
            spent += cost
    return kept, spent


def covered(kept, hunks_by_file, gold) -> tuple[int, list[str]]:
    """Gold findings whose file and line range fall inside a kept hunk."""
    kept_ranges: list[tuple[str, int, int]] = []
    for path, hunks in hunks_by_file.items():
        for hunk in hunks:
            if (path, hunk.new_start) in kept:
                span = max(hunk.new_count, 1)
                kept_ranges.append((path, hunk.new_start, hunk.new_start + span))
    hits = 0
    missed: list[str] = []
    for finding in gold:
        path = str(finding.get("file", ""))
        start = finding.get("start_line", 0)
        end = finding.get("end_line", 0)
        found = False
        for kept_path, low, high in kept_ranges:
            if kept_path.endswith(path) and low <= end and high >= start:
                found = True
                break
        if found:
            hits += 1
        else:
            missed.append(path)
    return hits, missed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--budget", type=int, default=6000)
    default_out = REPO_ROOT / "reports/real-run/selection-recall.json"
    parser.add_argument("--out", type=Path, default=default_out)
    args = parser.parse_args(argv)

    token = get_settings().github_token
    manifest = {}
    for entry in json.loads((args.benchmark / "corpus/manifest.json").read_text()):
        slug = entry["repo"].rstrip("/").split("github.com/")[-1].replace("/", "_")
        manifest[f"{slug}_{entry['pr_number']}"] = entry
    golden = sorted((args.benchmark / "golden").glob("*.json"))
    golden = golden[: args.limit]

    rows = []
    for path in golden:
        data = json.loads(path.read_text(encoding="utf-8"))
        gold = [f for f in data.get("findings", []) if str(f.get("tp_fp", "")).lower() == "tp"]
        if not gold:
            continue
        pr = data["pr"]
        repo = pr["repo"].rstrip("/").split("github.com/")[-1]
        try:
            files = parse_diff(fetch_diff(repo, pr["base"], pr["head"], token))
        except Exception as exc:
            print(f"SKIP {path.stem}: {type(exc).__name__}")
            continue
        hunks_by_file = {file.filename: parse_file_hunks(file) for file in files}
        findings = list(scan_signals(files).findings)
        scan = triage(files, hunks_by_file, findings)
        flagged = {p for p, _h, _s, reasons in scan.suspicious
                   if any(r.startswith("rule_hits") for r in reasons)}
        weights = {
            file.filename: (
                1 if file.filename in flagged else 0,
                sum(h.new_count + h.old_count for h in hunks_by_file.get(file.filename, [])),
            )
            for file in files
        }
        by_evidence = sorted(files, key=lambda f: weights[f.filename], reverse=True)
        file_kept, file_spent = keep_hunks(files, hunks_by_file, files, args.budget)
        ev_kept, ev_spent = keep_hunks(files, hunks_by_file, by_evidence, args.budget)
        file_hits, file_miss = covered(file_kept, hunks_by_file, gold)
        ev_hits, ev_miss = covered(ev_kept, hunks_by_file, gold)
        total_hunks = sum(len(v) for v in hunks_by_file.values())
        rows.append({
            "key": path.stem, "files": len(files), "hunks": total_hunks,
            "gold": len(gold), "flagged_files": len(flagged),
            "file_order_hits": file_hits, "evidence_order_hits": ev_hits,
            "hunks_kept_file": len(file_kept), "hunks_kept_evidence": len(ev_kept),
            "tokens_file": file_spent, "tokens_evidence": ev_spent,
            "missed_by_evidence": ev_miss[:6],
        })
        print(
            f"{path.stem[:40]:42s} gold={len(gold):3d} "
            f"file_order={file_hits:3d} evidence_order={ev_hits:3d}"
        )

    if rows:
        gold_total = sum(r["gold"] for r in rows)
        f_total = sum(r["file_order_hits"] for r in rows)
        e_total = sum(r["evidence_order_hits"] for r in rows)
        summary = {
            "cases": len(rows), "gold_total": gold_total,
            "file_order_hits": f_total, "evidence_order_hits": e_total,
            "file_order_recall": round(f_total / gold_total, 4),
            "evidence_order_recall": round(e_total / gold_total, 4),
            "hunks_kept": sum(r["hunks_kept_evidence"] for r in rows),
            "hunks_total": sum(r["hunks"] for r in rows),
        }
        print("\n" + json.dumps(summary, indent=2))
    else:
        summary = {"cases": 0}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "per_case": rows},
                                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
