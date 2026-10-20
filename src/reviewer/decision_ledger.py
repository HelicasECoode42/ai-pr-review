"""Candidate lifecycle and append-only local decision ledger."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.models import ReviewReport, ReviewSuggestion

LEDGER_VERSION = "2"
_MAX_CANDIDATES = 10_000
_FILTERS = frozenset(
    {
        "source_provenance",
        "counter_evidence",
        "chain_completeness",
        "witness_eval",
        "decision_is_confirmed",
        "evidence_present",
        "citation_valid",
    }
)
_CANDIDATE_STATES = frozenset(
    {
        "awaiting_verification",
        "confirmed",
        "dismissed",
        "needs_context",
        "filtered",
        "unverified",
        "unknown",
        "visible_report_candidate",
    }
)
_LIFECYCLE_STATES = frozenset({"ready_for_publication", "withheld", "unknown"})
_REASONS = frozenset(
    {
        "duplicate_identity",
        "anchor_not_in_changed_files",
        "candidate_verification_budget",
        "invalid_head_anchor",
        "candidate_not_in_final_suggestions",
        "per_file_cap",
        "behavior_verification_disabled",
        "no_verdict_returned",
        "invalid_decision",
        "dismissal_has_no_valid_counter_evidence",
        "insufficient_behavior_chain_or_source_evidence",
        "model_review_unavailable",
        "legacy_candidate_inventory_incomplete",
        "candidate_identity_unmapped",
        "candidate_identity_ambiguous",
        "pending_candidate",
        "non_ai_suggestion",
        "verified",
        "not_reported",
        "below_publish_threshold",
        "missing_publish_anchor",
        "publication_identity_unavailable",
    }
)


def _candidate_key(path: object, line: object, title: object) -> str:
    identity = {
        "file_path": path if isinstance(path, str) else "",
        "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
        "title": title.casefold() if isinstance(title, str) else "",
    }
    body = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _safe_path(value: object) -> str:
    return value.replace("\x00", "")[:400] if isinstance(value, str) else ""


def _safe_line(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _reason(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str):
            prefix = value.split(":", 1)[0].strip()
            if prefix in _REASONS | _FILTERS:
                return prefix
    return None


def _failed_filters(verification: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item["filter"]
                for item in verification.get("filter_verdicts", []) or []
                if isinstance(item, dict)
                and item.get("passed") is False
                and item.get("filter") in _FILTERS
            }
        )
    )


@dataclass(frozen=True)
class DecisionRecord:
    run_id: str
    criteria_version: str
    candidate_id: int | str
    file_path: str
    line: int | None
    decision: str | None
    reason: str | None
    failed_filters: tuple[str, ...]
    candidate_key_sha256: str = ""
    candidate_status: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Keep the original helper API while persisting only safe reason codes."""
        return {
            "record_type": "candidate",
            "ledger_version": LEDGER_VERSION,
            "run_id": self.run_id,
            "criteria_version": self.criteria_version[:100],
            "candidate_id": self.candidate_id,
            "candidate_key_sha256": self.candidate_key_sha256,
            "file_path": _safe_path(self.file_path),
            "line": _safe_line(self.line),
            "decision": self.decision
            if self.decision
            in {
                "confirmed",
                "dismissed",
                "needs_context",
                "unverified",
            }
            else None,
            "candidate_status": self.candidate_status
            if self.candidate_status in _CANDIDATE_STATES
            else "unknown",
            "reason_code": _reason(self.reason),
            "failed_filters": [name for name in self.failed_filters if name in _FILTERS],
        }


