from pathlib import Path

from src.analyzer.context_builder import build_review_context
from src.models import ChangedFile, FileStatus, PullRequest


def test_echo_forge_style_rules_enter_local_ai_context(tmp_path: Path) -> None:
    rules = tmp_path / ".ai-cr" / "mr-code-review.rules.md"
    rules.parent.mkdir(parents=True)
    rules.write_text("# Review rules\n- Block writes before verification.\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Prefer bounded retries.\n", encoding="utf-8")
    pr = PullRequest(repo="local/demo", number=1, title="Local diff")
    changed = ChangedFile(
        filename="app.py",
        status=FileStatus.MODIFIED,
        additions=1,
        patch="@@ -1 +1 @@\n-old\n+new",
    )

    context = build_review_context(pr, [changed], project_root=tmp_path)

    assert "Target Repository Review Rules" in context.text
    assert ".ai-cr/mr-code-review.rules.md" in context.text
    assert "Block writes before verification" in context.text
    assert "AGENTS.md" in context.text
    assert "untrusted project guidance" in context.text
