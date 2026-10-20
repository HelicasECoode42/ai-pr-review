import subprocess

from src.analyzer.repository_context import GitSnapshot
from src.evaluation.reviewbench import parse_diff
from src.reviewer.discovery_lenses import changed_symbols, lens_facts, runtime_constraints


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def make_repo(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "Fixture")
    (tmp_path / "demo.gemspec").write_text("Gem::Specification.new do |s|\n"
                                           "  s.required_ruby_version = '>= 1.9.2'\nend\n")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "parse.rb").write_text("def parse_size(io)\n  1\nend\n")
    (tmp_path / "lib" / "caller.rb").write_text("x = parse_resolution(io)\n")
    (tmp_path / "README.md").write_text("Use parse_size.\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "lib" / "parse.rb").write_text(
        "def parse_size(io)\n  2\nend\n\ndef parse_resolution(io, units: true)\n  io&.read\nend\n")
    (tmp_path / "README.md").write_text("Use parse_size and parse_resolution.\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "head")
    head = git(tmp_path, "rev-parse", "HEAD")
    return GitSnapshot(tmp_path, base, head), parse_diff(git(tmp_path, "diff", base, head))


def test_runtime_constraints_read_top_level_manifests(tmp_path):
    snapshot, _ = make_repo(tmp_path)
    assert runtime_constraints(snapshot) == ["demo.gemspec:2: s.required_ruby_version = '>= 1.9.2'"]


def test_changed_symbols_skip_docs_and_tests(tmp_path):
    _, files = make_repo(tmp_path)
    names = {name for _, name in changed_symbols(files)}
    assert "parse_resolution" in names
    assert all(path == "lib/parse.rb" for path, _ in changed_symbols(files))


def test_lens_facts_render_runtime_untested_callers_and_docs(tmp_path):
    snapshot, files = make_repo(tmp_path)
    facts = lens_facts(snapshot, files)
    assert "required_ruby_version = '>= 1.9.2'" in facts
    assert "parse_resolution (lib/parse.rb)" in facts  # no test references it
    assert "lib/caller.rb:1" in facts  # a caller outside the changed file
    assert "- README.md" in facts
