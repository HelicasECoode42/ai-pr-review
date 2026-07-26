"""Contracts for the rule-signal verification pipeline.

This module intentionally contains data contracts and protocols only. Phase 2
will add provider-backed verification and result merging without coupling those
implementations to the Agent runner.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from pydantic import BaseModel, Field

from src.models import ReviewSuggestion, RiskFinding, Severity


class SignalDecision(str, Enum):
    """Semantic decision made for a rule-generated signal."""

    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    UNSURE = "unsure"
    UNVERIFIED = "unverified"


class SignalKey(BaseModel, frozen=True):
    """Stable identity for matching a verification back to its signal."""

    rule_id: str
    file_path: str
    line: int | None = None

    @classmethod
    def from_finding(cls, finding: RiskFinding) -> SignalKey:
        return cls(
            rule_id=finding.rule_id,
            file_path=finding.file_path,
            line=finding.line,
        )


class SignalEnvelope(BaseModel):
    """A rule signal plus only the evidence needed for semantic verification."""

    key: SignalKey
    signal: RiskFinding
    patch_excerpt: str
    project_guidance: str | None = None


class SignalVerification(BaseModel):
    """Structured decision returned by a future SignalVerifier."""

    key: SignalKey
    decision: SignalDecision
    adjusted_severity: Severity | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class SignalVerificationBatch(BaseModel):
    """One batched verification response for all signals in a review."""

    items: list[SignalVerification] = Field(default_factory=list)
    provider_used: bool = False
    degradation_reason: str | None = None


class ReviewMergeResult(BaseModel):
    """Output contract for the future review merger."""

    suggestions: list[ReviewSuggestion] = Field(default_factory=list)
    confirmed: list[SignalVerification] = Field(default_factory=list)
    dismissed: list[SignalVerification] = Field(default_factory=list)
    unresolved: list[SignalVerification] = Field(default_factory=list)
    unverified: list[SignalVerification] = Field(default_factory=list)


class SignalVerifier(Protocol):
    """Port implemented by the Phase 2 provider-backed verifier."""

    def verify_batch(
        self,
        signals: list[SignalEnvelope],
    ) -> SignalVerificationBatch: ...


class ReviewMerger(Protocol):
    """Port implemented by the Phase 2 deterministic merge layer."""

    def merge(
        self,
        independent_suggestions: list[ReviewSuggestion],
        signals: list[RiskFinding],
        verification: SignalVerificationBatch,
    ) -> ReviewMergeResult: ...
