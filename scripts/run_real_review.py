#!/usr/bin/env python
"""Run the production review path over a local commit range and report the numbers.

    .venv/bin/python scripts/run_real_review.py --base d99524db81 --head HEAD \
        --units 12 --workers 4 --out reports/real-run/big-pr.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.analyzer.git_loader import load_frozen_plan_inputs  # noqa: E402
from src.analyzer.risk_rules import scan_signals  # noqa: E402
from src.models import PullRequest  # noqa: E402
from src.reviewer.engine import review_with_ai  # noqa: E402
from src.reviewer.provider import OpenAICompatibleProvider  # noqa: E402
from src.utils.config import get_settings  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--units", type=int, default=0, help="0 = disable per-unit review")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    snapshot, files, _paths = load_frozen_plan_inputs(REPO_ROOT, args.base, args.head)
    scan = scan_signals(files)
    pr = PullRequest(repo="HelicasECoode42/ai-pr-review", number=0,
                     title=f"{args.base[:7]}..{args.head[:7]}",
                     head_sha=snapshot.head, base_sha=snapshot.base)

    providers: list = []

    def provider_factory():
        provider = OpenAICompatibleProvider(
            api_key=settings.openai_api_key, model=settings.review_model,
            base_url=settings.openai_base_url, timeout=settings.request_timeout_seconds,
            stream=True, total_timeout=300, max_output_tokens=32768, reasoning_effort="low",
        )
        providers.append(provider)
        return provider

    started = time.perf_counter()
    report = review_with_ai(
        pr, files, list(scan.findings), provider_factory(), settings.max_suggestions,
        min_confidence=settings.min_comment_confidence,
        max_suggestions_per_file=settings.max_suggestions_per_file,
        language="en", follow_changes=True, repository_snapshot=snapshot,
        repository_evidence_tokens=settings.repository_evidence_tokens,
        verify_rule_signals=settings.verify_rule_signals,
        analyzer_warnings=list(scan.warnings),
        unit_review_max_units=args.units, unit_review_workers=args.workers,
        unit_provider_factory=provider_factory,
    )
    elapsed = time.perf_counter() - started

    from src.reviewer.usage import summarize_usage

    usage = summarize_usage([r for p in providers for r in p.usage_records])
    out = {
        "base": snapshot.base, "head": snapshot.head, "files": len(files),
        "unit_review_max_units": args.units, "workers": args.workers,
        "seconds": round(elapsed, 1),
        "execution_status": report.execution_status,
        "report_confidence": report.report_confidence,
        "suggestions": len(report.suggestions),
        "pending": len(report.pending_suggestions),
        "metrics": report.metrics,
        "usage": usage,
        "warnings": report.analysis_warnings[:8],
        "titles": [s.title for s in report.suggestions[:20]],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    Path(str(args.out).replace(".json", "-full.json")).write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    print(json.dumps(out, indent=2, ensure_ascii=False)[:2600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
