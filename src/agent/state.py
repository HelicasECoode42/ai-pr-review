"""Agent state models for the Review Task Agent.

These models track every tool step, strategy decision, and degradation path
during a review run. They are used by the Agent Runner to produce structured
trace output alongside the final ReviewReport.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AgentStepStatus = Literal["success", "failed", "skipped"]
AgentStrategy = Literal["rule_only", "one_shot_ai", "two_stage", "incremental"]


class AgentStep(BaseModel):
    """A single tool invocation record within a review run."""

    index: int
    tool: str
    input_summary: dict[str, Any] = Field(default_factory=dict)
    observation_summary: dict[str, Any] = Field(default_factory=dict)
    status: AgentStepStatus
    error: str | None = None
    duration_ms: int | None = None


class ReviewAgentState(BaseModel):
    """Immutable snapshot of agent state after each tool step.

    Intermediate data (PullRequest, ChangedFile list, etc.) is NOT stored
    here — it flows through tool functions directly.  This model only
    records what is needed for the agent trace and strategy decisions.
    """

    repo: str
    pr_number: int
    strategy: AgentStrategy | None = None
    strategy_reason: str | None = None
    degradation_path: list[str] = Field(default_factory=list)
    language: str = "en"
    use_ai: bool = True
    steps: list[AgentStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
