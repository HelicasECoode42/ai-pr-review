from __future__ import annotations

from src.models import ChangedFile, FileStatus, PullRequest, RiskFinding, Severity
from src.reviewer.engine import review_with_ai
from src.reviewer.review_merger import merge_review_results
from src.reviewer.signal_contracts import SignalDecision
from src.reviewer.signal_verifier import ProviderSignalVerifier, build_signal_envelopes


def _signal() -> RiskFinding:
    return RiskFinding(
        file_path="src/app.py", line=1, severity=Severity.HIGH,
        rule_id="secret-logging", title="Secret logged", evidence="print(token)",
        recommendation="Mask it.", confidence=0.75,
    )


class ConfirmingProvider:
    def complete_json(self, _system: str, _user: str) -> str:
        return (
            '{"items":[{"key":{"rule_id":"secret-logging",'
            '"file_path":"src/app.py","line":1},"decision":"confirmed",'
            '"adjusted_severity":"medium","confidence":0.9,'
            '"reason":"The changed line logs the token."}]}'
        )


def test_batch_verifier_uses_one_provider_call_and_merges_confirmed_signal() -> None:
    files = [ChangedFile(filename="src/app.py", status=FileStatus.MODIFIED,
                         patch="@@ -1,1 +1,2 @@\n+print(token)\n")]
    batch = ProviderSignalVerifier(ConfirmingProvider()).verify_batch(
        build_signal_envelopes([_signal()], files)
    )
    merged = merge_review_results([], [_signal()], batch)

    assert batch.provider_used is True
    assert len(merged.confirmed) == 1
    assert merged.suggestions[0].source == "rule_confirmed"
    assert merged.suggestions[0].severity == Severity.MEDIUM


def test_missing_verifier_decision_stays_unverified() -> None:
    merged = merge_review_results([], [_signal()],
                                  ProviderSignalVerifier(ConfirmingProvider()).verify_batch([]))

    assert merged.unverified[0].decision == SignalDecision.UNVERIFIED


class ReviewAndVerifyProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_json(self, _system: str, user: str) -> str:
        self.calls.append(user)
        if len(self.calls) == 1:
            return '{"summary":"No independent finding.","risk_level":"low","suggestions":[]}'
        return ConfirmingProvider().complete_json(_system, user)


def test_review_metrics_keep_independent_and_verified_suggestions_separate() -> None:
    provider = ReviewAndVerifyProvider()
    files = [ChangedFile(
        filename="src/app.py", status=FileStatus.MODIFIED,
        patch="@@ -1 +1 @@\n+print(token)\n",
    )]
    report = review_with_ai(
        PullRequest(repo="demo/repo", number=1, title="Log token"),
        files, [_signal()], provider, max_suggestions=5, verify_rule_signals=True,
    )

    assert "Rule findings" not in provider.calls[0]
    assert len(provider.calls) == 2
    assert report.metrics["independent_suggestion_count"] == 0
    assert report.metrics["confirmed_signal_count"] == 1
    assert report.confirmed_signals[0]["decision"] == "confirmed"
    assert report.suggestions[0].source == "rule_confirmed"


def test_rule_signals_are_telemetry_by_default() -> None:
    provider = ReviewAndVerifyProvider()
    files = [ChangedFile(
        filename="src/app.py", status=FileStatus.MODIFIED,
        patch="@@ -1 +1 @@\n+print(token)\n",
    )]
    report = review_with_ai(
        PullRequest(repo="demo/repo", number=3, title="Log token"),
        files, [_signal()], provider, max_suggestions=5,
    )

    assert len(provider.calls) == 1
    assert report.metrics["signal_verification_request_count"] == 0
    assert report.metrics["telemetry_only_signal_count"] == 1


class FilteredCriticalProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, _system: str, _user: str) -> str:
        self.calls += 1
        if self.calls == 1:
            return (
                '{"summary":"Draft includes an invalid critical finding.",'
                '"risk_level":"critical","suggestions":[{'
                '"file_path":"src/app.py","line":99,"severity":"critical",'
                '"confidence":0.9,"title":"Invalid location",'
                '"reason":"This line is not changed.","recommendation":"Fix it."}]}'
            )
        return '{"items":[]}'


def test_visible_risk_level_ignores_filtered_model_drafts() -> None:
    files = [ChangedFile(
        filename="src/app.py", status=FileStatus.MODIFIED,
        patch="@@ -1 +1 @@\n+print('ok')\n",
    )]
    report = review_with_ai(
        PullRequest(repo="demo/repo", number=2, title="Safe change"),
        files, [], FilteredCriticalProvider(), max_suggestions=5,
    )

    assert report.suggestions == []
    assert report.risk_level == Severity.LOW
