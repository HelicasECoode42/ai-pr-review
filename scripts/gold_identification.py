#!/usr/bin/env python
"""Known-answer error identification: run the reviewer on the synthetic gold set.

Two cases contain a real defect (division, negative-index) and two are correct
refactors that must NOT be flagged (safe-division, safe-index). Adjudication is
printed verbatim so the matching can be checked by hand, and the repo's own
bipartite scorer turns it into precision / recall / F1.

    .venv/bin/python scripts/gold_identification.py --output reports/real-run/gold-run.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.analyzer.context_builder import build_review_context  # noqa: E402
from src.evaluation.reviewbench import parse_diff  # noqa: E402
from src.evaluation.scoring import score  # noqa: E402
from src.models import PullRequest  # noqa: E402
from src.reviewer.grounded_review import review_grounded  # noqa: E402
from src.reviewer.provider import OpenAICompatibleProvider  # noqa: E402
from src.reviewer.usage import summarize_usage  # noqa: E402
from src.utils.config import get_settings  # noqa: E402

# One keyword per gold finding: the adjudication rule is stated, not hidden.
GOLD_KEYWORDS = {
    "division-bug": ("ZeroDivision", "division by zero", "zero", "count"),
    "negative-index-bug": ("negative", "index < 0", "index < 0", "bounds", "invalid index"),
}


def sides_from_diff(diff: str) -> dict[str, dict[str, str]]:
    """Rebuild base/head file text from a unified diff of small files."""
    sides: dict[str, dict[str, list[str]]] = {}
    path = None
    line_no = {"base": 0, "head": 0}
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            sides.setdefault(path, {"base": [], "head": []})
            line_no = {"base": 0, "head": 0}
            continue
        if line.startswith("@@") or line.startswith("--- ") or line.startswith("diff --git"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                line_no = {"base": int(match.group(1)) - 1, "head": int(match.group(2)) - 1}
            continue
        if path is None:
            continue
        marker, text = (line[:1], line[1:])
        if marker == " ":
            line_no["base"] += 1
            line_no["head"] += 1
            while len(sides[path]["base"]) < line_no["base"] - 1:
                sides[path]["base"].append("")
            while len(sides[path]["head"]) < line_no["head"] - 1:
                sides[path]["head"].append("")
            sides[path]["base"].append(text)
            sides[path]["head"].append(text)
        elif marker == "-":
            line_no["base"] += 1
            while len(sides[path]["base"]) < line_no["base"] - 1:
                sides[path]["base"].append("")
            sides[path]["base"].append(text)
        elif marker == "+":
            line_no["head"] += 1
            while len(sides[path]["head"]) < line_no["head"] - 1:
                sides[path]["head"].append("")
            sides[path]["head"].append(text)
    return {
        p: {side: "\n".join(lines) + "\n" for side, lines in both.items()}
        for p, both in sides.items()
    }


class GoldSnapshot:
    def __init__(self, sides, base="a" * 40, head="b" * 40):
        self.sides = sides
        self.base = base
        self.head = head

    def read(self, path, side="head"):
        return self.sides.get(path, {}).get(side)

    def search(self, symbol, side="head", limit=6):
        return []

    def audit(self):
        # The real snapshots report bounded search statistics here; a synthetic
        # two-line file has none to report.
        return {"kind": "synthetic_gold_snapshot", "search_limited": False, "errors": []}


def adjudicate(
    case_id: str, golden_ids: list[str], predictions: list[dict]
) -> list[tuple[int, str]]:
    """Edges from prediction index to gold id; a case with no gold yields none."""
    if not golden_ids:
        return []
    edges: list[tuple[int, str]] = []
    for index, prediction in enumerate(predictions):
        text = f"{prediction.get('title','')} {prediction.get('reason','')}".lower()
        for gold_id in golden_ids:
            keywords = GOLD_KEYWORDS.get(gold_id, ())
            if any(keyword.lower() in text for keyword in keywords):
                edges.append((index, gold_id))
    return edges


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path,
                        default=REPO_ROOT / "datasets/golden/synthetic-dev.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    settings = get_settings()
    model = args.model or settings.review_model
    cases = json.loads(args.gold.read_text())

    report: dict = {"model": model, "cases": [], "usage": {}}
    total_records: list[dict] = []
    scored: list[dict] = []
    for case in cases:
        sides = sides_from_diff(case["diff"])
        files = parse_diff(case["diff"])
        base = case["metadata"]["base"]
        head = case["metadata"]["head"]
        snapshot = GoldSnapshot(sides, base, head)
        pr = PullRequest(repo=case["metadata"]["nwo"], number=case["metadata"]["pr_number"],
                         title=case["metadata"].get("title", case["case_id"]),
                         base_sha=base, head_sha=head)
        context = build_review_context(pr, files, include_context_pack=False,
                                       allow_remote_file_fetch=False)
        provider = OpenAICompatibleProvider(
            api_key=settings.openai_api_key, model=model, base_url=settings.openai_base_url,
            timeout=settings.request_timeout_seconds, stream=True, total_timeout=300,
            max_output_tokens=32768, reasoning_effort="low",
        )
        result = review_grounded(pr, files, provider, snapshot, context,
                                 max_suggestions=settings.max_suggestions,
                                 evidence_tokens=settings.repository_evidence_tokens,
                                 verify_behavior=True)
        total_records.extend(provider.usage_records)
        provider.close()

        predictions = [
            {"file_path": s.file_path, "line": s.line, "severity": str(s.severity),
             "confidence": s.confidence, "title": s.title, "reason": s.reason,
             "source": s.source, "symbol": s.symbol}
            for s in result.suggestions
        ]
        held = [{"file_path": s.file_path, "line": s.line, "source": s.source,
                 "title": s.title} for s in result.pending]
        golden_ids = [f["id"] for f in case.get("findings", [])]
        edges = adjudicate(case["case_id"], golden_ids, predictions)
        case_score = score(len(predictions), golden_ids, edges)
        scored.append(case_score)
        report["cases"].append({
            "case_id": case["case_id"],
            "golden_ids": golden_ids,
            "negative_case": not golden_ids,
            "predictions": predictions,
            "held_as_pending": held,
            "candidates": len(result.candidates),
            "edges": edges,
            "score": case_score,
        })
        print(f"\n=== {case['case_id']} (gold={golden_ids}) ===")
        print(f"  predictions={len(predictions)} pending={len(held)} edges={edges}")
        for index, p in enumerate(predictions):
            print(f"   [{index}] {p['file_path']}:{p['line']} conf={p['confidence']} "
                  f"src={p['source']} | {p['title'][:90]}")

    total_tp = sum(s["tp"] for s in scored)
    total_pred = sum(s["tp"] + s["fp"] for s in scored)
    total_gold = sum(s["tp"] + s["fn"] for s in scored)
    report["aggregate"] = {
        "tp": total_tp,
        "fp": total_pred - total_tp,
        "fn": total_gold - total_tp,
        "golden_total": total_gold,
        "published_total": total_pred,
        "micro_precision": total_tp / total_pred if total_pred else None,
        "micro_recall": total_tp / total_gold if total_gold else None,
    }
    report["usage"] = summarize_usage(total_records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("\n" + json.dumps(report["aggregate"], indent=2, ensure_ascii=False))
    print("usage:", report["usage"])
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
