"""Write a prepared ReviewBench input file restricted to one split set (dev or holdout)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def subset(prepared: dict, split: dict, name: str) -> dict:
    wanted = split[name]
    by_id = {record["case_id"]: record for record in prepared["records"]}
    missing = [case_id for case_id in wanted if case_id not in by_id]
    if missing:
        raise KeyError(f"{name} case ids missing from prepared inputs: {', '.join(missing)}")
    return {
        **prepared,
        "subset": name,
        "task_count": len(wanted),
        "records": [by_id[case_id] for case_id in wanted],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared", type=Path)
    parser.add_argument("split", type=Path)
    parser.add_argument("--set", dest="name", required=True, choices=("dev", "holdout"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = subset(
        json.loads(args.prepared.read_text()), json.loads(args.split.read_text()), args.name
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"{args.name}: {len(result['records'])} cases -> {args.output}")


if __name__ == "__main__":
    main()
