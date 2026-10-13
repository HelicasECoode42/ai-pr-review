"""ReviewBench mounted-input entrypoint. No GitHub API and no golden labels."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path, PurePosixPath

from src.analyzer.context_builder import build_review_context, truncate_to_token_budget
from src.analyzer.diff_parser import parse_file_hunks
from src.evaluation.runtime import EvaluationFailure, ObservedProvider
from src.models import ChangedFile, FileStatus, PullRequest, ReviewMeta
from src.reviewer.engine import review_with_ai
from src.reviewer.provider import OpenAICompatibleProvider
from src.utils.config import get_settings


def parse_diff(diff: str) -> list[ChangedFile]:
    """Split unified git patches, including quoted Unicode paths and renames."""
    files = []
    for block in re.split(r"(?m)(?=^diff --git )", diff):
        if not block.startswith("diff --git "):
            continue
        lines = block.splitlines()
        old = next((x[4:] for x in lines if x.startswith("--- ")), None)
        new = next((x[4:] for x in lines if x.startswith("+++ ")), None)

        def decode(value: str) -> str:
            if value.startswith('"'):
                # Git quotes non-ASCII paths as UTF-8 octal byte escapes.
                import ast

                value = ast.literal_eval("b" + value).decode("utf-8")
            return value

        if old is None or new is None:
            # Git omits ---/+++ for pure renames, mode-only and empty files.
            header = re.fullmatch(
                r'diff --git ("(?:\\.|[^"\\])*"|a/.*?) ("(?:\\.|[^"\\])*"|b/.*)', lines[0]
            )
            if header is None:
                raise ValueError("Invalid git diff header")
            old, new = header.groups()
            if any(x.startswith("new file mode ") for x in lines):
                old = "/dev/null"
            if any(x.startswith("deleted file mode ") for x in lines):
                new = "/dev/null"
            known_metadata = any(
                x.startswith(
                    (
                        "rename from ",
                        "old mode ",
                        "new file mode ",
                        "deleted file mode ",
                        "Binary files ",
                        "GIT binary patch",
                    )
                )
                for x in lines
            )
            if not known_metadata:
                raise ValueError("Incomplete diff headers")
        old, new = decode(old), decode(new)
        raw_path = old if new == "/dev/null" else new
        if not raw_path.startswith(("a/", "b/")):
            raise ValueError("Invalid git path prefix")
        path = raw_path[2:]
        if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise ValueError("Invalid repository-relative path")
        status = (
            FileStatus.REMOVED
            if new == "/dev/null"
            else FileStatus.ADDED
            if old == "/dev/null"
            else FileStatus.RENAMED
            if old[2:] != new[2:]
            else FileStatus.MODIFIED
        )
        file = ChangedFile(
            filename=path,
            status=status,
            patch=block,
            previous_filename=old[2:] if status == FileStatus.RENAMED else None,
        )
        hunks = parse_file_hunks(file)
        file.additions = sum(len(h.added_lines) for h in hunks)
        file.deletions = sum(len(h.removed_lines) for h in hunks)
        file.changes = file.additions + file.deletions
        files.append(file)
    if diff.strip() and not files:
        raise ValueError("Input is not a unified git diff")
    return files


def run_review(
    metadata: dict,
    diff: str,
    provider,
    *,
    evidence=True,
    critic=False,
    context_pack=False,
    project_root: Path | None = None,
    prompt_addendum="",
    min_confidence=0.65,
    model="unknown",
    two_stage=False,
    verify_signals=False,
) -> tuple[dict, dict]:
    files = parse_diff(diff)
    pr = PullRequest(
        repo=metadata["nwo"],
        number=int(metadata["pr_number"]),
        title=metadata["title"],
        body=metadata.get("body"),
        base_ref=metadata["base"],
        head_ref=metadata["head"],
        head_sha=metadata["head"],
    )
    # Do not accidentally inject AI PR Review's own guide into other repositories.
    context = build_review_context(
        pr, files, include_context_pack=False, allow_remote_file_fetch=False
    )
    if context_pack and project_root is not None:
        root = project_root.resolve()
        for relative in ("AGENTS.md", "docs/review-guide.md"):
            path = (root / relative).resolve()
            if path.is_relative_to(root) and path.is_file():
                text, _, _ = truncate_to_token_budget(path.read_text(), 500)
                context.text += "\n## Untrusted target project guidance\n" + text
        from src.analyzer.context_builder import count_tokens

        context.token_count = count_tokens(context.text)
    started = time.perf_counter()
    observed = ObservedProvider(provider)
    usage_start = len(getattr(provider, "usage_records", []))
    from src.analyzer.risk_rules import collect_signals

    report = review_with_ai(
        pr,
        files,
        collect_signals(files) if verify_signals else [],
        observed,
        max_suggestions=20,
        two_stage=two_stage,
        verify_rule_signals=verify_signals,
        min_confidence=min_confidence,
        enable_critic=critic,
        enable_evidence_gate=evidence,
        review_context=context,
        prompt_addendum=prompt_addendum,
        review_meta=ReviewMeta(reviewed_commit=metadata["head"]),
    )
    if observed.errors:
        raise EvaluationFailure(
            "provider",
            status_code=observed.errors[-1].status_code,
            error_kind=observed.errors[-1].kind,
        )
    if not report.used_ai or report.ai_failure_reason or report.execution_status != "success":
        raise EvaluationFailure("review_failed_or_degraded", outputs=observed.outputs)
    if critic and any(d.get("decision") == "unverified" for d in report.critic_decisions):
        raise EvaluationFailure("critic_degraded")
    agent = metadata.get("agent", "ai-pr-review")
    findings = [
        {
            "file": s.file_path,
            "start_line": s.line,
            "end_line": s.line,
            "message": f"{s.title}: {s.reason}\nScenario: {s.failure_scenario}\n{s.recommendation}",
            "producer": agent,
        }
        for s in report.suggestions
    ]
    if any(x["start_line"] is None or x["start_line"] < 1 for x in findings):
        raise ValueError("Benchmark findings require positive head line numbers")
    output = {
        "pr": {
            "repo": "https://github.com/" + pr.repo,
            "pr_number": pr.number,
            "base": metadata["base"],
            "head": metadata["head"],
        },
        "agent": agent,
        "findings": findings,
    }
    audit = {
        "model": model,
        "latency_seconds": time.perf_counter() - started,
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "strategy": {
            "evidence": evidence,
            "critic": critic,
            "context_pack": context_pack,
            "two_stage": two_stage,
            "verify_signals": verify_signals,
            "min_confidence": min_confidence,
            "prompt_sha256": hashlib.sha256(prompt_addendum.encode()).hexdigest(),
        },
        "report": report.model_dump(mode="json"),
        "input_coverage": {
            "binary_files": [
                f.filename
                for f in files
                if f.patch and re.search(r"(?m)^(Binary files |GIT binary patch)", f.patch)
            ],
            "metadata_only_files": [f.filename for f in files if not parse_file_hunks(f)],
            "context_truncated": report.context_truncated,
            "skipped_files": [f.model_dump() for f in report.skipped_context_files],
        },
        "request_count": observed.calls,
        "usage_records": getattr(provider, "usage_records", [])[usage_start:],
        "token_usage": _usage_total(getattr(provider, "usage_records", [])[usage_start:]),
        "cost_usd": None,
    }
    return output, audit


def _usage_total(records: list[dict]) -> dict | None:
    if not records or any(not isinstance(r.get("usage"), dict) for r in records):
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    if any(type(r["usage"].get(k)) is not int for r in records for k in keys):
        return None
    return {k: sum(r["usage"][k] for r in records) for k in keys}


def main() -> None:
    output_path = Path(os.environ["RB_OUT"])
    # Remove stale artifacts before any input or provider failure.
    output_path.unlink(missing_ok=True)
    audit_path = output_path.with_suffix(".audit")
    audit_path.unlink(missing_ok=True)
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    metadata = json.loads(Path(os.environ["RB_PR_JSON"]).read_text())
    for key, env in (
        ("nwo", "RB_NWO"),
        ("pr_number", "RB_PR_NUMBER"),
        ("base", "RB_BASE"),
        ("head", "RB_HEAD"),
    ):
        if str(metadata[key]) != os.environ[env]:
            raise ValueError(f"Metadata mismatch: {key}")
    metadata["agent"] = os.environ.get("RB_AGENT", "ai-pr-review")
    model = os.environ.get("RB_CONFIG_MODEL", settings.review_model)

    def flag(name: str, default: str) -> bool:
        value = os.environ.get("RB_CONFIG_" + name, default).lower()
        if value not in ("true", "false"):
            raise ValueError(f"Invalid boolean configuration: {name}")
        return value == "true"

    evidence, critic, pack = (
        flag("EVIDENCE", "true"),
        flag("CRITIC", "false"),
        flag("CONTEXT_PACK", "false"),
    )
    print(f"model={model} evidence={evidence} critic={critic} context_pack={pack}")
    with OpenAICompatibleProvider(
        settings.openai_api_key,
        model,
        os.environ.get("RB_MODEL_BASE_URL", settings.openai_base_url),
        settings.request_timeout_seconds,
    ) as provider:
        output, audit = run_review(
            metadata,
            Path(os.environ["RB_DIFF"]).read_text(),
            provider,
            evidence=evidence,
            critic=critic,
            context_pack=pack,
            project_root=Path("/work/repo"),
            model=model,
            two_stage=flag("TWO_STAGE", "false"),
            verify_signals=flag("VERIFY_SIGNALS", "false"),
        )
    audit["reviewer_commit"] = (
        os.environ.get("REVIEWER_COMMIT")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2) + "\n")
    temporary = output_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(output, indent=2) + "\n")
    temporary.replace(output_path)


if __name__ == "__main__":
    main()
