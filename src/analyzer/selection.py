"""Choose which hunks to review under a token budget, by value density.

Context building used to truncate in file order, which is arbitrary: the tail was
dropped regardless of whether it mattered, and the only defence was an omission
list. When the diff does not fit, choosing what to review is a knapsack problem,
so it is solved as one - greedily, by value per token, because the sizes are tiny
and the ordering is stable.

The value function is a declared policy, not a hidden heuristic:

    value(hunk) = lines_changed
                + centrality_weight   * min(import_edges, cap)
                + rule_hit_weight     * (1 if a rule already flagged this file)
                + public_weight       * (1 if the hunk touches a public interface)

Every factor is observable before any model call, so the selection is reproducible
and the uncovered set can be reported with the value that was left on the table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.models import ChangedFile, DiffHunk


@dataclass(frozen=True)
class SelectionPolicy:
    centrality_weight: float = 3.0
    centrality_cap: int = 5
    rule_hit_weight: float = 5.0
    public_weight: float = 2.0

    def value(self, *, lines_changed: int, import_edges: int, rule_hit: bool,
              public_interface: bool) -> float:
        return (
            float(lines_changed)
            + self.centrality_weight * min(import_edges, self.centrality_cap)
            + self.rule_hit_weight * (1.0 if rule_hit else 0.0)
            + self.public_weight * (1.0 if public_interface else 0.0)
        )


@dataclass
class SelectionResult:
    selected: list[tuple[str, DiffHunk, int]] = field(default_factory=list)
    uncovered: list[tuple[str, DiffHunk, int]] = field(default_factory=list)
    total_tokens: int = 0
    covered_tokens: int = 0
    total_value: float = 0.0
    covered_value: float = 0.0

    @property
    def token_coverage(self) -> float:
        return self.covered_tokens / self.total_tokens if self.total_tokens else 1.0

    @property
    def value_coverage(self) -> float:
        return self.covered_value / self.total_value if self.total_value else 1.0

    def summary(self) -> dict:
        return {
            "hunks_total": len(self.selected) + len(self.uncovered),
            "hunks_selected": len(self.selected),
            "hunks_uncovered": len(self.uncovered),
            "tokens_total": self.total_tokens,
            "tokens_covered": self.covered_tokens,
            "token_coverage": round(self.token_coverage, 4),
            "value_total": round(self.total_value, 2),
            "value_covered": round(self.covered_value, 2),
            "value_coverage": round(self.value_coverage, 4),
        }


def _public_interface(path: str) -> bool:
    """Top-level modules are interfaces in practice; nested ones are internals."""
    parts = [p for p in path.split("/") if p]
    return len(parts) <= 2


def select_hunks(
    files: list[ChangedFile],
    hunks: dict[str, list[DiffHunk]],
    *,
    budget: int,
    policy: SelectionPolicy | None = None,
    import_edges: dict[str, int] | None = None,
    rule_hits: set[str] | None = None,
) -> SelectionResult:
    """Greedy value-density fill. Deterministic: ties break on (path, hunk index)."""
    policy = policy or SelectionPolicy()
    edges = import_edges or {}
    hits = rule_hits or set()
    candidates: list[tuple[float, float, str, DiffHunk, int]] = []
    result = SelectionResult()
    for file in files:
        for hunk in hunks.get(file.filename, []):
            body = "\n".join(
                line for line in (file.patch or "").splitlines()
                if line[:1] in {"+", "-"} and not line.startswith(("+++", "---"))
            )
            tokens = hunk.new_count + hunk.old_count
            value = policy.value(
                lines_changed=tokens,
                import_edges=edges.get(file.filename, 0),
                rule_hit=file.filename in hits,
                public_interface=_public_interface(file.filename),
            )
            candidates.append((value, tokens, file.filename, hunk, len(body)))
            result.total_tokens += tokens
            result.total_value += value
    # Value density first; ties break deterministically so two runs agree.
    candidates.sort(key=lambda item: (-(item[0] / max(item[1], 1)), item[2], item[3].new_start))
    remaining = budget
    for value, tokens, path, hunk, _chars in candidates:
        if tokens <= remaining:
            result.selected.append((path, hunk, tokens))
            remaining -= tokens
            result.covered_tokens += tokens
            result.covered_value += value
        else:
            result.uncovered.append((path, hunk, tokens))
    return result


def select_hunks_optimal(
    files: list[ChangedFile],
    hunks: dict[str, list[DiffHunk]],
    *,
    budget: int,
    policy: SelectionPolicy | None = None,
    import_edges: dict[str, int] | None = None,
    rule_hits: set[str] | None = None,
) -> SelectionResult:
    """0/1 knapsack over hunks: maximise total value within the token budget.

    Density-greedy looks attractive but measurably starves large hunks, because
    the value function has fixed charges (rule hit, public interface, centrality)
    that make small hunks look dense. Exact DP is affordable here - hundreds of
    hunks against a four-digit budget - so the optimum is used and the ordering is
    deterministic.
    """
    policy = policy or SelectionPolicy()
    edges = import_edges or {}
    hits = rule_hits or set()
    items: list[tuple[str, DiffHunk, int, int]] = []
    result = SelectionResult()
    for file in files:
        for hunk in hunks.get(file.filename, []):
            tokens = max(1, hunk.new_count + hunk.old_count)
            value = int(round(policy.value(
                lines_changed=tokens,
                import_edges=edges.get(file.filename, 0),
                rule_hit=file.filename in hits,
                public_interface=_public_interface(file.filename),
            )))
            items.append((file.filename, hunk, tokens, value))
            result.total_tokens += tokens
            result.total_value += value
    if not items:
        return result
    n = len(items)
    # take[i][b] = best value using the first i items with capacity b
    take = [bytearray(budget + 1) for _ in range(n + 1)]
    best = [0] * (budget + 1)
    for index, (_path, _hunk, tokens, value) in enumerate(items, start=1):
        row = take[index]
        previous = best[:]
        for capacity in range(tokens, budget + 1):
            candidate = previous[capacity - tokens] + value
            if candidate > best[capacity]:
                best[capacity] = candidate
                row[capacity] = 1
    chosen: list[tuple[str, DiffHunk, int]] = []
    capacity = budget
    for index in range(n, 0, -1):
        if take[index][capacity]:
            path, hunk, tokens, value = items[index - 1]
            chosen.append((path, hunk, tokens))
            result.covered_tokens += tokens
            result.covered_value += value
            capacity -= tokens
    chosen.sort(key=lambda item: (item[0], item[1].new_start))
    selected_ids = {(p, h.new_start) for p, h, _t in chosen}
    result.selected = chosen
    result.uncovered = [
        (path, hunk, tokens)
        for path, hunk, tokens, _value in items
        if (path, hunk.new_start) not in selected_ids
    ]
    return result
