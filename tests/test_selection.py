"""Budget-constrained hunk selection: exact, deterministic, and honest about coverage."""

from __future__ import annotations

from src.analyzer.selection import (
    SelectionPolicy,
    select_hunks,
    select_hunks_optimal,
)
from src.models import ChangedFile, DiffHunk


def hunk(start: int, size: int, path: str = "src/api.py") -> DiffHunk:
    return DiffHunk(file_path=path, old_start=start, old_count=size, new_start=start,
                    new_count=size, header=f"@@ -{start},{size} +{start},{size} @@",
                    added_lines=[], removed_lines=[], raw="")


def files_and_hunks():
    files = [
        ChangedFile(filename="src/api.py", patch="+x\n" * 10),
        ChangedFile(filename="src/deep/inner.py", patch="+y\n" * 10),
    ]
    hunks = {
        "src/api.py": [hunk(1, 10), hunk(50, 10)],
        "src/deep/inner.py": [hunk(1, 10, "src/deep/inner.py")],
    }
    return files, hunks


def test_selection_is_deterministic_and_respects_the_budget():
    files, hunks = files_and_hunks()
    first = select_hunks_optimal(files, hunks, budget=15, rule_hits={"src/api.py"})
    second = select_hunks_optimal(files, hunks, budget=15, rule_hits={"src/api.py"})
    assert first.selected == second.selected
    assert first.covered_tokens <= 15
    assert len(first.selected) + len(first.uncovered) == 3


def test_every_hunk_ends_up_selected_or_reported_as_uncovered():
    files, hunks = files_and_hunks()
    result = select_hunks_optimal(files, hunks, budget=5)
    assert len(result.selected) + len(result.uncovered) == 3
    assert result.token_coverage + (1 - result.token_coverage) == 1.0
    assert 0.0 <= result.value_coverage <= 1.0


def test_a_generous_budget_covers_everything():
    files, hunks = files_and_hunks()
    result = select_hunks_optimal(files, hunks, budget=1000)
    assert result.uncovered == []
    assert result.value_coverage == 1.0
    assert result.token_coverage == 1.0


def test_rule_hits_and_public_interfaces_outrank_internal_code():
    files, hunks = files_and_hunks()
    # One hunk costs 20 tokens (10 old + 10 new), so the budget fits exactly one.
    result = select_hunks_optimal(files, hunks, budget=20, rule_hits={"src/api.py"})
    chosen = {(path, h.new_start) for path, h, _tokens in result.selected}
    # src/api.py is both public and rule-flagged, so it wins the single slot.
    assert chosen == {("src/api.py", 1)}


def test_greedy_and_optimal_both_report_coverage_against_the_same_denominator():
    files, hunks = files_and_hunks()
    greedy = select_hunks(files, hunks, budget=0, rule_hits={"src/api.py"})
    optimal = select_hunks_optimal(files, hunks, budget=0, rule_hits={"src/api.py"})
    assert greedy.total_tokens == optimal.total_tokens
    assert greedy.total_value == optimal.total_value
    assert greedy.covered_tokens == 0 and optimal.covered_tokens == 0


def test_policy_weights_are_declared_not_hidden():
    policy = SelectionPolicy()
    assert policy.value(lines_changed=1, import_edges=0, rule_hit=False,
                        public_interface=False) == 1.0
    assert policy.value(lines_changed=1, import_edges=99, rule_hit=True,
                        public_interface=True) == 1.0 + 3.0 * 5 + 5.0 + 2.0


def test_cost_of_lets_the_budget_be_token_denominated():
    """A hunk costs what it costs: lines are a proxy, tokens are the real budget."""
    files, hunks = files_and_hunks()

    def cost_of(_file, hunk):
        return 100  # every hunk is expensive, so one fills a 150-cost budget

    result = select_hunks_optimal(files, hunks, budget=150, cost_of=cost_of)
    assert len(result.selected) == 1
    assert result.uncovered
