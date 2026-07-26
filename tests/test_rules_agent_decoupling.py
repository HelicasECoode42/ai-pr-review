from __future__ import annotations

from src.agent.policy import choose_agent_strategy
from src.analyzer.context_builder import build_review_context
from src.models import ChangedFile, FileStatus, PullRequest, RiskFinding, Severity


def _pr() -> PullRequest:
    return PullRequest(repo="demo/repo", number=1, title="Change CI")


def _signal() -> RiskFinding:
    return RiskFinding(
        file_path=".github/workflows/ci.yml",
        severity=Severity.HIGH,
        rule_id="risk-path-infra-workflow",
        title="CI/CD workflow changed",
        evidence="path matched",
        recommendation="Review the workflow.",
    )


def test_main_review_context_hides_rule_findings_by_default() -> None:
    ctx = build_review_context(
        _pr(),
        [ChangedFile(filename=".github/workflows/ci.yml", status=FileStatus.MODIFIED)],
        [_signal()],
    )

    assert "## Rule findings" not in ctx.text
    assert "CI/CD workflow changed" not in ctx.text


def test_rule_findings_can_be_explicitly_included_for_verification() -> None:
    ctx = build_review_context(
        _pr(),
        [ChangedFile(filename=".github/workflows/ci.yml", status=FileStatus.MODIFIED)],
        [_signal()],
        include_rule_findings=True,
    )

    assert "## Rule findings" in ctx.text
    assert "CI/CD workflow changed" in ctx.text


def test_auto_strategy_ignores_rule_signal_volume() -> None:
    strategy, _ = choose_agent_strategy(
        use_ai=True,
        has_api_key=True,
        files_count=4,
        additions=100,
        findings_count=100,
        high_severity_count=100,
        signal_files={f"src/file_{index}.py" for index in range(20)},
        critical_signal_files={"src/auth.py"},
    )

    assert strategy == "one_shot_ai"
