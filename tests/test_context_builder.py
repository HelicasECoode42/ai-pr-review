from src.analyzer.context_builder import build_review_context
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

    context, _truncated = build_review_context(
        PullRequest(repo="test/repo", number=1, title="Update deps"),
        files,
        [],
    )

    assert "new-lock-content" not in context
    assert "lockfile(s) excluded from patch context" in context
    assert "print('ok')" in context


def test_build_review_context_keeps_project_config_patch() -> None:
    files = [
        ChangedFile(
            filename="pyproject.toml",
            status=FileStatus.MODIFIED,
            additions=1,
            patch='@@ -1,1 +1,2 @@\n [project]\n+name = "ai-pr-review"',
        )
    ]

    context, _truncated = build_review_context(
        PullRequest(repo="test/repo", number=1, title="Update config"),
        files,
        [],
    )

    assert 'name = "ai-pr-review"' in context
