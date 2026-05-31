"""AST-based rules that catch structural issues beyond regex capabilities.

Walks Python ASTs of changed files and flags code patterns that regex
can't reliably detect: unsafe deserialization, weak hashes, over-broad
exception handling, debug imports, and structurally suspicious patterns.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Callable

from src.analyzer.diff_parser import parse_file_hunks
from src.models import ChangedFile, DiffHunk, RiskFinding, Severity
from src.utils import detect_programming_language

logger = logging.getLogger(__name__)

# ── Rule definition ─────────────────────────────────────

class AstRule:
    """One AST-level rule: a visitor that yields RiskFindings from AST nodes."""
    def __init__(
        self,
        rule_id: str,
        severity: Severity,
        title: str,
        recommendation: str,
        confidence: float,
    ):
        self.rule_id = rule_id
        self.severity = severity
        self.title = title
        self.recommendation = recommendation
        self.confidence = confidence


# ── AST visitor ───────────────────────────────────────────

class _RiskVisitor(ast.NodeVisitor):
    """Walk a Python AST and emit RiskFindings on changed lines.

    Uses a line_map (reconstructed_line → real_file_line) to correctly
    translate AST node positions back to the original file.
    """

    def __init__(
        self,
        file_path: str,
        source: str,
        line_map: dict[int, int],
        changed_real_lines: frozenset[int],
        rules_by_node: dict[type[ast.AST], list[tuple[AstRule, Callable]]],
    ):
        self.file_path = file_path
        self.source = source
        self.line_map = line_map
        self.changed_real_lines = changed_real_lines
        self.rules_by_node = rules_by_node
        self.findings: list[RiskFinding] = []

    def _to_real_line(self, ast_lineno: int) -> int:
        """Map an AST reconstructed-source line number to the real file line."""
        return self.line_map.get(ast_lineno, ast_lineno)

    def _is_changed(self, node: ast.AST) -> bool:
        """Check whether an AST node overlaps with changed lines (in real file)."""
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start)
        for line in range(start, end + 1):
            real = self._to_real_line(line)
            if real in self.changed_real_lines:
                return True
        return False

    def _node_source(self, node: ast.AST) -> str:
        """Get the source text for an AST node."""
        try:
            seg = ast.get_source_segment(self.source, node)
            if seg:
                return seg
        except Exception:
            pass
        return f"Line {getattr(node, 'lineno', '?')}"

    def visit(self, node: ast.AST):
        """Run registered rules for this node type, then continue walking."""
        for rule, check_fn in self.rules_by_node.get(type(node), []):
            try:
                if self._is_changed(node) and check_fn(node):
                    real_line = self._to_real_line(getattr(node, "lineno", 0)) or None
                    self.findings.append(
                        RiskFinding(
                            file_path=self.file_path,
                            line=real_line,
                            severity=rule.severity,
                            rule_id=rule.rule_id,
                            title=rule.title,
                            evidence=self._node_source(node),
                            recommendation=rule.recommendation,
                            confidence=rule.confidence,
                        )
                    )
            except Exception:
                continue
        self.generic_visit(node)


# ── Rule checkers ─────────────────────────────────────────

def _is_critical_deserialization(node: ast.Call) -> bool:
    """pickle.loads(...), yaml.load(...) without SafeLoader, marshal.loads(...)."""
    func = node.func
    if isinstance(func, ast.Attribute):
        full = _attr_name(func)
        if full in ("pickle.loads", "pickle.load", "marshal.loads", "marshal.load"):
            return True
        if full == "yaml.load":
            # yaml.load(..., Loader=yaml.SafeLoader) is fine
            return all(kw.arg != "Loader" for kw in node.keywords)
    return False


def _is_weak_hash(node: ast.Call) -> bool:
    """hashlib.md5(...), hashlib.sha1(...)."""
    func = node.func
    if isinstance(func, ast.Attribute):
        full = _attr_name(func)
        if full in ("hashlib.md5", "hashlib.sha1"):
            return True
    return False


def _is_broad_except(node: ast.ExceptHandler) -> bool:
    """except: or except Exception: without re-raise or logging."""
    if node.type is None:
        return True  # bare except
    if isinstance(node.type, ast.Name) and node.type.id == "Exception":
        return True  # too broad
    if isinstance(node.type, ast.Tuple):
        for elt in node.type.elts:
            if isinstance(elt, ast.Name) and elt.id == "Exception":
                return True
    return False


def _is_debug_import(node: ast.Import | ast.ImportFrom) -> bool:
    """import pdb / import ipdb / from pdb import ..."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name in ("pdb", "ipdb"):
                return True
    else:
        if node.module in ("pdb", "ipdb"):
            return True
    return False


def _is_dangerous_exec(node: ast.Call) -> bool:
    """exec(...), compile(..., 'exec'), __import__(...) used unsafely."""
    func = node.func
    return isinstance(func, ast.Name) and func.id in ("exec", "compile", "__import__")


def _is_hardcoded_secret(node: ast.Assign) -> bool:
    """Variable name suggests secret but value is a literal string."""
    secret_names = {"password", "passwd", "secret", "api_key", "apikey", "token"}
    for target in node.targets:
        if (isinstance(target, ast.Name)
                and target.id.lower() in secret_names
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and len(node.value.value) > 4):
            return True
    return False


def _is_unbounded_loop(node: ast.While) -> bool:
    """while True: without break/return in immediate body."""
    if isinstance(node.test, ast.Constant) and node.test.value is True:
        has_exit = any(
            isinstance(stmt, (ast.Return, ast.Break, ast.Raise))
            for stmt in node.body
        )
        return not has_exit
    return False


