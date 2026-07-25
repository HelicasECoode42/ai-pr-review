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