def decision_records(
    report: ReviewReport, *, run_id: str, criteria_version: str
) -> list[DecisionRecord]:
    """Inventory grounded candidates; older reports expose only survivors."""
    verifications = getattr(report, "behavior_verifications", []) or []
    verified_by_id = {
        row["candidate_id"]: row
        for row in verifications
        if isinstance(row, dict)
        and isinstance(row.get("candidate_id"), int)
        and not isinstance(row.get("candidate_id"), bool)
    }
    audit = getattr(report, "candidate_audit", None) or []
    entries: list[tuple[int | str, dict[str, Any], dict[str, Any]]] = []
    if audit:
        used_ids: set[int] = set()
        for index, candidate in enumerate(audit[:_MAX_CANDIDATES]):
            if not isinstance(candidate, dict):
                entries.append(
                    (
                        f"unmapped:{index}",
                        {
                            "status": "unknown",
                            "reason": "candidate_identity_unmapped",
                        },
                        {},
                    )
                )
                continue
            candidate_id = candidate.get("candidate_id", index)
            if isinstance(candidate_id, bool) or not isinstance(candidate_id, int):
                candidate_id = index
            if candidate_id in used_ids:
                entries.append(
                    (
                        f"unmapped:{index}",
                        {
                            "status": "unknown",
                            "reason": "candidate_identity_unmapped",
                        },
                        {},
                    )
                )
                continue
            used_ids.add(candidate_id)
            entries.append((candidate_id, candidate, verified_by_id.get(candidate_id, {})))
    else:
        entries = [
            (row.get("candidate_id", index), {}, row)
            for index, row in enumerate(verifications[:_MAX_CANDIDATES])
            if isinstance(row, dict)
        ]

    records = []
    for candidate_id, candidate, verification in entries:
        suggestion = candidate.get("suggestion") or {}
        if not isinstance(suggestion, dict):
            suggestion = {}
        path = _safe_path(verification.get("file_path") or suggestion.get("file_path"))
        line = _safe_line(verification.get("line", suggestion.get("line")))
        title = suggestion.get("title") or verification.get("title", "")
        decision = verification.get("decision")
        status = candidate.get("status")
        if status not in _CANDIDATE_STATES:
            status = decision
        reason = _reason(
            verification.get("validation_reason"),
            verification.get("reason"),
            candidate.get("reason"),
            candidate.get("publication_reason"),
        ) or ("verified" if decision == "confirmed" else None)
        if status == "awaiting_verification" and not verification:
            reason = "no_verdict_returned"
        records.append(
            DecisionRecord(
                run_id=run_id,
                criteria_version=criteria_version,
                candidate_id=candidate_id,
                file_path=path,
                line=line,
                decision=decision,
                reason=reason,
                failed_filters=_failed_filters(verification),
                candidate_key_sha256=_candidate_key(path, line, title),
                candidate_status=status,
            )
        )
    return records


