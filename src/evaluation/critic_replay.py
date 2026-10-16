"""Post-hoc Critic ablation on fixed baseline suggestions; no re-generation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.evaluation.corpus import write_json
from src.evaluation.reviewbench import parse_diff
from src.evaluation.runtime import FATAL_HTTP_STATUSES, ObservedProvider, source_identity
from src.models import ReviewSuggestion
from src.reviewer.critic import apply_critic, critique_batch, select_critic_candidates
from src.reviewer.provider import OpenAICompatibleProvider
from src.utils.config import get_settings


def replay(prepared: dict, baseline: Path, output: Path, provider) -> dict:
    if output.exists():
        raise ValueError("Use a new Critic replay output directory")
    original = json.loads((baseline / "run.json").read_text())
    if original["benchmark_commit"] != prepared["benchmark_commit"]:
        raise ValueError("Benchmark revision changed")
    by_id = {r["case_id"]: r for r in original["records"]}
    identity = source_identity()
    records = []
    stopped = False
    for task in prepared["records"]:
        record = {"case_id": task["case_id"], "status": "not_run"}
        prior = by_id.get(task["case_id"])
        if stopped or not prior or prior["status"] != "success":
            record["reason"] = "batch_stopped" if stopped else "baseline_not_successful"
        else:
            old_output = json.loads(
                (baseline / "candidates" / (task["case_id"] + ".json")).read_text()
            )
            old_audit = json.loads((baseline / "audits" / (task["case_id"] + ".json")).read_text())
            suggestions = [
                ReviewSuggestion.model_validate(s) for s in old_audit["report"]["suggestions"]
            ]
            if len(suggestions) != len(old_output["findings"]):
                raise ValueError("Baseline suggestion/finding counts differ")
            diff = (Path(task["input_dir"]) / "diff.patch").read_text()
            digest = hashlib.sha256(diff.encode()).hexdigest()
            if digest != task["diff_sha256"] or digest != old_audit["diff_sha256"]:
                raise ValueError("Baseline or prepared diff changed")
            files = parse_diff(diff)
            observed = ObservedProvider(provider)
            usage_start = len(getattr(provider, "usage_records", []))
            candidates = select_critic_candidates(suggestions)
            batch = critique_batch(observed, candidates, files)
            kept, decisions = apply_critic(suggestions, candidates, batch)
            if observed.errors or any(d["decision"] == "unverified" for d in decisions):
                record.update(status="failed", reason="critic_unverified")
                stopped = bool(
                    observed.errors and observed.errors[-1].status_code in FATAL_HTTP_STATUSES
                )
            else:
                # Critic decisions are indexed, including duplicate identities.
                output_findings = retained_findings(suggestions, old_output["findings"], decisions)
                write_json(
                    output / "candidates" / (task["case_id"] + ".json"),
                    {**old_output, "findings": output_findings},
                )
                write_json(
                    output / "audits" / (task["case_id"] + ".json"),
                    {
                        **identity,
                        "baseline_source": str(baseline),
                        "decisions": decisions,
                        "request_count": observed.calls,
                        "usage_records": getattr(provider, "usage_records", [])[usage_start:],
                    },
                )
                record.update(
                    status="success", before_count=len(suggestions), after_count=len(kept)
                )
        records.append(record)
        result = {
            **identity,
            "benchmark_commit": prepared["benchmark_commit"],
            "protocol": "fixed_baseline_suggestions_critic_replay",
            "quality_status": "unadjudicated",
            "records": records,
        }
        write_json(output / "run.json", result)
        print(task["case_id"] + ": " + record["status"], flush=True)
    return result


def retained_findings(suggestions, findings, decisions):
    """Preserve positional identity and exact original finding text."""
    if len(suggestions) != len(findings):
        raise ValueError("Baseline suggestion/finding counts differ")
    removed = {d["suggestion_index"] for d in decisions
               if d["decision"] in {"dismissed", "uncertain", "unverified"}}
    return [finding for index, finding in enumerate(findings) if index not in removed]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared", type=Path)
    parser.add_argument("baseline", type=Path)
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
        total_timeout=300,
        max_output_tokens=32768,
    ) as provider:
        result = replay(json.loads(args.prepared.read_text()), args.baseline, args.output, provider)
    if any(r["status"] != "success" for r in result["records"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
