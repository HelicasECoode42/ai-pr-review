"""Bounded parallel retries with one isolated provider per task."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event

from src.evaluation.corpus import write_json
from src.evaluation.reviewbench import run_review
from src.evaluation.runtime import EvaluationFailure, source_identity
from src.reviewer.provider import OpenAICompatibleProvider
from src.utils.config import get_settings


def run(
    prepared: dict,
    previous: Path,
    output: Path,
    provider_factory,
    model: str,
    concurrency=2,
    max_output_tokens=32768,
) -> dict:
    if output.exists() or not 1 <= concurrency <= 3:
        raise ValueError("Use fresh output and concurrency 1–3")
    prior = json.loads((previous / "run.json").read_text())
    if prior["benchmark_commit"] != prepared["benchmark_commit"] or prior["model"] != model:
        raise ValueError("Model or benchmark changed")
    by_id = {r["case_id"]: r for r in prior["records"]}
    identity = source_identity()
    records = {}
    pending = []
    stopped = Event()

    def attempt(task):
        record = {
            "case_id": task["case_id"],
            "status": "not_run",
            "previous_attempt": by_id.get(task["case_id"]),
        }
        if stopped.is_set():
            record["reason"] = "fatal_provider_error"
            return record
        work = Path(task["input_dir"])
        diff = (work / "diff.patch").read_text()
        if hashlib.sha256(diff.encode()).hexdigest() != task["diff_sha256"]:
            raise ValueError("Prepared diff changed")
        try:
            with provider_factory() as provider:
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
                transport="streaming",
                timeout_seconds=180,
                total_time_budget_seconds=300,
                max_output_tokens=max_output_tokens,
            )
            write_json(output / "candidates" / (task["case_id"] + ".json"), candidate)
            write_json(output / "audits" / (task["case_id"] + ".json"), audit)
            record.update(status="success", request_count=audit["request_count"])
        except Exception as exc:
            record.update(
                status="failed",
                error=type(exc).__name__,
                failure=exc.record() if isinstance(exc, EvaluationFailure) else None,
            )
            if isinstance(exc, EvaluationFailure) and exc.fatal:
                stopped.set()
        return record

    def checkpoint():
        result = {
            **identity,
            "benchmark_commit": prepared["benchmark_commit"],
            "model": model,
            "concurrency": concurrency,
            "quality_status": "unadjudicated",
            "records": [
                records.get(
                    t["case_id"],
                    {"case_id": t["case_id"], "status": "not_run", "reason": "pending"},
                )
                for t in prepared["records"]
            ],
        }
        write_json(output / "run.json", result)
        return result

    for task in prepared["records"]:
        old = by_id.get(task["case_id"])
        if old and old["status"] == "success":
            for kind in ("candidates", "audits"):
                path = previous / kind / (task["case_id"] + ".json")
                destination = output / kind / path.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
            records[task["case_id"]] = {**old, "reused_from": str(previous)}
        elif task["status"] != "ready":
            records[task["case_id"]] = {
                "case_id": task["case_id"],
                "status": "not_run",
                "reason": "input_not_ready",
            }
        else:
            pending.append(task)
    checkpoint()
    consecutive_failures = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(attempt, t): t for t in pending}
        for future in as_completed(futures):
            task = futures[future]
            try:
                records[task["case_id"]] = future.result()
            except Exception as exc:
                records[task["case_id"]] = {
                    "case_id": task["case_id"],
                    "status": "failed",
                    "error": type(exc).__name__,
                }
                stopped.set()
            if records[task["case_id"]]["status"] == "failed":
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    stopped.set()
            elif records[task["case_id"]]["status"] == "success":
                consecutive_failures = 0
            checkpoint()
            print(task["case_id"] + ": " + records[task["case_id"]]["status"], flush=True)
    return checkpoint()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared", type=Path)
    parser.add_argument("previous", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-output-tokens", type=int, default=32768)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY required")

    def factory():
        return OpenAICompatibleProvider(
            settings.openai_api_key,
            settings.review_model,
            settings.openai_base_url,
            timeout=180,
            stream=True,
            total_timeout=300,
            max_output_tokens=args.max_output_tokens,
        )

    result = run(
        json.loads(args.prepared.read_text()),
        args.previous,
        args.output,
        factory,
        settings.review_model,
        args.concurrency,
        args.max_output_tokens,
    )
    if any(r["status"] != "success" for r in result["records"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