def build_candidate_lifecycle(
    report: ReviewReport,
    *,
    run_id: str,
    criteria_version: str,
    publish_min_confidence: float = 0.0,
) -> dict[str, Any]:
    """Describe the enumerable candidate scope without claiming publication."""
    records = decision_records(report, run_id=run_id, criteria_version=criteria_version)
    audit = getattr(report, "candidate_audit", None) or []
    trace = (getattr(report, "repository_context", {}) or {}).get("review_trace")
    grounded = isinstance(trace, dict)
    failed = bool(getattr(report, "ai_failure_reason", None))
    used_ai = bool(getattr(report, "used_ai", False))
    if grounded:
        scope, inventory, count_known = (
            "grounded_final_payload_candidates",
            "complete_within_scope",
            True,
        )
        trace_counts = trace.get("candidate_counts") if isinstance(trace, dict) else None
        raw_count = trace_counts.get("raw") if isinstance(trace_counts, dict) else None
        if len(audit) > _MAX_CANDIDATES or any(not isinstance(row, dict) for row in audit):
            inventory, count_known = "partial", False
        if isinstance(raw_count, int) and raw_count != len(audit):
            inventory, count_known = "partial", False
    elif failed:
        scope, inventory, count_known = "no_parsed_model_candidate_inventory", "unknown", False
    elif used_ai:
        scope, inventory, count_known = (
            "surviving_legacy_report_suggestions_and_pending",
            "partial",
            False,
        )
    else:
        scope, inventory, count_known = "model_candidates_not_requested", "not_applicable", True

    suggestions = getattr(report, "suggestions", []) or []
    pending = getattr(report, "pending_suggestions", []) or []
    visible_by_key: dict[str, list[tuple[int, ReviewSuggestion]]] = {}
    for index, suggestion in enumerate(suggestions):
        key = _candidate_key(suggestion.file_path, suggestion.line, suggestion.title)
        visible_by_key.setdefault(key, []).append((index, suggestion))
    pending_keys = {_candidate_key(item.file_path, item.line, item.title) for item in pending}
    confirmed_key_counts: dict[str, int] = {}
    for record in records:
        if record.candidate_status == record.decision == "confirmed":
            confirmed_key_counts[record.candidate_key_sha256] = (
                confirmed_key_counts.get(record.candidate_key_sha256, 0) + 1
            )

    rows: list[dict[str, Any]] = []
    publish_ids: dict[str, int | str] = {}
    if grounded:
        for record in records:
            matches = visible_by_key.get(record.candidate_key_sha256, [])
            exact = len(matches) == 1 and record.candidate_status != "unknown"
            status, reason = "withheld", _reason(record.reason) or "pending_candidate"
            duplicate_confirmed = confirmed_key_counts.get(record.candidate_key_sha256, 0) > 1
            blocked = record.candidate_key_sha256 in pending_keys or record.candidate_status in {
                "needs_context",
                "dismissed",
                "filtered",
                "unverified",
                "awaiting_verification",
            }
            if not blocked and record.candidate_status == record.decision == "confirmed":
                if duplicate_confirmed or len(matches) > 1:
                    status, reason = "unknown", "candidate_identity_ambiguous"
                elif not exact:
                    reason = "candidate_not_in_final_suggestions"
                else:
                    index, suggestion = matches[0]
                    publish_ids[str(index)] = record.candidate_id
                    if suggestion.line is None:
                        reason = "missing_publish_anchor"
                    elif suggestion.confidence < publish_min_confidence:
                        reason = "below_publish_threshold"
                    elif not all((report.pr.repo, report.pr.number, report.pr.head_sha)):
                        status, reason = "unknown", "publication_identity_unavailable"
                    else:
                        status, reason = "ready_for_publication", "verified"
            elif not blocked and record.candidate_status in {None, "unknown"}:
                status, reason = "unknown", "candidate_identity_unmapped"
            row = {
                "candidate_id": record.candidate_id,
                "candidate_key_sha256": record.candidate_key_sha256,
                "file_path": record.file_path,
                "line": record.line,
                "candidate_status": record.candidate_status or "unknown",
                "decision": record.decision,
                "lifecycle_status": status,
                "reason_code": reason,
                "failed_filters": list(record.failed_filters),
                "suggestion_mapping": "exact" if exact else "unknown",
                "publish_fingerprint": None,
            }
            rows.append(row)
    elif used_ai and not failed:
        for suggestion in suggestions:
            key = _candidate_key(suggestion.file_path, suggestion.line, suggestion.title)
            matches = visible_by_key.get(key, [])
            eligible = len(matches) == 1 and key not in pending_keys
            status = "ready_for_publication" if eligible else "withheld"
            reason = "legacy_candidate_inventory_incomplete" if eligible else "non_ai_suggestion"
            if eligible and (
                suggestion.line is None or suggestion.confidence < publish_min_confidence
            ):
                status, reason = (
                    "withheld",
                    (
                        "missing_publish_anchor"
                        if suggestion.line is None
                        else "below_publish_threshold"
                    ),
                )
            elif eligible and not all((report.pr.repo, report.pr.number, report.pr.head_sha)):
                status, reason = "unknown", "publication_identity_unavailable"
            row_id = f"report:{key[:24]}"
            rows.append(
                {
                    "candidate_id": row_id,
                    "candidate_key_sha256": key,
                    "file_path": _safe_path(suggestion.file_path),
                    "line": _safe_line(suggestion.line),
                    "candidate_status": "visible_report_candidate",
                    "decision": None,
                    "lifecycle_status": status,
                    "reason_code": reason,
                    "failed_filters": [],
                    "suggestion_mapping": "exact" if len(matches) == 1 else "unknown",
                    "publish_fingerprint": None,
                }
            )
            if len(matches) == 1:
                publish_ids[str(matches[0][0])] = row_id
        for suggestion in pending:
            key = _candidate_key(suggestion.file_path, suggestion.line, suggestion.title)
            rows.append(
                {
                    "candidate_id": f"report:{key[:24]}",
                    "candidate_key_sha256": key,
                    "file_path": _safe_path(suggestion.file_path),
                    "line": _safe_line(suggestion.line),
                    "candidate_status": "visible_report_candidate",
                    "decision": None,
                    "lifecycle_status": "withheld",
                    "reason_code": "pending_candidate",
                    "failed_filters": [],
                    "suggestion_mapping": "unknown",
                    "publish_fingerprint": None,
                }
            )

    overflow = max(0, len(rows) - _MAX_CANDIDATES)
    if overflow:
        rows = rows[:_MAX_CANDIDATES]
        inventory, count_known = "partial", False
    counts = {
        state: sum(row["lifecycle_status"] == state for row in rows)
        for state in ("ready_for_publication", "withheld", "unknown")
    }
    return {
        "schema_version": 1,
        "run_id": run_id,
        "criteria_version": criteria_version,
        "candidate_scope": scope,
        "inventory_status": inventory,
        "candidate_count": len(rows),
        "candidate_count_known": count_known,
        "candidate_overflow_count": overflow,
        "counts": counts,
        "candidates": rows,
        "suggestion_candidate_ids": publish_ids,
        "persistence_status": "pending",
    }


