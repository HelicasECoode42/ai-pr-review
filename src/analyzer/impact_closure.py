"""Impact closure: what a reviewer must have seen for a change to be judgeable.

Hunk coverage answers "how many lines did we show". It does not answer the
question that decides whether a defect is findable: were the callers and the
contract of the changed symbol in context? A changed guard is only judgeable if
the inputs its callers pass are known.

The closure of a diff is defined on the symbol graph:

    closure = { changed symbols } ∪ { 1-hop callers of changed symbols }

and the coverage we report is the fraction of that closure that reached the
prompt. Both are computed from the frozen snapshot with no model calls, so the
number is reproducible and can gate a release.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.models import ChangedFile
from src.reviewer.suggestion_filter import locate_symbol


@dataclass
class ImpactClosure:
    changed_symbols: set[str] = field(default_factory=set)
    callers: dict[str, set[str]] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)

    @property
    def nodes(self) -> set[str]:
        """Every symbol that should have been visible: changed ∪ callers."""
        out = set(self.changed_symbols)
        for callers in self.callers.values():
            out |= set(callers)
        return out

    def summary(self) -> dict:
        return {
            "changed_symbols": len(self.changed_symbols),
            "caller_nodes": len(self.nodes - self.changed_symbols),
            "closure_nodes": len(self.nodes),
            "unresolved_hunks": len(self.unresolved),
        }


def build_impact_closure(files: list[ChangedFile], hunks_by_file, snapshot,
                         *, caller_limit: int = 3) -> ImpactClosure:
    """Changed symbols plus one hop of callers, read from the frozen snapshot."""
    closure = ImpactClosure()
    for file in files:
        for hunk in hunks_by_file.get(file.filename, []):
            symbol, status, _reason = locate_symbol(files, file.filename, hunk.new_start)
            if status != "resolved" or not symbol:
                closure.unresolved.append(f"{file.filename}:{hunk.new_start}:{status}")
                continue
            closure.changed_symbols.add(symbol)
            if symbol in closure.callers:
                continue
            try:
                hits = snapshot.search(symbol, side="head", limit=caller_limit)
            except Exception:  # a search backend must not break the metric
                closure.callers[symbol] = set()
                continue
            changed_paths = {f.filename for f in files}
            closure.callers[symbol] = {
                f"{path}:{line}" for path, line in hits if path not in changed_paths
            }
    return closure


def impact_coverage(closure: ImpactClosure, seen_text: str) -> dict:
    """How much of the closure actually reached the prompt.

    A symbol counts as seen when its name appears in the text we sent, which is a
    lower bound: a symbol whose definition was included without its name being
    echoed is counted as missing rather than assumed present.
    """
    nodes = closure.nodes
    if not nodes:
        return {"closure_nodes": 0, "seen": 0, "coverage": 1.0, "missing": []}
    seen = {node for node in nodes if node in seen_text}
    missing = sorted(nodes - seen)
    return {
        "closure_nodes": len(nodes),
        "seen": len(seen),
        "coverage": round(len(seen) / len(nodes), 4),
        "missing": missing[:20],
    }
