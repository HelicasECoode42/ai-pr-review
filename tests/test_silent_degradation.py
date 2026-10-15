"""Silent degradation must leave a trace instead of dropping data."""

from __future__ import annotations

import pytest

from src.cli.main import _warn_step_summary_failure
from src.github.client import (
    GitHubApiError,
    GitHubClient,
    _as_int,
    _as_text,
    _changed_file,
)
from src.models import ChangedFile, PullRequest, ReviewReport, ReviewSuggestion, Severity
from src.reviewer.evidence_validator import validate_ai_evidence


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def paged_client(batch):
    """A client whose first page is `batch` and whose second page is empty."""
    client = GitHubClient(token=None)

    def fake_get(path, **kwargs):
        page = (kwargs.get("params") or {}).get("page", 1)
        return FakeResponse(batch if page == 1 else [])

    client._get = fake_get  # type: ignore[method-assign]
    return client


def test_field_type_surprises_never_remove_a_changed_file():
    item = {
        "filename": "src/a.py",
        "status": 7,
        "additions": "many",
        "deletions": None,
        "changes": True,
        "patch": 42,
        "previous_filename": ["old.py"],
    }
    changed = _changed_file(item)
    assert changed.filename == "src/a.py"
    assert changed.additions == 0 and changed.deletions == 0 and changed.changes == 0
    assert changed.patch is None and changed.previous_filename is None


def test_unrepresentable_changed_file_fails_loudly():
    client = paged_client([{"filename": "src/a.py"}, {"title": "no filename here"}])
    with pytest.raises(GitHubApiError) as excinfo:
        client.get_changed_files("owner/repo", 1)
    assert "missing filename" in str(excinfo.value)
    assert "#1" in str(excinfo.value)


def test_changed_files_keep_every_sanitizable_entry():
    client = paged_client(
        [
            {"filename": "src/a.py", "status": "modified", "additions": "x"},
            {"filename": "src/b.py", "status": "not-a-status"},
        ]
    )
    files = client.get_changed_files("owner/repo", 1)
    assert [f.filename for f in files] == ["src/a.py", "src/b.py"]
    assert files[0].additions == 0


def test_compare_endpoint_also_refuses_to_drop_entries():
    client = GitHubClient(token=None)
    client._get = lambda path, **kw: FakeResponse({"files": [{"no": "filename"}]})  # type: ignore[method-assign]
    with pytest.raises(GitHubApiError):
        client.get_compare("owner/repo", "a" * 40, "b" * 40)


def test_helpers_are_type_strict():
    assert _as_int(3) == 3
    assert _as_int(True) == 0 and _as_int("3") == 0 and _as_int(None) == 0
    assert _as_text("x") == "x" and _as_text(b"x") is None


def test_step_summary_failure_is_printed_and_audited(capsys):
    report = ReviewReport(
        pr=PullRequest(repo="owner/repo", number=1, title="t"),
        files=[],
        summary="s",
        risk_level=Severity.LOW,
    )
    message = _warn_step_summary_failure(report, RuntimeError("disk full"))
    assert "RuntimeError" in message and "disk full" in message
    assert report.analysis_warnings == [message]
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "disk full" in out


def test_step_summary_failure_without_a_report_does_not_raise(capsys):
    message = _warn_step_summary_failure(None, ValueError("no report yet"))
    assert "ValueError" in message
    assert "no report yet" in capsys.readouterr().out


def make_suggestion(path: str, line: int, evidence: list[str]) -> ReviewSuggestion:
    return ReviewSuggestion(
        file_path=path,
        line=line,
        severity=Severity.HIGH,
        confidence=0.9,
        title="t",
        reason="r",
        recommendation="fix it",
        evidence=evidence,
        failure_scenario="the input is null and the call raises",
    )


def test_evidence_gate_blames_the_tool_when_a_file_has_no_parseable_hunk():
    # GitHub omits the patch for a large file; the gate cannot verify anything
    # on it, and must not report that as a bad model citation.
    files = [ChangedFile(filename="src/big.py", patch=None)]
    accepted, rejected = validate_ai_evidence(
        [make_suggestion("src/big.py", 10, ["x = 1"])], files
    )
    assert accepted == []
    assert rejected[0]["reason"] == (
        "Changed file has no parseable diff hunk, so the citation cannot be verified."
    )


def test_evidence_gate_still_names_a_genuinely_unadded_line():
    files = [ChangedFile(filename="src/a.py", patch="@@ -1,1 +1,1 @@\n-old\n+new = 1\n")]
    accepted, rejected = validate_ai_evidence(
        [make_suggestion("src/a.py", 99, ["new = 1"])], files
    )
    assert accepted == []
    assert rejected[0]["reason"] == "Cited location is not an added line in this diff."


def test_evidence_gate_accepts_a_verifiable_citation():
    files = [ChangedFile(filename="src/a.py", patch="@@ -1,1 +1,1 @@\n-old\n+new = 1\n")]
    accepted, rejected = validate_ai_evidence(
        [make_suggestion("src/a.py", 1, ["new = 1"])], files
    )
    assert rejected == []
    assert [s.file_path for s in accepted] == ["src/a.py"]
