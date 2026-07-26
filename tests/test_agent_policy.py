"""Tests for agent policy strategy selection."""

from __future__ import annotations

import pytest

from src.agent.policy import choose_agent_strategy


class TestChooseAgentStrategy:
    # ── AI unavailable ──────────────────────────────────────

    def test_no_ai_disabled_by_caller(self) -> None:
        strategy, reason = choose_agent_strategy(
            use_ai=False,
            has_api_key=True,
            files_count=5,
            additions=100,
            findings_count=3,
            high_severity_count=1,
        )
        assert strategy == "rule_only"
        assert "no-ai" in reason.lower() or "disabled" in reason.lower()

    def test_no_api_key(self) -> None:
        strategy, reason = choose_agent_strategy(
            use_ai=True,
            has_api_key=False,
            files_count=5,
            additions=100,
            findings_count=3,
            high_severity_count=1,
        )
        assert strategy == "rule_only"
        assert "api_key" in reason.lower() or "not set" in reason.lower()

    # ── Explicit override ───────────────────────────────────

    def test_explicit_rule_only(self) -> None:
        strategy, reason = choose_agent_strategy(
            use_ai=True,
            has_api_key=True,
            files_count=5,
            additions=100,
            findings_count=3,
            high_severity_count=1,
            requested_mode="rule_only",
        )
        assert strategy == "rule_only"

    def test_explicit_one_shot(self) -> None:
        strategy, reason = choose_agent_strategy(
            use_ai=True,
            has_api_key=True,
            files_count=5,
            additions=100,
            findings_count=3,
            high_severity_count=1,
            requested_mode="one_shot_ai",
        )
        assert strategy == "one_shot_ai"

    def test_explicit_two_stage(self) -> None:
        """Explicit two_stage should be honoured even in round 1."""
        strategy, reason = choose_agent_strategy(
            use_ai=True,
            has_api_key=True,
            files_count=5,
            additions=100,
            findings_count=3,
            high_severity_count=1,
            requested_mode="two_stage",
        )
        assert strategy == "two_stage"

    def test_unknown_requested_mode_falls_back(self) -> None:
        strategy, reason = choose_agent_strategy(
            use_ai=True,
            has_api_key=True,
            files_count=5,
            additions=100,
            findings_count=3,
            high_severity_count=1,
            requested_mode="garbage",
        )
        assert strategy == "one_shot_ai"
        assert "falling back" in reason.lower()

    # ── Auto selection ──────────────────────────────────────

    def test_auto_defaults_to_one_shot(self) -> None:
        strategy, reason = choose_agent_strategy(
            use_ai=True,
            has_api_key=True,
            files_count=5,
            additions=100,
            findings_count=3,
            high_severity_count=1,
        )
        assert strategy == "one_shot_ai"

    def test_auto_large_pr_uses_two_stage(self) -> None:
        strategy, reason = choose_agent_strategy(
            use_ai=True,
            has_api_key=True,
            files_count=30,
            additions=1000,
            findings_count=10,
            high_severity_count=5,
        )
        assert strategy == "two_stage"

    def test_auto_distributed_signals_do_not_change_primary_strategy(self) -> None:
        strategy, reason = choose_agent_strategy(
            use_ai=True,
            has_api_key=True,
            files_count=10,
            additions=300,
            findings_count=8,
            high_severity_count=1,
            signal_files={f"src/file_{i}.py" for i in range(8)},
        )
        assert strategy == "one_shot_ai"

    def test_auto_critical_signals_do_not_change_primary_strategy(self) -> None:
        strategy, reason = choose_agent_strategy(
            use_ai=True,
            has_api_key=True,
            files_count=12,
            additions=400,
            findings_count=3,
            high_severity_count=2,
            signal_files={"src/auth.py", "src/token.py"},
            critical_signal_files={"src/auth.py", "src/token.py"},
        )
        assert strategy == "one_shot_ai"

    # ── Edge cases ──────────────────────────────────────────

    def test_zero_files(self) -> None:
        strategy, _ = choose_agent_strategy(
            use_ai=True,
            has_api_key=True,
            files_count=0,
            additions=0,
            findings_count=0,
            high_severity_count=0,
        )
        assert strategy == "rule_only"

    def test_explicit_overrides_no_api_key(self) -> None:
        """Explicit one_shot_ai with no key → still rule_only (AI unavailable wins)."""
        strategy, _ = choose_agent_strategy(
            use_ai=True,
            has_api_key=False,
            files_count=5,
            additions=100,
            findings_count=3,
            high_severity_count=1,
            requested_mode="one_shot_ai",
        )
        # AI unavailable check runs first, so rule_only wins
        assert strategy == "rule_only"

    def test_reason_string_is_non_empty(self) -> None:
        for mode in ("auto", "rule_only", "one_shot_ai", "two_stage"):
            strategy, reason = choose_agent_strategy(
                use_ai=True,
                has_api_key=True,
                files_count=5,
                additions=100,
                findings_count=3,
                high_severity_count=1,
                requested_mode=mode,
            )
            assert len(reason) > 0, f"Reason empty for mode={mode}"
