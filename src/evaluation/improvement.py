"""Constrained configuration search; never edits production code or judge labels."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.evaluation.scoring import accept_candidate


class ReviewStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    prompt_addendum: str = Field(default="", max_length=2000)
    min_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    evidence: bool = True
    critic: bool = False
    context_pack: bool = False


def improve(
    current: ReviewStrategy,
    failures: list[dict],
    provider,
    evaluate: Callable[[ReviewStrategy, str], dict],
    *,
    split: Literal["dev"] = "dev",
) -> dict:
    if split != "dev":
        raise ValueError("Only development failures may enter candidate generation")
    raw = provider.complete_json(
        "Propose one review configuration from development failures. Return JSON with only "
        "prompt_addendum, min_confidence, evidence, critic, context_pack. "
        "Describe general review checks, never task-specific answers. Do not change scoring.",
        json.dumps({"current": current.model_dump(), "development_failures": failures}),
    )
    candidate = ReviewStrategy.model_validate_json(raw)
    # Evaluation callback owns frozen labels and validation split. The proposer
    # never receives validation traces, labels or reports.
    baseline_result = evaluate(current, "validation")
    candidate_result = evaluate(candidate, "validation")
    accepted = accept_candidate(baseline_result, candidate_result)

    def digest(strategy):
        return hashlib.sha256(strategy.model_dump_json().encode()).hexdigest()

    return {
        "decision": "accept" if accepted else "rollback",
        "scope": "offline_configuration",
        "baseline_id": digest(current),
        "candidate_id": digest(candidate),
        "candidate": candidate.model_dump(),
        "baseline_metrics": baseline_result,
        "candidate_metrics": candidate_result,
        "selected": (candidate if accepted else current).model_dump(),
    }
