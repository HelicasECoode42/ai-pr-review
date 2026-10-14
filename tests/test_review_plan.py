from __future__ import annotations

import pytest
from typer.testing import CliRunner

from src.analyzer.context_builder import count_tokens
from src.analyzer.review_plan import SourceIndexCache, build_review_plan
from src.cli.main import app
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
    refs = [e for e in plan.relationships if e.kind == "symbol_reference"]
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
        app, ["plan", "--repo-path", str(tmp_path), "--base", base, "--output", str(output)]
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
