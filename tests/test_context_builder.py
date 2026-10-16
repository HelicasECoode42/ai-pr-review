from src.analyzer.context_builder import (
    build_review_context,
    count_tokens,
    truncate_to_token_budget,
)
from src.models import ChangedFile, FileStatus, PullRequest


def test_build_review_context_skips_lockfile_patch() -> None:
    files = [
        ChangedFile(
            filename="uv.lock",
            status=FileStatus.MODIFIED,
            additions=1,
            patch="@@ -1,1 +1,1 @@\n-old\n+new-lock-content",
        ),
        ChangedFile(
            filename="src/app.py",
            status=FileStatus.MODIFIED,
            additions=1,
            patch="@@ -1,1 +1,2 @@\n ctx\n+print('ok')",
        ),
    ]

    ctx = build_review_context(
        PullRequest(repo="test/repo", number=1, title="Update deps"),
        files,
        [],
    )

    assert "new-lock-content" not in ctx.text
    assert "lockfile(s) excluded from patch context" in ctx.text
    assert "print('ok')" in ctx.text


def test_build_review_context_keeps_project_config_patch() -> None:
    files = [
        ChangedFile(
            filename="pyproject.toml",
            status=FileStatus.MODIFIED,
            additions=1,
            patch='@@ -1,1 +1,2 @@\n [project]\n+name = "ai-pr-review"',
        )
    ]

    ctx = build_review_context(
        PullRequest(repo="test/repo", number=1, title="Update config"),
        files,
        [],
    )

    assert 'name = "ai-pr-review"' in ctx.text


def test_truncate_to_token_budget_is_language_independent_and_bounded() -> None:
    text = "鉴权逻辑发生变化，需要检查权限边界。" * 20
    fitted, consumed, truncated = truncate_to_token_budget(text, 24)

    assert truncated is True
    assert consumed <= 24
    assert count_tokens(fitted) <= 24
    assert "[truncated]" in fitted


def test_build_review_context_reports_token_usage_and_truncation() -> None:
    files = [
        ChangedFile(
            filename="src/auth.py",
            status=FileStatus.MODIFIED,
            additions=100,
            patch="@@ -1,1 +1,100 @@\n" + "\n".join(
                f"+check_permission(user, resource_{i})" for i in range(100)
            ),
        )
    ]

    ctx = build_review_context(
        PullRequest(repo="test/repo", number=2, title="Change permissions"),
        files,
        [],
        max_patch_tokens=80,
    )

    assert ctx.truncated is True
    assert ctx.patch_token_budget == 80
    assert ctx.token_count == count_tokens(ctx.text)
    assert "[truncated]" in ctx.text


def test_budget_omissions_and_evaluation_data_are_explicit() -> None:
    files = [
        ChangedFile(filename="docs/evaluation/run/input.json", status=FileStatus.ADDED,
                    additions=1000, patch='@@ -0,0 +1 @@\n+QUOTED_EXTERNAL_CODE'),
        ChangedFile(filename="src/first.py", status=FileStatus.ADDED,
                    additions=100, patch='@@ -0,0 +1 @@\n+' + 'actual_code ' * 100),
        ChangedFile(filename="src/second.py", status=FileStatus.ADDED,
                    additions=1, patch='@@ -0,0 +1 @@\n+second_code'),
    ]
    ctx = build_review_context(PullRequest(repo="test/repo", number=1, title="Budget"), files, [],
                               max_patch_tokens=1, allow_remote_file_fetch=False,
                               include_context_pack=False)
    assert "QUOTED_EXTERNAL_CODE" not in ctx.text
    assert ctx.truncated
    assert ("src/second.py", "patch_budget_exhausted") in ctx.skipped_files
    assert any(path.endswith("input.json") for path, _ in ctx.skipped_files)


def test_a_diff_within_budget_keeps_every_hunk() -> None:
    """The gate must never drop a hunk from a diff that fits."""
    files = [
        ChangedFile(
            filename="src/small.py",
            status=FileStatus.MODIFIED,
            additions=2,
            patch="@@ -1,2 +1,3 @@\n keep = 1\n-old = 1\n+new = 1\n+extra = 2\n",
        )
    ]
    ctx = build_review_context(
        PullRequest(repo="test/repo", number=9, title="Small change"), files, [],
        max_patch_tokens=10_000,
    )
    assert "new = 1" in ctx.text and "extra = 2" in ctx.text
    assert not [o for o in ctx.omissions if o.reason == "not_selected_by_triage"]


def test_an_over_budget_diff_keeps_flagged_hunks_and_still_fills_the_budget() -> None:
    """Suspicion is a priority, not the only admission condition: a diff with no
    flagged hunk must still reach the model, and a flagged hunk must always be in."""
    plain = "\n".join(f"+value_{i} = compute_{i}()" for i in range(100))
    files = [ChangedFile(filename=f"src/plain_{i}.py", status=FileStatus.MODIFIED,
                         additions=100, patch="@@ -1,1 +1,100 @@\n" + plain)
             for i in range(8)]
    # The later production-path sort used to place this small, flagged docs file
    # after eight large source files, so its hunk never reached the final prompt.
    files.append(ChangedFile(
        filename="docs/risky.py", status=FileStatus.MODIFIED, additions=2,
        patch="@@ -1,1 +1,2 @@\n keep\n+exec(user_input)\n",
    ))
    ctx = build_review_context(
        PullRequest(repo="test/repo", number=10, title="Big change"), files, [],
        max_patch_tokens=800,
    )
    # The flagged hunk is always present, and the budget did not go to waste.
    assert "exec(user_input)" in ctx.text
    assert "value_" in ctx.text
    assert ctx.coverage_summary.hunks_total == 9
    assert ctx.coverage_summary.complete


def _over_budget_files():
    plain = "\n".join(f"+value_{i} = compute_{i}()" for i in range(120))
    return [
        ChangedFile(filename="src/plain.py", status=FileStatus.MODIFIED, additions=120,
                    patch="@@ -1,1 +1,120 @@\n" + plain),
        ChangedFile(filename="src/other.py", status=FileStatus.MODIFIED, additions=120,
                    patch="@@ -1,1 +1,120 @@\n" + plain),
    ]


def test_over_budget_context_accounts_for_every_hunk() -> None:
    """Guardrail: replacing the over-budget policy must not break the ledger.

    Whatever selection rule is used, these must hold: the context cannot silently
    lose hunks, every changed file has a terminal coverage state, and the hunk
    counts add up.
    """
    ctx = build_review_context(
        PullRequest(repo="test/repo", number=11, title="Over budget"), _over_budget_files(), [],
        max_patch_tokens=40,
    )
    summary = ctx.coverage_summary
    assert summary.hunks_total == (
        summary.hunks_complete + summary.hunks_partial + summary.hunks_omitted
    )
    assert summary.unaccounted_files == ()
    assert {entry.path for entry in ctx.coverage} == {"src/plain.py", "src/other.py"}
    assert ctx.truncated is True
    assert ctx.text.strip()


def test_over_budget_context_still_shows_the_model_something() -> None:
    """Guardrail: a diff that does not fit must not become an empty prompt.

    Filtering to flagged hunks alone did exactly that, and it is the failure this
    replacement exists to avoid.
    """
    ctx = build_review_context(
        PullRequest(repo="test/repo", number=12, title="Over budget"), _over_budget_files(), [],
        max_patch_tokens=40,
    )
    assert "value_" in ctx.text
