from __future__ import annotations

import pytest
from typer.testing import CliRunner

from src.analyzer.context_builder import count_tokens
from src.analyzer.review_plan import SourceIndexCache, build_review_plan
from src.analyzer.symbol_index import (
    PythonSymbolIndex,
    parse_python,
    resolve_python_relationships,
)
from src.cli.plan import app
from src.models import ChangedFile, FileStatus


class MemorySnapshot:
    base = "1" * 40
    head = "2" * 40

    def __init__(self, head, base=None):
        self.sources = {"head": head, "base": base or head}

    def read(self, path, side="head"):
        return self.sources[side].get(path)


def changed(path, line=1, text="value = 1", status=FileStatus.MODIFIED):
    return ChangedFile(
        filename=path, status=status, patch=f"@@ -{line},1 +{line},1 @@\n-old\n+{text}\n"
    )


def test_python_import_chain_groups_and_unrelated_stays_separate():
    sources = {
        "app/api.py": "from .service import save\ndef route():\n    return save()\n",
        "app/service.py": "def save():\n    return 1\n",
        "other.py": "def other():\n    return 2\n",
    }
    files = [changed(p) for p in sources]
    plan = build_review_plan(MemorySnapshot(sources), files, list(sources))
    groups = [{s.path for s in u.slices} for u in plan.units]
    assert {"app/api.py", "app/service.py"} in groups
    assert {"other.py"} in groups
    assert any(e.kind == "import" and e.strength == "strong" for e in plan.relationships)
    assert len([c for c in plan.coverage if c.status == "planned"]) == 3
    assert (
        plan.model_dump()
        == build_review_plan(
            MemorySnapshot(sources), list(reversed(files)), list(reversed(sources))
        ).model_dump()
    )


def test_large_hunk_fragments_without_losing_bytes_and_bounds_each_unit():
    patch = "@@ -1,1 +1,1 @@\n-old\n+" + ("abcdefgh! " * 500) + "\n"
    file = ChangedFile(filename="a.py", patch=patch)
    plan = build_review_plan(MemorySnapshot({"a.py": "x=1"}), [file], ["a.py"], token_budget=128)
    assert len(plan.units) > 1
    fragments = [s for u in plan.units for s in u.slices]
    # slice ids are sorted during allocation, so reassemble by original part hash.
    from src.analyzer.review_plan import _hash

    hunk_id = fragments[0].hunk_id
    by_id = {s.slice_id: s.fragment for s in fragments}
    rebuilt = "".join(by_id[_hash(hunk_id + ":" + str(i))] for i in range(len(fragments)))
    from src.analyzer.diff_parser import parse_file_hunks

    assert rebuilt == parse_file_hunks(file)[0].raw
    assert all(count_tokens("\n".join(s.fragment for s in u.slices)) <= 128 for u in plan.units)
    by_part = sorted(fragments, key=lambda item: item.fragment_index)
    assert by_part[0].fragment_start_byte == 0
    assert by_part[-1].fragment_end_byte == by_part[-1].hunk_byte_length
    assert all(a.fragment_end_byte == b.fragment_start_byte
               for a, b in zip(by_part, by_part[1:], strict=False))
    assert all(len(item.fragment.encode("utf-8")) ==
               item.fragment_end_byte - item.fragment_start_byte for item in by_part)


def test_utf8_fragment_boundary_records_byte_ranges_and_partial_line_evidence():
    patch = "@@ -1,1 +1,1 @@\n-old\n+" + ("界🙂" * 160) + "\n"
    file = ChangedFile(filename="utf8.py", patch=patch)
    plan = build_review_plan(MemorySnapshot({"utf8.py": "x=1"}), [file], ["utf8.py"],
                             token_budget=128)
    slices = sorted((item for unit in plan.units for item in unit.slices),
                    key=lambda item: item.fragment_index)
    assert len(slices) > 1
    from src.analyzer.diff_parser import parse_file_hunks

    expected_hunk = parse_file_hunks(file)[0].raw
    assert "".join(item.fragment for item in slices) == expected_hunk
    assert slices[0].fragment_start_byte == 0
    assert slices[-1].fragment_end_byte == len(expected_hunk.encode("utf-8"))
    assert all(left.fragment_end_byte == right.fragment_start_byte
               for left, right in zip(slices, slices[1:], strict=False))
    assert all(item.lines.cuts_a_diff_line for item in slices[1:-1])


