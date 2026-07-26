from __future__ import annotations

from src.models import RiskFinding, Severity
from src.reviewer.signal_contracts import (
    SignalDecision,
    SignalKey,
    SignalVerification,
    SignalVerificationBatch,
)


def _finding() -> RiskFinding:
    return RiskFinding(
        file_path="src/auth.py",
        line=12,
        severity=Severity.HIGH,
        rule_id="secret-logging",
        title="Potential secret logging",
        evidence="print(token)",
        recommendation="Mask sensitive values.",
        confidence=0.75,
    )


def test_signal_key_is_derived_from_rule_identity() -> None:
    key = SignalKey.from_finding(_finding())

    assert key.rule_id == "secret-logging"
    assert key.file_path == "src/auth.py"
    assert key.line == 12


def test_verification_batch_supports_explicit_decisions() -> None:
    key = SignalKey.from_finding(_finding())
    batch = SignalVerificationBatch(
        provider_used=True,
        items=[
            SignalVerification(
                key=key,
                decision=SignalDecision.DISMISSED,
                confidence=0.92,
                reason="The value is masked before logging.",
            )
        ],
    )

    assert batch.provider_used is True
    assert batch.items[0].decision == SignalDecision.DISMISSED
