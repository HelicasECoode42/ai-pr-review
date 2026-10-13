"""Score an experiment using independent, exhaustive human/judge annotations."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.evaluation.scoring import score


def summarize(dataset: list[dict], experiment: dict, annotations: list[dict]) -> dict:
    cases = {case["case_id"]: case for case in dataset}
    if len(cases) != len(dataset):
        raise ValueError("Duplicate dataset case IDs")
    expected_hash = experiment.get("dataset_sha256")
    if (
        expected_hash
        and expected_hash
        != hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest()
    ):
        raise ValueError("Dataset does not match experiment fingerprint")
    decisions = {}
    for item in annotations:
        key = (item["case_id"], item["variant"])
        if key in decisions or not item.get("reviewer") or not item.get("exhaustive"):
            raise ValueError("Require unique exhaustive annotations with reviewer provenance")
        decisions[key] = item
    variants = {}
    records = experiment["records"]
    keys = [(r["case_id"], r["variant"]) for r in records]
    if len(keys) != len(set(keys)) or set(decisions) - set(keys):
        raise ValueError("Duplicate records or annotations without a corresponding run")
    for record in records:
        if record["case_id"] not in cases:
            raise ValueError("Unknown case")
        variant = record["variant"]
        aggregate = variants.setdefault(
            variant, {"tp": 0, "fp": 0, "fn": 0, "failed_cases": 0, "unadjudicated_cases": 0}
        )
        if record["status"] != "success":
            aggregate["failed_cases"] += 1
            continue
        key = (record["case_id"], variant)
        if key not in decisions:
            aggregate["unadjudicated_cases"] += 1
            continue
        case = cases[record["case_id"]]
        result = score(
            len(record["output"]["findings"]),
            [f["id"] for f in case["findings"]],
            [tuple(edge) for edge in decisions[key]["matches"]],
        )
        for count in ("tp", "fp", "fn"):
            aggregate[count] += result[count]
    for variant, aggregate in variants.items():
        present = {r["case_id"] for r in records if r["variant"] == variant}
        aggregate["unadjudicated_cases"] += len(set(cases) - present)
        if aggregate["failed_cases"] or aggregate["unadjudicated_cases"]:
            aggregate.update(precision=None, recall=None, f1=None)
        else:
            tp, fp, fn = (aggregate[k] for k in ("tp", "fp", "fn"))
            aggregate.update(
                precision=tp / (tp + fp) if tp + fp else None,
                recall=tp / (tp + fn) if tp + fn else None,
                f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            )
    return {
        "scoring_protocol": "strict_internal_adjudication_not_official_reviewbench",
        "variants": variants,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("experiment", type=Path)
    parser.add_argument("annotations", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = summarize(
        *[
            json.loads(path.read_text())
            for path in (args.dataset, args.experiment, args.annotations)
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
