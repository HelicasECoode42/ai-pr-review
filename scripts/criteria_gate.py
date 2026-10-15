#!/usr/bin/env python
"""Offline criteria gate: the witness check must still separate equivalence from defect.

This is the regression gate for H4/H5 and it costs nothing: it needs no model, no
network and no API key. It re-derives the base and head expressions from the
checked-in gold diffs and asserts that

* every case labelled safe is mechanically indistinguishable (``no_witness``), so
  it can never be published as a confirmed behaviour change, and
* every case carrying a seeded defect yields a concrete witness.

A change that breaks either property fails CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.knowledge.playbook import contract_domains, load_playbook  # noqa: E402
from src.reviewer.criteria import domain_violation  # noqa: E402
from src.reviewer.witness import evaluate_equivalence, expression_from_quote  # noqa: E402

PLAYBOOK = REPO_ROOT / "knowledge/playbook.md"

# What each checked-in case must do under the criteria. `safe-index` is provably
# equivalent; `safe-division` differs only for an input the declared contract
# excludes; the other two carry seeded defects and must yield a witness.
EXPECTED = {
    "safe-index": "no_witness",
    "safe-division": "blocked_by_contract",
    "division": "differs",
    "negative-index": "differs",
}

GOLD = REPO_ROOT / "datasets/golden/synthetic-dev.json"


def sides(diff: str) -> tuple[str | None, str | None]:
    base = head = None
    for line in diff.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            base = expression_from_quote(line) or base
        elif line.startswith("+") and not line.startswith("+++"):
            head = expression_from_quote(line) or head
    return base, head


def main() -> int:
    cases = json.loads(GOLD.read_text(encoding="utf-8"))
    domains = contract_domains(load_playbook(PLAYBOOK))
    failures: list[str] = []
    checked = 0
    for case in cases:
        case_id = case["case_id"]
        base, head = sides(case["diff"])
        if not base or not head:
            failures.append(f"{case_id}: could not derive an expression pair")
            continue
        result = evaluate_equivalence(base, head)
        if result.status == "differs" and domain_violation(result.witness, domains):
            observed = "blocked_by_contract"
        else:
            observed = result.status
        expected = EXPECTED.get(case_id, "differs" if case.get("findings") else "no_witness")
        checked += 1
        status = "ok" if observed == expected else "FAIL"
        print(f"{status:4s} {case_id:16s} expected={expected:18s} got={observed:18s} "
              f"domain={result.domain_size}")
        if observed != expected:
            failures.append(f"{case_id}: expected {expected}, got {observed} ({result.reason})")
    print(f"\nchecked {checked} case(s)")
    if failures:
        print("criteria gate FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("criteria gate passed: equivalence is rejected, defects produce witnesses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
