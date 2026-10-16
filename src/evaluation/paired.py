"""Fresh paired legacy/repository-grounded ReviewBench runs; never read gold labels."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event, Lock

from src.analyzer.repository_context import GitSnapshot
from src.evaluation.corpus import write_json
from src.evaluation.reviewbench import _usage_total, run_review
from src.evaluation.runtime import EvaluationFailure, FATAL_HTTP_STATUSES, source_identity
from src.reviewer.provider import OpenAICompatibleProvider, ProviderError
from src.utils.config import get_settings


DEFAULT_OUTPUT_TOKENS = 32768
MAX_REVIEW_MODEL_CALLS = 5  # three discovery rounds and two verification calls
HTTP_ATTEMPTS_PER_CALL = 2  # original request plus response_format fallback


class RunRequestBudget:
    """Atomic run-wide cap on HTTP attempts started, not provider-billed calls."""

    def __init__(self, limit, stop, *, attempted=0, rejected=0, stopped=0):
        self.limit = limit
        self.stop = stop
        self._lock = Lock()
        self.attempted = attempted
        self.rejected = rejected
        self.stopped = stopped
        self.exhausted = attempted >= limit
        self.stop_reason = "request_budget_exhausted" if self.exhausted else None
        if self.exhausted:
            self.stop.set()

    def begin_attempt(self):
        with self._lock:
            if self.stop.is_set():
                if self.exhausted:
                    self.rejected += 1
                    raise ProviderError(
                        "Run-wide HTTP request-attempt budget exhausted",
                        kind="request_budget_exhausted",
                    )
                self.stopped += 1
                raise ProviderError(
                    "Run stopped after a fatal provider error",
                    kind="run_stopped",
                )
            if self.attempted >= self.limit:
                self.rejected += 1
                self.exhausted = True
                self.stop_reason = "request_budget_exhausted"
                self.stop.set()
                raise ProviderError(
                    "Run-wide HTTP request-attempt budget exhausted",
                    kind="request_budget_exhausted",
                )
            self.attempted += 1

    def stop_after_fatal_error(self):
        with self._lock:
            if not self.stop.is_set():
                self.stop_reason = "fatal_provider_error"
                self.stop.set()

    def snapshot(self):
        with self._lock:
            return {"limit": self.limit, "attempted": self.attempted,
                    "rejected_before_send": self.rejected,
                    "stopped_before_send": self.stopped,
                    "exhausted": self.exhausted,
                    "stop_reason": self.stop_reason,
                    "semantics": "HTTP attempts started; not equivalent to billed calls"}


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


def _positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _safe_failure_record(exc):
    record = exc.record()
    # Preserve a count for diagnosis without storing model response bodies.
    outputs = record.pop("model_outputs", [])
    record["model_output_count"] = len(outputs)
    return record


def _usage_status(usage_records, http_attempts):
    known = sum(_usage_total([item]) is not None for item in usage_records)
    unknown = max(0, http_attempts - known)
    return {
        "known_usage_records": known,
        "unknown_http_attempts": unknown,
        "complete_for_attempts": unknown == 0,
    }


def paired(prepared, mirrors, output, *, arms=("control", "grounded"), concurrency=3, limit=None,
           max_attempts=2, max_output_tokens=DEFAULT_OUTPUT_TOKENS,
           max_http_attempts=None):
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if max_output_tokens < 1:
        raise ValueError("max_output_tokens must be >= 1")
    if max_http_attempts is not None and max_http_attempts < 1:
        raise ValueError("max_http_attempts must be >= 1")
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("Model credential required")
    identity = source_identity()
    if identity["working_tree_dirty"]:
        raise ValueError("Paired evaluation requires a clean committed reviewer")
    tasks = prepared["records"][:limit] if limit else prepared["records"]
    resolved_http_attempts = max_http_attempts or max(
        1, len(tasks) * len(arms) * max_attempts * MAX_REVIEW_MODEL_CALLS * HTTP_ATTEMPTS_PER_CALL
    )
    protocol = {"benchmark_commit": prepared["benchmark_commit"], "model": settings.review_model,
                "reviewer": identity, "source_token_budget": 12000,
                "repository_token_budget": 6000, "max_output_tokens_per_call": max_output_tokens,
                "max_http_attempts_per_run": resolved_http_attempts,
                "http_attempt_semantics": "HTTP attempts started; billing may differ",
                "read_timeout_seconds": 180, "total_timeout_per_call_seconds": 300,
                "reasoning_effort": "low", "temperature_requested": .1, "max_suggestions": 20, "control_min_confidence": .65,
                "grounded_acceptance": "confirmed causal chain plus validated source citations; confidence is reported, not a calibrated probability",
                "guidance": True, "concurrency": concurrency, "max_attempts_per_task": max_attempts,
                "discovery_max_calls": 3, "verification_max_calls": 2,
                "note": "Same per-call settings and maximum source budget; grounded uses more calls. Report cost and latency, not equal-compute claims."}
    output.mkdir(parents=True, exist_ok=True)
    old_protocol = output / "protocol.json"
    if old_protocol.exists() and json.loads(old_protocol.read_text()) != protocol:
        raise ValueError("Refusing mixed source/model/budget resumption")
    write_json(old_protocol, protocol)
    stop = Event()
    records = {arm: {} for arm in arms}
    prior_budget_snapshots = []
    for arm in arms:
        prior = output / arm / "run.json"
        if prior.exists():
            prior_run = json.loads(prior.read_text())
            if prior_run.get("protocol") != protocol:
                raise ValueError("Refusing mixed source/model/budget resumption")
            records[arm] = {r["case_id"]: r for r in prior_run["records"]}
            snapshot = prior_run.get("run_http_attempt_budget")
            if snapshot:
                prior_budget_snapshots.append(snapshot)
    for arm in {"control", "grounded"} - set(arms):
        prior = output / arm / "run.json"
        if prior.exists():
            prior_run = json.loads(prior.read_text())
            if prior_run.get("protocol") != protocol:
                raise ValueError("Refusing mixed source/model/budget resumption")
            snapshot = prior_run.get("run_http_attempt_budget")
            if snapshot:
                prior_budget_snapshots.append(snapshot)
    # Each arm checkpoint contains the same shared counter as of that write.
    # Restore the greatest known snapshot on ordinary resume; a hard process kill
    # between a send and its next checkpoint remains inherently unobservable.
    request_budget = RunRequestBudget(
        resolved_http_attempts,
        stop,
        attempted=max((s.get("attempted", 0) for s in prior_budget_snapshots), default=0),
        rejected=max((s.get("rejected_before_send", 0) for s in prior_budget_snapshots), default=0),
        stopped=max((s.get("stopped_before_send", 0) for s in prior_budget_snapshots), default=0),
    )

    def attempt(task, arm):
        prior = records[arm].get(task["case_id"])
        if prior and prior["status"] == "success":
            return prior
        record = {"case_id": task["case_id"], "arm": arm, "status": "not_run", "attempts": []}
        if task["status"] != "ready" or stop.is_set():
            if task["status"] != "ready":
                record["reason"] = "input_not_ready"
            elif request_budget.exhausted:
                record["reason"] = "run_http_attempt_budget_exhausted"
            elif request_budget.stop_reason:
                record["reason"] = "run_stopped_after_fatal_provider_error"
            else:
                record["reason"] = "fatal_provider_error"
            return record
        work = Path(task["input_dir"])
        diff = (work / "diff.patch").read_text()
        if hashlib.sha256(diff.encode()).hexdigest() != task["diff_sha256"]:
            raise ValueError("Prepared diff changed")
        meta = task["metadata"]
        root = mirrors / meta["nwo"].replace("/", "_")
        for index in range(max_attempts):
            if stop.is_set():
                break
            provider = OpenAICompatibleProvider(
                settings.openai_api_key,
                settings.review_model,
                settings.openai_base_url,
                timeout=180,
                stream=True,
                total_timeout=300,
                max_output_tokens=max_output_tokens,
                reasoning_effort="low",
                request_attempt_hook=request_budget.begin_attempt,
            )
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
                error_kind = measured.calls[-1].get("error_kind") if measured.calls else None
                record.update(
                    status="failed",
                    error=type(exc).__name__,
                    error_kind=error_kind,
                    failure=(
                        _safe_failure_record(exc)
                        if isinstance(exc, EvaluationFailure)
                        else None
                    ),
                )
                if error_kind == "request_budget_exhausted":
                    record["reason"] = "run_http_attempt_budget_exhausted"
                if error_kind == "run_stopped":
                    record["reason"] = "run_stopped_after_fatal_provider_error"
                if (isinstance(exc, EvaluationFailure) and exc.fatal) or any(c.get("http_status") in FATAL_HTTP_STATUSES for c in measured.calls):
                    request_budget.stop_after_fatal_error()
            finally:
                record["attempts"].append({
                    "attempt": index + 1,
                    "seconds": time.monotonic() - started,
                    "calls": measured.calls,
                    "http_attempts": provider.http_attempts,
                    "usage": provider.usage_records,
                    "usage_status": _usage_status(
                        provider.usage_records, provider.http_attempts
                    ),
                })
                provider.close()
            if record["status"] == "success" or stop.is_set():
                break
        return record

    def checkpoint(arm):
        write_json(output / arm / "run.json", {**identity, "benchmark_commit": prepared["benchmark_commit"],
                   "model": settings.review_model, "protocol": protocol,
                   "run_http_attempt_budget": request_budget.snapshot(),
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
    parser.add_argument("--max-attempts", type=_positive_int, default=2,
                        help="Attempts per task and arm; 1 disables retry (default: 2)")
    parser.add_argument(
        "--max-output-tokens",
        type=_positive_int,
        default=DEFAULT_OUTPUT_TOKENS,
        help=f"Maximum generated tokens per model request (default: {DEFAULT_OUTPUT_TOKENS})",
    )
    parser.add_argument("--max-http-attempts", type=_positive_int,
                        help="Shared hard cap across all tasks, arms, retries, and HTTP fallbacks")
    args = parser.parse_args()
    result = paired(json.loads(args.prepared.read_text()), args.mirrors, args.output,
                    arms=args.arms, concurrency=args.concurrency, limit=args.limit,
                    max_attempts=args.max_attempts, max_output_tokens=args.max_output_tokens,
                    max_http_attempts=args.max_http_attempts)
    if any(r["status"] != "success" for arm in result.values() for r in arm.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
