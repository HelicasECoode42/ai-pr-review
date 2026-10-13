"""Controlled ablations over frozen inputs. No labels passed into run_review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.evaluation.reviewbench import run_review
from src.evaluation.runtime import EvaluationFailure, source_identity
from src.evaluation.scoring import GoldenCase, validate_splits
from src.reviewer.provider import OpenAICompatibleProvider
from src.utils.config import get_settings

VARIANTS = {
    "baseline": {"evidence": True, "critic": False, "context_pack": True},
    "no_evidence": {"evidence": False, "critic": False, "context_pack": True},
    "with_critic": {"evidence": True, "critic": True, "context_pack": True},
    "no_context": {"evidence": True, "critic": False, "context_pack": False},
}


def run(cases: list[GoldenCase], provider, model: str, project_root=None) -> dict:
    validate_splits(cases)
    records = []
    stopped = False
    # Identical cases, model, confidence and suggestion budgets across variants.
    for case in cases:
        for variant, config in VARIANTS.items():
            if stopped:
                records.append(
                    {
                        "case_id": case.case_id,
                        "variant": variant,
                        "status": "not_run",
                        "reason": "fatal_provider_error",
                    }
                )
                continue
            try:
                output, audit = run_review(
                    case.metadata,
                    case.diff,
                    provider,
                    project_root=project_root,
                    model=model,
                    **config,
                )
                records.append(
                    {
                        "case_id": case.case_id,
                        "variant": variant,
                        "status": "success",
                        "output": output,
                        "audit": audit,
                    }
                )
            except Exception as exc:
                # Never count an execution failure as a no-defect prediction.
                records.append(
                    {
                        "case_id": case.case_id,
                        "variant": variant,
                        "status": "failed",
                        "error": type(exc).__name__,
                        "failure": exc.record() if isinstance(exc, EvaluationFailure) else None,
                    }
                )
                stopped = isinstance(exc, EvaluationFailure) and exc.fatal
    return {
        **source_identity(),
        "quality_status": "unadjudicated",
        "model": model,
        "dataset_sha256": hashlib.sha256(
            json.dumps([c.model_dump() for c in cases], sort_keys=True).encode()
        ).hexdigest(),
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    cases = [GoldenCase.model_validate(c) for c in json.loads(args.dataset.read_text())]
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required; fixtures cannot stand in for a real model")
    with OpenAICompatibleProvider(
        settings.openai_api_key,
        settings.review_model,
        settings.openai_base_url,
        settings.request_timeout_seconds,
    ) as provider:
        results = run(cases, provider, settings.review_model, args.project_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    if any(r["status"] != "success" for r in results["records"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
