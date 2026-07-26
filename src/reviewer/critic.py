"""Batch adversarial review for evidence-grounded AI suggestions."""

from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, Field

from src.models import ChangedFile, ReviewSuggestion, Severity
from src.reviewer.provider import ProviderError, ReviewModelProvider


class CriticDecision(str, Enum):
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    UNCERTAIN = "uncertain"
    UNVERIFIED = "unverified"


class CriticVerdict(BaseModel):
    suggestion_index: int
    decision: CriticDecision
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class CriticBatch(BaseModel):
    items: list[CriticVerdict] = Field(default_factory=list)
    provider_used: bool = False
    degradation_reason: str | None = None


def select_critic_candidates(
    suggestions: list[ReviewSuggestion],
) -> list[tuple[int, ReviewSuggestion]]:
    """Critique medium-or-higher findings; low risk stays evidence-gated only."""
    return [
        (index, suggestion)
        for index, suggestion in enumerate(suggestions)
        if suggestion.severity in {Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL}
    ]


def critique_batch(
    provider: ReviewModelProvider,
    candidates: list[tuple[int, ReviewSuggestion]],
    files: list[ChangedFile],
) -> CriticBatch:
    if not candidates:
        return CriticBatch()
    patches = {file.filename: file.patch or "" for file in files}
    items = [
        {
            "suggestion_index": index,
            "candidate": suggestion.model_dump(mode="json"),
            "patch": patches.get(suggestion.file_path, "")[:3000],
        }
        for index, suggestion in candidates
    ]
    system = (
        "You are an adversarial code-review critic. Verify candidate findings against "
        "the supplied patch. Try to disprove each failure scenario using the actual code. "
        "Return confirmed only when the scenario follows from the changed code; return "
        "dismissed when the patch contradicts it; return uncertain when evidence is insufficient."
    )
    prompt = (
        "Return JSON {items:[{suggestion_index,decision:confirmed|dismissed|uncertain,"
        "confidence,reason}]}. Do not invent new findings.\n\n"
        + json.dumps(items, ensure_ascii=False)
    )
    try:
        raw = provider.complete_json(system, prompt)
        payload = json.loads(raw)
        return CriticBatch(
            items=[CriticVerdict.model_validate(item) for item in payload.get("items", [])],
            provider_used=True,
        )
    except (ProviderError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return CriticBatch(provider_used=False, degradation_reason=str(exc))


def apply_critic(
    suggestions: list[ReviewSuggestion],
    candidates: list[tuple[int, ReviewSuggestion]],
    batch: CriticBatch,
) -> tuple[list[ReviewSuggestion], list[dict[str, object]]]:
    """Keep confirmed candidates, audit every Critic decision, and fail open on outage."""
    candidate_indexes = {index for index, _ in candidates}
    verdicts = {item.suggestion_index: item for item in batch.items}
    kept: list[ReviewSuggestion] = []
    audit: list[dict[str, object]] = []
    for index, suggestion in enumerate(suggestions):
        if index not in candidate_indexes:
            kept.append(suggestion)
            continue
        verdict = verdicts.get(index)
        if verdict is None:
            decision = CriticDecision.UNVERIFIED
            reason = batch.degradation_reason or "No Critic verdict returned."
            confidence = 0.0
        else:
            decision = verdict.decision
            reason = verdict.reason
            confidence = verdict.confidence
        audit.append({
            "suggestion_index": index,
            "file_path": suggestion.file_path,
            "line": suggestion.line,
            "title": suggestion.title,
            "decision": decision.value,
            "confidence": confidence,
            "reason": reason,
        })
        if decision == CriticDecision.CONFIRMED:
            kept.append(suggestion.model_copy(update={"source": "ai_critic_confirmed"}))
        elif decision == CriticDecision.UNVERIFIED:
            kept.append(suggestion.model_copy(update={"source": "ai_critic_unverified"}))
    return kept, audit