def _candidate_row(item: dict[str, Any], lifecycle: dict[str, Any]) -> dict[str, Any]:
    candidate_id = item.get("candidate_id")
    identity = item.get("candidate_key_sha256")
    return {
        "record_type": "candidate",
        "ledger_version": LEDGER_VERSION,
        "run_id": lifecycle["run_id"],
        "criteria_version": lifecycle["criteria_version"],
        "candidate_id": candidate_id
        if isinstance(candidate_id, (int, str)) and not isinstance(candidate_id, bool)
        else "unknown",
        "candidate_key_sha256": identity
        if isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{64}", identity)
        else "",
        "file_path": _safe_path(item.get("file_path")),
        "line": _safe_line(item.get("line")),
        "candidate_status": item.get("candidate_status")
        if item.get("candidate_status") in _CANDIDATE_STATES
        else "unknown",
        "decision": item.get("decision")
        if item.get("decision")
        in {
            "confirmed",
            "dismissed",
            "needs_context",
            "unverified",
        }
        else None,
        "lifecycle_status": item.get("lifecycle_status")
        if item.get("lifecycle_status") in _LIFECYCLE_STATES
        else "unknown",
        "reason_code": item.get("reason_code")
        if item.get("reason_code") in _REASONS | _FILTERS
        else "not_reported",
        "failed_filters": [name for name in item.get("failed_filters", []) if name in _FILTERS],
        "suggestion_mapping": item.get("suggestion_mapping")
        if item.get("suggestion_mapping") in {"exact", "unknown"}
        else "unknown",
        "publish_fingerprint": item.get("publish_fingerprint")
        if isinstance(item.get("publish_fingerprint"), str)
        and re.fullmatch(r"[0-9a-f]{24}", item["publish_fingerprint"])
        else None,
    }


def append_candidate_ledger(path: Path | str, lifecycle: dict[str, Any]) -> int:
    """Append one run summary and its bounded candidate rows in one batch."""
    candidates = lifecycle.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) > _MAX_CANDIDATES:
        raise ValueError("invalid candidate lifecycle row count")
    run_id = str(lifecycle.get("run_id", ""))[:100]
    criteria = str(lifecycle.get("criteria_version", ""))[:100]
    rows = [
        {
            "record_type": "run",
            "ledger_version": LEDGER_VERSION,
            "run_id": run_id,
            "criteria_version": criteria,
            "candidate_scope": str(lifecycle.get("candidate_scope", "unknown"))[:100],
            "inventory_status": str(lifecycle.get("inventory_status", "unknown"))[:40],
            "candidate_count": len(candidates),
            "candidate_count_known": bool(lifecycle.get("candidate_count_known", False)),
            "candidate_overflow_count": int(lifecycle.get("candidate_overflow_count", 0) or 0),
        }
    ]
    if any(not isinstance(item, dict) for item in candidates):
        raise ValueError("candidate lifecycle contains a malformed row")
    rows.extend(
        _candidate_row(item, {"run_id": run_id, "criteria_version": criteria})
        for item in candidates
    )
    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows
    ).encode("utf-8")
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    if location.is_symlink():
        raise OSError("candidate ledger path cannot be a symlink")
    descriptor = os.open(location, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        end = os.lseek(descriptor, 0, os.SEEK_END)
        if end:
            os.lseek(descriptor, end - 1, os.SEEK_SET)
            needs_separator = os.read(descriptor, 1) != b"\n"
            os.lseek(descriptor, 0, os.SEEK_END)
            if needs_separator:
                payload = b"\n" + payload
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("candidate ledger write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return len(rows)


def append_ledger(path: Path | str, records: list[DecisionRecord]) -> int:
    """Append legacy candidate records through the same local JSONL boundary."""
    if not records:
        return 0
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(record.as_dict(), ensure_ascii=False) + "\n" for record in records)
    with location.open("a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return len(records)


def load_ledger(path: Path | str) -> list[dict[str, Any]]:
    """Skip a malformed/torn JSONL row while reading previous records."""
    location = Path(path)
    if not location.exists():
        return []
    rows = []
    for line in location.read_bytes().splitlines():
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    actual: dict[str, int] = {}
    for row in rows:
        if row.get("record_type") == "candidate":
            run_id = str(row.get("run_id"))
            actual[run_id] = actual.get(run_id, 0) + 1
    for row in rows:
        if row.get("record_type") == "run":
            row["append_status"] = (
                "complete"
                if actual.get(str(row.get("run_id")), 0) == row.get("candidate_count")
                else "incomplete"
            )
    return rows


def filter_failure_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for name in record.get("failed_filters", []) or []:
            counts[str(name)] = counts.get(str(name), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))
