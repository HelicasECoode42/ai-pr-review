"""Judgement guards applied to verifier decisions."""
from __future__ import annotations

import re

_DISMISSAL_BASES = {"unreachable", "incorrect", "not_introduced"}


# Wording that concedes the failure happens and only argues it is acceptable.
# The verifier has paired these with a false factual basis ("not_introduced"
# for a documented default), so they override any declared basis.
_STRONG_JUDGEMENT = re.compile(
    r"\b(intended|intentional(ly)?|by design|design choice|documented|self[- ]correcting|benign|"
    r"concedes?|candidate itself|inefficien\w*|performance[- ]only|not a correctness|"
    r"not (a )?(correctness |functional |real )?(bug|defect)( per se)?)\b",
    re.IGNORECASE,
)


# Weaker wording: a judgement only when no factual basis is declared.
_WEAK_JUDGEMENT = re.compile(
    r"\b(minor|harmless|cosmetic|low[- ](impact|severity|risk)|expected behaviou?r|"
    r"acceptable|edge case only|nitpick|style)\b",
    re.IGNORECASE,
)


def _dismissal_rests_on_judgement(item) -> bool:
    """A dismissal needs a factual basis; intent or impact is not one."""
    reason = str(item.get("reason") or "")
    if _STRONG_JUDGEMENT.search(reason):
        return True
    basis = str(item.get("dismissal_basis") or "").strip().lower()
    if basis in _DISMISSAL_BASES:
        return False
    if basis:
        return True  # any other declared basis (intended, low_impact ...) is a judgement
    return bool(_WEAK_JUDGEMENT.search(reason))


# A confirmation that hedges its own causal claim, or whose only failure is on
# malformed input, was a false positive in every case on the six-case dev set
# (12 of 12): these stay in the review queue instead of being published.
_HEDGED_CONFIRMATION = re.compile(
    r"\b(may|might|could|potentially|possibly|theoretically)\b", re.IGNORECASE)


_MALFORMED_ONLY = re.compile(
    r"\b(malformed|truncated|corrupt\w*|short (file|stream|read|segment)|EOF|"
    r"end of (file|input|stream))\b", re.IGNORECASE)


def _speculative_confirmation(item) -> str | None:
    if _HEDGED_CONFIRMATION.search(str(item.get("reason") or "")):
        return "hedged_confirmation"
    if _MALFORMED_ONLY.search(str(item.get("actual_behavior") or "")):
        return "malformed_input_only"
    return None
