"""A decision ledger that outlives a run.

filter_verdicts explain one run in memory. Persisting them makes three things
possible: attributing a change in behaviour to a criteria version, auditing why a
finding was or was not published after the fact, and feeding the self-improvement
gate with per-case outcomes instead of impressions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

LEDGER_VERSION = "1"


@dataclass(frozen=True)
class DecisionRecord:
    run_id: str
    criteria_version: str
    candidate_id: int
    file_path: str
    line: int | None
    decision: str | None
    reason: str | None
    failed_filters: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "ledger_version": LEDGER_VERSION,
            "run_id": self.run_id,
            "criteria_version": self.criteria_version,
            "candidate_id": self.candidate_id,
            "file_path": self.file_path,
            "line": self.line,
            "decision": self.decision,
            "reason": self.reason,
            "failed_filters": list(self.failed_filters),
        }


def decision_records(report, *, run_id: str, criteria_version: str) -> list[DecisionRecord]:
    """One record per verified candidate, including the filters that rejected it."""
    records: list[DecisionRecord] = []
    for index, item in enumerate(getattr(report, "behavior_verifications", []) or []):
        failed = tuple(
            str(verdict.get("filter")) for verdict in item.get("filter_verdicts", []) or []
            if not verdict.get("passed")
        )
        records.append(DecisionRecord(
            run_id=run_id,
            criteria_version=criteria_version,
            candidate_id=int(item.get("candidate_id", index) or 0),
            file_path=str(item.get("file_path", "")),
            line=item.get("line"),
            decision=item.get("decision"),
            reason=item.get("validation_reason") or item.get("reason"),
            failed_filters=failed,
        ))
    return records


def append_ledger(path: Path | str, records: list[DecisionRecord]) -> int:
    """Append records, one JSON object per line; returns how many were written."""
    if not records:
        return 0
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    with location.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.as_dict(), ensure_ascii=False) + "\n")
        handle.flush()
    return len(records)


def load_ledger(path: Path | str) -> list[dict]:
    """Read the ledger back; a torn final line is skipped rather than fatal."""
    location = Path(path)
    if not location.exists():
        return []
    out: list[dict] = []
    for line in location.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def filter_failure_counts(records: list[dict]) -> dict[str, int]:
    """How often each filter rejected a candidate - the tuning signal."""
    counts: dict[str, int] = {}
    for record in records:
        for name in record.get("failed_filters", []) or []:
            counts[str(name)] = counts.get(str(name), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))
