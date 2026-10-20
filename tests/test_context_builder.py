import pytest

from src.analyzer import context_builder
from src.analyzer.context_builder import (
    TRUNCATION_MARKER,
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


def test_same_path_changed_files_keep_their_own_parsed_hunks(monkeypatch) -> None:
    first = ChangedFile(
        filename="src/shared.py",
        status=FileStatus.MODIFIED,
        additions=1,
        patch="@@ -1,1 +1,2 @@\n+from_first_input()",
    )
    second = ChangedFile(
        filename="src/shared.py",
        status=FileStatus.MODIFIED,
        additions=1,
        patch="@@ -1,1 +1,2 @@\n+from_second_input()",
    )
    parsed_inputs = []
    parse_file_hunks = context_builder.parse_file_hunks

    def track_parse(file):
        parsed_inputs.append(file)
        return parse_file_hunks(file)

    monkeypatch.setattr(context_builder, "parse_file_hunks", track_parse)

    ctx = build_review_context(
        PullRequest(repo="test/repo", number=4, title="Repeated path"),
        [first, second],
        [],
        include_context_pack=False,
        allow_remote_file_fetch=False,
    )

    assert "from_first_input()" in ctx.text
    assert "from_second_input()" in ctx.text
    assert parsed_inputs == [first, second]


def test_truncate_to_token_budget_is_language_independent_and_bounded() -> None:
    text = "鉴权逻辑发生变化，需要检查权限边界。" * 20
    fitted, consumed, truncated = truncate_to_token_budget(text, 24)

    assert truncated is True
    assert consumed <= 24
    assert count_tokens(fitted) <= 24
    assert "[truncated]" in fitted


@pytest.mark.parametrize("use_utf8_fallback", [False, True])
def test_tiny_budgets_count_rendered_text_including_marker(monkeypatch, use_utf8_fallback):
    class CharacterEncoding:
        def encode(self, text, disallowed_special=()):
            return list(text)

        def decode(self, tokens):
            return "".join(tokens)

    monkeypatch.setattr(
        context_builder,
        "_token_encoding",
        (lambda: None) if use_utf8_fallback else (lambda: CharacterEncoding()),
    )
    text = ("权限🧪变化 " * 20) + ("long_line_" * 40)
    marker_budget = count_tokens(TRUNCATION_MARKER)

    for budget in (0, 1, marker_budget - 1, marker_budget):
        fitted, consumed, clipped = truncate_to_token_budget(text, budget)
        assert clipped is True
        assert consumed == count_tokens(fitted)
        assert consumed <= budget
        fitted.encode("utf-8")  # decoding must never leave a broken Unicode string

    pack = context_builder._build_context_pack(["src/auth.py"], budget=marker_budget - 1)
    assert count_tokens(pack) <= marker_budget - 1

    ctx = build_review_context(
        PullRequest(repo="test/repo", number=3, title="Tiny patch cap"),
        [ChangedFile(filename="src/auth.py", patch="@@ -1,1 +1,2 @@\n+" + text)],
        [],
        max_patch_tokens=marker_budget - 1,
        include_context_pack=False,
        allow_remote_file_fetch=False,
    )
    patch_budget = next(line for line in ctx.budgets.lines if line.name == "patch_context")
    assert patch_budget.used <= patch_budget.limit
    assert ctx.status.value == "partial"
    assert ctx.coverage_summary.hunks_omitted == 1
    assert ctx.coverage_summary.hunks_partial == 0
    assert ctx.coverage_summary.hunks_total == (
        ctx.coverage_summary.hunks_complete
        + ctx.coverage_summary.hunks_partial
        + ctx.coverage_summary.hunks_omitted
    )


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


def test_over_budget_context_still_shows_the_model_something(monkeypatch) -> None:
    """Guardrail: a diff that does not fit must not become an empty prompt.

    Filtering to flagged hunks alone did exactly that, and it is the failure this
    replacement exists to avoid.
    """
    class DeterministicEncoding:
        """Keep this budget-selection regression independent of local tiktoken cache."""

        def encode(self, text, disallowed_special=()):
            return [text[index:index + 8] for index in range(0, len(text), 8)]

        def decode(self, tokens):
            return "".join(tokens)

    monkeypatch.setattr(
        "src.analyzer.context_builder._token_encoding", DeterministicEncoding
    )
    ctx = build_review_context(
        PullRequest(repo="test/repo", number=12, title="Over budget"), _over_budget_files(), [],
        max_patch_tokens=40,
    )
    assert "value_" in ctx.text
    patch_budget = next(line for line in ctx.budgets.lines if line.name == "patch_context")
    assert patch_budget.used <= patch_budget.limit
    assert ctx.coverage_summary.hunks_total == (
        ctx.coverage_summary.hunks_complete
        + ctx.coverage_summary.hunks_partial
        + ctx.coverage_summary.hunks_omitted
    )
    assert ctx.coverage_summary.hunks_complete < ctx.coverage_summary.hunks_total


def test_documentation_patch_budget_is_off_by_default() -> None:
    files = [
        ChangedFile(
            filename="CHANGELOG.md",
            status=FileStatus.MODIFIED,
            additions=1,
            patch="@@ -1,1 +1,2 @@\n # Changelog\n+## v2\n",
        )
    ]
    context = build_review_context(
        PullRequest(repo="test/repo", number=1, title="Docs"),
        files,
        [],
    )
    assert "documentation_only_change" in [reason for _, reason in context.skipped_files]
    assert "## v2" not in context.text
    assert context.rendered_head_ranges == {}


def test_documentation_patch_budget_keeps_doc_hunks_and_records_ranges() -> None:
    files = [
        ChangedFile(
            filename="CHANGELOG.md",
            status=FileStatus.MODIFIED,
            additions=1,
            patch="@@ -1,1 +1,2 @@\n # Changelog\n+## v2\n",
        )
    ]
    context = build_review_context(
        PullRequest(repo="test/repo", number=1, title="Docs"),
        files,
        [],
        max_documentation_tokens=600,
    )
    assert "## v2" in context.text
    assert "documentation_only_change" not in [reason for _, reason in context.skipped_files]
    assert context.rendered_head_ranges["CHANGELOG.md"] == ((1, 2),)
