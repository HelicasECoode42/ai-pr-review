"""Capture provider failures before production fallback hides their status."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from src.reviewer.provider import ProviderError

FATAL_HTTP_STATUSES = frozenset({400, 401, 402, 403, 404})


class EvaluationFailure(RuntimeError):
    def __init__(
        self, stage: str, *, status_code: int | None = None, outputs=None, error_kind=None
    ):
        super().__init__(f"Evaluation failed in {stage}")
        self.stage = stage
        self.status_code = status_code
        self.fatal = status_code in FATAL_HTTP_STATUSES
        self.outputs = outputs or []
        self.error_kind = error_kind

    def record(self) -> dict:
        return {
            "stage": self.stage,
            "http_status": self.status_code,
            "fatal": self.fatal,
            "error_kind": self.error_kind,
            "model_outputs": self.outputs,
        }


class ObservedProvider:
    """No prompts or credentials are persisted. Fatal failures short-circuit."""

    def __init__(self, provider):
        self.provider = provider
        self.errors: list[ProviderError] = []
        self.calls = 0
        self.outputs: list[str] = []

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        if self.errors and (
            self.errors[-1].status_code in FATAL_HTTP_STATUSES
            or self.errors[-1].kind in {"request_budget_exhausted", "run_stopped"}
        ):
            raise self.errors[-1]
        self.calls += 1
        try:
            output = self.provider.complete_json(system_prompt, user_prompt)
            self.outputs.append(output)
            return output
        except ProviderError as exc:
            self.errors.append(exc)
            raise


def source_identity() -> dict:
    root = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True))
    digest = hashlib.sha256()
    for path in sorted((root / "src").rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return {
        "reviewer_commit": commit,
        "working_tree_dirty": dirty,
        "reviewer_source_sha256": digest.hexdigest(),
    }
