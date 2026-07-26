"""Deterministic merge of independent AI suggestions and verified rule signals."""

from __future__ import annotations

from src.models import ReviewSuggestion, RiskFinding
from src.reviewer.signal_contracts import (
    ReviewMergeResult,
    SignalDecision,
    SignalKey,
    SignalVerification,
    SignalVerificationBatch,
)


def merge_review_results(
    independent_suggestions: list[ReviewSuggestion],
    signals: list[RiskFinding],
    verification: SignalVerificationBatch,
) -> ReviewMergeResult:
    by_key = {item.key: item for item in verification.items}
    result = ReviewMergeResult(suggestions=list(independent_suggestions))
    occupied = {(item.file_path, item.line) for item in result.suggestions}
    for signal in signals:
        item = by_key.get(SignalKey.from_finding(signal))
        if item is None:
            item = SignalVerification(
                key=SignalKey.from_finding(signal), decision=SignalDecision.UNVERIFIED,
                confidence=0.0, reason="No verifier decision returned.",
            )
        if item.decision == SignalDecision.CONFIRMED:
            result.confirmed.append(item)
            location = (signal.file_path, signal.line)
            if location not in occupied:
                result.suggestions.append(ReviewSuggestion(
                    file_path=signal.file_path, line=signal.line,
                    severity=item.adjusted_severity or signal.severity,
                    confidence=item.confidence, title=signal.title, reason=item.reason,
                    recommendation=signal.recommendation, source="rule_confirmed",
                    evidence=[signal.evidence], failure_scenario=item.reason,
                ))
                occupied.add(location)
        elif item.decision == SignalDecision.DISMISSED:
            result.dismissed.append(item)
        elif item.decision == SignalDecision.UNSURE:
            result.unresolved.append(item)
        else:
            result.unverified.append(item)
    return result
