"""Agent tool registry — a simple name → callable mapping.

The registry ensures tool names are unique and provides a single lookup
point for the Agent Runner.  Tools are thin wrappers around existing
functions in github/, analyzer/, reviewer/, and output/.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class AgentTool:
    """A named tool that the Runner can invoke.

    The execute callable receives a mutable ``ctx: dict[str, Any]`` and
    reads/writes intermediate data (PullRequest, ChangedFile list, etc.)
    through it.  The Runner records AgentStep entries in ReviewAgentState
    separately — tools do not touch state directly.
    """

    name: str
    execute: Callable[..., Any]
    description: str = ""   # Human-readable, appears in trace and error messages
    risk: str = "read"      # "read" | "write" — whether the tool has side effects


class AgentToolRegistry:
    """Registry of named AgentTool instances.

    Enforces unique tool names and provides type-safe lookup.
    """

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate agent tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except KeyError:
            available = ", ".join(sorted(self._tools.keys()))
            raise ValueError(
                f"Unknown agent tool: {name}. Available: {available}"
            ) from None

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
