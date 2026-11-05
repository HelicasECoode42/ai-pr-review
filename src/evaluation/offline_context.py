"""Shared offline input and evidence preparation for evaluation CLIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.analyzer.context_builder import ReviewContext, build_review_context
from src.analyzer.repository_context import EvidenceBundle, GitSnapshot
from src.evaluation.reviewbench import parse_diff
from src.models import PullRequest

PATCH_TOKENS = 12_000
EVIDENCE_TOKENS = 6_000


def prepare_offline_context(
    record: dict[str, Any],
    mirrors: Path,
    *,
    max_documentation_tokens: int = 0,
) -> tuple[ReviewContext, EvidenceBundle, list[dict[str, Any]]]:
    """Build the local review context and snapshot evidence for one prepared case."""
    metadata = record["metadata"]
    input_dir = Path(record["input_dir"])
    files = parse_diff((input_dir / "diff.patch").read_text(encoding="utf-8"))
    pr = PullRequest(
        repo=metadata["nwo"],
        number=int(metadata["pr_number"]),
        title=metadata.get("title", ""),
        body=metadata.get("body"),
        base_ref=metadata.get("base"),
        base_sha=metadata["base"],
        head_ref=metadata.get("head"),
        head_sha=metadata["head"],
    )
    context = build_review_context(
        pr,
        files,
        include_context_pack=False,
        allow_remote_file_fetch=False,
        max_patch_tokens=PATCH_TOKENS,
        max_documentation_tokens=max_documentation_tokens,
    )
    mirror = mirrors / str(metadata["nwo"]).replace("/", "_")
    snapshot = GitSnapshot(mirror, metadata["base"], metadata["head"])
    bundle = EvidenceBundle(snapshot, budget=EVIDENCE_TOKENS)
    bundle.follow(files)
    evidence_refs = [
        {
            "path": item["path"],
            "side": item["side"],
            "start_line": item["start_line"],
            "end_line": item["end_line"],
        }
        for item in bundle.excerpts
    ]
    return context, bundle, evidence_refs
