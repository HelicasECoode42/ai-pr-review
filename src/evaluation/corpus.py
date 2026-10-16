"""Prepare and run all official ReviewBench tasks without exposing golden labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from src.evaluation.reviewbench import parse_diff, run_review
from src.evaluation.runtime import EvaluationFailure, source_identity
from src.reviewer.provider import OpenAICompatibleProvider
from src.utils.config import get_settings

SHA = re.compile(r"[0-9a-f]{40}")
NWO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE, timeout=180
    )


def case_id(task: dict) -> str:
    if not NWO.fullmatch(task["nwo"]) or any(not SHA.fullmatch(task[k]) for k in ("base", "head")):
        raise ValueError("Invalid official repository or SHA")
    return f"{task['nwo'].replace('/', '_')}_{int(task['pr_number'])}-{task['head'][:8]}"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def prepare(benchmark: Path, cache: Path, output: Path, *, fetch=False) -> dict:
    # Read only the task manifest, never golden/, findings.json or later fixes.
    tasks = json.loads((benchmark / "corpus/test/test.json").read_text())
    records = []
    benchmark_commit = git(benchmark, "rev-parse", "HEAD").strip()
    for task in tasks:
        identity = case_id(task)
        root = cache / task["nwo"].replace("/", "_")
        work = output / "inputs" / identity
        record = {"case_id": identity, "metadata": task, "status": "failed"}
        try:
            if not (root / ".git").exists():
                if not fetch:
                    raise FileNotFoundError("Mirror not cached")
                root.mkdir(parents=True, exist_ok=True)
                git(root, "init")
                git(
                    root,
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/review-bench/" + task["nwo"].replace("/", "_"),
                )
                git(root, "config", "remote.origin.promisor", "true")
                git(root, "config", "remote.origin.partialclonefilter", "blob:none")
            if fetch:
                git(
                    root,
                    "fetch",
                    "--filter=blob:none",
                    "--depth=50",
                    "--no-tags",
                    "origin",
                    task["base"],
                    task["head"],
                )
            if fetch:
                try:
                    git(root, "merge-base", task["base"], task["head"])
                except subprocess.CalledProcessError:
                    git(
                        root,
                        "fetch",
                        "--filter=blob:none",
                        "--unshallow",
                        "--no-tags",
                        "origin",
                        task["base"],
                        task["head"],
                    )
            diff = git(
                root, "diff", "--no-ext-diff", "--no-textconv", task["base"] + "..." + task["head"]
            )
            work.mkdir(parents=True, exist_ok=True)
            (work / "diff.patch").write_text(diff)
            write_json(work / "pr.json", task)
            # Extract guidance from immutable HEAD, not the reviewer's repository.
            for relative in ("AGENTS.md", "docs/review-guide.md"):
                try:
                    guidance = git(root, "show", task["head"] + ":" + relative)
                except subprocess.CalledProcessError:
                    continue
                target = work / "project" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(guidance)
            record.update(
                status="ready",
                input_dir=str(work.resolve()),
                diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
            )
            try:
                files = parse_diff(diff)
                record["file_count"] = len(files)
                record["added_lines"] = sum(f.additions for f in files)
            except ValueError:
                record.update(status="unsupported", error="UnsupportedPatch")
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            # Persist failures per task; no silent exclusion and no semantic score.
            record["error"] = type(exc).__name__
            if isinstance(exc, subprocess.CalledProcessError):
                record["git_exit_code"] = exc.returncode
                record["git_error"] = (exc.stderr or "")[-1000:]
        records.append(record)
        result = {
            "scope": "official_test_set_input_preparation",
            "benchmark_commit": benchmark_commit,
            "task_count": len(tasks),
            "provider_used": False,
            "quality_status": "not_evaluated",
            "records": records,
        }
        write_json(output / "prepared.json", result)
        print(identity + ": " + record["status"], flush=True)
    return result


def evaluate(
    prepared: dict,
    output: Path,
    provider,
    model: str,
    *,
    critic=False,
    two_stage=False,
    verify_signals=False,
) -> dict:
    if (output / "run.json").exists() or any((output / "candidates").glob("*.json")):
        raise ValueError("Use a new run output directory; stale candidates must not be reused")
    records = []
    stopped = False
    identity = source_identity()
    for task in prepared["records"]:
        record = {"case_id": task["case_id"], "status": "not_run"}
        if stopped:
            record["reason"] = "fatal_provider_error"
        elif task["status"] != "ready":
            record["reason"] = "input_" + task["status"]
        else:
            work = Path(task["input_dir"])
            diff = (work / "diff.patch").read_text()
            if hashlib.sha256(diff.encode()).hexdigest() != task["diff_sha256"]:
                raise ValueError("Prepared input changed")
            try:
                findings, audit = run_review(
                    task["metadata"],
                    diff,
                    provider,
                    model=model,
                    critic=critic,
                    two_stage=two_stage,
                    verify_signals=verify_signals,
                    context_pack=True,
                    project_root=work / "project",
                )
                audit.update(identity)
                write_json(output / "candidates" / (task["case_id"] + ".json"), findings)
                write_json(output / "audits" / (task["case_id"] + ".json"), audit)
                record.update(status="success", request_count=audit["request_count"])
            except Exception as exc:
                record.update(
                    status="failed",
                    error=type(exc).__name__,
                    failure=exc.record() if isinstance(exc, EvaluationFailure) else None,
                )
                stopped = isinstance(exc, EvaluationFailure) and exc.fatal
        records.append(record)
        result = {
            **identity,
            "benchmark_commit": prepared["benchmark_commit"],
            "model": model,
            "quality_status": "unadjudicated",
            "records": records,
        }
        write_json(output / "run.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    preparation = sub.add_parser("prepare")
    preparation.add_argument("--benchmark", type=Path, required=True)
    preparation.add_argument("--cache", type=Path, required=True)
    preparation.add_argument("--output", type=Path, required=True)
    preparation.add_argument("--fetch", action="store_true")
    execution = sub.add_parser("run")
    execution.add_argument("prepared", type=Path)
    execution.add_argument("--output", type=Path, required=True)
    execution.add_argument("--critic", action="store_true")
    execution.add_argument("--two-stage", action="store_true")
    execution.add_argument("--verify-signals", action="store_true")
    judging = sub.add_parser("judge")
    judging.add_argument("--benchmark", type=Path, required=True)
    judging.add_argument("--candidates", type=Path, required=True)
    judging.add_argument("--provider", required=True)
    judging.add_argument("--model", required=True)
    judging.add_argument("--output", type=Path, required=True)
    judging.add_argument("--repo-cache", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.benchmark, args.cache, args.output, fetch=args.fetch)
        print(dict(Counter(r["status"] for r in result["records"])))
        if any(r["status"] != "ready" for r in result["records"]):
            raise SystemExit(1)
    elif args.command == "judge":
        # Official scoring, not an approximation of its matching/classification.
        # Judge credentials are read only by the official SDK environment.
        command = [
            "npm",
            "run",
            "judge",
            "--",
            "--candidate",
            str(args.candidates.resolve()),
            "--golden",
            str((args.benchmark / "golden").resolve()),
            "--manifest",
            str((args.benchmark / "corpus/manifest.json").resolve()),
            "--provider",
            args.provider,
            "--model",
            args.model,
            "--output",
            str(args.output.resolve()),
            "--repo-dir",
            str(args.repo_cache.resolve()),
        ]
        write_json(
            args.output.with_suffix(".provenance.json"),
            {
                "benchmark_commit": git(args.benchmark, "rev-parse", "HEAD").strip(),
                "judge_provider": args.provider,
                "judge_model": args.model,
                "scoring_protocol": "official_local_reviewbench",
                "status": "started_not_complete",
            },
        )
        subprocess.run(command, cwd=args.benchmark, check=True)
        provenance = args.output.with_suffix(".provenance.json")
        metadata = json.loads(provenance.read_text())
        metadata["status"] = "complete"
        write_json(provenance, metadata)
    else:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY required")
        with OpenAICompatibleProvider(
            settings.openai_api_key,
            settings.review_model,
            settings.openai_base_url,
            settings.request_timeout_seconds,
        ) as provider:
            result = evaluate(
                json.loads(args.prepared.read_text()),
                args.output,
                provider,
                settings.review_model,
                critic=args.critic,
                two_stage=args.two_stage,
                verify_signals=args.verify_signals,
            )
        if any(r["status"] != "success" for r in result["records"]):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
