"""Idempotent publishing: never say the same thing twice, never repeat a refusal.

Two rules, both deterministic:

* identical comments are identified by a fingerprint over (path, line, body) after
  normalisation, so a re-run updates rather than duplicates;
* a dismissal is remembered by the same fingerprint, so a finding the author has
  already rejected as "won't fix" is not raised again.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_FINGERPRINT_LENGTH = 20
_DYNAMIC = re.compile(r"\b(?:20\d\d-\d\d-\d\d|\d{2}:\d{2}:\d{2}|run[-_ ]?id[:=]\s*\S+)",
                      re.IGNORECASE)


def normalize_comment(body: str) -> str:
    """Drop volatile content so a re-run fingerprints the same finding."""
    without_dynamic = _DYNAMIC.sub("", body or "")
    lines = [re.sub(r"\s+", " ", line).strip() for line in without_dynamic.splitlines()]
    return "\n".join(line for line in lines if line)


def comment_fingerprint(file_path: str, line: int | None, body: str) -> str:
    payload = f"{file_path}:{line if line is not None else '-'}\n{normalize_comment(body)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_FINGERPRINT_LENGTH]


def review_comment_body(suggestion) -> str:
    """Render the stable human portion used by the reusable workflow."""
    severity = getattr(suggestion, "severity", "unknown")
    severity = getattr(severity, "value", severity)
    confidence = round(float(getattr(suggestion, "confidence", 0)) * 100)
    return (
        f"**{severity}** ({confidence}%): {getattr(suggestion, 'title', '')}\n\n"
        f"{getattr(suggestion, 'reason', '')}\n\n"
        f"> {getattr(suggestion, 'recommendation', '')}"
    )


def review_publication_fingerprint(
    *, repo: str, pr_number: int, head_sha: str, criteria_version: str,
    file_path: str, line: int | None, body: str,
) -> str:
    """Identify one comment within its exact repo/PR/head/criteria scope."""
    identity = {
        "repo": repo.casefold(), "pr": pr_number, "head": head_sha,
        "criteria": criteria_version, "path": file_path, "line": line,
        "body": normalize_comment(body),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def duplicates(new_fingerprints: list[str], existing: set[str]) -> list[bool]:
    """True where a comment has already been posted."""
    return [fingerprint in existing for fingerprint in new_fingerprints]


class DismissalStore:
    """Append-only memory of comments the author has already refused."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self._dismissed: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn line must not lose the rest of the memory
            if isinstance(record, dict) and record.get("fingerprint"):
                self._dismissed.add(str(record["fingerprint"]))

    def is_dismissed(self, fingerprint: str) -> bool:
        return fingerprint in self._dismissed

    def dismiss(self, fingerprint: str, *, reason: str = "wont_fix") -> None:
        if fingerprint in self._dismissed:
            return
        self._dismissed.add(fingerprint)
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"fingerprint": fingerprint, "reason": reason}) + "\n")

    @property
    def dismissed(self) -> set[str]:
        return set(self._dismissed)
