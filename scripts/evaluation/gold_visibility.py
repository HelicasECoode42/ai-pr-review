#!/usr/bin/env python3
"""Measure the S layer of the review funnel offline (no model calls, no cost).

For every gold finding, report whether its head line range was visible to the
reviewer: in the rendered patch section, in a frozen source excerpt, or not at
all. This is the first measurement to run before any paid D/P experiment,
because a gold bug that never entered the prompt cannot be found.

Two modes:

    # Offline selection over prepared inputs + local mirrors (free).
    .venv/bin/python scripts/evaluation/gold_visibility.py \
        --prepared ../eval-data/prepared/dev.json \
        --gold ../eval-data/reviewbench/golden \
        --mirrors ../eval-data/mirrors \
        --out /tmp/gold-visibility.json

    # Recompute from a saved review trace (a run artifact, still free).
    .venv/bin/python scripts/evaluation/gold_visibility.py \
        --report reports/evaluation/.../run.json \
        --gold ../eval-data/reviewbench/golden \
        --out /tmp/gold-visibility.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.analyzer.context_builder import build_review_context  # noqa: E402
from src.analyzer.repository_context import EvidenceBundle, GitSnapshot  # noqa: E402
from src.evaluation.gold_scope import summarize_gold_scope  # noqa: E402
from src.evaluation.gold_visibility import (  # noqa: E402
    gold_visibility,
    summarize_visibility,
    visibility_from_trace,
)
from src.evaluation.reviewbench import parse_diff  # noqa: E402
from src.models import PullRequest  # noqa: E402

PATCH_TOKENS = 12000
EVIDENCE_TOKENS = 6000


def _records(prepared: Any) -> list[dict[str, Any]]:
    records = prepared.get("records") if isinstance(prepared, dict) else prepared
    if not isinstance(records, list):
        raise SystemExit("prepared input must be a list or contain a records list")
    return [record for record in records if isinstance(record, dict)]


def _gold_path(gold_dir: Path, case_id: str) -> Path:
    return gold_dir / f"{case_id}.json"


def _resolve_gold_case(report: dict[str, Any], gold_dir: Path, override: str | None) -> str:
    """Map a saved report back to its gold file without trusting a hand-typed name."""
    if override:
        return override
    inner = report.get("report")
    if not isinstance(inner, dict) and isinstance(report.get("audit"), dict):
        inner = report["audit"].get("report")
    trace = (inner or {}).get("repository_context", {}).get("review_trace", {})
    identity = trace.get("identity", {}) if isinstance(trace, dict) else {}
    repository = str(identity.get("repository", ""))
    number = identity.get("pr_number")
    head_sha = str(identity.get("head_sha", ""))
    raw_case = str(identity.get("case_id", ""))
    if not repository and "#" in raw_case:
        prefix = raw_case.split("@", 1)[0]
        repository, _, number_text = prefix.rpartition("#")
        number = int(number_text) if number_text.isdigit() else number
    if not repository or not isinstance(number, int):
        raise SystemExit(
            "cannot resolve the gold case from the trace identity; pass --case <case_id>"
        )
    for path in sorted(gold_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pr = data.get("pr", {})
        slug = str(pr.get("repo", "")).rstrip("/").split("github.com/")[-1]
        if (slug == repository and pr.get("pr_number") == number
                and (not head_sha or str(pr.get("head", "")) == head_sha)):
            return path.stem
    raise SystemExit(
        f"no gold file matches {repository}#{number}@{head_sha}; pass --case <case_id>"
    )


def _load_gold(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "findings" not in data:
        raise SystemExit(f"gold file has no findings: {path}")
    return data


def _offline_case(record: dict[str, Any], gold_dir: Path, mirrors: Path) -> dict[str, Any]:
    case_id = str(record["case_id"])
    metadata = record["metadata"]
    input_dir = Path(record["input_dir"])
    diff = (input_dir / "diff.patch").read_text(encoding="utf-8")
    files = parse_diff(diff)
    pr = PullRequest(
        repo=metadata["nwo"],
        number=int(metadata["pr_number"]),
        title=metadata.get("title", ""),
        body=metadata.get("body"),
        base_ref=metadata.get("base"),
        base_sha=metadata["base"],
        head_ref=metadata.get("head"),
        head_sha=metadata["head"],
    )
    context = build_review_context(
        pr, files, include_context_pack=False, allow_remote_file_fetch=False,
        max_patch_tokens=PATCH_TOKENS,
    )
    mirror = mirrors / str(metadata["nwo"]).replace("/", "_")
    snapshot = GitSnapshot(mirror, metadata["base"], metadata["head"])
    bundle = EvidenceBundle(snapshot, budget=EVIDENCE_TOKENS)
    bundle.follow(files)
    evidence_refs = [
        {
            "path": item["path"], "side": item["side"],
            "start_line": item["start_line"], "end_line": item["end_line"],
        }
        for item in bundle.excerpts
    ]
    gold = _load_gold(_gold_path(gold_dir, case_id))["findings"]
    results = gold_visibility(
        gold,
        rendered_ranges=getattr(context, "rendered_head_ranges", {}),
        evidence_refs=evidence_refs,
    )
    return {
        "case_id": case_id,
        "gold": summarize_gold_scope(gold),
        "visibility": summarize_visibility(results),
        "evidence": {
            "excerpts": len(bundle.excerpts),
            "omitted": len(bundle.omitted),
            "tokens": bundle.tokens,
            "budget": bundle.budget,
            "context_truncated": context.truncated,
            "evidence_sources": evidence_refs,
        },
    }


def _report_case(report_path: Path, gold_dir: Path, override: str | None = None) -> dict[str, Any]:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    report = payload.get("report")
    if not isinstance(report, dict) and isinstance(payload.get("audit"), dict):
        report = payload["audit"].get("report")
    trace = (
        report.get("repository_context", {}).get("review_trace")
        if isinstance(report, dict) else None
    )
    if not isinstance(trace, dict):
        raise SystemExit(f"no review_trace in {report_path}")
    case_id = _resolve_gold_case(payload, gold_dir, override)
    gold = _load_gold(_gold_path(gold_dir, case_id))["findings"]
    results = visibility_from_trace(trace, gold)
    return {
        "case_id": case_id,
        "gold": summarize_gold_scope(gold),
        "visibility": summarize_visibility(results),
        "trace_sha256": trace.get("trace_sha256"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--prepared", type=Path, help="prepared corpus JSON")
    source.add_argument("--report", type=Path, help="saved run/report JSON with a review trace")
    parser.add_argument("--gold", type=Path, required=True, help="directory of gold JSON files")
    parser.add_argument("--mirrors", type=Path, default=ROOT.parent / "eval-data" / "mirrors",
                        help="local repository mirrors for offline snapshot reads")
    parser.add_argument("--case", action="append", default=[],
                        help="limit to a case id (repeatable)")
    parser.add_argument("--limit", type=int, default=0, help="maximum number of cases")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    cases: list[dict[str, Any]] = []
    if args.report is not None:
        cases.append(_report_case(args.report, args.gold, args.case[0] if args.case else None))
    else:
        prepared = json.loads(args.prepared.read_text(encoding="utf-8"))
        selected = [
            record for record in _records(prepared)
            if not args.case or record.get("case_id") in set(args.case)
        ]
        if args.limit:
            selected = selected[: args.limit]
        for record in selected:
            case_id = str(record.get("case_id"))
            if not _gold_path(args.gold, case_id).exists():
                print(f"SKIP {case_id}: no gold file")
                continue
            try:
                cases.append(_offline_case(record, args.gold, args.mirrors))
            except Exception as exc:  # noqa: BLE001 - report and continue across cases
                print(f"SKIP {case_id}: {type(exc).__name__}: {exc}")

    denominator = sum(case["visibility"]["gold_denominator"] for case in cases)
    fully_visible = sum(case["visibility"]["fully_visible"] for case in cases)
    any_visible = sum(case["visibility"]["any_visible"] for case in cases)
    summary = {
        "cases": len(cases),
        "gold_denominator": denominator,
        "fully_visible": fully_visible,
        "partially_visible": sum(
            case["visibility"]["partially_visible"] for case in cases
        ),
        "any_visible": any_visible,
        "not_visible": denominator - any_visible,
        "s_recall": round(fully_visible / denominator, 4) if denominator else None,
        "any_visible_recall": (
            round(any_visible / denominator, 4) if denominator else None
        ),
        "per_case": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in (
        "cases", "gold_denominator", "fully_visible", "partially_visible",
        "any_visible", "s_recall", "any_visible_recall", "not_visible")},
        ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
