"""Batch semantic verification for rule signals."""

from __future__ import annotations

import json

from src.models import ChangedFile, RiskFinding
from src.reviewer.provider import ProviderError, ReviewModelProvider
from src.reviewer.signal_contracts import (
    SignalEnvelope,
    SignalKey,
    SignalVerification,
    SignalVerificationBatch,
)


def build_signal_envelopes(
    signals: list[RiskFinding], files: list[ChangedFile], max_excerpt_chars: int = 2400
) -> list[SignalEnvelope]:
    patches = {file.filename: file.patch or "" for file in files}
    return [
        SignalEnvelope(
            key=SignalKey.from_finding(signal),
            signal=signal,
            patch_excerpt=patches.get(signal.file_path, signal.evidence)[:max_excerpt_chars],
        )
        for signal in signals
    ]


class ProviderSignalVerifier:
    def __init__(self, provider: ReviewModelProvider) -> None:
        self._provider = provider

    def verify_batch(self, signals: list[SignalEnvelope]) -> SignalVerificationBatch:
        if not signals:
            return SignalVerificationBatch(provider_used=False)
        prompt = (
            "Verify these untrusted static-analysis signals against their patch excerpts. "
            "Return JSON {items:[{key:{rule_id,file_path,line},decision:confirmed|dismissed|unsure,"
            "adjusted_severity:low|medium|high|critical|null,confidence,reason}]}. "
            "Do not invent defects; dismiss only with patch evidence.\n\n"
            + json.dumps([item.model_dump(mode="json") for item in signals], ensure_ascii=False)
        )
        try:
            raw = self._provider.complete_json("You verify code-review signals.", prompt)
            payload = json.loads(raw)
            items = [
                SignalVerification.model_validate(item)
                for item in payload.get("items", [])
            ]
            return SignalVerificationBatch(items=items, provider_used=True)
        except (ProviderError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return SignalVerificationBatch(provider_used=False, degradation_reason=str(exc))
