"""Run bounded, isolated Ruff diagnostics against immutable repository evidence.

Lint output is a mechanical candidate, not a semantic finding. In particular, a
diagnostic on an added line is only attributable to the diff; it is not proof of
a user-visible defect.
"""

from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from src.analyzer.diff_parser import changed_line_map
from src.analyzer.repository_context import Snapshot, safe_source_path
from src.models import ChangedFile, RiskFinding

DEFAULT_SELECT = "E9,F4,F6,F7,F82,B"
TIMEOUT_SECONDS = 20
MAX_FINDINGS = 200
MAX_FILES = 10
MAX_SNAPSHOT_READS = 20
MAX_SNAPSHOT_FETCH_BYTES = 1_000_000
MAX_SNAPSHOT_SECONDS = 30
MAX_FILE_BYTES = 100_000
MAX_TOTAL_BYTES = 400_000
MAX_OUTPUT_BYTES = 1_000_000


@dataclass
class DeterministicResult:
    findings: list[RiskFinding] = field(default_factory=list)
    checked_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    unavailable_reason: str | None = None
    status: str = "not_applicable"
    tool_version: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    notes: list[str] = field(default_factory=list)
    snapshot_read_calls: int = 0
    snapshot_bytes_read: int = 0
    baseline_diagnostics: int = 0
    unattributed_diagnostics: int = 0

    def summary(self) -> dict:
        return {
            "lint_status": self.status,
            "lint_checked": len(self.checked_paths),
            "lint_skipped": len(self.skipped_paths),
            "lint_findings": len(self.findings),
            "lint_unavailable": self.unavailable_reason,
            "lint_tool_version": self.tool_version,
            "lint_base_sha": self.base_sha,
            "lint_head_sha": self.head_sha,
            "lint_notes": list(self.notes),
            "lint_snapshot_read_calls": self.snapshot_read_calls,
            "lint_snapshot_bytes_read": self.snapshot_bytes_read,
            "lint_baseline_diagnostics": self.baseline_diagnostics,
            "lint_unattributed_diagnostics": self.unattributed_diagnostics,
        }


def _bounded_process(command: list[str], *, cwd: Path, timeout: int) -> tuple[int | None, bytes, bytes, str | None]:
    """Drain both pipes while enforcing a hard combined output and time limit."""
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell or project code
            command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return None, b"", b"", "ruff not installed"
    except OSError as exc:
        return None, b"", b"", f"ruff could not start: {type(exc).__name__}"
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    try:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    except OSError as exc:
        process.kill()
        process.wait()
        selector.close()
        process.stdout.close()
        process.stderr.close()
        return None, b"", b"", f"bounded pipe reading unsupported: {type(exc).__name__}"
    output = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                return None, b"", b"", f"ruff timed out after {timeout}s"
            events = selector.select(remaining)
            if not events:
                process.kill()
                process.wait()
                return None, b"", b"", f"ruff timed out after {timeout}s"
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    process.kill()
                    process.wait()
                    return None, b"", b"", f"ruff output exceeded {MAX_OUTPUT_BYTES} bytes"
                output[key.data].extend(chunk)
        try:
            returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return None, b"", b"", f"ruff timed out after {timeout}s"
        return returncode, bytes(output["stdout"]), bytes(output["stderr"]), None
    except OSError as exc:
        process.kill()
        process.wait()
        return None, b"", b"", f"ruff output read failed: {type(exc).__name__}"
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _run_ruff(
    paths: list[str], *, cwd: Path, timeout: int, executable: str,
) -> tuple[list[dict] | None, str | None]:
    """Run Ruff without repository config and reject oversized or failed output."""
    command = [executable, "check", "--isolated", "--no-cache",
               "--output-format", "json", "--select", DEFAULT_SELECT, "--", *paths]
    returncode, stdout, stderr, error = _bounded_process(command, cwd=cwd, timeout=timeout)
    if error:
        return None, error
    assert returncode is not None
    if returncode not in (0, 1):
        detail = stderr[:500].decode("utf-8", errors="replace").strip()
        return None, f"ruff exited {returncode}" + (f": {detail}" if detail else "")
    try:
        payload = json.loads(stdout.decode("utf-8") or "[]")
    except json.JSONDecodeError:
        return None, "ruff returned invalid JSON"
    if not isinstance(payload, list):
        return None, "ruff returned an invalid result shape"
    if len(payload) > MAX_FINDINGS:
        return None, f"ruff finding count exceeded {MAX_FINDINGS}"
    normalized = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            path = Path(str(item.get("filename", ""))).resolve().relative_to(cwd.resolve())
        except (OSError, ValueError):
            return None, "ruff returned a path outside its isolated input directory"
        normalized.append({**item, "filename": path.as_posix()})
    return normalized, None


