"""Batch adversarial review for evidence-grounded AI suggestions."""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Callable

from pydantic import BaseModel, Field

from src.analyzer.diff_parser import parse_file_hunks
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
    read_head_file: Callable[[str], str | None] | None = None,
) -> CriticBatch:
    if not candidates:
        return CriticBatch()
    by_path = {file.filename: file for file in files}
    items = [
        {
            "suggestion_index": index,
            "candidate": suggestion.model_dump(mode="json"),
            "patch": _candidate_window(by_path.get(suggestion.file_path), suggestion.line),
            "related_changed_modules": _related_changed_modules(by_path.get(suggestion.file_path), by_path, read_head_file,
                                       focus=suggestion.reason + " " + suggestion.failure_scenario),
        }
        for index, suggestion in candidates
    ]
    system = (
        "You are an adversarial code-review critic. Verify candidate findings against "
        "the supplied patch and related changed modules. Missing or truncated context is not evidence of correctness. Try to disprove each failure scenario using the actual code. "
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
        audit.append(
            {
                "suggestion_index": index,
                "file_path": suggestion.file_path,
                "line": suggestion.line,
                "title": suggestion.title,
                "decision": decision.value,
                "confidence": confidence,
                "reason": reason,
            }
        )
        if decision == CriticDecision.CONFIRMED:
            kept.append(suggestion.model_copy(update={"source": "ai_critic_confirmed"}))
        elif decision == CriticDecision.UNVERIFIED:
            kept.append(suggestion.model_copy(update={"source": "ai_critic_unverified"}))
    return kept, audit


def _candidate_window(file: ChangedFile | None, line: int | None, budget: int = 3000) -> str:
    """Use the candidate's hunk, not the beginning of a possibly huge file diff."""
    if file is None or line is None:
        return ""
    for hunk in parse_file_hunks(file):
        if not hunk.new_start <= line < hunk.new_start + max(hunk.new_count, 1):
            continue
        lines = hunk.raw.splitlines()[1:]
        current = hunk.new_start
        for index, raw in enumerate(lines):
            if not raw.startswith(("-", "\\")):
                if current == line:
                    # Preserve cited line first; then include bounded nearby code.
                    anchor = raw[: budget // 2]
                    nearby = "\n".join(lines[max(0, index - 15) : index + 16])
                    return (f"{hunk.header}\nCited head line {line}: {anchor}\n" + nearby)[:budget]
                current += 1
    return ""


def _related_changed_modules(
    file: ChangedFile | None, by_path: dict[str, ChangedFile],
    read_head_file: Callable[[str], str | None] | None = None,
    focus: str = "",
) -> list[dict]:
    """Bounded import dependencies from this same frozen diff, never rule conclusions."""
    if file is None or not file.filename.endswith(".py"):
        return []
    patch = file.patch or ""
    try:
        full_source = read_head_file(file.filename) if read_head_file else None
    except Exception:
        full_source = None
    modules = re.findall(r"^[ +]?(?:from|import)\s+(src(?:\.\w+)+)", (full_source or patch), re.MULTILINE)
    imports = re.findall(r"^[ +]?from\s+(src(?:\.\w+)+)\s+import\s+([^\n]+)",
                         (full_source or patch), re.MULTILINE)
    referenced = {module for module, names in imports
                  if any(re.search(r"\b" + re.escape(name) + r"\b", focus)
                         for name in re.findall(r"\b\w+\b", names))}
    modules = sorted(dict.fromkeys(modules), key=lambda module: module not in referenced)
    related = []
    for module in modules:
        path = module.replace(".", "/") + ".py"
        dependency = by_path.get(path)
        if path == file.filename:
            continue
        try:
            full_dependency = read_head_file(path) if read_head_file else None
        except Exception:
            full_dependency = None
        text = full_dependency or (dependency.patch if dependency else None)
        if not text:
            continue
        related.append({"file": path, "patch": text[:8000],
                        "context_kind": "frozen_head_file" if full_dependency else "changed_patch",
                        "truncated": len(text) > 8000})
        if len(related) == 2:
            break
    return related
