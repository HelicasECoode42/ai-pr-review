"""Finding-level scoring from independently adjudicated match edges.

Location overlap alone is never a semantic match. A human or external judge
supplies possible (prediction index, golden id) matches, then maximum matching
prevents duplicate comments from inflating recall.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field, model_validator


class GoldenFinding(BaseModel):
    id: str
    file: str
    description: str
    failure_scenario: str
    category: str
    severity: str


class GoldenCase(BaseModel):
    case_id: str
    source: str
    split: str = Field(pattern="^(dev|validation|test)$")
    group: str
    metadata: dict
    diff: str
    findings: list[GoldenFinding]
    label_provenance: str
    negative_verified: bool = False

    @model_validator(mode="after")
    def validate_labels(self):
        ids = [f.id for f in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate golden finding IDs")
        if not ids and not self.negative_verified:
            raise ValueError("Negative cases require explicit verification")
        return self


def validate_splits(cases: list[GoldenCase]) -> None:
    seen: set[str] = set()
    groups: dict[str, str] = {}
    for case in cases:
        if case.case_id in seen:
            raise ValueError("Duplicate case IDs")
        seen.add(case.case_id)
        if case.group in groups and groups[case.group] != case.split:
            raise ValueError("Repository/task group leaks across splits")
        groups[case.group] = case.split


def score(prediction_count: int, golden_ids: list[str], edges: list[tuple[int, str]]) -> dict:
    if prediction_count < 0 or len(set(golden_ids)) != len(golden_ids):
        raise ValueError("Invalid finding counts or IDs")
    graph: dict[int, set[str]] = {}
    for prediction, golden in edges:
        if prediction < 0 or prediction >= prediction_count or golden not in golden_ids:
            raise ValueError("Invalid adjudication edge")
        graph.setdefault(prediction, set()).add(golden)
    assigned: dict[str, int] = {}

    def augment(prediction: int, visited: set[str]) -> bool:
        for golden in sorted(graph.get(prediction, set())):
            if golden in visited:
                continue
            visited.add(golden)
            if golden not in assigned or augment(assigned[golden], visited):
                assigned[golden] = prediction
                return True
        return False

    for prediction in range(prediction_count):
        augment(prediction, set())
    tp = len(assigned)
    fp, fn = prediction_count - tp, len(golden_ids) - tp
    precision = tp / prediction_count if prediction_count else None
    recall = tp / len(golden_ids) if golden_ids else None
    denominator = 2 * tp + fp + fn
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * tp / denominator if denominator else None,
        "matches": assigned,
    }


def accept_candidate(baseline: dict, candidate: dict, *, max_latency_ratio=1.5) -> bool:
    """Fail closed for missing metrics, recall loss, failed cases or budget excess."""
    keys = ("precision", "recall", "f1", "latency_seconds")
    for result in (baseline, candidate):
        if result.get("failed_cases", 1) != 0:
            return False
        if any(
            not isinstance(result.get(k), (float, int)) or not math.isfinite(result[k])
            for k in keys
        ):
            return False
        if any(not 0 <= result[k] <= 1 for k in keys[:3]):
            return False
        if result["latency_seconds"] <= 0:
            return False
    return (
        candidate["precision"] >= baseline["precision"]
        and candidate["recall"] >= baseline["recall"]
        and candidate["f1"] > baseline["f1"]
        and candidate["latency_seconds"] <= baseline["latency_seconds"] * max_latency_ratio
    )
