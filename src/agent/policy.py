"""Agent policy: choose review strategy based on task characteristics.

The policy is rule-driven, not LLM-driven.  It takes measurable inputs
(files count, additions, findings severity, API key availability) and
returns a deterministic (strategy, reason) pair.
"""

from __future__ import annotations

from src.agent.state import AgentStrategy

# Valid agent strategies
VALID_STRATEGIES: frozenset[str] = frozenset(
    {"rule_only", "one_shot_ai", "two_stage", "incremental"}
)


def choose_agent_strategy(
    *,
    use_ai: bool,
    has_api_key: bool,
    files_count: int,
    additions: int,
    findings_count: int,
    high_severity_count: int,
    requested_mode: str = "auto",
) -> tuple[str, str]:
    """Pick a review strategy and return (strategy, reason).

    Parameters
    ----------
    use_ai : bool
        Whether the caller wants AI review at all (--ai/--no-ai).
    has_api_key : bool
        Whether a valid API key is available.
    files_count : int
        Number of changed files.
    additions : int
        Total added lines.
    findings_count : int
        Total rule-based findings.
    high_severity_count : int
        Number of critical + high severity findings.
    requested_mode : str
        Explicit mode override: "auto" | "rule_only" | "one_shot_ai" |
        "two_stage" | "incremental".

    Returns
    -------
    (strategy, reason) — strategy is one of the AgentStrategy literals.
    """
    # ── 1. AI unavailable ──────────────────────────────────
    if not use_ai:
        return ("rule_only", "AI disabled by caller (--no-ai)")
    if not has_api_key:
        return ("rule_only", "OPENAI_API_KEY not set")

    # ── 2. Explicit override ───────────────────────────────
    if requested_mode != "auto":
        if requested_mode not in VALID_STRATEGIES:
            return (
                "one_shot_ai",
                f"Unknown requested mode '{requested_mode}', falling back to one_shot_ai",
            )
        return (requested_mode, f"Explicit mode: {requested_mode}")

    # ── 3. Auto selection ──────────────────────────────────
    # Round 1: two_stage not yet implemented — auto-select never picks it.
    # When two_stage is ready, enable the condition below.
    #
    # if files_count > 20 and high_severity_count >= 3:
    #     return ("two_stage", "Large PR with concentrated high-risk areas")

    return ("one_shot_ai", "Default strategy for AI-enabled review")
