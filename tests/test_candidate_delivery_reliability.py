import json

import pytest

from src.application.review import ReviewApplication
from src.models import PullRequest, ReviewReport, ReviewSuggestion, Severity
from src.output.json_report import render_json
from src.output.markdown import render_markdown
from src.reviewer.decision_ledger import (
    append_candidate_ledger,
    build_candidate_lifecycle,
    load_ledger,
)
from src.utils.config import Settings

SECRET_TEXT = "MODEL_TEXT_SHOULD_NOT_ENTER_LEDGER_9F27"


def _suggestion(*, title="Stable behavior", line=7, source="ai_behavior_verified"):
    return ReviewSuggestion(
        file_path="src/example.py",
        line=line,
        severity=Severity.HIGH,
        confidence=0.93,
        title=title,
        reason=SECRET_TEXT,
        recommendation="Preserve the caller contract.",
        source=source,
        evidence=[SECRET_TEXT],
    )


def _grounded_report(*, confirmed_duplicates=False):
    suggestion = _suggestion()
    candidates = [
        {
            "candidate_id": 80,
            "suggestion": suggestion.model_dump(mode="json"),
            "status": "filtered",
            "reason": "duplicate_identity",
        },
        {
            "candidate_id": 12,
            "suggestion": {
                "file_path": "src/unreviewed.py",
                "line": 21,
                "title": "No verdict candidate",
                "reason": SECRET_TEXT,
            },
            "status": "awaiting_verification",
            "reason": SECRET_TEXT,
        },
        {
            "candidate_id": 4,
            "suggestion": suggestion.model_dump(mode="json"),
            "status": "confirmed",
            "reason": "verified",
        },
    ]
    verifications = [
        {
            "candidate_id": 4,
            "file_path": "src/example.py",
            "line": 7,
            "decision": "confirmed",
            "validation_reason": "verified",
            "filter_verdicts": [{"filter": "witness_eval", "passed": True}],
            "reason": SECRET_TEXT,
            "evidence": [{"quote": SECRET_TEXT}],
        }
    ]
    if confirmed_duplicates:
        candidates.append(
            {
                "candidate_id": 5,
                "suggestion": suggestion.model_dump(mode="json"),
                "status": "confirmed",
                "reason": "verified",
            }
        )
        verifications.append(
            {
                "candidate_id": 5,
                "file_path": "src/example.py",
                "line": 7,
                "decision": "confirmed",
                "validation_reason": "verified",
                "filter_verdicts": [],
            }
        )
    return ReviewReport(
        pr=PullRequest(
            repo="owner/repo",
            number=7,
            title="PR",
            head_sha="a" * 40,
        ),
        files=[],
        summary="review summary",
        risk_level=Severity.LOW,
        used_ai=True,
        suggestions=[suggestion],
        candidate_audit=candidates,
        behavior_verifications=verifications,
        repository_context={
            "review_trace": {
                "schema_version": 1,
                "candidate_counts": {"raw": len(candidates)},
            }
        },
    )


class _Provider:
    usage_records = []

    def close(self):
        pass


