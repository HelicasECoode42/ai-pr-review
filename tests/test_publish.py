"""Idempotent publishing: fingerprints, duplicates and dismissal memory."""

from __future__ import annotations

from src.reviewer.publish import DismissalStore, comment_fingerprint, duplicates


def test_volatile_content_does_not_change_the_fingerprint():
    first = comment_fingerprint("a.py", 3, "Guard removed\nRun: 2026-10-16 01:20:00\nfix it")
    second = comment_fingerprint("a.py", 3, "Guard removed\nRun: 2027-01-01 09:00:00\nfix it")
    assert first == second


def test_different_findings_get_different_fingerprints():
    assert comment_fingerprint("a.py", 3, "x") != comment_fingerprint("a.py", 4, "x")
    assert comment_fingerprint("a.py", 3, "x") != comment_fingerprint("b.py", 3, "x")


def test_duplicates_are_detected_against_posted_fingerprints():
    fingerprint = comment_fingerprint("a.py", 1, "x")
    assert duplicates([fingerprint], {fingerprint}) == [True]
    assert duplicates([fingerprint], set()) == [False]


def test_dismissal_memory_survives_a_restart(tmp_path):
    store = DismissalStore(tmp_path / "dismissed.jsonl")
    fingerprint = comment_fingerprint("a.py", 1, "x")
    assert store.is_dismissed(fingerprint) is False
    store.dismiss(fingerprint, reason="wont_fix")
    assert DismissalStore(tmp_path / "dismissed.jsonl").is_dismissed(fingerprint) is True


def test_a_torn_ledger_line_does_not_lose_the_memory(tmp_path):
    path = tmp_path / "dismissed.jsonl"
    path.write_text('{"fingerprint": "abc"}\n{"fingerprint": "de\n')
    store = DismissalStore(path)
    assert store.dismissed == {"abc"}


def test_dismissing_twice_writes_once(tmp_path):
    path = tmp_path / "dismissed.jsonl"
    store = DismissalStore(path)
    store.dismiss("abc")
    store.dismiss("abc")
    assert path.read_text().count("abc") == 1
