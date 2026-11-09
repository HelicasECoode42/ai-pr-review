"""Mutable state shared by the stages of one grounded review."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GroundedRun:
    """Inputs and accumulators of one run; each stage owns the fields it writes."""

    pr: Any
    files: Any
    provider: Any
    snapshot: Any
    context: Any
    bundle: Any
    max_suggestions: int
    language: str
    verify_behavior: bool
    max_suggestions_per_file: int
    contract_domains: dict | None
    discovery_rounds: int
    verify_rounds: int
    prompt_addendum: str
    missing_evidence_followup: bool
    missing_evidence_followup_only: bool
    raw_discovery: bool
    repository_index_enabled: bool
    critic_provider: Any
    critic_batch_cap: int
    critic_budget: Any
    changed_paths: set
    available_sources: set
    search_resolver: Any
    discovery_budget: int
    raw_discovery_scope: str | None
    diff_text: str
    intent_plan: dict | None = None
    intent_query_keys: set = field(default_factory=set)
    discovery_trace: list = field(default_factory=list)
    discovery_calls: int = 0
    verification_calls: int = 0
    failed: bool = False
    # Counts a confirmed candidate whose witness was rejected; a run with no
    # eligible candidates must still report a number.
    witness_blocked: int = 0
    rule_rebuttals: dict = field(default_factory=dict)
    host_rule_candidate_keys: set = field(default_factory=set)
    host_rule_candidate_ids: set = field(default_factory=set)
    candidates: list = field(default_factory=list)
    eligible: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    kept_ids: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    duplicate_of: dict = field(default_factory=dict)
