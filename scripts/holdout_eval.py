#!/usr/bin/env python
"""Held-out evaluation of the witness criteria, with no model calls.

The dev set in datasets/golden/synthetic-dev.json is what the criteria gate is
tuned against, so it cannot also be the evidence that the criteria generalise.
This set is disjoint from it: fourteen guard-and-boundary expression pairs whose
verdict is mechanically decidable, half of them equivalence-preserving rewrites
that must NOT produce a witness and half that must.

Scope, stated plainly: this measures the criterion, not the model's ability to
propose candidates. It is free, deterministic, and therefore usable as a gate.

    .venv/bin/python scripts/holdout_eval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.reviewer.witness import evaluate_equivalence  # noqa: E402

HOLDOUT = REPO_ROOT / "datasets/golden/guard-holdout.jsonl"
EXPECTED = {"witness": "differs", "equivalent": "no_witness"}


def main() -> int:
    cases = [json.loads(line) for line in HOLDOUT.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    per_class = {"witness": [0, 0], "equivalent": [0, 0]}  # [correct, total]
    failures: list[str] = []
    for case in cases:
        result = evaluate_equivalence(case["base"], case["head"])
        want = EXPECTED[case["expects"]]
        ok = result.status == want
        per_class[case["expects"]][1] += 1
        if ok:
            per_class[case["expects"]][0] += 1
        else:
            failures.append(f"{case['id']}: expected {want}, got {result.status} ({result.reason})")
        print(f"{'ok  ' if ok else 'FAIL'} {case['id']:28s} expects={case['expects']:10s} "
              f"got={result.status:10s} domain={result.domain_size}")
    print()
    for name, (correct, total) in per_class.items():
        print(f"{name:10s} {correct}/{total} = {correct / total:.0%}")
    if failures:
        print("\nholdout FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("holdout passed: equivalence rejected, behaviour changes witnessed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
