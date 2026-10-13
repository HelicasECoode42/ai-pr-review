"""Fresh paired legacy/repository-grounded ReviewBench runs; never read gold labels."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event

from src.analyzer.repository_context import GitSnapshot
from src.evaluation.corpus import write_json
from src.evaluation.reviewbench import run_review
from src.evaluation.runtime import EvaluationFailure, FATAL_HTTP_STATUSES, source_identity
from src.reviewer.provider import OpenAICompatibleProvider, ProviderError
from src.utils.config import get_settings


class MeasuredProvider:
    def __init__(self, provider):
        self.provider = provider
        self.calls = []
    @property
    def usage_records(self):
        return self.provider.usage_records
    def complete_json(self, system, prompt):
        started = time.monotonic()
        try:
            output = self.provider.complete_json(system, prompt)
            self.calls.append({"status": "success", "seconds": time.monotonic() - started,
                               "input_chars": len(system) + len(prompt)})
            return output
        except ProviderError as exc:
            self.calls.append({"status": "failed", "seconds": time.monotonic() - started,
                               "http_status": exc.status_code, "error_kind": exc.kind})
            raise


def paired(prepared, mirrors, output, *, arms=("control", "grounded"), concurrency=3, limit=None):
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("Model credential required")
    identity = source_identity()
    if identity["working_tree_dirty"]:
        raise ValueError("Paired evaluation requires a clean committed reviewer")
    tasks = prepared["records"][:limit] if limit else prepared["records"]
    protocol = {"benchmark_commit": prepared["benchmark_commit"], "model": settings.review_model,
                "reviewer": identity, "source_token_budget": 12000,
                "repository_token_budget": 6000, "max_output_tokens_per_call": 32768,
                "read_timeout_seconds": 180, "total_timeout_per_call_seconds": 300,
                "temperature": .1, "max_suggestions": 20, "min_confidence": .65,
                "guidance": True, "concurrency": concurrency, "max_attempts_per_task": 2,
                "discovery_max_calls": 3, "verification_max_calls": 2,
                "note": "Same per-call settings and maximum source budget; grounded uses more calls. Report cost and latency, not equal-compute claims."}
    output.mkdir(parents=True, exist_ok=True)
    old_protocol = output / "protocol.json"
    if old_protocol.exists() and json.loads(old_protocol.read_text()) != protocol:
        raise ValueError("Refusing mixed source/model/budget resumption")
    write_json(old_protocol, protocol)
    stop = Event()
    records = {arm: {} for arm in arms}
    for arm in arms:
        prior = output / arm / "run.json"
        if prior.exists():
            records[arm] = {r["case_id"]: r for r in json.loads(prior.read_text())["records"]}

    def attempt(task, arm):
        prior = records[arm].get(task["case_id"])
        if prior and prior["status"] == "success":
            return prior
        record = {"case_id": task["case_id"], "arm": arm, "status": "not_run", "attempts": []}
        if task["status"] != "ready" or stop.is_set():
            record["reason"] = "input_not_ready" if task["status"] != "ready" else "fatal_provider_error"
            return record
        work = Path(task["input_dir"])
        diff = (work / "diff.patch").read_text()
        if hashlib.sha256(diff.encode()).hexdigest() != task["diff_sha256"]:
            raise ValueError("Prepared diff changed")
        meta = task["metadata"]
        root = mirrors / meta["nwo"].replace("/", "_")
        for index in range(2):
            if stop.is_set():
                break
            provider = OpenAICompatibleProvider(settings.openai_api_key, settings.review_model,
                         settings.openai_base_url, timeout=180, stream=True,
                         total_timeout=300, max_output_tokens=32768)
            measured = MeasuredProvider(provider)
            started = time.monotonic()
            try:
                candidate, audit = run_review(meta, diff, measured, model=settings.review_model,
                    context_pack=True, project_root=work / "project", critic=False,
                    follow_changes=arm == "grounded",
                    repository_snapshot=GitSnapshot(root, meta["base"], meta["head"]) if arm == "grounded" else None,
                    patch_and_repository_tokens=12000, repository_evidence_tokens=6000)
                audit.update(identity)
                write_json(output / arm / "candidates" / (task["case_id"] + ".json"), candidate)
                write_json(output / arm / "audits" / (task["case_id"] + ".json"), audit)
                record.update(status="success", finding_count=len(candidate["findings"]))
            except Exception as exc:
                record.update(status="failed", error=type(exc).__name__,
                              failure=exc.record() if isinstance(exc, EvaluationFailure) else None)
                if (isinstance(exc, EvaluationFailure) and exc.fatal) or any(c.get("http_status") in FATAL_HTTP_STATUSES for c in measured.calls):
                    stop.set()
            finally:
                record["attempts"].append({"attempt": index + 1, "seconds": time.monotonic() - started,
                                           "calls": measured.calls, "usage": provider.usage_records})
                provider.close()
            if record["status"] == "success" or stop.is_set():
                break
        return record

    def checkpoint(arm):
        write_json(output / arm / "run.json", {**identity, "benchmark_commit": prepared["benchmark_commit"],
                   "model": settings.review_model, "protocol": protocol,
                   "quality_status": "unadjudicated", "records": [records[arm].get(t["case_id"],
                        {"case_id": t["case_id"], "status": "not_run"}) for t in tasks]})

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(attempt, task, arm): (task, arm) for task in tasks for arm in arms}
        for future in as_completed(futures):
            task, arm = futures[future]
            record = future.result()
            records[arm][task["case_id"]] = record
            checkpoint(arm)
            print(f"{arm} {task['case_id']}: {record['status']} findings={record.get('finding_count', '?')}", flush=True)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared", type=Path)
    parser.add_argument("--mirrors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arms", nargs="+", choices=("control", "grounded"), default=["control", "grounded"])
    parser.add_argument("--concurrency", type=int, default=3, choices=range(1, 4))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    result = paired(json.loads(args.prepared.read_text()), args.mirrors, args.output,
                    arms=args.arms, concurrency=args.concurrency, limit=args.limit)
    if any(r["status"] != "success" for arm in result.values() for r in arm.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
