"""Create a reproducible structural evaluation from a local Git diff.

This command deliberately makes no model call.  It records the boundary that
Phase 1 can prove locally: raw rules remain auditable, but are absent from the
main Agent context.  Semantic precision needs a configured provider and human
labels, so the output calls that out instead of inventing a quality claim.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

from src.analyzer.context_builder import build_review_context
from src.analyzer.risk_rules import collect_signals
from src.models import ChangedFile, FileStatus, PullRequest
from src.reviewer.engine import build_rule_only_report

STATUS_MAP = {
    "A": FileStatus.ADDED,
    "M": FileStatus.MODIFIED,
    "D": FileStatus.REMOVED,
    "R": FileStatus.RENAMED,
}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def _changed_files(base: str, head: str) -> list[ChangedFile]:
    revision = f"{base}...{head}"
    numstat: dict[str, tuple[int, int]] = {}
    for row in _git("diff", "--numstat", "--find-renames", revision).splitlines():
        additions, deletions, filename = row.split("\t", maxsplit=2)
        numstat[filename] = (
            int(additions) if additions.isdigit() else 0,
            int(deletions) if deletions.isdigit() else 0,
        )

    files: list[ChangedFile] = []
    for row in _git("diff", "--name-status", "--find-renames", revision).splitlines():
        fields = row.split("\t")
        code = fields[0][0]
        previous_filename = fields[1] if code == "R" else None
        filename = fields[-1]
        additions, deletions = numstat.get(filename, (0, 0))
        patch = _git("diff", "--unified=3", revision, "--", filename)
        files.append(
            ChangedFile(
                filename=filename,
                previous_filename=previous_filename,
                status=STATUS_MAP.get(code, FileStatus.UNKNOWN),
                additions=additions,
                deletions=deletions,
                changes=additions + deletions,
                patch=patch or None,
            )
        )
    return files


def evaluate(base: str, head: str) -> dict[str, object]:
    """Evaluate a local range without invoking a provider."""
    files = _changed_files(base, head)
    pr = PullRequest(
        repo="local/ai-pr-review",
        number=36,
        title=f"Local diff {base}...{head}",
        base_ref=base,
        head_ref=head,
    )
    signals = collect_signals(files)
    legacy_context = build_review_context(
        pr, files, signals, include_rule_findings=True, prioritize_rule_files=True,
        include_context_pack=False, allow_remote_file_fetch=False,
    )
    current_context = build_review_context(
        pr, files, include_context_pack=False, allow_remote_file_fetch=False
    )
    rule_only = build_rule_only_report(pr, files, signals)
    signal_by_rule = Counter(signal.rule_id for signal in signals)

    return {
        "scope": {"base": base, "head": head, "file_count": len(files)},
        "provider_used": False,
        "quality_claim": "structural_only",
        "next_required_data": ["provider_verification", "human_labels"],
        "signals_by_rule": dict(sorted(signal_by_rule.items())),
        "phase_1_comparison": {
            "raw_signal_count": len(signals),
            "legacy_main_prompt_signal_count": len(signals),
            "current_main_prompt_signal_count": 0,
            "legacy_context_token_count": legacy_context.token_count,
            "current_context_token_count": current_context.token_count,
            "context_token_delta": current_context.token_count - legacy_context.token_count,
            "legacy_rule_section_present": "## Rule findings" in legacy_context.text,
            "current_rule_section_present": "## Rule findings" in current_context.text,
        },
        "rule_only_fallback": {
            "visible_rule_suggestion_count": len(rule_only.suggestions),
            "unverified_signal_count": len(rule_only.unverified_signals),
            "metrics": rule_only.metrics,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument(
        "--output", default="reports/evaluation/local-diff-structural.json",
        help="JSON output path, relative to the repository root.",
    )
    args = parser.parse_args()
    result = evaluate(args.base, args.head)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
