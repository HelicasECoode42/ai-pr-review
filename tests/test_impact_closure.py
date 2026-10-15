"""Impact closure: what must be visible for a change to be judgeable."""

from __future__ import annotations

from src.analyzer.diff_parser import parse_file_hunks
from src.analyzer.impact_closure import build_impact_closure, impact_coverage
from src.models import ChangedFile

PATCH = "@@ -10,3 +10,4 @@ def pick(values, index):\n ctx\n-old\n+new\n"


class _Snapshot:
    """A frozen snapshot whose search returns one caller outside the diff."""

    base = "a" * 40
    head = "b" * 40

    def __init__(self, hits):
        self.hits = hits

    def read(self, path, side="head"):
        return "def pick(values, index):\n    return values[index]\n"

    def search(self, symbol, side="head", limit=6):
        return self.hits[:limit]


def files_and_hunks():
    files = [ChangedFile(filename="src/a.py", patch=PATCH)]
    return files, {f.filename: parse_file_hunks(f) for f in files}


def test_closure_adds_callers_from_outside_the_diff():
    files, hunks = files_and_hunks()
    snapshot = _Snapshot([("src/a.py", 11), ("src/other.py", 3)])
    closure = build_impact_closure(files, hunks, snapshot)
    assert closure.changed_symbols == {"pick"}
    # The caller inside the diff is already visible; only the outside one counts.
    assert closure.callers["pick"] == {"src/other.py:3"}
    assert closure.nodes == {"pick", "src/other.py:3"}


def test_coverage_only_counts_symbols_whose_names_were_sent():
    files, hunks = files_and_hunks()
    closure = build_impact_closure(files, hunks, _Snapshot([("src/other.py", 3)]))
    assert impact_coverage(closure, "def pick(...)")["coverage"] == 0.5
    full = impact_coverage(closure, "def pick(...) src/other.py:3")
    assert full["coverage"] == 1.0
    assert full["missing"] == []


def test_unresolvable_hunks_are_recorded_not_ignored():
    files = [ChangedFile(filename="docs/x.md", patch="@@ -1,1 +1,1 @@\n-a\n+b\n")]
    hunks = {f.filename: parse_file_hunks(f) for f in files}
    closure = build_impact_closure(files, hunks, _Snapshot([]))
    assert closure.changed_symbols == set()
    # Docs have no symbol by design, and that is recorded rather than dropped.
    assert closure.unresolved == ["docs/x.md:1:not_applicable"]


def test_a_search_failure_does_not_break_the_metric():
    class Broken(_Snapshot):
        def search(self, symbol, side="head", limit=6):
            raise RuntimeError("backend down")

    files, hunks = files_and_hunks()
    closure = build_impact_closure(files, hunks, Broken([]))
    assert closure.changed_symbols == {"pick"}
    assert closure.callers["pick"] == set()


def test_empty_closure_reports_full_coverage_rather_than_dividing_by_zero():
    assert impact_coverage(build_impact_closure([], {}, _Snapshot([])), "")["coverage"] == 1.0
