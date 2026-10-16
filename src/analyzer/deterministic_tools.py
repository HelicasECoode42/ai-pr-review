"""Deterministic checks that cost nothing and never guess.

A linter and a type checker are not reviewers: they decide narrow classes of
question mechanically. But they are free, they never hallucinate, and what they
report is a fact about the code rather than an opinion about it. So they are run
first and their output enters the same pipeline as everything else, labelled with
the rule that produced it.

Only files that exist on disk can be checked: a frozen snapshot may contain paths
this working tree does not have, and inventing findings for them would be worse
than skipping them. Every skip is recorded, so a file that was never checked is
visible rather than assumed clean.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.models import ChangedFile, RiskFinding

# Rules that describe defects rather than taste, chosen by measurement.
# Formatting and import order are absent, and so is the bandit set: on this
# repository S contributed 115 of 142 findings, all "assert used", because a test
# suite asserts. Measured on a real range, this selection yields 16 findings -
# unused imports, raise-without-from, mutable defaults - instead of 142.
DEFAULT_SELECT = "E9,F4,F6,F7,F82,B"
TIMEOUT_SECONDS = 45
MAX_FINDINGS = 200


@dataclass
class DeterministicResult:
    findings: list[RiskFinding] = field(default_factory=list)
    checked_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    unavailable_reason: str | None = None

    def summary(self) -> dict:
        return {
            "lint_checked": len(self.checked_paths),
            "lint_skipped": len(self.skipped_paths),
            "lint_findings": len(self.findings),
            "lint_unavailable": self.unavailable_reason,
        }


def _command(paths: list[str], select: str) -> list[str]:
    return [sys.executable, "-m", "ruff", "check", "--output-format", "json",
            "--select", select, "--no-cache", *paths]


def run_linters(
    files: list[ChangedFile],
    *,
    root: Path | str | None = None,
    select: str = DEFAULT_SELECT,
    timeout: int = TIMEOUT_SECONDS,
) -> DeterministicResult:
    """Run ruff over the changed Python files that exist, and translate its output."""
    base = Path(root or Path.cwd())
    result = DeterministicResult()
    paths: list[str] = []
    for file in files:
        if not str(file.filename).endswith(".py"):
            continue
        if (base / file.filename).exists():
            paths.append(str(base / file.filename))
            result.checked_paths.append(file.filename)
        else:
            result.skipped_paths.append(file.filename)
    if not paths:
        return result
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, bounded timeout
            _command(paths, select), capture_output=True, text=True, timeout=timeout,
            cwd=str(base), check=False,
        )
    except FileNotFoundError:
        result.unavailable_reason = "ruff not installed"
        return result
    except subprocess.TimeoutExpired:
        result.unavailable_reason = f"timed out after {timeout}s"
        return result
    if not completed.stdout.strip():
        return result
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result.unavailable_reason = "unparsable ruff output"
        return result
    for item in payload[:MAX_FINDINGS]:
        location = item.get("location") or {}
        path = str(item.get("filename", ""))
        try:
            relative = str(Path(path).resolve().relative_to(base.resolve()))
        except ValueError:
            relative = path
        code = str(item.get("code") or "ruff")
        result.findings.append(RiskFinding(
            file_path=relative,
            line=int(location.get("row") or 0) or None,
            severity="medium",
            rule_id=f"lint-{code.lower()}",
            title=f"{code}: mechanical defect",
            evidence=str(item.get("message", ""))[:400],
            recommendation="Fix the reported defect; this is a fact about the code.",
            confidence=1.0,
        ))
    return result
