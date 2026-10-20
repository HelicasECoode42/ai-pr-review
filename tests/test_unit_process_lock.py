"""Process-level tests for checkpoint exclusivity without a provider."""

from __future__ import annotations

import multiprocessing

from src.analyzer.review_plan import build_review_plan
from src.models import ChangedFile
from src.reviewer.grounded_review import GroundedResult
from src.reviewer.unit_review import review_by_units


class _Snapshot:
    base = "1" * 40
    head = "2" * 40

    def read(self, path, side="head"):
        return "def changed():\n    return 2\n"


def _one_unit():
    files = [ChangedFile(
        filename="src/one.py",
        patch="@@ -1,1 +1,1 @@\n-return 1\n+return 2\n",
    )]
    return build_review_plan(_Snapshot(), files, [files[0].filename], token_budget=1000), files


def _run_in_process(checkpoint, calls, entered, release, results, mode):
    plan, files = _one_unit()

    def offline_runner(context, selected_files):
        with calls.get_lock():
            calls.value += 1
        if mode == "hold":
            entered.set()
            if not release.wait(20):
                raise TimeoutError("test did not release the held process")
        return GroundedResult(
            summary="offline result", suggestions=[], pending=[], candidates=[], decisions=[],
            bundle={}, discovery_calls=1, verification_calls=1,
        )

    summary = review_by_units(
        plan, files, offline_runner, max_units=1, max_attempts=1,
        checkpoint_path=checkpoint,
        checkpoint_identity={"repo": "offline/process-lock", "model": "offline"},
    )
    results.put({
        "statuses": [outcome.status for outcome in summary.outcomes],
        "errors": [outcome.error for outcome in summary.outcomes],
        "attempts": [outcome.attempts for outcome in summary.outcomes],
        "counts": summary.counts(),
        "checkpoint_issues": summary.checkpoint_issues,
    })


def test_process_lock_prevents_duplicate_calls_then_allows_resume(tmp_path):
    context = multiprocessing.get_context("spawn")
    checkpoint = str(tmp_path / "units.jsonl")
    calls = context.Value("i", 0)
    entered = context.Event()
    release = context.Event()
    results = context.Queue()

    first = context.Process(
        target=_run_in_process,
        args=(checkpoint, calls, entered, release, results, "hold"),
    )
    first.start()
    try:
        assert entered.wait(15), "first process did not reach its offline runner"

        second = context.Process(
            target=_run_in_process,
            args=(checkpoint, calls, None, None, results, "quick"),
        )
        second.start()
        second.join(15)
        assert not second.is_alive(), "second process did not return while the lock was busy"
        assert second.exitcode == 0
        busy = results.get(timeout=5)
        assert busy["statuses"] == ["checkpoint_busy"]
        assert busy["attempts"] == [0]
        assert busy["errors"] and "checkpoint_busy" in busy["errors"][0]
        assert busy["counts"]["units_failed"] == 1
        assert busy["checkpoint_issues"] == ["checkpoint_lock_busy"]
        assert calls.value == 1

        release.set()
        first.join(15)
        assert not first.is_alive(), "first process did not finish after release"
        assert first.exitcode == 0
        assert results.get(timeout=5)["statuses"] == ["reviewed"]

        resumed = context.Process(
            target=_run_in_process,
            args=(checkpoint, calls, None, None, results, "quick"),
        )
        resumed.start()
        resumed.join(15)
        assert not resumed.is_alive()
        assert resumed.exitcode == 0
        assert results.get(timeout=5)["statuses"] == ["resumed"]
        assert calls.value == 1
    finally:
        release.set()
        if first.is_alive():
            first.join(15)


def test_symlink_lock_target_fails_closed_without_running_provider(tmp_path):
    plan, files = _one_unit()
    checkpoint = tmp_path / "units.jsonl"
    lock_path = checkpoint.with_name(checkpoint.name + ".lock")
    target = tmp_path / "important-file"
    target.write_text("preserve this", encoding="utf-8")
    try:
        lock_path.symlink_to(target)
    except OSError as exc:
        import pytest

        pytest.skip(f"symlink creation unavailable: {exc}")

    calls = []
    summary = review_by_units(
        plan, files,
        lambda context, selected_files: calls.append(1) or GroundedResult(
            summary="unexpected", suggestions=[], pending=[], candidates=[], decisions=[],
            bundle={}, discovery_calls=0, verification_calls=0,
        ),
        max_units=1, checkpoint_path=checkpoint,
        checkpoint_identity={"repo": "offline/process-lock", "model": "offline"},
    )

    assert calls == []
    assert summary.outcomes[0].status == "checkpoint_unavailable"
    assert summary.outcomes[0].error
    assert target.read_text(encoding="utf-8") == "preserve this"

    from src.reviewer.unit_review import run_units_for_review

    _, details = run_units_for_review(
        lambda context, selected_files: calls.append(1) or GroundedResult(
            summary="unexpected", suggestions=[], pending=[], candidates=[], decisions=[],
            bundle={}, discovery_calls=0, verification_calls=0,
        ),
        _Snapshot(), files, token_budget=1000, max_units=1,
        checkpoint_path=checkpoint,
        checkpoint_identity={"repo": "offline/process-lock", "model": "offline"},
    )
    assert calls == []
    assert details["units_failed"] == 1
    assert details["unit_checkpoint_failed"] == 1
    assert details["unit_checkpoint_lock_unavailable"] == 1
    assert details["unit_errors"] and "checkpoint_unavailable" in details["unit_errors"][0]