def _materialize(root: Path, contents: dict[str, str]) -> list[str]:
    """Write only validated relative paths into a fresh, private temp directory."""
    paths = []
    for relative, text in contents.items():
        path = PurePosixPath(relative)
        if not _safe_lint_path(relative) or path.suffix.lower() != ".py":
            raise ValueError(f"unsafe Ruff input path: {relative}")
        target = root.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        paths.append(relative)
    return paths


def _safe_lint_path(relative: str) -> bool:
    """Reject native Windows separators/drives as well as unsafe POSIX paths."""
    windows_path = PureWindowsPath(relative)
    return (
        "\\" not in relative and not windows_path.is_absolute() and not windows_path.drive
        and ".." not in windows_path.parts and safe_source_path(relative)
    )


def run_linters(
    files: list[ChangedFile], *, snapshot: Snapshot | None = None,
    timeout: int = TIMEOUT_SECONDS,
) -> DeterministicResult:
    """Compare bounded Ruff diagnostics on frozen base/head texts, never cwd files."""
    result = DeterministicResult()
    python_files = [f for f in files if f.filename.lower().endswith(".py")]
    if not python_files:
        return result
    if snapshot is None:
        result.status = "unavailable"
        result.unavailable_reason = "frozen repository snapshot unavailable"
        result.skipped_paths = [f.filename for f in python_files]
        return result

    result.base_sha, result.head_sha = snapshot.base, snapshot.head
    ruff = shutil.which("ruff")
    if not ruff:
        result.status = "unavailable"
        result.unavailable_reason = "ruff executable not installed"
        result.skipped_paths = [f.filename for f in python_files]
        return result
    version_rc, version_stdout, _, version_error = _bounded_process(
        [ruff, "--version"], cwd=Path.cwd(), timeout=min(timeout, 5),
    )
    if version_error:
        result.status = "unavailable"
        result.unavailable_reason = f"ruff version check failed: {version_error}"
        result.skipped_paths = [f.filename for f in python_files]
        return result
    if version_rc != 0 or len(version_stdout) > 256:
        result.status = "unavailable"
        result.unavailable_reason = "ruff version check failed"
        result.skipped_paths = [f.filename for f in python_files]
        return result
    result.tool_version = version_stdout.decode("utf-8", errors="replace").strip()

    eligible = []
    for file in python_files:
        if not _safe_lint_path(file.filename):
            result.skipped_paths.append(file.filename)
            result.notes.append(f"unsafe_path:{file.filename}")
        elif getattr(file.status, "value", file.status) == "removed":
            result.skipped_paths.append(file.filename)
            result.notes.append(f"removed_file:{file.filename}")
        else:
            eligible.append(file)
    if len(eligible) > MAX_FILES:
        result.skipped_paths.extend(f.filename for f in eligible[MAX_FILES:])
        eligible = eligible[:MAX_FILES]
        result.notes.append(f"file_limit:{MAX_FILES}")

    head_texts: dict[str, str] = {}
    base_texts: dict[str, str] = {}
    total_bytes = 0
    snapshot_deadline = time.monotonic() + MAX_SNAPSHOT_SECONDS
    for file in eligible:
        if time.monotonic() >= snapshot_deadline or result.snapshot_read_calls >= MAX_SNAPSHOT_READS:
            result.skipped_paths.extend(f.filename for f in eligible if f.filename not in head_texts)
            result.notes.append("snapshot_read_budget_exhausted")
            break
        head = snapshot.read(file.filename, "head")
        result.snapshot_read_calls += 1
        if head is not None:
            result.snapshot_bytes_read += len(head.encode("utf-8"))
        else:
            result.notes.append(f"head_size_unknown:{file.filename}")
        if result.snapshot_bytes_read > MAX_SNAPSHOT_FETCH_BYTES:
            result.skipped_paths.append(file.filename)
            result.notes.append(f"snapshot_fetch_bytes_limit:{MAX_SNAPSHOT_FETCH_BYTES}")
            break
        if time.monotonic() >= snapshot_deadline or result.snapshot_read_calls >= MAX_SNAPSHOT_READS:
            result.skipped_paths.extend(f.filename for f in eligible if f.filename not in head_texts)
            result.notes.append("snapshot_read_budget_exhausted")
            break
        is_added = getattr(file.status, "value", file.status) == "added"
        base_path = file.previous_filename if getattr(file.status, "value", file.status) == "renamed" else file.filename
        base = None if is_added else snapshot.read(base_path, "base")
        if not is_added:
            result.snapshot_read_calls += 1
        if base is not None:
            result.snapshot_bytes_read += len(base.encode("utf-8"))
        elif not is_added:
            result.notes.append(f"base_size_unknown:{file.filename}")
        if result.snapshot_bytes_read > MAX_SNAPSHOT_FETCH_BYTES:
            result.skipped_paths.append(file.filename)
            result.notes.append(f"snapshot_fetch_bytes_limit:{MAX_SNAPSHOT_FETCH_BYTES}")
            break
        if base is None and is_added:
            base = ""
        if head is None:
            result.skipped_paths.append(file.filename)
            result.notes.append(f"head_unavailable:{file.filename}")
            continue
        head_size = len(head.encode("utf-8"))
        base_size = len(base.encode("utf-8")) if base is not None else 0
        if max(head_size, base_size) > MAX_FILE_BYTES:
            result.skipped_paths.append(file.filename)
            result.notes.append(f"file_size_limit:{file.filename}")
            continue
        if total_bytes + head_size + base_size > MAX_TOTAL_BYTES:
            result.skipped_paths.extend(f.filename for f in eligible if f.filename not in head_texts)
            result.notes.append(f"total_size_limit:{MAX_TOTAL_BYTES}")
            break
        head_texts[file.filename] = head
        if base is not None:
            base_texts[file.filename] = base
        else:
            result.notes.append(f"base_unavailable_unattributed:{file.filename}")
        total_bytes += head_size + base_size

    if not head_texts:
        result.status = "partial" if result.notes else "unavailable"
        result.unavailable_reason = "no complete Python base/head text available"
        return result

    try:
        with tempfile.TemporaryDirectory(prefix="pr-review-ruff-") as tmp:
            temp_root = Path(tmp)
            base_root, head_root = temp_root / "base", temp_root / "head"
            base_root.mkdir()
            head_root.mkdir()
            head_paths = _materialize(head_root, head_texts)
            head_diagnostics, error = _run_ruff(
                head_paths, cwd=head_root, timeout=timeout, executable=ruff,
            )
            if error:
                result.status = "unavailable"
                result.unavailable_reason = f"head: {error}"
                return result
            base_paths = _materialize(base_root, base_texts)
            if base_paths:
                base_diagnostics, base_error = _run_ruff(
                    base_paths, cwd=base_root, timeout=timeout, executable=ruff,
                )
            else:
                base_diagnostics, base_error = [], None
    except (OSError, ValueError) as exc:
        result.status = "unavailable"
        result.unavailable_reason = f"could not prepare isolated Ruff inputs: {type(exc).__name__}"
        return result

    assert head_diagnostics is not None
    if base_error:
        result.notes.append(f"base_ruff_unavailable:{base_error}")
        base_diagnostics = None
    added_lines = changed_line_map(files)
    for path in head_texts:
        result.checked_paths.append(path)
    for item in head_diagnostics:
        path = str(item.get("filename", ""))
        location = item.get("location") or {}
        line = int(location.get("row") or 0) or None
        base_match = False
        # An unchanged-line diagnostic is baseline only at the same source line
        # position. Anything on an added line is a candidate only after a
        # complete base run; missing/unparseable patches never imply "new".
        if line not in added_lines.get(path, set()) and base_diagnostics is not None and path in base_texts:
            base_match = any(
                str(base_item.get("filename", "")) == path
                and int((base_item.get("location") or {}).get("row") or 0) == line
                and str(base_item.get("code", "")) == str(item.get("code", ""))
                and str(base_item.get("message", "")) == str(item.get("message", ""))
                for base_item in base_diagnostics
            )
        if base_match:
            result.baseline_diagnostics += 1
            continue
        if base_diagnostics is None or path not in base_texts:
            result.unattributed_diagnostics += 1
            result.notes.append(f"unattributed:{path}:{line or 0}:{item.get('code', '')}")
            continue
        if line is None or line not in added_lines.get(path, set()):
            result.unattributed_diagnostics += 1
            result.notes.append(f"unattributed:{path}:{line or 0}:{item.get('code', '')}")
            continue
        code = str(item.get("code") or "ruff")
        result.findings.append(RiskFinding(
            file_path=path, line=line, severity="low", rule_id=f"lint-candidate-{code.lower()}",
            title=f"Ruff diagnostic candidate ({code}; not semantically confirmed)",
            evidence=str(item.get("message", ""))[:400],
            recommendation="Review this added-line diagnostic; Ruff output alone does not establish a defect.",
            confidence=0.2,
        ))
    result.status = "partial" if result.skipped_paths or result.notes else "complete"
    return result
