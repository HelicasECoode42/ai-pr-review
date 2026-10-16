"""The knowledge playbook: loading, scope-filtered recall, mechanical expiry."""

from __future__ import annotations

import pytest

from src.knowledge.playbook import (
    PlaybookError,
    contract_domains,
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
    recalled = recall(entries, repo="o/r", paths=["a.py"], languages=["python"],
                      symbols_by_path={"a.py": {"pick"}})
    # Only the authoritative, in-scope entry is offered to the model.
    assert [e.id for e in recalled] == ["kb-x"]
    assert render(recalled).startswith("## Project knowledge")


def test_recall_matches_path_globs():
    entry = parse_entry(ENTRY.replace('languages: [python], symbols: ["pick"]',
                                      'paths: ["src/analyzer/*.py"]'))
    assert recall([entry], repo="o/r", paths=["src/analyzer/x.py"], languages=[]) == [entry]
    assert recall([entry], repo="o/r", paths=["docs/x.md"], languages=[]) == []


def test_symbol_scope_requires_a_known_symbol_from_a_matching_changed_path():
    entry = parse_entry(ENTRY)
    assert recall([entry], repo="o/r", paths=["a.py"], languages=["python"]) == []
    assert recall([entry], repo="o/r", paths=["a.py"], languages=["python"],
                  symbols_by_path={"a.py": set()}) == []
    assert recall([entry], repo="o/r", paths=["a.py"], languages=["python"],
                  symbols_by_path={"a.py": {"unrelated"}}) == []
    assert recall([entry], repo="o/r", paths=["a.py"], languages=["python"],
                  symbols_by_path={"a.py": {"pick"}}) == [entry]


def test_symbol_wildcard_does_not_require_ast_symbol_resolution():
    entry = parse_entry(ENTRY.replace('symbols: ["pick"]', 'symbols: ["*"]'))
    assert recall([entry], repo="o/r", paths=["a.py"], languages=["python"]) == [entry]


def test_kotlin_scope_matches_dot_kt_files():
    entry = parse_entry(ENTRY.replace('languages: [python], symbols: ["pick"]',
                                      'languages: [kotlin]'))
    assert recall([entry], repo="o/r", paths=["src/Worker.kt"], languages=["kotlin"]) == [entry]


def test_scoped_contract_domains_are_not_promoted_to_run_global_domains():
    scoped = parse_entry(ENTRY.replace("kind: false_positive_pattern", "kind: contract")
                         .replace("version: 2", "domain: {count: int}\nversion: 2"))
    global_contract = parse_entry(ENTRY.replace("kind: false_positive_pattern", "kind: contract")
                                  .replace(
                                      'scope: {repo: "o/r", languages: [python], '
                                      'symbols: ["pick"]}',
                                      'scope: {repo: "o/r"}',
                                  )
                                  .replace("version: 2", "domain: {total: int}\nversion: 2"))
    assert contract_domains([scoped, global_contract]) == {"total": "int"}


def test_conflicting_global_contract_domains_fail_closed():
    first = parse_entry(ENTRY.replace("kind: false_positive_pattern", "kind: contract")
                        .replace('scope: {repo: "o/r", languages: [python], symbols: ["pick"]}',
                                 'scope: {repo: "o/r"}')
                        .replace("version: 2", "domain: {count: int}\nversion: 2"))
    second = parse_entry(ENTRY.replace("kb-x", "kb-y")
                         .replace("kind: false_positive_pattern", "kind: contract")
                         .replace('scope: {repo: "o/r", languages: [python], symbols: ["pick"]}',
                                  'scope: {repo: "o/r"}')
                         .replace("version: 2", "domain: {count: number}\nversion: 2"))
    with pytest.raises(PlaybookError, match="conflicting contract domain"):
        contract_domains([first, second])


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
    # Scoped natural-language guidance stays available when it declares no domain.
    assert report.metrics["experience_count"] >= 2
    assert "kb-0004" in ids


def test_application_scopes_playbook_by_the_actual_pr_repo(tmp_path):
    from src.application.review import ReviewApplication
    from src.models import ChangedFile, PullRequest, ReviewReport, Severity
    from src.utils.config import Settings

    entry = ENTRY.replace('repo: "o/r", languages: [python], symbols: ["pick"]',
                          'repo: "owner/repo", languages: [python]')
    entry = entry.replace("kind: false_positive_pattern", "kind: contract")
    entry = entry.replace("An inverted guard with swapped branches is equivalent.",
                          "Check the relevant caller contract before judging this change.")
    playbook = tmp_path / "playbook.md"
    playbook.write_text(entry, encoding="utf-8")
    report = ReviewReport(
        pr=PullRequest(repo="owner/repo", number=1, title="t"), files=[], summary="s",
        risk_level=Severity.LOW,
    )
    seen: dict = {}

    def engine(**kwargs):
        seen.update(kwargs)
        return report

    application = ReviewApplication(
        Settings(enable_critic=False, playbook_path=str(playbook)),
        provider_factory=lambda **kw: _NullProvider(), engine=engine,
    )
    class NoReadSnapshot:
        reads = []

        def read(self, path, side):
            self.reads.append((path, side))
            raise AssertionError("path-scoped guidance must not trigger symbol source reads")

    snapshot = NoReadSnapshot()
    file = ChangedFile(filename="src/a.py", patch="@@ -1 +1 @@\n-a\n+b\n")
    application.review(PullRequest(repo="owner/repo", number=1, title="t"), [file], [],
                       repository_snapshot=snapshot)
    assert "Check the relevant caller contract" in seen["prompt_addendum"]
    assert snapshot.reads == []

    seen.clear()
    application.review(PullRequest(repo="other/repo", number=1, title="t"), [file], [])
    assert "prompt_addendum" not in seen


def test_scoped_contract_domain_is_neither_global_nor_prompted(tmp_path):
    from src.application.review import ReviewApplication
    from src.models import ChangedFile, PullRequest, ReviewReport, Severity
    from src.utils.config import Settings

    contract = """---
id: synthetic-average-contract
scope: {repo: "owner/repo", languages: [python], symbols: [average]}
kind: contract
statement: "count must be an int"
source: synthetic fixture
domain: {count: int}
status: authoritative
---
Synthetic-only assertion; no repository API contract was supplied.
"""
    playbook = tmp_path / "playbook.md"
    playbook.write_text(contract, encoding="utf-8")
    report = ReviewReport(
        pr=PullRequest(repo="owner/repo", number=1, title="t"), files=[], summary="s",
        risk_level=Severity.LOW,
    )
    seen: dict = {}

    def engine(**kwargs):
        seen.update(kwargs)
        return report

    class FrozenSnapshot:
        reads = []

        def read(self, path, side):
            self.reads.append((path, side))
            return "def average(count=None):\n    return count\n\n" \
                   "def unrelated(count=None):\n    return count\n"

    snapshot = FrozenSnapshot()
    application = ReviewApplication(
        Settings(enable_critic=False, playbook_path=str(playbook)),
        provider_factory=lambda **kw: _NullProvider(), engine=engine,
    )
    file = ChangedFile(filename="src/math.py", patch=(
        "@@ -1,1 +1,2 @@\n def average(count=None):\n+    return count\n"
    ))
    application.review(PullRequest(repo="owner/repo", number=1, title="t"), [file], [],
                       repository_snapshot=snapshot)
    assert snapshot.reads == [("src/math.py", "head")]
    assert not seen.get("contract_domains")
    assert "count must be an int" not in seen.get("prompt_addendum", "")
    assert report.analysis_warnings


def test_symbol_index_file_budget_stops_additional_snapshot_reads(monkeypatch):
    import src.application.review as review_module
    from src.models import ChangedFile

    monkeypatch.setattr(review_module, "_SYMBOL_SCOPE_MAX_FILES", 1)

    class CountingSnapshot:
        reads = []

        def read(self, path, side):
            self.reads.append((path, side))
            return "def pick(value):\n    return value\n"

    files = [
        ChangedFile(filename=f"src/{name}.py", patch=(
            "@@ -1,1 +1,2 @@\n def pick(value):\n+    return value\n"
        ))
        for name in ("a", "b")
    ]
    snapshot = CountingSnapshot()
    symbols, limited = review_module.ReviewApplication._changed_symbols_by_path(
        files, snapshot, relevant_paths={"src/a.py", "src/b.py"}
    )
    assert limited
    assert len(snapshot.reads) == 1
    assert set(symbols) == {"src/a.py"}


def test_symbol_index_enforces_per_file_and_total_source_size(monkeypatch):
    import src.application.review as review_module
    from src.models import ChangedFile

    monkeypatch.setattr(review_module, "_SYMBOL_SCOPE_MAX_FILES", 4)
    monkeypatch.setattr(review_module, "_SYMBOL_SCOPE_MAX_FILE_BYTES", 50)
    monkeypatch.setattr(review_module, "_SYMBOL_SCOPE_MAX_TOTAL_BYTES", 30)

    class CountingSnapshot:
        reads = []

        def read(self, path, side):
            self.reads.append((path, side))
            return "def pick():\n    return 1\n"

    files = [
        ChangedFile(filename=f"src/{name}.py", patch=(
            "@@ -1,1 +1,2 @@\n def pick():\n+    return 1\n"
        ))
        for name in ("a", "b")
    ]
    snapshot = CountingSnapshot()
    symbols, limited = review_module.ReviewApplication._changed_symbols_by_path(
        files, snapshot, relevant_paths={"src/a.py", "src/b.py"}
    )
    # One frozen source is indexed; fetching the second would exceed the total
    # source-byte budget, so that path stays unknown and no further read occurs.
    assert limited
    assert len(snapshot.reads) == 2
    assert set(symbols) == {"src/a.py"}


def test_symbol_index_skips_an_oversized_single_source(monkeypatch):
    import src.application.review as review_module
    from src.models import ChangedFile

    monkeypatch.setattr(review_module, "_SYMBOL_SCOPE_MAX_FILE_BYTES", 10)

    class Snapshot:
        reads = []

        def read(self, path, side):
            self.reads.append((path, side))
            return "def pick():\n    return 1\n"

    snapshot = Snapshot()
    file = ChangedFile(filename="src/a.py", patch=(
        "@@ -1,1 +1,2 @@\n def pick():\n+    return 1\n"
    ))
    symbols, limited = review_module.ReviewApplication._changed_symbols_by_path(
        [file], snapshot, relevant_paths={"src/a.py"}
    )
    assert limited and len(snapshot.reads) == 1
    assert symbols == {}


def test_parse_errors_keep_symbol_scope_unknown_and_warn(tmp_path, monkeypatch):
    import src.analyzer.review_plan as plan_module
    from src.application.review import ReviewApplication
    from src.models import ChangedFile
    from src.utils.config import Settings

    playbook = tmp_path / "playbook.md"
    playbook.write_text("""---
id: scoped-pick
scope: {repo: "owner/repo", languages: [typescript], symbols: [pick]}
kind: retrieval_hint
statement: "Check callers of pick."
status: authoritative
---
Only use when the changed symbol is resolved.
""", encoding="utf-8")

    def malformed_parse(path, text, side):
        return plan_module.SourceFile(
            path=path, side=side, content_hash="fixture", parser="tree-sitter-typescript",
            symbols=(plan_module.SourceSymbol(name="pick", start=1, end=2),),
            limitations=("parse_errors",),
        )

    monkeypatch.setattr(plan_module.SourceIndexCache, "parse", lambda self, path, text, side:
                        malformed_parse(path, text, side))

    class Snapshot:
        def read(self, path, side):
            return "function pick() {\n  return 1;\n}\n"

    app = ReviewApplication(Settings(playbook_path=str(playbook)))
    file = ChangedFile(filename="src/pick.ts", patch=(
        "@@ -1,1 +1,2 @@\n function pick() {\n+  return 1;\n"
    ))
    recalled, warnings, _ = app._recall_playbook(
        [file], {"repository_snapshot": Snapshot()}, repo="owner/repo"
    )
    assert recalled == []
    assert any("could not be resolved" in warning for warning in warnings)