def test_common_hub_does_not_merge_every_consumer():
    sources = {"common.py": "def shared():\n    return 1\n"}
    sources.update(
        {
            f"consumer{i}.py": "from common import shared\ndef f():\n    return shared()\n"
            for i in range(10)
        }
    )
    plan = build_review_plan(MemorySnapshot(sources), [changed(p) for p in sources], list(sources))
    assert len(plan.units) > 1
    assert any("common.py" in u.related_paths for u in plan.units)


def test_cycles_are_bounded_not_lost():
    sources = {
        "a.py": "import b\ndef a():\n    return b.b()\n",
        "b.py": "import a\ndef b():\n    return a.a()\n",
    }
    plan = build_review_plan(MemorySnapshot(sources), [changed(p) for p in sources], list(sources))
    assert len(plan.units) == 1
    assert len(plan.units[0].slices) == 2


def test_ambiguous_bare_calls_never_become_strong_edges():
    sources = {
        "a.py": "def run():\n    return save()\n",
        "b.py": "def save():\n    return 1\n",
        "c.py": "def save():\n    return 2\n",
    }
    plan = build_review_plan(MemorySnapshot(sources), [changed(p) for p in sources], list(sources))
    refs = [e for e in plan.relationships if e.kind == "call" and e.side == "head"]
    assert len(refs) == 2 and all(e.strength == "weak" for e in refs)
    assert len(plan.units) == 3


def test_missing_source_missing_patch_excluded_paths_and_index_budget_are_visible():
    files = [changed("a.py"), changed("b.py"), changed(".env"), ChangedFile(filename="c.py")]
    plan = build_review_plan(
        MemorySnapshot({"a.py": "x=1", "b.py": "x=2"}), files, ["a.py", "b.py"], index_file_limit=1
    )
    assert len([c for c in plan.coverage if c.status == "planned"]) == 2
    reasons = {c.reason for c in plan.coverage}
    assert {"index_budget", "source_policy", "missing_or_invalid_patch"} <= reasons
    assert "repository_index_budget_exhausted" in plan.limitations


def test_deleted_source_uses_base_and_keeps_coverage():
    snapshot = MemorySnapshot({}, {"old.py": "def removed():\n    return 1\n"})
    file = ChangedFile(
        filename="old.py",
        status=FileStatus.REMOVED,
        patch="@@ -1,2 +0,0 @@\n-def removed():\n-    return 1\n",
    )
    plan = build_review_plan(snapshot, [file], [])
    s = plan.units[0].slices[0]
    assert s.side == "base" and s.symbols == ("removed",)
    assert s.evidence[0].side == "base"


def test_invalid_python_retains_diff_and_reports_parse_failure():
    plan = build_review_plan(MemorySnapshot({"a.py": "def ???"}), [changed("a.py")], ["a.py"])
    assert len(plan.units) == 1 and "parse_failed" in plan.limitations


def test_typescript_real_parser_resolves_relative_import_and_interface():
    snapshot = MemorySnapshot(
        {
            "api.ts": (
                "import { User } from './types.js';\nexport function f(u: User) { return u.id; }\n"
            ),
            "types.ts": "export interface User { id: string }\n",
        }
    )
    plan = build_review_plan(
        snapshot, [changed("api.ts"), changed("types.ts")], ["api.ts", "types.ts"]
    )
    assert len(plan.units) == 1
    assert all(f.parser == "tree-sitter-typescript" for f in plan.files)
    assert any(s.name == "User" for f in plan.files for s in f.symbols)


def test_cache_reuses_unchanged_content_but_invalidates_changed_definition():
    cache = SourceIndexCache(2)
    a = cache.parse("a.py", "def a(): pass", "head")
    assert cache.parse("a.py", "def a(): pass", "head") is a
    assert cache.hits == 1
    b = cache.parse("a.py", "def b(): pass", "head")
    assert b.symbols[0].name == "b" and b.content_hash != a.content_hash
    cache.parse("c.py", "x=1", "head")
    assert len(cache.entries) == 2


def test_no_changes_and_bad_budget():
    plan = build_review_plan(MemorySnapshot({}), [], [])
    assert plan.units == () and plan.coverage == ()
    with pytest.raises(ValueError):
        build_review_plan(MemorySnapshot({}), [], [], token_budget=0)


