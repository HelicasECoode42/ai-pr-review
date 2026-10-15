"""Patch context must account for every file it does not fully include."""

from __future__ import annotations

from src.analyzer.context_builder import (
    _fetch_repo_file_text,
    _is_frozen_ref,
    build_review_context,
    partial_inclusion_counts,
    partial_inclusion_warning,
)
from src.models import ChangedFile, FileStatus, PullRequest, StepStatus

OVERSIZED = "@@ -1,1 +1,1 @@\n-old\n+" + ("abcdefgh! " * 400) + "\n"


def frozen_pr(head_sha: str | None = None, base_sha: str | None = None) -> PullRequest:
    return PullRequest(
        repo="owner/repo", number=1, title="t", head_sha=head_sha, base_sha=base_sha
    )


def test_only_immutable_commits_are_read_as_review_evidence():
    assert _is_frozen_ref("a" * 40)
    assert not _is_frozen_ref("main")
    assert not _is_frozen_ref("feature/branch")
    assert not _is_frozen_ref("a" * 39)
    assert not _is_frozen_ref(None)
    # A branch name is refused before any HTTP client is created.
    assert _fetch_repo_file_text("owner/repo", "a.py", "main") is None


def test_file_without_usable_patch_is_recorded_instead_of_dropped():
    files = [ChangedFile(filename="src/a.py", patch="not a unified diff\n")]
    ctx = build_review_context(frozen_pr(), files, max_patch_tokens=1000)
    assert ctx.status is StepStatus.PARTIAL
    assert ("src/a.py", "patch_without_hunks") in ctx.skipped_files
    assert "patch_without_hunks" in {o.reason for o in ctx.omissions}


def test_missing_patch_without_frozen_sha_records_why_nothing_was_read():
    files = [ChangedFile(filename="src/new.py", status=FileStatus.ADDED, patch=None)]
    ctx = build_review_context(frozen_pr(), files, max_patch_tokens=1000)
    assert ("src/new.py", "no_frozen_ref") in ctx.skipped_files
    assert "no_frozen_ref" in {o.reason for o in ctx.omissions}


def test_clipped_file_is_partial_omission_not_a_fully_skipped_file():
    files = [ChangedFile(filename="src/a.py", patch=OVERSIZED)]
    ctx = build_review_context(frozen_pr(), files, max_patch_tokens=200)
    assert ctx.truncated is True
    assert all(path != "src/a.py" for path, _ in ctx.skipped_files)
    clipped = [o for o in ctx.omissions if o.reason == "patch_clipped"]
    assert len(clipped) == 1 and clipped[0].path == "src/a.py"
    assert clipped[0].detail is not None


def test_context_budget_ledger_names_its_scope_and_respects_its_limits():
    files = [ChangedFile(filename="src/a.py", patch="@@ -1,1 +1,1 @@\n-old\n+new\n")]
    ctx = build_review_context(frozen_pr(), files, max_patch_tokens=1000)
    assert ctx.budgets.scope == "build_review_context"
    names = {line.name for line in ctx.budgets.lines}
    assert {"context_pack", "patch_context", "per_file_patch"} <= names
    assert ctx.budgets.over_limit == ()


def test_partial_inclusion_is_reported_as_one_warning_line():
    files = [ChangedFile(filename="src/a.py", patch=OVERSIZED)]
    ctx = build_review_context(frozen_pr(), files, max_patch_tokens=200)
    assert partial_inclusion_counts(ctx) == {"patch_clipped": 1}
    warning = partial_inclusion_warning(ctx)
    assert warning is not None
    assert "patch_clipped=1" in warning


def test_complete_context_has_no_partial_inclusion_warning():
    files = [ChangedFile(filename="src/a.py", patch="@@ -1,1 +1,1 @@\n-old\n+new\n")]
    ctx = build_review_context(frozen_pr(), files, max_patch_tokens=1000)
    assert partial_inclusion_counts(ctx) == {}
    assert partial_inclusion_warning(ctx) is None


def test_full_file_substitution_is_refused_without_a_frozen_head_sha():
    # More context lines than added lines looks like a truncated hunk, but no
    # frozen SHA means no read: the diff stays and nothing is claimed.
    patch = "@@ -1,4 +1,4 @@\n ctx1\n ctx2\n ctx3\n+new\n"
    files = [ChangedFile(filename="src/a.py", patch=patch)]
    ctx = build_review_context(frozen_pr(), files, max_patch_tokens=1000)
    assert ctx.omissions == []
    assert "new" in ctx.text

