from __future__ import annotations

from src.models import ChangedFile, FileStatus, ReviewSuggestion, Severity
from src.reviewer.evidence_validator import validate_ai_evidence


def _files() -> list[ChangedFile]:
    return [ChangedFile(
        filename="src/app.py", status=FileStatus.MODIFIED,
        patch="@@ -1 +1 @@\n+cache.set(user_id, profile)\n",
    )]


def test_evidence_validator_keeps_grounded_suggestion() -> None:
    suggestion = ReviewSuggestion(
        file_path="src/app.py", line=1, severity=Severity.MEDIUM, confidence=0.8,
        title="Cache key lacks tenant", reason="The cache key only uses user_id.",
        recommendation="Include tenant_id in the key.", evidence=["cache.set(user_id, profile)"],
        failure_scenario="Two tenants with the same user_id can read each other's profile.",
    )

    accepted, rejected = validate_ai_evidence([suggestion], _files())

    assert accepted == [suggestion]
    assert rejected == []


def test_evidence_validator_rejects_unquoted_or_speculative_suggestion() -> None:
    suggestion = ReviewSuggestion(
        file_path="src/app.py", line=1, severity=Severity.HIGH, confidence=0.9,
        title="Speculative", reason="This might be unsafe.", recommendation="Fix it.",
        evidence=["cache.set(password, profile)"], failure_scenario="",
    )

    accepted, rejected = validate_ai_evidence([suggestion], _files())

    assert accepted == []
    assert rejected[0]["reason"] == (
        "None of the AI evidence excerpts appears in the cited changed line."
    )
