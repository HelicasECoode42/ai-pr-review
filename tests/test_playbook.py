"""The knowledge playbook: loading, scope-filtered recall, mechanical expiry."""

from __future__ import annotations

import pytest

from src.knowledge.playbook import (
    PlaybookError,
    expired_by_symbols,
    load_playbook,
    load_text,
    parse_entry,
    recall,
    render,
)

ENTRY = """---
id: kb-x
scope: {repo: "o/r", languages: [python], symbols: ["pick"]}
kind: false_positive_pattern
statement: "An inverted guard with swapped branches is equivalent."
source: "gold case"
intent_symptoms: [equivalent_rewrite_confirmed]
version: 2
status: authoritative
---

Detail that must be preserved rather than summarised away.
"""


def test_entry_round_trips_with_its_detail():
    entry = parse_entry(ENTRY)
    assert entry.id == "kb-x" and entry.kind == "false_positive_pattern"
    assert entry.version == 2 and entry.status == "authoritative"
    assert entry.intent_symptoms == ("equivalent_rewrite_confirmed",)
    assert "preserved" in entry.body


def test_duplicate_ids_are_refused_rather_than_silently_picked():
    with pytest.raises(PlaybookError):
        load_text(ENTRY + "\n" + ENTRY)


def test_bad_metadata_is_refused():
    with pytest.raises(PlaybookError):
        parse_entry("---\nid: x\nkind: nonsense\nstatement: s\nstatus: authoritative\n---\n")
    with pytest.raises(PlaybookError):
        parse_entry("no front matter at all")


def test_recall_filters_by_scope_and_status():
    entries = [
        parse_entry(ENTRY),
        parse_entry(ENTRY.replace("kb-x", "kb-y").replace("authoritative", "candidate")),
        parse_entry(ENTRY.replace("kb-x", "kb-z").replace('repo: "o/r"', 'repo: "other/repo"')),
    ]
    recalled = recall(entries, repo="o/r", paths=["a.py"], languages=["python"])
    # Only the authoritative, in-scope entry is offered to the model.
    assert [e.id for e in recalled] == ["kb-x"]
    assert render(recalled).startswith("## Project knowledge")


def test_recall_matches_path_globs():
    entry = parse_entry(ENTRY.replace('languages: [python]', 'paths: ["src/analyzer/*.py"]'))
    assert recall([entry], repo="o/r", paths=["src/analyzer/x.py"], languages=[]) == [entry]
    assert recall([entry], repo="o/r", paths=["docs/x.md"], languages=[]) == []


def test_expiry_is_mechanical_not_judgemental():
    entry = parse_entry(ENTRY)
    assert expired_by_symbols([entry], lambda symbol: True) == []
    assert [e.id for e in expired_by_symbols([entry], lambda symbol: False)] == ["kb-x"]


def test_missing_playbook_is_an_empty_playbook(tmp_path):
    assert load_playbook(tmp_path / "absent.md") == []


def test_a_malformed_playbook_is_not_silently_empty(tmp_path):
    broken = tmp_path / "broken.md"
    broken.write_text("---\nid: x\nkind: nonsense\nstatement: s\n---\n")
    with pytest.raises(PlaybookError):
        load_playbook(broken)


class _NullProvider:
    usage_records: list = []

    def close(self):
        pass


def test_recall_is_recorded_in_the_report_metrics():
    from src.application.review import ReviewApplication
    from src.models import ChangedFile, PullRequest, ReviewReport, Severity
    from src.utils.config import Settings

    report = ReviewReport(
        pr=PullRequest(repo="o/r", number=1, title="t"), files=[], summary="s",
        risk_level=Severity.LOW,
    )
    seen: dict = {}

    def engine(**kwargs):
        seen.update(kwargs)
        return report

    application = ReviewApplication(
        Settings(enable_critic=False, playbook_path="knowledge/playbook.md"),
        provider_factory=lambda **kw: _NullProvider(), engine=engine,
    )
    files = [ChangedFile(filename="src/analyzer/x.py", patch="@@ -1,1 +1,1 @@\n-a\n+b\n")]
    application.review(PullRequest(repo="o/r", number=1, title="t"), files, [])
    assert "prompt_addendum" in seen and "Project knowledge" in seen["prompt_addendum"]
    ids = report.metrics["experience_ids"].split(",")
    assert {"kb-0001", "kb-0002"} <= set(ids)
    assert report.metrics["experience_count"] == len(ids)
    # The contract entry declares domains for exactly the symbols it names.
    assert report.metrics["experience_count"] >= 2