def test_plan_cli_freezes_commits_and_does_not_read_worktree(tmp_path):
    import subprocess

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    git("add", "a.py")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "a.py").write_text("def a():\n    return 2\n")
    git("add", "a.py")
    git("commit", "-qm", "head")
    (tmp_path / "a.py").write_text("UNCOMMITTED_SECRET_SENTINEL")
    output = tmp_path / "plan.json"
    result = CliRunner().invoke(
        app, ["--repo-path", str(tmp_path), "--base", base, "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert "UNCOMMITTED_SECRET_SENTINEL" not in output.read_text()
    assert "return 2" in output.read_text()


def test_more_than_twelve_files_have_explicit_hunk_coverage():
    sources = {f"file{i:02}.py": f"value = {i}\n" for i in range(25)}
    plan = build_review_plan(MemorySnapshot(sources), [changed(p) for p in sources], list(sources))
    assert {c.path for c in plan.coverage if c.status == "planned"} == set(sources)
    assert {s.path for u in plan.units for s in u.slices} == set(sources)


def test_renamed_file_keeps_original_base_definition_reference():
    snapshot = MemorySnapshot(
        {"new.py": "def renamed():\n    return 2\n"}, {"old.py": "def original():\n    return 1\n"}
    )
    file = changed("new.py", status=FileStatus.RENAMED)
    file.previous_filename = "old.py"
    plan = build_review_plan(snapshot, [file], ["new.py"])
    assert any(r.path == "old.py" and r.side == "base" for r in plan.units[0].evidence)


def test_index_limit_does_not_get_bypassed_during_hunk_mapping():
    snapshot = MemorySnapshot({"a.py": "def a(): pass", "b.py": "def b(): pass"})
    plan = build_review_plan(
        snapshot, [changed("a.py"), changed("b.py")], ["a.py", "b.py"], index_file_limit=1
    )
    assert len(plan.files) == 1
    assert any(
        c.path == "b.py" and c.reason == "definition_source_unavailable" for c in plan.coverage
    )
    assert any(s.path == "b.py" for u in plan.units for s in u.slices)


def test_cut_report_flags_symbol_split_across_fragments_and_units():
    file = ChangedFile(
        filename="a.py", patch="@@ -1,1 +1,1 @@\n-old\n+" + ("abcdefgh! " * 500) + "\n"
    )
    plan = build_review_plan(
        MemorySnapshot({"a.py": "def big():\n    return 1\n"}), [file], ["a.py"], token_budget=128
    )
    entries = {(s.path, s.symbol): s for s in plan.cuts.split_symbols}
    assert entries[("a.py", "big")].fragments > 1
    assert entries[("a.py", "big")].units == len(plan.units) > 1
    assert plan.cuts.counts.split_symbols == len(plan.cuts.split_symbols) >= 1


def test_cut_report_lists_import_linked_changed_files_in_different_units():
    sources = {
        "app/api.py": "from .service import save\ndef route():\n    return save()\n",
        "app/service.py": "def save():\n    return 1\n",
    }
    files = [
        ChangedFile(
            filename="app/api.py", patch="@@ -1,1 +1,1 @@\n-old\n+" + ("abcdefgh! " * 60) + "\n"
        ),
        ChangedFile(
            filename="app/service.py", patch="@@ -1,1 +1,1 @@\n-old\n+" + ("abcdefgh! " * 60) + "\n"
        ),
    ]
    plan = build_review_plan(MemorySnapshot(sources), files, list(sources), token_budget=128)
    cut = [e for e in plan.cuts.cut_edges if e.kind == "import"]
    assert [(e.source, e.target, e.strength) for e in cut] == [
        ("app/api.py", "app/service.py", "strong")
    ]
    assert plan.cuts.counts.cut_edges_strong >= 1
    assert all(not e.targets_changed_symbol for e in cut)


def test_cut_report_finds_unchanged_caller_of_changed_function():
    sources = {
        "service.py": "def save():\n    return 1\n",
        "caller.py": "def run():\n    return save()\n",
    }
    plan = build_review_plan(MemorySnapshot(sources), [changed("service.py")], list(sources))
    assert [(c.source, c.target, c.symbol) for c in plan.cuts.external_callers_of_changed] == [
        ("caller.py", "service.py", "save")
    ]
    assert plan.cuts.counts.external_callers_of_changed == 1
    assert plan.cuts.counts.units == 1
    assert plan.cuts.cut_edges == ()


def test_python_import_aliases_and_same_named_methods_bind_to_distinct_symbols():
    sources = {
        "user.py": "def save():\n    return 'user'\n",
        "order.py": "def save():\n    return 'order'\n",
        "client.py": (
            "import user as u\nfrom order import save as save_order\n"
            "def run():\n    return u.save(), save_order()\n"
        ),
    }
    plan = build_review_plan(MemorySnapshot(sources), [changed("client.py")], list(sources))
    run_symbol = next(
        s
        for f in plan.files
        if f.path == "client.py" and f.side == "head"
        for s in f.symbols
        if s.name == "run"
    )
    index = PythonSymbolIndex(plan.files, plan.relationships, side="head")
    query = index.query(run_symbol.symbol_id, direction="callees")
    edges = {(e.target, e.symbol, e.evidence_level) for e in query.relationships}
    assert edges == {
        ("user.py", "save", "exact_binding"),
        ("order.py", "save_order", "exact_binding"),
    }
    assert query.searched_scope == ("client.py", "order.py", "user.py")
    assert query.completeness == "complete"


def test_python_assignment_parameter_and_nested_function_shadow_module_import():
    sources = {
        "service.py": "def save():\n    return 1\n",
        "caller.py": (
            "from service import save as persist\n"
            "def parameter(save):\n    return save()\n"
            "def outer():\n    persist = lambda: 2\n"
            "    def inner():\n        return persist()\n"
            "    return inner()\n"
            "def exact():\n    return persist()\n"
        ),
    }
    plan = build_review_plan(MemorySnapshot(sources), [changed("caller.py")], list(sources))
    calls = [e for e in plan.relationships if e.kind == "call" and e.source == "caller.py"]
    assert [(e.symbol, e.target, e.strength) for e in calls if e.symbol == "persist"] == [
        ("persist", "service.py", "strong")
    ]
    assert any(e.symbol == "inner" and e.strength == "strong" for e in calls)
    unresolved = {(u.name, u.reason) for u in plan.unresolved_references}
    assert ("save", "dynamic_binding") in unresolved
    assert ("persist", "dynamic_binding") in unresolved


def test_python_import_alias_shadows_module_and_local_module_assignment():
    sources = {
        "user.py": "def save():\n    return 1\n",
        "service.py": "def save():\n    return 2\n",
        "caller.py": (
            "import user as u\nfrom service import save\n"
            "u = replacement\n"
            "def parameter(u):\n    return u.save()\n"
            "save = replacement\n"
            "def run():\n    return save()\n"
            "def module_alias():\n    return u.save()\n"
        ),
    }
    plan = build_review_plan(MemorySnapshot(sources), [changed("caller.py")], list(sources))
    edges = [e for e in plan.relationships if e.kind == "call" and e.source == "caller.py"]
    assert edges == []
    assert (
        sum(
            r.side == "head" and r.name == "save" and r.reason == "dynamic_binding"
            for r in plan.unresolved_references
        )
        == 3
    )
    run = next(s for f in plan.files if f.path == "caller.py" for s in f.symbols if s.name == "run")
    result = PythonSymbolIndex.from_plan(plan, side="head").query(
        run.symbol_id, direction="callers"
    )
    assert result.completeness == "partial"


def test_shadowed_import_alias_marks_possible_caller_query_partial():
    sources = {
        "service.py": "def save(): return 1\n",
        "caller.py": "from service import save as persist\ndef run(persist): return persist()\n",
    }
    plan = build_review_plan(MemorySnapshot(sources), [changed("caller.py")], list(sources))
    target = next(s for f in plan.files if f.path == "service.py" for s in f.symbols)
    unresolved = next(ref for ref in plan.unresolved_references if ref.name == "persist")
    assert target.symbol_id in unresolved.possible_target_symbol_ids
    result = PythonSymbolIndex.from_plan(plan, side="head").query(
        target.symbol_id, direction="callers"
    )
    assert result.completeness == "partial"
    assert "unresolved:dynamic_binding" in result.omissions


def test_bare_method_name_does_not_resolve_through_class_namespace():
    source = parse_python(
        "client.py",
        "class C:\n    def save(self): pass\n    def run(self): return save()\n",
        "head",
        "1" * 40,
        "2" * 40,
    )
    edges, unresolved, _ = resolve_python_relationships((source,), {"client.py"}, side="head")
    assert edges == ()
    assert [(ref.name, ref.reason) for ref in unresolved] == [("save", "unresolved_name")]


def test_dotted_import_without_as_alias_is_not_treated_as_submodule_binding():
    sources = {
        "pkg/__init__.py": "",
        "pkg/sub.py": "def save(): return 1\n",
        "caller.py": "import pkg.sub\ndef run(): return pkg.save()\n",
    }
    plan = build_review_plan(MemorySnapshot(sources), [changed("caller.py")], list(sources))
    assert not [e for e in plan.relationships if e.kind == "call" and e.source == "caller.py"]
    assert any(
        r.name == "save" and r.reason == "dynamic_binding" for r in plan.unresolved_references
    )
    aliased = dict(sources)
    aliased["caller.py"] = "import pkg.sub as ps\ndef run(): return ps.save()\n"
    alias_plan = build_review_plan(MemorySnapshot(aliased), [changed("caller.py")], list(aliased))
    assert any(
        e.kind == "call" and e.strength == "strong" and e.target == "pkg/sub.py"
        for e in alias_plan.relationships
    )


def test_plan_index_query_propagates_omissions_and_unresolved_scope():
    sources = {
        "service.py": "def save(): return 1\n",
        "caller.py": "from service import save as persist\ndef run(persist): return persist()\n",
        "omitted.py": "def other(): return 2\n",
    }
    plan = build_review_plan(
        MemorySnapshot(sources),
        [changed("service.py"), changed("caller.py")],
        list(sources),
        index_file_limit=2,
    )
    target = next(s for f in plan.files if f.path == "service.py" for s in f.symbols)
    result = PythonSymbolIndex.from_plan(plan, side="head").query(
        target.symbol_id, direction="callers"
    )
    assert result.completeness == "partial"
    assert any(item.startswith("index_budget:") for item in result.omissions)
    assert "unresolved:dynamic_binding" in result.omissions


def test_base_relationships_keep_base_head_identity_for_changed_callers():
    base = {
        "service.py": "def save(): return 1\n",
        "caller.py": "from service import save\ndef run(): return save()\n",
    }
    head = {
        "service.py": "def save(): return 2\n",
        "caller.py": "from service import save\ndef run(): return save()\n",
    }
    plan = build_review_plan(
        MemorySnapshot(head, base), [changed("service.py"), changed("caller.py")], list(head)
    )
    base_edges = [
        edge for edge in plan.relationships if edge.kind == "call" and edge.side == "base"
    ]
    assert len(base_edges) == 1
    assert (base_edges[0].base, base_edges[0].head) == ("1" * 40, "2" * 40)
    assert (
        PythonSymbolIndex.from_plan(plan, side="base")
        .query(base_edges[0].target_symbol_id, direction="callers")
        .completeness
        == "partial"
    )


def test_python_dynamic_attribute_and_parse_failure_are_explicit_unknowns():
    sources = {"dynamic.py": "def run(obj):\n    return obj.save()\n", "bad.py": "def ???"}
    plan = build_review_plan(
        MemorySnapshot(sources), [changed("dynamic.py"), changed("bad.py")], list(sources)
    )
    assert any(
        u.reason == "dynamic_attribute" and u.name == "save" for u in plan.unresolved_references
    )
    assert "parse_failed" in plan.limitations
    dynamic_symbol = next(
        s
        for f in plan.files
        if f.path == "dynamic.py" and f.side == "head"
        for s in f.symbols
        if s.name == "run"
    )
    index = PythonSymbolIndex(
        plan.files,
        plan.relationships,
        side="head",
        unresolved_references=plan.unresolved_references,
    )
    result = index.query(dynamic_symbol.symbol_id, direction="callees")
    assert result.completeness == "partial"
    assert "unresolved:dynamic_attribute" in result.omissions
    failed_index = PythonSymbolIndex(plan.files, plan.relationships, side="head")
    assert (
        "parse_failed"
        in failed_index.query(dynamic_symbol.symbol_id, direction="callees").omissions
    )


def test_symbol_identity_is_side_and_snapshot_bound_and_queries_report_limits():
    cache = SourceIndexCache()
    head = cache.parse("a.py", "def a(): return b()\ndef b(): return 1", "head", "1" * 40, "2" * 40)
    base = cache.parse("a.py", "def a(): return b()\ndef b(): return 1", "base", "1" * 40, "2" * 40)
    assert head.symbols[0].symbol_id != base.symbols[0].symbol_id
    assert (head.symbols[0].base, head.symbols[0].head, head.symbols[0].side) == (
        "1" * 40,
        "2" * 40,
        "head",
    )
    caller = next(s for s in head.symbols if s.name == "a")
    target = next(s for s in head.symbols if s.name == "b")
    edge = next(
        e
        for e in resolve_python_relationships((head,), {"a.py"}, side="head")[0]
        if e.source_symbol_id == caller.symbol_id and e.target_symbol_id == target.symbol_id
    )
    index = PythonSymbolIndex(
        (head,),
        (edge,),
        side="head",
        max_depth=1,
        max_results=1,
        index_omissions=("index_scope_incomplete",),
    )
    result = index.query(caller.symbol_id, direction="callees", depth=2, limit=5)
    assert result.completeness == "partial"
    assert {"index_scope_incomplete", "depth_limit"} <= set(result.omissions)
    assert result.searched_scope == ("a.py",)
    other_side = PythonSymbolIndex((head,), (edge,), side="base")
    assert other_side.query(caller.symbol_id, direction="callees").completeness == "partial"


def test_plan_byte_limit_reports_missing_index_scope():
    sources = {"a.py": "def a(): return 1\n", "b.py": "def b(): return 2\n"}
    plan = build_review_plan(
        MemorySnapshot(sources), [changed("a.py")], list(sources), index_char_limit=5
    )
    assert "repository_index_budget_exhausted" in plan.limitations
    assert any(c.reason == "index_budget" for c in plan.coverage)
    assert any(omission.startswith("index_budget:") for omission in plan.index_omissions)


def test_fragment_spans_map_back_to_the_source_lines_they_cover():
    patch = "@@ -10,4 +10,5 @@\n keep1\n-old line\n+new line\n keep2\n+" + ("z" * 400) + "\n"
    file = ChangedFile(filename="a.py", status=FileStatus.MODIFIED, patch=patch)
    plan = build_review_plan(
        MemorySnapshot({"a.py": "def f():\n    return 1\n"}), [file], ["a.py"], token_budget=128
    )
    slices = [s for u in plan.units for s in u.slices]
    new_lines: set[int] = set()
    old_lines: set[int] = set()
    for s in slices:
        assert s.lines.new_start is not None or s.lines.old_start is not None
        if s.lines.new_start is not None:
            new_lines.update(range(s.lines.new_start, s.lines.new_end + 1))
        if s.lines.old_start is not None:
            old_lines.update(range(s.lines.old_start, s.lines.old_end + 1))
    # Context lines advance both sides; the added line only advances the new side.
    assert new_lines == {10, 11, 12, 13}
    assert old_lines == {10, 11, 12}
    whole = [s for s in slices if not s.lines.cuts_a_diff_line]
    assert len(whole) == 1
    assert (whole[0].lines.new_start, whole[0].lines.new_end) == (10, 12)
    cut = [s for s in slices if s.lines.cuts_a_diff_line]
    assert len(cut) >= 2
    assert {(s.lines.new_start, s.lines.new_end) for s in cut} == {(13, 13)}
    assert all(s.lines.old_start is None for s in cut)


def test_coverage_summary_accounts_for_every_changed_file_and_hunk():
    files = [
        changed("ok.py"),
        ChangedFile(filename="nopatch.py", patch="not a unified diff\n"),
        ChangedFile(filename=".env", patch=None),
    ]
    plan = build_review_plan(MemorySnapshot({"ok.py": "x = 1\n"}), files, ["ok.py"])
    summary = plan.coverage_summary
    assert summary.changed_files == 3
    assert summary.parsed_hunks == 1
    assert summary.planned_hunks == 1
    assert summary.unaccounted_hunks == ()
    assert summary.unaccounted_files == ()
    assert summary.complete is True
    reasons = {o.reason for o in plan.budgets.omissions}
    assert {"missing_or_invalid_patch", "source_policy", "head_unavailable"} <= reasons
    assert plan.budgets.scope == "build_review_plan"
    assert plan.budgets.over_limit == ()


def test_plan_budget_lines_separate_per_unit_cap_from_measured_total():
    sources = {f"file{i}.py": f"value = {i}\n" for i in range(6)}
    files = [changed(p) for p in sources]
    plan = build_review_plan(MemorySnapshot(sources), files, list(sources), token_budget=256)
    lines = {line.name: line for line in plan.budgets.lines}
    assert plan.version == "syntax-plan-v3"
    assert lines["diff_fragments_per_unit"].limit == 256
    assert lines["diff_fragments_per_unit"].used <= 256
    assert lines["diff_fragments_total"].limit is None
    assert lines["diff_fragments_total"].used == sum(u.estimated_tokens for u in plan.units)
    assert plan.budgets.over_limit == ()