# ── Helpers ───────────────────────────────────────────────

def _attr_name(node: ast.Attribute) -> str:
    """Resolve a.b.c to 'a.b.c'."""
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))




# ── Source reconstruction with line mapping ──────────────

def _reconstruct_source_with_map(
    hunk: DiffHunk,
) -> tuple[str, dict[int, int]]:
    """Reconstruct the 'new' side of a hunk for AST parsing.

    Returns (source, line_map) where line_map maps each line number in
    the reconstructed source to its real line number in the changed file.
    """
    source_lines: list[str] = []
    line_map: dict[int, int] = {}
    recon_line = 1
    real_line = hunk.new_start

    for raw in hunk.raw.splitlines():
        if raw.startswith("@@"):
            continue
        if raw.startswith("---") or raw.startswith("+++"):
            continue
        if raw.startswith("-"):
            continue  # removed line — not in the new version
        content = raw[1:] if raw.startswith(("+", " ")) else raw

        source_lines.append(content)
        line_map[recon_line] = real_line
        recon_line += 1
        real_line += 1

    return "\n".join(source_lines), line_map


def _get_changed_real_lines(hunks: list[DiffHunk]) -> frozenset[int]:
    """Collect all added-line numbers (real file line numbers) from hunks."""
    lines: set[int] = set()
    for hunk in hunks:
        for added in hunk.added_lines:
            lines.add(added.line)
    return frozenset(lines)


def _try_parse(source: str) -> ast.AST | None:
    """Try to parse Python source, returning None on failure."""
    try:
        return ast.parse(source)
    except SyntaxError:
        # Try adding a dummy function wrapper for statement-only snippets
        wrapped = "def __ast_dummy__():\n    " + source.replace("\n", "\n    ")
        try:
            return ast.parse(wrapped)
        except SyntaxError:
            return None


# ── Rule registry ─────────────────────────────────────────

def _build_rule_map() -> dict[type[ast.AST], list[tuple[AstRule, Callable]]]:
    """Build the complete AST rule registry."""
    return {
        ast.Call: [
            (
                AstRule(
                    "ast-unsafe-deserialization",
                    Severity.CRITICAL,
                    "Unsafe deserialization detected",
                    "Use yaml.SafeLoader or avoid pickle for untrusted data.",
                    0.85,
                ),
                _is_critical_deserialization,
            ),
            (
                AstRule(
                    "ast-weak-hash",
                    Severity.HIGH,
                    "Weak cryptographic hash used",
                    "Use hashlib.sha256() or stronger for security-sensitive hashing.",
                    0.80,
                ),
                _is_weak_hash,
            ),
            (
                AstRule(
                    "ast-dangerous-exec",
                    Severity.CRITICAL,
                    "Dynamic code execution",
                    "Avoid exec/compile with user input. Use explicit logic or sandbox.",
                    0.90,
                ),
                _is_dangerous_exec,
            ),
        ],
        ast.ExceptHandler: [
            (
                AstRule(
                    "ast-broad-except",
                    Severity.MEDIUM,
                    "Overly broad exception handler",
                    "Catch specific exception types or re-raise after logging.",
                    0.70,
                ),
                _is_broad_except,
            ),
        ],
        ast.Import: [
            (
                AstRule(
                    "ast-debug-import",
                    Severity.LOW,
                    "Debugger import left in code",
                    "Remove pdb/ipdb imports before committing to production branches.",
                    0.95,
                ),
                _is_debug_import,
            ),
        ],
        ast.ImportFrom: [
            (
                AstRule(
                    "ast-debug-import",
                    Severity.LOW,
                    "Debugger import left in code",
                    "Remove pdb/ipdb imports before committing to production branches.",
                    0.95,
                ),
                _is_debug_import,
            ),
        ],
        ast.Assign: [
            (
                AstRule(
                    "ast-hardcoded-secret",
                    Severity.HIGH,
                    "Hardcoded credential detected",
                    "Store secrets in environment variables or a secret manager.",
                    0.75,
                ),
                _is_hardcoded_secret,
            ),
        ],
        ast.While: [
            (
                AstRule(
                    "ast-unbounded-loop",
                    Severity.MEDIUM,
                    "Potentially infinite loop without exit",
                    "Add a break condition, timeout, or max-retry guard.",
                    0.60,
                ),
                _is_unbounded_loop,
            ),
        ],
    }


_RULE_MAP = _build_rule_map()


# ── Public entry point ────────────────────────────────────

def scan_ast_risks(files: list[ChangedFile] | None) -> list[RiskFinding]:
    """Scan changed Python files with AST-level rules.

    Only checks added/modified lines — never flags existing code.
    Falls back gracefully on parse failures (non-compilable hunks).
    """
    if not files:
        return []

    findings: list[RiskFinding] = []

    for file in files:
        if detect_programming_language(file.filename) != "python":
            continue

        try:
            hunks = parse_file_hunks(file)
        except Exception:
            continue

        changed_real_lines = _get_changed_real_lines(hunks)
        if not changed_real_lines:
            continue

        for hunk in hunks:
            source, line_map = _reconstruct_source_with_map(hunk)
            if not source.strip():
                continue

            tree = _try_parse(source)
            if tree is None:
                continue

            visitor = _RiskVisitor(
                file.filename, source, line_map,
                changed_real_lines, _RULE_MAP,
            )
            try:
                visitor.visit(tree)
            except Exception:
                continue
            findings.extend(visitor.findings)

    return findings
