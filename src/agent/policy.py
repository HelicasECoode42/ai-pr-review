"""Agent policy: choose review strategy based on task characteristics.

The policy is rule-driven, not LLM-driven.  It takes measurable inputs
(files count, additions, findings count, API key availability) and
returns a deterministic (strategy, reason) pair.
"""

from __future__ import annotations

# Valid agent strategies
VALID_STRATEGIES: frozenset[str] = frozenset(
    {"rule_only", "one_shot_ai", "two_stage"}
)


def choose_agent_strategy(
    *,
    use_ai: bool,
    has_api_key: bool,
    files_count: int,
    additions: int,
    findings_count: int,
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
    requested_mode : str
        Explicit mode override: "auto" | "rule_only" | "one_shot_ai" |
        "two_stage". Incremental review is a separate review_mode, not an
        Agent execution strategy.

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
    if files_count == 0:
        return ("rule_only", "No changed files available; skip model call")

    if files_count >= 20 or additions >= 1200:
        return (
            "one_shot_ai",
            "Large PR; retain grounded evidence review and report bounded coverage",
        )

    return (
        "one_shot_ai",
        f"Bounded PR ({files_count} files, {additions} additions, {findings_count} rule signals)",
    )
