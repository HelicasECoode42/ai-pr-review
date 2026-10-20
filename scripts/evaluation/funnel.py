#!/usr/bin/env python3
"""Report the N -> S -> D -> P funnel for dev cases (S is free; D/P need an artifact).

S-only (no model calls, free):

    .venv/bin/python scripts/evaluation/funnel.py \
        --prepared ../eval-data/prepared/dev.json \
        --gold ../eval-data/reviewbench/golden \
        --mirrors ../eval-data/mirrors --out /tmp/funnel.json

Add a saved run artifact to fill D (identified: published + pending) and
P (published), still positionally matched:

    .venv/bin/python scripts/evaluation/funnel.py \
        --prepared ../eval-data/prepared/dev.json \
        --gold ../eval-data/reviewbench/golden \
        --mirrors ../eval-data/mirrors \
        --report reports/evaluation/real-dev-pair-2026-10-19/grounded.json \
        --case brexhq_CrabTrap_15-5ef0884c --out /tmp/funnel-crab.json

D/P are positional proxies (ReviewBench raw match), not semantic adjudication.
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
from src.evaluation.funnel import funnel_row, overlapping_gold, summarize_funnel  # noqa: E402
from src.evaluation.gold_visibility import gold_visibility  # noqa: E402
from src.evaluation.reviewbench import parse_diff  # noqa: E402
from src.models import PullRequest  # noqa: E402

PATCH_TOKENS = 12000
EVIDENCE_TOKENS = 6000


def _records(prepared: Any) -> list[dict[str, Any]]:
    records = prepared.get("records") if isinstance(prepared, dict) else prepared
    if not isinstance(records, list):
        raise SystemExit("prepared input must be a list or contain a records list")
    return [record for record in records if isinstance(record, dict)]


def _case_gold(gold_dir: Path, case_id: str) -> Any:
    path = gold_dir / f"{case_id}.json"
    if not path.exists():
        raise SystemExit(f"missing gold file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))["findings"]


def _offline_visibility(record: dict[str, Any], gold_dir: Path, mirrors: Path,
                        max_documentation_tokens: int):
    metadata = record["metadata"]
    input_dir = Path(record["input_dir"])
    files = parse_diff((input_dir / "diff.patch").read_text(encoding="utf-8"))
    pr = PullRequest(
        repo=metadata["nwo"], number=int(metadata["pr_number"]),
        title=metadata.get("title", ""), body=metadata.get("body"),
        base_ref=metadata.get("base"), base_sha=metadata["base"],
        head_ref=metadata.get("head"), head_sha=metadata["head"],
    )
    context = build_review_context(
        pr, files, include_context_pack=False, allow_remote_file_fetch=False,
        max_patch_tokens=PATCH_TOKENS,
        max_documentation_tokens=max_documentation_tokens,
    )
    snapshot = GitSnapshot(
        mirrors / str(metadata["nwo"]).replace("/", "_"), metadata["base"], metadata["head"]
    )
    bundle = EvidenceBundle(snapshot, budget=EVIDENCE_TOKENS)
    bundle.follow(files)
    refs = [
        {"path": item["path"], "side": item["side"],
         "start_line": item["start_line"], "end_line": item["end_line"]}
        for item in bundle.excerpts
    ]
    gold = _case_gold(gold_dir, str(record["case_id"]))
    return gold, gold_visibility(
        gold, rendered_ranges=context.rendered_head_ranges, evidence_refs=refs,
    ), context.token_count


def _artifact_payload(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
    report = audit.get("report") if isinstance(audit.get("report"), dict) else payload.get("report")
    trace = (
        report.get("repository_context", {}).get("review_trace")
        if isinstance(report, dict) else None
    )
    if not isinstance(trace, dict):
        # The paired runner persists traces beside the audit directory.
        sibling = path.parent.parent / "traces" / path.name
        if sibling.exists():
            trace = json.loads(sibling.read_text(encoding="utf-8"))
    identity = trace.get("identity", {}) if isinstance(trace, dict) else {}
    return payload, {"report": report or {}, "identity": identity, "trace": trace or {}}


def _published_candidates(payload: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    published = payload.get("findings")
    if not isinstance(published, list):
        published = report.get("suggestions") if isinstance(report, dict) else None
    return [item for item in published or [] if isinstance(item, dict)]


def _pending_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    pending = report.get("pending_suggestions") if isinstance(report, dict) else None
    return [item for item in pending or [] if isinstance(item, dict)]


def _load_adjudication(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("adjudication file must be a JSON object")
    return data


def _adjudication_for(data: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    if not data:
        return None
    if isinstance(data.get(case_id), dict):
        return data[case_id]
    if "published" in data or "pending" in data:
        return data
    return None


def _candidate_pack(
    gold: list[dict[str, Any]],
    published: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    case_id: str,
) -> dict[str, Any]:
    """Everything a human needs to adjudicate candidates against gold."""
    def gold_entry(index: int, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "gold_index": index, "file": item.get("file"),
            "start_line": item.get("start_line"), "end_line": item.get("end_line"),
            "tp_fp": item.get("tp_fp"), "scope": item.get("scope"),
            "message": item.get("message"),
        }

    def candidate_entry(index: int, item: dict[str, Any]) -> dict[str, Any]:
        matches = [
            gold_entry(gold_index, gold_item)
            for gold_index, gold_item in overlapping_gold(item, gold)
        ]
        return {
            "index": index,
            "file": item.get("file") or item.get("file_path"),
            "start_line": item.get("start_line", item.get("line")),
            "end_line": item.get("end_line", item.get("start_line", item.get("line"))),
            "title": item.get("title"),
            "reason": item.get("reason"),
            "evidence": item.get("evidence"),
            "overlapping_gold": matches,
        }

    return {
        "case_id": case_id,
        "gold": [gold_entry(index, item) for index, item in enumerate(gold)],
        "published": [candidate_entry(index, item) for index, item in enumerate(published)],
        "pending": [candidate_entry(index, item) for index, item in enumerate(pending)],
        "adjudication_format": {
            "published": ["gold_index or null, one per published candidate"],
            "pending": ["gold_index or null, one per pending candidate"],
        },
    }


def _report_matches_case(identity: dict[str, Any], record: dict[str, Any]) -> bool:
    metadata = record["metadata"]
    return (
        str(identity.get("repository", "")) == str(metadata["nwo"])
        and identity.get("pr_number") == int(metadata["pr_number"])
        and str(identity.get("head_sha", "")) == str(metadata["head"])
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--mirrors", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=None,
                        help="optional saved run artifact to fill D/P for its case")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--documentation-patch-tokens", type=int, default=0)
    parser.add_argument("--adjudication", type=Path, default=None,
                        help="manual candidate->gold mapping for a semantic D")
    parser.add_argument("--candidates-out", type=Path, default=None,
                        help="write the candidate/gold evidence pack for adjudication")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    records = _records(json.loads(args.prepared.read_text(encoding="utf-8")))
    wanted = set(args.case)
    if wanted:
        records = [record for record in records if record["case_id"] in wanted]
    if args.limit:
        records = records[: args.limit]

    artifact = None
    if args.report is not None:
        artifact = _artifact_payload(args.report)
    adjudication_data = _load_adjudication(args.adjudication)
    candidate_packs: dict[str, Any] = {}

    rows = []
    for record in records:
        try:
            gold, visibility, tokens = _offline_visibility(
                record, args.gold, args.mirrors, args.documentation_patch_tokens
            )
        except Exception as exc:  # noqa: BLE001 - skip unreadable cases, keep the run
            print(f"SKIP {record['case_id']}: {type(exc).__name__}: {exc}")
            continue
        published: list[dict[str, Any]] | None = None
        pending: list[dict[str, Any]] | None = None
        case_id = str(record["case_id"])
        if artifact is not None and _report_matches_case(artifact[1]["identity"], record):
            published = _published_candidates(artifact[0], artifact[1]["report"])
            pending = _pending_candidates(artifact[1]["report"])
            candidate_packs[case_id] = _candidate_pack(
                gold, published, pending, case_id
            )
        rows.append(funnel_row(
            case_id, gold,
            visibility=visibility, published=published, pending=pending,
            adjudication=_adjudication_for(adjudication_data, case_id),
        ))
        row = rows[-1]
        print(
            f"{case_id[:44]:46s} N={row.gold_pr_scope:3d} "
            f"S={row.s_fully_visible:3d} D={row.d_identified} "
            f"D_sem={row.d_semantic} P={row.p_published} ctx_tokens={tokens}"
        )

    summary = summarize_funnel(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.candidates_out is not None:
        args.candidates_out.parent.mkdir(parents=True, exist_ok=True)
        args.candidates_out.write_text(
            json.dumps(candidate_packs, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote candidates {args.candidates_out}")
    print(json.dumps({key: summary[key] for key in (
        "cases", "gold_pr_scope", "s_fully_visible", "s_any_visible",
        "d_identified", "d_semantic", "p_published", "s_recall",
        "d_recall", "d_semantic_recall", "p_recall")},
        ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
