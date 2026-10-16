"""Chunk coverage accounting for the production patch context."""

from __future__ import annotations

import pytest

from src.analyzer.context_builder import _secret_adjacent_reason, build_review_context
from src.application.protocol import ChunkCoverage, ChunkCoverageSummary
from src.models import ChangedFile, FileStatus, PullRequest

BIG_LINE = "+" + ("abcdefgh! " * 40) + "\n"


def frozen_pr(head_sha: str | None = None) -> PullRequest:
    return PullRequest(
        repo="owner/repo", number=1, title="t", head_sha=head_sha, base_sha=None
    )


def multihunk(name: str, hunks: int) -> ChangedFile:
    parts = [
        f"@@ -{index * 20 + 1},1 +{index * 20 + 1},1 @@\n-old{index}\n{BIG_LINE}"
        for index in range(hunks)
    ]
    return ChangedFile(filename=name, status=FileStatus.MODIFIED, patch="".join(parts))


def test_every_changed_file_gets_exactly_one_coverage_entry():
    files = [
        ChangedFile(filename="src/ok.py", patch="@@ -1,1 +1,1 @@\n-old\n+new\n"),
        ChangedFile(filename="src/nopatch.py", patch="not a diff\n"),
        ChangedFile(filename=".env", patch=None),
    ]
    ctx = build_review_context(frozen_pr(), files, allow_remote_file_fetch=False)
    assert [entry.path for entry in ctx.coverage] == [f.filename for f in files]
    summary = ctx.coverage_summary
    assert summary.changed_files == 3
    assert summary.complete is True
    assert summary.unaccounted_files == ()
    assert {entry.reason for entry in ctx.coverage} == {
        None,
        "patch_without_hunks",
        "secret_adjacent_path_not_analyzed",
    }


def test_secret_adjacent_and_vendored_paths_never_enter_the_prompt():
    changed = "@@ -1,1 +1,1 @@\n-OLD_VALUE\n+NEW_VALUE_LEAK\n"
    files = [
        ChangedFile(filename=".env", patch=changed.replace("NEW_VALUE_LEAK", "KEY=new")),
        ChangedFile(filename="deploy/server.pem", patch=changed),
        ChangedFile(filename="node_modules/dep/index.js", patch=changed),
        ChangedFile(filename="src/keep.py", patch="@@ -1,1 +1,1 @@\n-old\n+kept\n"),
    ]
    ctx = build_review_context(frozen_pr(), files, allow_remote_file_fetch=False)
    assert "KEY=new" not in ctx.text
    assert "NEW_VALUE_LEAK" not in ctx.text
    assert "src/keep.py" in ctx.text and "kept" in ctx.text
    reasons = {entry.path: entry.reason for entry in ctx.coverage}
    assert reasons[".env"] == "secret_adjacent_path_not_analyzed"
    assert reasons["deploy/server.pem"] == "secret_adjacent_path_not_analyzed"
    assert reasons["node_modules/dep/index.js"] == "vendored_path_not_analyzed"
    assert reasons["src/keep.py"] is None
    # go.mod / requirements.txt style files are still reviewed on purpose.
    assert _secret_adjacent_reason("go.mod") is None
    assert _secret_adjacent_reason("requirements.txt") is None
    assert _secret_adjacent_reason("app/styles.css") is None


def test_missing_entry_is_reported_as_unaccounted_not_assumed_fine():
    summary = ChunkCoverageSummary.from_entries(
        ["a.py", "b.py"], (ChunkCoverage(path="a.py"),)
    )
    assert summary.complete is False
    assert summary.unaccounted_files == ("b.py",)


def test_unprocessed_entry_does_not_count_as_accounted():
    summary = ChunkCoverageSummary.from_entries(
        ["a.py"],
        (ChunkCoverage(path="a.py", status="excluded", reason="not_processed"),),
    )
    assert summary.complete is False
    assert summary.unaccounted_files == ("a.py",)


def test_hunk_buckets_pin_where_the_budget_boundary_falls():
    ctx = build_review_context(
        frozen_pr(),
        [multihunk("src/a.py", 4)],
        allow_remote_file_fetch=False,
        max_patch_tokens=128,
    )
    summary = ctx.coverage_summary
    assert summary.hunks_total == 4
    assert summary.hunks_complete + summary.hunks_partial + summary.hunks_omitted == 4
    # The first hunk alone exceeds the per-file cap, so nothing is claimed complete.
    assert summary.hunks_complete == 0
    assert summary.hunks_partial == 1
    assert summary.hunks_omitted == 3
    entry = ctx.coverage[0]
    assert entry.status == "partial"
    assert entry.reason == "patch_clipped"
    assert entry.mode == "diff"


def test_budget_exhausted_files_keep_their_hunks_in_the_denominator():
    files = [multihunk(f"src/f{index:02}.py", 1) for index in range(20)]
    ctx = build_review_context(
        frozen_pr(), files, allow_remote_file_fetch=False, max_patch_tokens=64
    )
    summary = ctx.coverage_summary
    # A shrinking budget must not shrink the denominator.
    assert summary.hunks_total == 20
    assert summary.hunks_complete + summary.hunks_partial + summary.hunks_omitted == 20
    dropped = [entry for entry in ctx.coverage if entry.reason == "patch_budget_exhausted"]
    assert dropped, "a 64-token budget over 20 files must exhaust the patch budget"
    assert all(entry.status == "excluded" and entry.hunks_total == 1 for entry in dropped)
    assert summary.hunks_omitted == sum(entry.hunks_omitted for entry in ctx.coverage)
    assert summary.files_excluded == sum(
        entry.status == "excluded" for entry in ctx.coverage
    )


def test_substituted_file_is_not_counted_as_diff_hunk_coverage(monkeypatch):
    monkeypatch.setattr(
        "src.analyzer.context_builder._fetch_repo_file_text",
        lambda repo, path, ref: "def f():\n    return 1\n",
    )
    files = [ChangedFile(filename="src/new.py", status=FileStatus.ADDED, patch=None)]
    ctx = build_review_context(
        frozen_pr(head_sha="a" * 40), files, allow_remote_file_fetch=True
    )
    assert ctx.coverage[0].mode == "substituted"
    assert ctx.coverage[0].status == "included"
    summary = ctx.coverage_summary
    assert summary.substituted_files == 1
    # Substituted files have unknown hunk coverage and are excluded from the counts.
    assert summary.hunks_total == 0
    assert "return 1" in ctx.text


def test_coverage_accounting_is_deterministic():
    def build():
        files = [multihunk("src/a.py", 2), multihunk("src/b.py", 3)]
        return build_review_context(
            frozen_pr(), files, allow_remote_file_fetch=False, max_patch_tokens=256
        )

    first, second = build(), build()
    assert first.coverage_summary.model_dump() == second.coverage_summary.model_dump()
    assert [entry.model_dump() for entry in first.coverage] == [
        entry.model_dump() for entry in second.coverage
    ]


@pytest.mark.parametrize("budget", [64, 128, 512])
def test_hunk_counts_always_add_up(budget):
    files = [multihunk("src/a.py", 3), multihunk("src/b.py", 2)]
    ctx = build_review_context(
        frozen_pr(), files, allow_remote_file_fetch=False, max_patch_tokens=budget
    )
    summary = ctx.coverage_summary
    assert summary.hunks_total == 5
    assert summary.hunks_complete + summary.hunks_partial + summary.hunks_omitted == 5
