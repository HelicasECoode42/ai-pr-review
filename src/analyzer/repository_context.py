"""Bounded, immutable repository evidence. Never executes target repository code."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import selectors
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

from src.analyzer.context_builder import _skip_patch_reason, count_tokens
from src.analyzer.diff_parser import parse_file_hunks
from src.models import BudgetLine, BudgetReport, Omission

SHA = re.compile(r"[0-9a-f]{40}")
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".c", ".h",
                   ".cpp", ".hpp", ".cs", ".rb", ".php", ".f", ".f90", ".swift", ".kt",
                   ".vue", ".yaml", ".yml", ".toml", ".json", ".md", ".sh", ".sql",
                   ".ipynb", ".svelte", ".ex", ".exs", ".scala", ".dart", ".fs", ".r",
                   ".gemspec", ".gradle", ".kts"}
SOURCE_MAX_BYTES = 500_000
GIT_TREE_MAX_BYTES = 2_000_000
GIT_SEARCH_MAX_BYTES = 1_000_000
# How many edited regions per file seed repository evidence. Four covers the common
# "definition in hunk 1, helper call in hunk 3" shape without unbounded fan-out.
HUNK_SEED_LIMIT = 4
# A component longer than this is rendered as a bounded window around the changed
# line instead of its full body; 90 lines is roughly one screen of evidence.
MAX_SEED_WINDOW_LINES = 90
_SEED_NAME_BLOCKLIST = {
    "main", "test", "init", "constructor", "func", "function", "class", "type",
    "if", "for", "while", "switch", "return", "new", "len", "append",
}


class _BoundedIOUnavailable(RuntimeError):
    """The platform cannot provide the bounded subprocess pipe used here."""


def _run_bounded_command(
    command: list[str], *, max_bytes: int, timeout: float
) -> tuple[bytes, bool]:
    """Capture at most ``max_bytes`` from a child process and stop on limit/deadline.

    ``subprocess.check_output`` buffers the complete output before returning. A
    selector lets snapshot reads drain stdout incrementally and kill git as soon
    as the byte cap or wall-clock deadline is reached. Windows pipes are not
    selectable; callers fail closed there instead of reverting to full capture.
    """
    if os.name != "posix":
        raise _BoundedIOUnavailable("bounded subprocess output is unavailable on this platform")
    if max_bytes < 0 or timeout <= 0:
        raise ValueError("invalid bounded subprocess limits")

    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    assert process.stdout is not None
    captured = bytearray()
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout, output=bytes(captured))
            ready = selector.select(remaining)
            if not ready:
                raise subprocess.TimeoutExpired(command, timeout, output=bytes(captured))
            for key, _ in ready:
                chunk = os.read(key.fd, min(64 * 1024, max_bytes - len(captured) + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                remaining_capacity = max_bytes - len(captured)
                captured.extend(chunk[:remaining_capacity])
                if len(chunk) > remaining_capacity:
                    process.kill()
                    process.wait()
                    return bytes(captured), True

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout, output=bytes(captured))
        return_code = process.wait(timeout=remaining)
        if return_code:
            raise subprocess.CalledProcessError(return_code, command, output=bytes(captured))
        return bytes(captured), False
    except (subprocess.TimeoutExpired, OSError):
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()


def safe_source_path(path: str) -> bool:
    p = PurePosixPath(path)
    return (bool(path) and not p.is_absolute() and ".." not in p.parts
            and not any(part in {".git", "node_modules", "vendor", "dist", "build"} for part in p.parts)
            and not p.name.startswith(".env") and _skip_patch_reason(path) is None
            and (p.suffix.lower() in SOURCE_SUFFIXES or p.name in {"Dockerfile", "Makefile", "Gemfile", "Rakefile"}))


class Snapshot(Protocol):
    head: str
    base: str
    def read(self, path: str, side: str = "head") -> str | None: ...
    def search(self, symbol: str, side: str = "head", limit: int = 6) -> list[tuple[str, int]]: ...
    def audit(self) -> dict: ...


class GitSnapshot:
    """Read/search only the named git objects, not a checkout or later fixes."""
    def __init__(self, root: Path, base: str, head: str):
        if not SHA.fullmatch(base) or not SHA.fullmatch(head):
            raise ValueError("Repository context requires immutable base/head SHA")
        self.root, self.base, self.head = root.resolve(), base, head
        self.cache: dict[tuple[str, str], str | None] = {}
        self.errors: list[dict] = []
        self.search_limited = False

    def _record_error(
        self, *, reason: str, path: str | None = None,
        side: str | None = None, symbol: str | None = None,
    ) -> None:
        error = {"reason": reason}
        if path is not None:
            error["path"] = path
        if side is not None:
            error["side"] = side
        if symbol is not None:
            error["symbol"] = symbol
        self.errors.append(error)

    def read(self, path, side="head"):
        if side not in {"head", "base"} or not safe_source_path(path):
            return None
        key = (side, path)
        if key not in self.cache:
            try:
                raw, limited = _run_bounded_command(
                    ["git", "-C", str(self.root), "show", getattr(self, side) + ":" + path],
                    max_bytes=SOURCE_MAX_BYTES,
                    timeout=15,
                )
                if limited:
                    self.cache[key] = None
                    self._record_error(reason="source_too_large", path=path, side=side)
                    return None
                text = raw.decode("utf-8")
                if "\0" in text:
                    self.cache[key] = None
                    self._record_error(reason="binary_source", path=path, side=side)
                else:
                    self.cache[key] = text
            except subprocess.TimeoutExpired:
                self.cache[key] = None
                self._record_error(reason="read_timed_out", path=path, side=side)
            except UnicodeError:
                self.cache[key] = None
                self._record_error(reason="source_encoding_unavailable", path=path, side=side)
            except _BoundedIOUnavailable:
                self.cache[key] = None
                self._record_error(reason="bounded_io_unavailable", path=path, side=side)
            except (OSError, subprocess.SubprocessError):
                self.cache[key] = None
                self._record_error(reason="unavailable", path=path, side=side)
        return self.cache[key]

    def search(self, symbol, side="head", limit=6):
        if side not in {"head", "base"} or not re.fullmatch(r"[A-Za-z_]\w{2,79}", symbol):
            return []
        try:
            raw_paths, tree_limited = _run_bounded_command(
                ["git", "-C", str(self.root), "ls-tree", "-r", "--name-only",
                 getattr(self, side)],
                max_bytes=GIT_TREE_MAX_BYTES,
                timeout=15,
            )
            if tree_limited:
                self.search_limited = True
                self._record_error(reason="search_tree_output_limited", side=side, symbol=symbol)
            path_text = raw_paths.decode("utf-8")
            path_lines = path_text.splitlines()
            if tree_limited and path_text and not path_text.endswith("\n"):
                path_lines = path_lines[:-1]
            paths = [p for p in path_lines
                     if safe_source_path(p) and Path(p).suffix not in {".md", ".json", ".ipynb"}]
            if not paths:
                return []
            bounded = []
            size = 0
            for path in paths:
                size += len(path.encode()) + 1
                if size > 120_000 or len(bounded) >= 5000:
                    self.search_limited = True
                    self._record_error(reason="search_path_limit", side=side, symbol=symbol)
                    break
                bounded.append(path)
            raw_bytes, output_limited = _run_bounded_command(
                ["git", "-C", str(self.root), "grep", "-n", "-I", "-w", "-F",
                 "-m", "6", "-e", symbol, getattr(self, side), "--", *bounded],
                max_bytes=GIT_SEARCH_MAX_BYTES,
                timeout=15,
            )
            raw = raw_bytes.decode("utf-8", errors="replace")
            if output_limited:
                self.search_limited = True
                self._record_error(reason="search_output_limited", side=side, symbol=symbol)
        except subprocess.CalledProcessError as exc:
            if getattr(exc, "returncode", None) != 1:
                self._record_error(reason="search_failed", side=side, symbol=symbol)
            return []
        except subprocess.TimeoutExpired:
            self.search_limited = True
            self._record_error(reason="search_timed_out", side=side, symbol=symbol)
            return []
        except UnicodeError:
            self.search_limited = True
            self._record_error(reason="search_path_encoding_unavailable", side=side, symbol=symbol)
            return []
        except _BoundedIOUnavailable:
            self.search_limited = True
            self._record_error(reason="bounded_io_unavailable", side=side, symbol=symbol)
            return []
        except (OSError, subprocess.SubprocessError):
            self._record_error(reason="search_failed", side=side, symbol=symbol)
            return []
        result = []
        for line in raw.splitlines():
            match = re.match(r"[0-9a-f]+:(.*?):(\d+):", line)
            if match and safe_source_path(match[1]):
                result.append((match[1], int(match[2])))
        # Preserve several files instead of allowing one file to exhaust the limit.
        result.sort(key=lambda pair: (pair[0], pair[1]))
        unique, seen = [], set()
        for path, line_number in result:
            if path not in seen:
                unique.append((path, line_number)); seen.add(path)
            if len(unique) >= limit:
                break
        return unique

    def audit(self):
        return {"backend": "git_objects", "base": self.base, "head": self.head,
                "search_limited": self.search_limited,
                "read_files": [{"side": side, "path": path, "available": text is not None}
                               for (side, path), text in self.cache.items()], "errors": self.errors}


class GitHubSnapshot:
    """Bounded search over a frozen tree; reports incomplete searches explicitly."""
    def __init__(self, client, repo, base, head, changed_paths):
        if not SHA.fullmatch(base or "") or not SHA.fullmatch(head or ""):
            raise ValueError("Repository context requires immutable base/head SHA")
        self.client, self.repo, self.base, self.head = client, repo, base, head
        self.changed_paths = changed_paths
        self.cache, self.errors = {}, []
        # Tree inventories belong to a commit side. Reusing HEAD paths for a BASE
        # search can hide deleted callers and mix evidence across the frozen range.
        self.paths: dict[str, list[str]] = {}
        self.search_limited = False

    def _record_error(
        self, *, reason: str, path: str | None = None,
        side: str | None = None, symbol: str | None = None,
    ) -> None:
        error = {"reason": reason}
        if path is not None:
            error["path"] = path
        if side is not None:
            error["side"] = side
        if symbol is not None:
            error["symbol"] = symbol
        self.errors.append(error)

    def read(self, path, side="head"):
        if side not in {"head", "base"} or not safe_source_path(path):
            return None
        key = (side, path)
        if key not in self.cache:
            if len(self.cache) >= 60:
                self.search_limited = True
                self._record_error(reason="source_read_limit", path=path, side=side)
                return None
            try:
                text = self.client.get_file_contents(self.repo, path, getattr(self, side))
                if not text:
                    self.cache[key] = None
                    self._record_error(reason="source_unavailable", path=path, side=side)
                elif len(text.encode("utf-8")) > SOURCE_MAX_BYTES:
                    self.cache[key] = None
                    self._record_error(reason="source_too_large", path=path, side=side)
                else:
                    self.cache[key] = text
            except Exception as exc:
                self.cache[key] = None
                reason = getattr(exc, "reason", "unavailable")
                self._record_error(reason=reason, path=path, side=side)
        return self.cache[key]

    def search(self, symbol, side="head", limit=6):
        if not re.fullmatch(r"[A-Za-z_]\w{2,79}", symbol) or side not in {"head", "base"}:
            return []
        if side not in self.paths:
            try:
                fetch_tree = getattr(self.client, "get_json_limited", None)
                tree_path = f"/repos/{self.repo}/git/trees/{getattr(self, side)}"
                if not callable(fetch_tree):
                    raise _BoundedIOUnavailable(
                        "bounded GitHub tree reads are unavailable"
                    )
                response = fetch_tree(
                    tree_path,
                    params={"recursive": "1"},
                    max_response_bytes=GIT_TREE_MAX_BYTES,
                )
                if not isinstance(response, dict) or not isinstance(response.get("tree"), list):
                    raise ValueError("GitHub tree response is malformed")
                self.paths[side] = [x["path"] for x in response["tree"]
                              if x.get("type") == "blob" and safe_source_path(x["path"])]
                if response.get("truncated", False):
                    self.search_limited = True
                    self._record_error(reason="tree_truncated", side=side, symbol=symbol)
            except Exception as exc:
                self.paths[side] = list(self.changed_paths)
                self.search_limited = True
                reason = getattr(exc, "reason", "tree_unavailable")
                self._record_error(reason=reason, side=side, symbol=symbol)
        # Prefer files close to a changed module and its tests; no moving code-search index.
        parents = {str(PurePosixPath(p).parent) for p in self.changed_paths}
        paths = sorted(self.paths[side], key=lambda p: (p not in self.changed_paths,
                        str(PurePosixPath(p).parent) not in parents, "test" not in p.lower(), p))
        if len(paths) > 40:
            self.search_limited = True
            self._record_error(reason="search_file_limit", side=side, symbol=symbol)
        matches = []
        pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
        for path in paths[:40]:
            text = self.read(path, side)
            if not text:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    matches.append((path, number)); break
            if len(matches) >= limit:
                break
        return matches

    def audit(self):
        return {"backend": "github_frozen_tree", "base": self.base, "head": self.head,
                "search_limited": self.search_limited, "errors": self.errors,
                "read_files": [{"side": side, "path": path, "available": text is not None}
                               for (side, path), text in self.cache.items()]}


_BRACE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs", ".cs",
                   ".cpp", ".c", ".php", ".swift", ".kt"}
_MODIFIERS = (
    "export|default|public|private|protected|static|async|override|final|suspend|"
    "pub|unsafe|extern|inline|virtual|abstract|sealed|partial|declare|readonly"
)
# A declaration recognisable from its keyword. Go methods carry a receiver between
# `func` and the name, so the receiver is optional here.
_KEYWORD_DECL = re.compile(
    rf"^\s*(?:(?:{_MODIFIERS})\s+)*(?:def|function|func|fn|class|interface|subroutine|type)\s+"
    r"(?:\(\s*[A-Za-z_]\w*\s+\*?[A-Za-z_][\w.]*\s*\)\s*)?([A-Za-z_]\w*)",
    re.IGNORECASE,
)
# Method signatures in brace languages have no keyword. The line must end at the
# opening brace (or the signature itself) so calls inside a body never match.
_METHOD_DECL = re.compile(
    rf"^\s*(?:(?:{_MODIFIERS})\s+)*([A-Za-z_$][\w$]*)\s*(?:<[^<>;{{}}]*>)?"
    r"\s*\([^;{}]*\)\s*(?::[^{};]+)?\s*\{?\s*$"
)
# Last resort, kept for languages whose signatures do not start at the line head
# (for example C++ `std::string Foo::bar() {`). Only searched when the two
# anchored patterns fail, and names are filtered afterwards.
_LEGACY_BRACE_DECL = re.compile(
    r"(?:function|fn|func|class|interface)\s+(\w+)"
    rf"|(?:\b(?:{_MODIFIERS})\s+)*(\w+)\s*(?:<[^>]*>)?\s*\([^;{{}}]*\)\s*"
    r"(?::[^{}]+)?\s*\{"
)
_NON_DECLARATION_NAMES = {
    "if", "for", "while", "switch", "catch", "with", "else", "do", "try",
    "return", "new", "make", "len", "append", "range", "select", "go", "defer",
    "func", "function", "def", "class", "interface", "type", "in", "of",
}
_DECLARATION_PREAMBLE = 12


def _resolve_brace_declaration(header_lines, header_start):
    """Name and 1-based start line of the declaration that opens a brace block.

    ``header_lines`` is the masked run of lines up to and including the opening
    brace line. It is searched bottom-up so that a nested call cannot win over
    the signature it sits in.
    """
    for index in range(len(header_lines) - 1, -1, -1):
        candidate = header_lines[index]
        keyword = _KEYWORD_DECL.match(candidate)
        if keyword and keyword.group(1).lower() not in _NON_DECLARATION_NAMES:
            return keyword.group(1), header_start + index + 1
        method = _METHOD_DECL.match(candidate)
        if method and method.group(1).lower() not in _NON_DECLARATION_NAMES:
            return method.group(1), header_start + index + 1
    header = " ".join(header_lines)
    matches = list(_LEGACY_BRACE_DECL.finditer(header))
    for match in reversed(matches):
        name = next((group for group in match.groups() if group), None)
        if name and name.lower() not in _NON_DECLARATION_NAMES:
            return name, max(1, header_start + 1)
    return None


def function_window(path, text, line):
    lines = text.splitlines()
    line = max(1, min(line, len(lines) or 1))
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
            nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.lineno <= line <= (n.end_lineno or n.lineno)]
            if nodes:
                node = min(nodes, key=lambda n: (n.end_lineno or n.lineno) - n.lineno)
                start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
                return start, node.end_lineno or node.lineno, node.name, "complete_python_definition"
        except SyntaxError:
            pass
    suffix = Path(path).suffix
    # Brace languages: use balanced lexical blocks and a declaration header. Strings
    # and comments are masked; this remains explicitly heuristic, not a semantic AST.
    if suffix in _BRACE_SUFFIXES:
        masked = re.sub(r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`",
                        lambda m: "".join("\n" if c == "\n" else " " for c in m[0]), text, flags=re.S)
        stack, blocks, number = [], [], 1
        for char in masked:
            if char == "\n":
                number += 1
            elif char == "{":
                stack.append(number)
            elif char == "}" and stack:
                opening = stack.pop()
                if opening <= line <= number:
                    header_start = max(0, opening - 1 - _DECLARATION_PREAMBLE)
                    header_lines = masked.splitlines()[header_start:opening]
                    resolved = _resolve_brace_declaration(header_lines, header_start)
                    if resolved is not None:
                        name, decl_start = resolved
                        blocks.append((decl_start, number, name, "heuristic_brace_definition"))
        if blocks:
            return min(blocks, key=lambda b: b[1] - b[0])
    # A language-independent source window. Never claim heuristic parsing is a full
    # definition. Only a line that starts with a declaration keyword may name the
    # window: a bare `name(` match turns SQL `IN (` or Go `len(` into fake symbols.
    start, end = max(1, line - 25), min(len(lines), line + 55)
    for number in range(line, max(0, line - 60), -1):
        keyword = _KEYWORD_DECL.match(lines[number - 1])
        if keyword:
            return max(1, number - 2), end, keyword.group(1), "source_window"
    return start, end, None, "source_window"


@dataclass
class EvidenceBundle:
    snapshot: Snapshot
    budget: int = 6000
    excerpts: list[dict] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)
    omitted: list[dict] = field(default_factory=list)
    evicted: list[dict] = field(default_factory=list)
    tokens: int = 0
    _request_cache: dict[tuple, dict] = field(default_factory=dict, repr=False)

    @property
    def budget_report(self) -> BudgetReport:
        """This bundle's own budget and omissions, so a run can account for evidence too."""
        omissions = [
            Omission(
                path=str(item.get("path", "?")),
                reason=str(item.get("reason", "unspecified")),
                detail=item.get("side"),
            )
            for item in self.omitted
        ]
        omissions += [
            Omission(
                path=str(item.get("path", "?")),
                reason="evidence_evicted_for_requested_context",
                detail=item.get("kind"),
            )
            for item in self.evicted
        ]
        return BudgetReport(
            scope="repository_evidence",
            lines=(
                BudgetLine(
                    name="evidence_excerpts",
                    limit=self.budget,
                    used=self.tokens,
                    covers=(
                        "frozen source excerpts placed in the prompt; excludes the system "
                        "prompt, patch context and model output"
                    ),
                ),
            ),
            omissions=tuple(omissions),
        )

    def add(self, path, line=1, side="head", kind="related_code", max_lines=None):
        text = self.snapshot.read(path, side)
        if not text:
            self.omitted.append({"path": path, "side": side, "reason": "source_unavailable"})
            return None
        if not 1 <= int(line) <= len(text.splitlines()):
            self.omitted.append({"path": path, "side": side, "reason": "line_out_of_range"})
            return None
        start, end, symbol, extent = function_window(path, text, int(line))
        if max_lines is not None and end - start + 1 > max_lines:
            # A huge component (hundreds of lines) is not evidence a reviewer can
            # use; keep a bounded window around the changed line instead of
            # spending the whole evidence budget on one definition.
            start = max(start, int(line) - max_lines // 3)
            end = min(end, start + max_lines - 1)
        existing = next((e for e in self.excerpts
                         if e["path"] == path and e["side"] == side
                         and e["start_line"] <= int(line) <= e["end_line"]), None)
        if existing:
            if kind.startswith(("requested", "candidate")):
                # A caller/task citing this excerpt must keep the exact rendered
                # source available through verification, even under later pressure.
                existing["kind"] = kind
            return existing["evidence_ref"]
        lines = text.splitlines()
        # Large definitions keep a window around the requested line, with explicit clipping.
        clipped = end - start > 150
        if clipped:
            start, end = max(start, int(line) - 40), min(end, int(line) + 80)
        numbered = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
        entry = {"path": path, "side": side, "commit": getattr(self.snapshot, side),
                 "start_line": start, "end_line": end, "kind": kind, "symbol": symbol,
                 "extent": extent, "truncated": clipped or (extent == "source_window" and (start > 1 or end < len(lines))), "code": numbered}
        entry["evidence_ref"] = hashlib.sha256(json.dumps(
            {"base": self.snapshot.base, "head": self.snapshot.head, "side": side,
             "path": path, "start_line": start, "end_line": end,
             "code_sha256": hashlib.sha256(numbered.encode()).hexdigest()},
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        cost = count_tokens(json.dumps(entry, ensure_ascii=False))
        if kind.startswith(("requested", "candidate")) and cost <= self.budget:
            while self.tokens + cost > self.budget and self.excerpts:
                # Explicit questions can replace low-value initial windows; do not
                # silently declare the displaced code reviewed in the final pass.
                disposable = next((e for e in self.excerpts if not e["kind"].startswith(("requested", "candidate"))), None)
                if disposable is None:
                    break
                self.excerpts.remove(disposable)
                self.tokens -= count_tokens(json.dumps(disposable, ensure_ascii=False))
                self.evicted.append({k: v for k, v in disposable.items() if k != "code"})
        if self.tokens + cost > self.budget:
            self.omitted.append({"path": path, "side": side, "reason": "evidence_budget_exhausted"})
            return None
        self.excerpts.append(entry); self.tokens += cost
        return entry["evidence_ref"]

    def follow(self, files):
        """Seed bounded repository evidence for changed regions.

        Priority is: resolved definitions on HEAD, cheaper windows first, then the
        same on BASE. Unresolved blind windows are skipped - the diff already shows
        the changed lines, and a blind window only spends budget. A symbol search
        follows once definitions are in, so a helper called by a changed hunk can
        still enter the bundle.
        """
        original_budget = self.budget
        self.budget = max(1, int(original_budget * .80))
        eligible = [f for f in files if safe_source_path(f.filename)]
        eligible.sort(key=lambda f: ("test" in f.filename.lower(), f.filename.endswith(".md"), f.filename))
        candidates = []
        seeds = []
        for file_order, file in enumerate(eligible[:12]):
            hunks = parse_file_hunks(file)
            head_path = file.filename
            base_path = file.previous_filename or file.filename
            for hunk_index, hunk in enumerate(hunks[:HUNK_SEED_LIMIT]):
                head_line = hunk.added_lines[0].line if hunk.added_lines else max(1, hunk.new_start)
                base_line = max(1, hunk.old_start)
                for side, path, anchor, kind in (
                    ("head", head_path, head_line, "changed_definition"),
                    ("base", base_path, base_line, "previous_definition"),
                ):
                    source = self.snapshot.read(path, side)
                    if not source:
                        continue
                    source_lines = source.splitlines()
                    start, end, symbol, extent = function_window(path, source, anchor)
                    if symbol is None or not (1 <= start <= end <= len(source_lines)):
                        # No recognised definition: a blind window is not evidence.
                        continue
                    if end - start + 1 > MAX_SEED_WINDOW_LINES:
                        start = max(start, anchor - MAX_SEED_WINDOW_LINES // 3)
                        end = min(end, start + MAX_SEED_WINDOW_LINES - 1)
                    code = "\n".join(
                        f"{number}: {source_lines[number - 1]}"
                        for number in range(start, end + 1)
                    )
                    cost = count_tokens(json.dumps({
                        "path": path, "side": side, "start_line": start,
                        "end_line": end, "code": code,
                    }))
                    is_test = "test" in path.lower()
                    candidates.append({
                        "rank": (side != "head", is_test, extent == "source_window",
                                 cost, file_order, hunk_index, path),
                        "path": path, "side": side, "anchor": anchor, "kind": kind,
                        "start": start, "end": end,
                    })
                    if side == "head" and len(symbol) >= 3 and symbol not in _SEED_NAME_BLOCKLIST:
                        seeds.append((symbol, head_path))
        seen = set()
        for candidate in sorted(candidates, key=lambda item: item["rank"]):
            key = (candidate["path"], candidate["side"], candidate["start"], candidate["end"])
            if key in seen:
                continue
            seen.add(key)
            self.add(candidate["path"], candidate["anchor"], candidate["side"],
                     candidate["kind"], max_lines=MAX_SEED_WINDOW_LINES)
        for file in eligible[12:]:
            self.omitted.append({"path": file.filename, "reason": "changed_file_seed_limit"})
        # Symbol follow uses whatever budget the definition pass left; requested
        # evidence can still evict these entries later.
        for symbol, changed_path in dict.fromkeys(seeds):
            if self.tokens >= self.budget * .95:
                break
            for path, line in self.snapshot.search(symbol, limit=4):
                if path != changed_path:
                    self.add(path, line, kind="symbol_reference_or_caller")

        self.budget = original_budget

    def execute(self, requests):
        """Run bounded read/search requests and reuse results for identical frozen queries."""
        outcomes = []
        for request in requests[:6]:
            if not isinstance(request, dict):
                entry = {"request": request, "executed": False,
                         "status": "invalid_request", "error": "invalid_request"}
                self.requests.append(entry)
                outcomes.append(entry)
                continue
            entry = {"request": request, "executed": False, "status": "invalid_request"}
            for field_name in ("hypothesis_id", "purpose"):
                if field_name in request:
                    entry[field_name] = request[field_name]
            try:
                if "tool" not in request and isinstance(request.get("path"), str):
                    request = {**request, "tool": "read"}
                    entry["format_notice"] = "path_only_read_request"
                side = request.get("side", "head")
                if side not in {"base", "head"}:
                    raise ValueError("Invalid snapshot side")
                if request.get("tool") == "read":
                    line = request.get("line", request.get("start_line"))
                    if line is None:
                        previous = [e for e in self.excerpts if e["path"] == request["path"] and e["side"] == side]
                        line = max((e["end_line"] for e in previous), default=0) + 1
                        entry["continuation_line"] = line
                    if isinstance(line, bool):
                        raise ValueError("Invalid source line")
                    line = max(1, int(line))
                    path = request["path"]
                    if not safe_source_path(path):
                        raise ValueError("Unsafe source path")
                    key = (self.snapshot.base, self.snapshot.head, "read", side, path, line)
                    entry["normalized_query"] = {"tool": "read", "path": path,
                                                  "side": side, "line": line}
                    cached = self._request_cache.get(key)
                    current_refs = {item["evidence_ref"] for item in self.excerpts}
                    cached_refs = set(cached.get("evidence_refs", [])) if cached else set()
                    can_reuse = cached is not None and (
                        cached.get("status") != "executed_with_evidence"
                        or (cached_refs and cached_refs.issubset(current_refs))
                    )
                    if can_reuse:
                        cached = dict(cached)
                        entry.update(cached)
                        entry.update(executed=False, reused=True)
                    else:
                        omitted_before = len(self.omitted)
                        evidence_ref = self.add(path, line, side, "requested_definition")
                        entry.update(executed=True, reused=False,
                                     status=("executed_with_evidence" if evidence_ref
                                             else "no_evidence"),
                                     evidence_refs=[evidence_ref] if evidence_ref else [])
                        if not evidence_ref and len(self.omitted) > omitted_before:
                            entry["omission_reason"] = self.omitted[-1]["reason"]
                        self._request_cache[key] = {
                            k: entry[k] for k in ("status", "evidence_refs", "omission_reason")
                            if k in entry
                        }
                elif request.get("tool") == "search":
                    raw_symbol = request["symbol"]
                    if not isinstance(raw_symbol, str):
                        raise ValueError("Invalid search symbol")
                    symbol = raw_symbol.strip()
                    if not re.fullmatch(r"[A-Za-z_]\w{2,79}", symbol):
                        raise ValueError("Invalid search symbol")
                    key = (self.snapshot.base, self.snapshot.head, "search", side, symbol, 4)
                    entry["normalized_query"] = {"tool": "search", "symbol": symbol,
                                                  "side": side, "limit": 4}
                    cached = self._request_cache.get(key)
                    current_refs = {item["evidence_ref"] for item in self.excerpts}
                    cached_refs = set(cached.get("evidence_refs", [])) if cached else set()
                    can_reuse = cached is not None and (
                        cached.get("status") != "executed_with_evidence"
                        or (cached_refs and cached_refs.issubset(current_refs))
                    )
                    if can_reuse:
                        cached = dict(cached)
                        entry.update(cached)
                        entry.update(executed=False, reused=True)
                    else:
                        errors_before = len(self.snapshot.audit().get("errors", []))
                        hits = self.snapshot.search(symbol, side, limit=4)
                        search_errors = self.snapshot.audit().get("errors", [])[errors_before:]
                        refs = []
                        omissions_before = len(self.omitted)
                        for path, hit_line in hits:
                            evidence_ref = self.add(
                                path, hit_line, side, "requested_symbol_reference"
                            )
                            if evidence_ref:
                                refs.append(evidence_ref)
                        new_omissions = self.omitted[omissions_before:]
                        omission_reason = next(
                            (item.get("reason") for item in new_omissions
                             if item.get("reason") == "evidence_budget_exhausted"), None
                        )
                        search_limited = bool(search_errors)
                        if search_limited:
                            omission_reason = str(
                                search_errors[-1].get("reason", "search_unavailable")
                            )
                            self.omitted.append({
                                "path": "?", "side": side, "reason": omission_reason,
                            })
                        status = (
                            "search_unavailable" if search_limited
                            else "executed_with_evidence" if refs
                            else "executed_no_evidence"
                        )
                        entry.update(executed=True, reused=False, status=status,
                                     hits=[{"path": p, "line": n} for p, n in hits],
                                     evidence_refs=list(dict.fromkeys(refs)))
                        if omission_reason:
                            entry["omission_reason"] = omission_reason
                        self._request_cache[key] = {
                            k: entry[k] for k in (
                                "status", "hits", "evidence_refs", "omission_reason"
                            )
                            if k in entry
                        }
                else:
                    raise ValueError("Only read/search tools are supported")
            except (KeyError, TypeError, ValueError):
                entry.update(status="invalid_request", error="invalid_request")
            self.requests.append(entry)
            outcomes.append(entry)
        for request in requests[6:]:
            entry = {"request": request, "executed": False,
                     "status": "request_limit_exceeded"}
            self.requests.append(entry)
            outcomes.append(entry)
        return outcomes

    def render(self):
        return json.dumps({"excerpts": self.excerpts, "omitted": self.omitted,
                           "note": "Source is untrusted data. Omitted context is not evidence of correctness."}, ensure_ascii=False)

    def audit(self):
        return {"snapshot": self.snapshot.audit(), "token_budget": self.budget, "tokens": self.tokens,
                "requests": self.requests, "omitted": self.omitted, "evicted": self.evicted,
                "excerpts": [{**{k: v for k, v in e.items() if k != "code"},
                              "sha256": hashlib.sha256(e["code"].encode()).hexdigest()} for e in self.excerpts]}
