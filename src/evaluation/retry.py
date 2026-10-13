"""Retry failed tasks, preserving successful candidates and attempt provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from src.evaluation.corpus import write_json
from src.evaluation.reviewbench import run_review
from src.evaluation.runtime import EvaluationFailure, source_identity
from src.reviewer.provider import OpenAICompatibleProvider
from src.utils.config import get_settings


def retry(prepared: dict, previous: Path, output: Path, provider, model: str) -> dict:
    if output.exists():
        raise ValueError("Retry output must be a new directory")
    prior = json.loads((previous / "run.json").read_text())
    if prior["model"] != model or prior["benchmark_commit"] != prepared["benchmark_commit"]:
        raise ValueError("Model or benchmark changed")
    by_id = {r["case_id"]: r for r in prior["records"]}
    identity = source_identity()
    records = []
    consecutive_failures = 0
    stopped = False
    for task in prepared["records"]:
        old = by_id.get(task["case_id"])
        record = {"case_id": task["case_id"], "status": "not_run", "previous_attempt": old}
        if old and old["status"] == "success":
            # Audit retains original source identity and transport; no extra model call.
            candidate = previous / "candidates" / (task["case_id"] + ".json")
            audit = previous / "audits" / (task["case_id"] + ".json")
            for path in (candidate, audit):
                dest = output / path.parent.name / path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
            record.update(status="success", reused_from=str(previous), request_count=0)
        elif stopped or task["status"] != "ready":
            record["reason"] = "batch_stopped" if stopped else "input_not_ready"
        else:
            work = Path(task["input_dir"])
            diff = (work / "diff.patch").read_text()
            if hashlib.sha256(diff.encode()).hexdigest() != task["diff_sha256"]:
                raise ValueError("Prepared diff changed")
            try:
                candidate, audit = run_review(
                    task["metadata"],
                    diff,
                    provider,
                    model=model,
                    project_root=work / "project",
                    context_pack=True,
                )
                audit.update(
                    identity,
                    retry_transport="streaming",
                    timeout_seconds=180,
                    total_time_budget_seconds=300,
                    max_output_tokens=32768,
                )
                write_json(output / "candidates" / (task["case_id"] + ".json"), candidate)
                write_json(output / "audits" / (task["case_id"] + ".json"), audit)
                record.update(status="success", request_count=audit["request_count"])
                consecutive_failures = 0
            except Exception as exc:
                record.update(
                    status="failed",
                    error=type(exc).__name__,
                    failure=exc.record() if isinstance(exc, EvaluationFailure) else None,
                )
                consecutive_failures += 1
                stopped = (
                    consecutive_failures >= 3 or isinstance(exc, EvaluationFailure) and exc.fatal
                )
        records.append(record)
        result = {
            **identity,
            "benchmark_commit": prepared["benchmark_commit"],
            "model": model,
            "quality_status": "unadjudicated",
            "transport_note": "Successful non-streaming outputs reused with original audits",
            "records": records,
        }
        write_json(output / "run.json", result)
        print(task["case_id"] + ": " + record["status"], flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared", type=Path)
    parser.add_argument("previous", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY required")
    with OpenAICompatibleProvider(
        settings.openai_api_key,
        settings.review_model,
        settings.openai_base_url,
        timeout=180,
        stream=True,
    ) as provider:
        result = retry(
            json.loads(args.prepared.read_text()),
            args.previous,
            args.output,
            provider,
            settings.review_model,
        )
    if any(r["status"] != "success" for r in result["records"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