def _application(tmp_path, report, *, enabled=True, engine=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    playbook = tmp_path / "empty-playbook.md"
    playbook.write_text("", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        enable_repository_review=False,
        decision_ledger_enabled=enabled,
        decision_ledger_path=str(tmp_path / "reports" / "decision-ledger.jsonl"),
        strategy_policy_store_dir=str(tmp_path / "policy"),
        playbook_path=str(playbook),
    )
    return ReviewApplication(
        settings,
        provider_factory=lambda **_: _Provider(),
        engine=engine or (lambda **_: report),
    )


def test_grounded_candidate_inventory_maps_ids_and_persists_redacted_states(tmp_path):
    report = _grounded_report()
    app = _application(tmp_path, report)

    app.review(report.pr, [], [])

    lifecycle = report.candidate_lifecycle
    assert lifecycle["candidate_scope"] == "grounded_final_payload_candidates"
    assert lifecycle["inventory_status"] == "complete_within_scope"
    assert lifecycle["candidate_count"] == 3
    assert lifecycle["candidate_count_known"] is True
    by_id = {candidate["candidate_id"]: candidate for candidate in lifecycle["candidates"]}
    assert by_id[80]["lifecycle_status"] == "withheld"
    assert by_id[12]["decision"] is None
    assert by_id[12]["lifecycle_status"] == "withheld"
    assert by_id[12]["reason_code"] == "no_verdict_returned"
    assert by_id[4]["lifecycle_status"] == "ready_for_publication"
    assert lifecycle["suggestion_candidate_ids"] == {"0": 4}
    assert by_id[4]["publish_fingerprint"]
    assert lifecycle["persistence_status"] == "persisted"

    report_json = json.loads(render_json(report))
    assert report_json["candidate_lifecycle"]["run_id"] == lifecycle["run_id"]
    markdown = render_markdown(report)
    assert "Candidate lifecycle" in markdown
    assert "complete within scope" in markdown
    assert "earlier provisional or unparsed responses are outside this inventory" in markdown

    ledger_path = tmp_path / "reports" / "decision-ledger.jsonl"
    rows = load_ledger(ledger_path)
    run_rows = [row for row in rows if row["record_type"] == "run"]
    candidate_rows = [row for row in rows if row["record_type"] == "candidate"]
    assert run_rows[0]["candidate_count"] == 3
    assert run_rows[0]["inventory_status"] == "complete_within_scope"
    assert run_rows[0]["append_status"] == "complete"
    assert len(candidate_rows) == 3
    assert {row["candidate_id"] for row in candidate_rows} == {4, 12, 80}
    assert not any(SECRET_TEXT in json.dumps(row) for row in rows)
    assert "Stable behavior" not in ledger_path.read_text(encoding="utf-8")


def test_duplicate_confirmed_identity_is_unknown_and_never_mapped_by_order():
    report = _grounded_report(confirmed_duplicates=True)

    lifecycle = build_candidate_lifecycle(report, run_id="run-a", criteria_version="grounded-v3")

    assert lifecycle["suggestion_candidate_ids"] == {}
    assert all(
        candidate["lifecycle_status"] == "unknown"
        for candidate in lifecycle["candidates"]
        if candidate["candidate_id"] in {4, 5}
    )
    assert all(
        candidate["reason_code"] == "candidate_identity_ambiguous"
        for candidate in lifecycle["candidates"]
        if candidate["candidate_id"] in {4, 5}
    )


def test_low_confidence_candidate_is_withheld_but_exactly_mapped():
    report = _grounded_report()
    report.suggestions[0].confidence = 0.2

    lifecycle = build_candidate_lifecycle(
        report,
        run_id="run-low-confidence",
        criteria_version="grounded-v3",
        publish_min_confidence=0.8,
    )

    confirmed = next(row for row in lifecycle["candidates"] if row["candidate_id"] == 4)
    assert confirmed["lifecycle_status"] == "withheld"
    assert confirmed["reason_code"] == "below_publish_threshold"
    assert lifecycle["suggestion_candidate_ids"] == {"0": 4}


def test_legacy_parse_failure_and_malformed_inventory_keep_scope_uncertainty():
    legacy = ReviewReport(
        pr=PullRequest(repo="owner/repo", number=8, title="legacy"),
        files=[],
        summary="old format",
        risk_level=Severity.LOW,
        used_ai=True,
        suggestions=[_suggestion(source="ai")],
    )
    legacy_lifecycle = build_candidate_lifecycle(
        legacy, run_id="legacy-run", criteria_version="old"
    )
    assert legacy_lifecycle["inventory_status"] == "partial"
    assert legacy_lifecycle["candidate_count_known"] is False
    assert "surviving_legacy" in legacy_lifecycle["candidate_scope"]
    assert all(row["lifecycle_status"] != "published" for row in legacy_lifecycle["candidates"])

    failed = ReviewReport(
        pr=legacy.pr,
        files=[],
        summary="failed",
        risk_level=Severity.LOW,
        used_ai=True,
        ai_failure_reason=SECRET_TEXT,
    )
    failed_lifecycle = build_candidate_lifecycle(
        failed, run_id="failed-run", criteria_version="grounded-v3"
    )
    assert failed_lifecycle["inventory_status"] == "unknown"
    assert failed_lifecycle["candidate_count_known"] is False
    assert failed_lifecycle["candidates"] == []

    malformed = ReviewReport.model_construct(
        pr=legacy.pr,
        files=[],
        summary="malformed",
        risk_level=Severity.LOW,
        used_ai=True,
        candidate_audit=["bad row"],
        repository_context={
            "review_trace": {
                "candidate_counts": {"raw": 1},
            }
        },
    )
    malformed_lifecycle = build_candidate_lifecycle(
        malformed, run_id="malformed-run", criteria_version="grounded-v3"
    )
    assert malformed_lifecycle["inventory_status"] == "partial"
    assert malformed_lifecycle["candidate_count_known"] is False
    assert malformed_lifecycle["candidates"][0]["lifecycle_status"] == "unknown"


def test_local_ledger_failure_degrades_report_and_disabled_write_is_explicit(
    tmp_path,
    monkeypatch,
):
    import src.reviewer.decision_ledger as ledger

    report = _grounded_report()
    monkeypatch.setattr(
        ledger,
        "append_candidate_ledger",
        lambda *_: (_ for _ in ()).throw(OSError(SECRET_TEXT)),
    )
    _application(tmp_path / "failed", report).review(report.pr, [], [])
    assert report.candidate_lifecycle["persistence_status"] == "failed"
    assert report.execution_status == "degraded"
    assert report.report_confidence == "partial"
    assert report.degradation_reason == "candidate_ledger_persistence_failed"
    warning = report.analysis_warnings[-1]
    assert "OSError" in warning
    assert SECRET_TEXT not in warning

    disabled = _grounded_report()
    disabled_app = _application(tmp_path / "disabled", disabled, enabled=False)
    disabled_app.review(disabled.pr, [], [])
    assert disabled.candidate_lifecycle["persistence_status"] == "not_persisted"
    assert "disabled" in disabled.analysis_warnings[-1]
    assert not (tmp_path / "disabled" / "reports" / "decision-ledger.jsonl").exists()


def test_two_runs_with_same_candidate_ids_remain_separate_in_jsonl(tmp_path):
    reports = [_grounded_report(), _grounded_report()]
    app = _application(tmp_path, reports[0], engine=lambda **_: reports.pop(0))
    first = app.review(
        PullRequest(repo="owner/repo", number=7, title="PR", head_sha="a" * 40), [], []
    )
    second = app.review(
        PullRequest(repo="owner/repo", number=7, title="PR", head_sha="a" * 40), [], []
    )

    first_run = first.candidate_lifecycle["run_id"]
    second_run = second.candidate_lifecycle["run_id"]
    assert first_run != second_run
    rows = load_ledger(tmp_path / "reports" / "decision-ledger.jsonl")
    run_ids = {row["run_id"] for row in rows}
    assert run_ids == {first_run, second_run}
    assert {
        run_id: {
            row["candidate_id"]
            for row in rows
            if row["record_type"] == "candidate" and row["run_id"] == run_id
        }
        for run_id in run_ids
    } == {first_run: {4, 12, 80}, second_run: {4, 12, 80}}


def test_torn_run_batch_is_detectable_and_malformed_rows_are_rejected(tmp_path):
    path = tmp_path / "torn.jsonl"
    path.write_text(
        '{"record_type":"run","run_id":"r","candidate_count":1}\n'
        '{"record_type":"candidate","run_id":"r"\n',
        encoding="utf-8",
    )
    run = next(row for row in load_ledger(path) if row["record_type"] == "run")
    assert run["append_status"] == "incomplete"

    lifecycle = {
        "run_id": "r2",
        "criteria_version": "v3",
        "candidates": ["malformed"],
    }
    with pytest.raises(ValueError):
        append_candidate_ledger(tmp_path / "malformed.jsonl", lifecycle)


def test_ledger_keeps_prior_rows_when_tail_ends_inside_utf8_character(tmp_path):
    from src.reviewer.decision_ledger import load_ledger

    path = tmp_path / "ledger.jsonl"
    path.write_bytes(
        b'{"record_type":"run","run_id":"previous","candidate_count":0}\n' + b"\xe4\xbd"
    )
    records = load_ledger(path)
    assert len(records) == 1
    assert records[0]["run_id"] == "previous"
    assert records[0]["append_status"] == "complete"
