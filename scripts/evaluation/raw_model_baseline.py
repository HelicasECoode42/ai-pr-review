#!/usr/bin/env python3
"""Raw-model baseline: the same frozen diff without the review harness.

The D-layer question is whether the harness (schema, hypotheses, bounded tools,
two stages) helps or suppresses the model's own bug-finding. This script holds
the input fixed and removes the harness:

* one model call per case and variant, no tools, no verification, no criteria;
* variant ``diff``: PR metadata + unified diff only;
* variant ``diff+evidence``: the same auto-seeded frozen source excerpts the
  harness would render, so evidence dilution can be separated from prompt shape.

The output is a candidate list with a positional pre-match and the overlapping
gold messages for a semantic adjudication. It never reads gold at prompt time.

    REVIEW_THINKING_ENABLED=false .venv/bin/python scripts/evaluation/raw_model_baseline.py \
        --prepared ../eval-data/prepared/dev.json \
        --gold ../eval-data/reviewbench/golden --mirrors ../eval-data/mirrors \
        --case brexhq_CrabTrap_15-5ef0884c --modes diff diff+evidence \
        --max-http-attempts 4 --out /tmp/raw-baseline.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.analyzer.repository_context import EvidenceBundle, GitSnapshot  # noqa: E402
from src.evaluation.funnel import overlapping_gold  # noqa: E402
from src.evaluation.gold_scope import summarize_gold_scope  # noqa: E402
from src.evaluation.reviewbench import parse_diff  # noqa: E402
from src.reviewer.provider import OpenAICompatibleProvider  # noqa: E402
from src.utils.config import get_settings, is_deepseek_chat_model  # noqa: E402

SYSTEM = (
    "You are a senior code reviewer. Review the pull request below and report "
    "real defects introduced or worsened by it. Return a JSON object only: "
    '{"findings":[{"file":"relative/path","line":123,"title":"short title",'
    '"reason":"why it is a defect and how it is triggered"}]}. '
    "At most 20 findings, most severe first. Do not report style nits. "
    "A finding must name a file and a positive line number in the new version."
)


def _records(prepared: Any) -> list[dict[str, Any]]:
    records = prepared.get("records") if isinstance(prepared, dict) else prepared
    return [item for item in records or [] if isinstance(item, dict)]


def _gold(gold_dir: Path, case_id: str) -> list[dict[str, Any]]:
    return json.loads((gold_dir / f"{case_id}.json").read_text(encoding="utf-8"))["findings"]


def _evidence_text(record: dict[str, Any], mirrors: Path) -> tuple[str, list[dict[str, Any]]]:
    metadata = record["metadata"]
    files = parse_diff((Path(record["input_dir"]) / "diff.patch").read_text(encoding="utf-8"))
    snapshot = GitSnapshot(
        mirrors / str(metadata["nwo"]).replace("/", "_"), metadata["base"], metadata["head"]
    )
    bundle = EvidenceBundle(snapshot, budget=6000)
    bundle.follow(files)
    refs = [
        {"path": item["path"], "side": item["side"],
         "start_line": item["start_line"], "end_line": item["end_line"]}
        for item in bundle.excerpts
    ]
    return bundle.render(), refs


def _prompt(record: dict[str, Any], mode: str, evidence: str) -> str:
    metadata = record["metadata"]
    diff = (Path(record["input_dir"]) / "diff.patch").read_text(encoding="utf-8")
    parts = [
        f"# Pull request {metadata['nwo']}#{metadata['pr_number']}",
        f"Title: {metadata.get('title', '')}",
        "## Description",
        str(metadata.get("body") or "(empty)"),
        "## Diff",
        diff,
    ]
    if mode == "diff+evidence":
        parts.extend(["## Frozen repository evidence", evidence])
    return "\n".join(parts)


def _parse_findings(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.startswith("```")]
        text = "\n".join(lines)
    try:
        payload = json.loads(text)
    except ValueError:
        return []
    findings = payload.get("findings") if isinstance(payload, dict) else None
    return [item for item in findings or [] if isinstance(item, dict)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--mirrors", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--modes", nargs="+", choices=("diff", "diff+evidence"),
                        default=["diff", "diff+evidence"])
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--max-http-attempts", type=int, default=6)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("Model credential required")
    thinking = (
        settings.review_thinking_enabled
        if is_deepseek_chat_model(settings.review_model, settings.openai_base_url)
        else None
    )
    wanted = set(args.case)
    records = [
        record for record in _records(json.loads(args.prepared.read_text(encoding="utf-8")))
        if not wanted or record["case_id"] in wanted
    ]
    budget = {"limit": args.max_http_attempts, "attempted": 0}

    def begin_attempt():
        if budget["attempted"] >= budget["limit"]:
            raise RuntimeError("raw baseline HTTP attempt cap reached")
        budget["attempted"] += 1

    provider = OpenAICompatibleProvider(
        api_key=settings.openai_api_key, model=settings.review_model,
        base_url=settings.openai_base_url, timeout=180, stream=False,
        total_timeout=300, max_output_tokens=args.max_output_tokens,
        reasoning_effort="low", request_attempt_hook=begin_attempt,
        thinking_enabled=thinking,
    )
    rows = []
    for record in records:
        case_id = str(record["case_id"])
        gold = _gold(args.gold, case_id)
        evidence, refs = _evidence_text(record, args.mirrors)
        for mode in args.modes:
            if budget["attempted"] >= budget["limit"]:
                print(f"SKIP {case_id} {mode}: HTTP attempt cap reached")
                continue
            prompt = _prompt(record, mode, evidence)
            usage_start = len(provider.usage_records)
            raw = provider.complete_json(SYSTEM, prompt)
            usage = provider.usage_records[usage_start:]
            findings = _parse_findings(raw)
            pack = [
                {
                    "index": index,
                    "file": item.get("file"), "line": item.get("line"),
                    "title": item.get("title"), "reason": item.get("reason"),
                    "overlapping_gold": [
                        {"gold_index": gold_index, "message": gold_item.get("message")}
                        for gold_index, gold_item in overlapping_gold(item, gold)
                    ],
                }
                for index, item in enumerate(findings)
            ]
            token = usage[0]["usage"] if usage and isinstance(usage[0].get("usage"), dict) else {}
            rows.append({
                "case_id": case_id,
                "mode": mode,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "prompt_chars": len(prompt),
                "raw_response": raw,
                "findings": findings,
                "candidate_pack": pack,
                "gold_scope": summarize_gold_scope(gold),
                "evidence_refs": refs if mode == "diff+evidence" else [],
                "usage": token,
            })
            print(
                f"{case_id[:40]:42s} {mode:13s} findings={len(findings):2d} "
                f"prompt={token.get('prompt_tokens')} completion={token.get('completion_tokens')}"
            )
    payload = {
        "experiment": "raw-model-baseline",
        "model": settings.review_model,
        "thinking_enabled": thinking,
        "max_output_tokens": args.max_output_tokens,
        "http_attempts": budget["attempted"],
        "http_attempt_cap": budget["limit"],
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({budget['attempted']} HTTP attempts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
