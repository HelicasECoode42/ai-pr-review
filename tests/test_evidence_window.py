"""Regression tests for frozen evidence windows and automatic seeding.

These cover the failure found on the CrabTrap dev case: Go methods were not
recognised, a fallback declaration regex invented symbols from SQL/builtins, and
`follow()` only seeded the first two hunks so a helper in a later hunk never
reached the prompt.
"""

from __future__ import annotations

import subprocess

from src.analyzer.diff_parser import parse_file_hunks
from src.analyzer.repository_context import (
    MAX_SEED_WINDOW_LINES,
    EvidenceBundle,
    GitSnapshot,
    function_window,
)
from src.evaluation.reviewbench import parse_diff


def _run(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _window(path, text, line):
    return function_window(path, text, line)


GO_SOURCE = """package admin

import "context"

type Reader struct{}

func (r *Reader) Query(filter string) string {
	return r.query(filter, "id")
}

func (r *Reader) query(filter string, cols string) string {
	ctx := context.Background()
	_ = ctx
	rows := r.pool.Query(ctx, filter)
	if rows == nil {
		return ""
	}
	return cols
}
"""


def test_go_receiver_method_resolves_to_full_definition():
    start, end, symbol, extent = _window("reader.go", GO_SOURCE, 11)
    assert symbol == "query"
    assert extent == "heuristic_brace_definition"
    assert start == 11
    assert end == 19


def test_go_receiver_method_window_contains_context_and_return_type():
    # The changed line is inside the SQL body; the window must still include the
    # ``ctx := context.Background()`` line that the bug depends on.
    start, end, symbol, _ = _window("reader.go", GO_SOURCE, 15)
    assert symbol == "query"
    lines = GO_SOURCE.splitlines()
    assert any("context.Background()" in lines[number - 1] for number in range(start, end + 1))


def test_go_interface_literal_in_signature_does_not_break_the_body():
    source = """package admin

func scan(rows interface {
	Scan(dest ...interface{}) error
}) *Entry {
	value := rows.Scan()
	if value != nil {
		return nil
	}
	return &Entry{}
}
"""
    start, end, symbol, _ = _window("scan.go", source, 7)
    assert symbol == "scan"
    assert start == 3
    assert end == 9


def test_fallback_does_not_invent_symbols_from_sql_or_builtins():
    source = """package admin

const selectCols = `
	al.id, al.name
	FROM audit_log al
	WHERE al.id IN (
`
const summaryCols = `
	al.id
`
"""
    # A changed line inside the raw SQL string is not a definition. The old
    # fallback returned the SQL word "IN" as a symbol.
    _, _, symbol, extent = _window("reader.go", source, 4)
    assert symbol is None
    assert extent == "source_window"
    source_with_len = "package admin\n\nfunc check() {\n\tif len(items) > 0 {\n\t\treturn\n\t}\n}\n"
    start, end, symbol, _ = _window("check.go", source_with_len, 4)
    assert symbol == "check"
    assert start == 3


def test_unknown_go_receiver_method_is_found_by_the_source_window_scanner():
    source = (
        "package admin\n\nfunc (r *Reader) Query(\n"
        "\tfilter string,\n) string {\n\treturn r.query(filter)\n}\n"
    )
    start, end, symbol, extent = _window("reader.go", source, 6)
    # The multiline signature is still recognised as the enclosing declaration.
    assert symbol == "Query"
    assert start <= 3
    assert end >= 7


def test_add_caps_component_windows_to_the_evidence_limit():
    lines = (
        ["package main", "func (r *Reader) huge() {"]
        + [f"\t_ = {index}" for index in range(300)]
        + ["}"]
    )
    source = "\n".join(lines) + "\n"

    class Snapshot:
        base = "a" * 40
        head = "b" * 40

        def read(self, path, side="head"):
            return source

        def search(self, symbol, side="head", limit=6):
            return []

        def audit(self):
            return {"errors": []}

    bundle = EvidenceBundle(Snapshot(), budget=6000)
    bundle.add("huge.go", 150, max_lines=MAX_SEED_WINDOW_LINES)
    assert len(bundle.excerpts) == 1
    excerpt = bundle.excerpts[0]
    assert excerpt["end_line"] - excerpt["start_line"] + 1 <= MAX_SEED_WINDOW_LINES


def test_follow_seeds_a_helper_in_a_later_hunk(tmp_path):
    """A helper definition in hunk 3 must reach the bundle, not only hunks 1-2."""
    filler = "\n".join(f"// filler {index}" for index in range(12))
    base_text = (
        "package main\n\n"
        "func first() {\n\treturn\n}\n\n"
        f"{filler}\n\n"
        "func second() {\n\treturn\n}\n\n"
        f"{filler}\n\n"
        "func third() {\n\treturn\n}\n"
    )
    head_text = base_text.replace(
        "func first() {\n\treturn\n}",
        "func first() {\n\treturn 1\n}",
    ).replace(
        "func second() {\n\treturn\n}",
        "func second() {\n\treturn 2\n}",
    ).replace(
        "func third() {\n\treturn\n}",
        "func third() {\n\treturn 3\n}",
    )
    (tmp_path / "main.go").write_text(base_text)
    _run(tmp_path, "init")
    _run(tmp_path, "config", "user.email", "fixture@example.invalid")
    _run(tmp_path, "config", "user.name", "Fixture")
    _run(tmp_path, "add", ".")
    _run(tmp_path, "commit", "-m", "base")
    base = _run(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "main.go").write_text(head_text)
    _run(tmp_path, "add", ".")
    _run(tmp_path, "commit", "-m", "head")
    head = _run(tmp_path, "rev-parse", "HEAD")
    patch = subprocess.check_output(
        ["git", "-C", str(tmp_path), "diff", base, head], text=True
    )
    files = parse_diff(patch)
    assert files and len(parse_file_hunks(files[0])) >= 3

    bundle = EvidenceBundle(GitSnapshot(tmp_path, base, head), budget=6000)
    bundle.follow(files)
    head_excerpts = [item for item in bundle.excerpts if item["side"] == "head"]
    assert any(item["symbol"] == "third" for item in head_excerpts), [
        (item["symbol"], item["start_line"], item["end_line"]) for item in head_excerpts
    ]
