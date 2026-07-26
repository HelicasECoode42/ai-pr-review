"""Select only high-precision mechanical signals for optional AI verification."""

from __future__ import annotations

from src.models import RiskFinding

HIGH_PRECISION_RULE_IDS = frozenset({
    "secret-logging",
    "shell-execution",
    "dynamic-execution",
    "sql-string-concat",
})


def select_verifiable_signals(signals: list[RiskFinding]) -> list[RiskFinding]:
    """Return signals whose evidence is concrete enough to justify an AI call."""
    return [signal for signal in signals if signal.rule_id in HIGH_PRECISION_RULE_IDS]
