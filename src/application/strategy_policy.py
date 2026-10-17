"""Small, fail-closed runtime policy loader for reviewed strategy versions.

This module intentionally has no dependency on the evaluation CLI. It validates
the tiny policy surface that production is allowed to consume and verifies the
active file's content hash before returning it. The hash is an integrity check,
not an authenticated signature.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROMPT_TEMPLATES = {
    "": "",
    "cross_file_contract_check_v1": (
        "When a finding depends on behavior across files, inspect the relevant caller, "
        "callee, and declared contract using bounded read-only evidence. State the trigger, "
        "expected behavior, actual behavior, and PR-introduced change. If the required "
        "contract or path is unavailable, keep the conclusion unverified instead of "
        "inferring it from names or the PR description."
    ),
}
PROMPT_TEMPLATE_VALUES = frozenset(PROMPT_TEMPLATES.values())
MAX_PROMPT_ADDENDUM_CHARS = 1200
MAX_PROMPT_ADDENDUM_UTF8_BYTES = 2048
MAX_PROMPT_ADDENDUM_ESTIMATED_TOKENS = 400
DEFAULT_PLAYBOOK_RECALL_K = 5
POLICY_STORE_NAME = "strategy-store"
_VERSION_ID = re.compile(r"^(?:baseline|[0-9a-f]{64})$")


class PolicyError(ValueError):
    """An active policy is malformed or fails integrity validation."""


class RuntimePolicy(BaseModel):
    """Only prompt guidance and the existing playbook recall count are mutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_addendum: str = Field(default="", max_length=MAX_PROMPT_ADDENDUM_CHARS)
    playbook_recall_k: int = Field(default=DEFAULT_PLAYBOOK_RECALL_K, ge=1, le=10)

    @field_validator("prompt_addendum")
    @classmethod
    def validate_prompt_template(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_PROMPT_ADDENDUM_UTF8_BYTES:
            raise ValueError("prompt_addendum exceeds the 2048-byte input bound")
        if estimate_prompt_tokens(value) > MAX_PROMPT_ADDENDUM_ESTIMATED_TOKENS:
            raise ValueError("prompt_addendum exceeds the 400-token planning estimate")
        if value not in PROMPT_TEMPLATE_VALUES:
            raise ValueError("prompt_addendum must be an approved deterministic template")
        return value

    @field_validator("playbook_recall_k")
    @classmethod
    def reject_unreplayed_retrieval_change(cls, value: int) -> int:
        if value != DEFAULT_PLAYBOOK_RECALL_K:
            raise ValueError("unsupported_policy_change: playbook_recall_k replay is not enabled")
        return value


@dataclass(frozen=True)
class LoadedPolicy:
    policy: RuntimePolicy
    policy_id: str
    status: Literal["baseline", "active", "invalid_fallback"]
    reason: str | None = None


BASELINE_POLICY = RuntimePolicy()
BASELINE_CONTENT_SHA256 = hashlib.sha256(
    json.dumps(BASELINE_POLICY.model_dump(), sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def estimate_prompt_tokens(text: str) -> int:
    """Conservative planning estimate for the bounded English template (~3 chars/token)."""
    return (len(text) + 2) // 3


def _safe_version_path(root: Path, version_id: str) -> Path:
    if not _VERSION_ID.fullmatch(version_id):
        raise PolicyError("invalid policy version id")
    resolved_root = root.resolve()
    path = root / "versions" / f"{version_id}.json"
    if not path.resolve().is_relative_to(resolved_root):
        raise PolicyError("policy version path escapes the store")
    return path


def load_active_policy(store_dir: str | Path | None = None) -> LoadedPolicy:
    """Load a verified active version, or return baseline with explicit status.

    ``None`` means production should use the baseline. This function never
    searches the current directory or reads environment files.
    """
    if store_dir is None:
        return LoadedPolicy(BASELINE_POLICY, "baseline", "baseline")
    root = Path(store_dir)
    pointer_path = root / "active.json"
    if not pointer_path.exists():
        return LoadedPolicy(BASELINE_POLICY, "baseline", "baseline")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        if not isinstance(pointer, dict) or pointer.get("schema_version") != 1:
            raise PolicyError("unsupported active pointer schema")
        version_id = pointer.get("version_id")
        expected_sha = pointer.get("content_sha256")
        if not isinstance(version_id, str) or not _VERSION_ID.fullmatch(version_id):
            raise PolicyError("invalid active version id")
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise PolicyError("invalid active content hash")
        if version_id == "baseline":
            if expected_sha != BASELINE_CONTENT_SHA256:
                raise PolicyError("baseline content hash mismatch")
            return LoadedPolicy(BASELINE_POLICY, "baseline", "baseline")
        version_path = _safe_version_path(root, version_id)
        raw = version_path.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        if actual_sha != expected_sha:
            raise PolicyError("active version content hash mismatch")
        record = json.loads(raw)
        if record.get("schema_version") != 1 or record.get("version_id") != version_id:
            raise PolicyError("active version metadata mismatch")
        if record.get("status") != "accepted":
            raise PolicyError("active version is not accepted")
        policy = RuntimePolicy.model_validate(record.get("policy"))
        return LoadedPolicy(policy, version_id, "active")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return LoadedPolicy(BASELINE_POLICY, "baseline", "invalid_fallback", str(exc))


__all__ = [
    "BASELINE_POLICY",
    "DEFAULT_PLAYBOOK_RECALL_K",
    "LoadedPolicy",
    "MAX_PROMPT_ADDENDUM_CHARS",
    "MAX_PROMPT_ADDENDUM_ESTIMATED_TOKENS",
    "MAX_PROMPT_ADDENDUM_UTF8_BYTES",
    "PROMPT_TEMPLATES",
    "PolicyError",
    "RuntimePolicy",
    "estimate_prompt_tokens",
    "load_active_policy",
]
