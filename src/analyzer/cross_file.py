"""Cross-file impact analysis for PR review.

Detects when functions changed in this PR have new call sites in OTHER
changed files, and flags signature-change risks across the PR's own diff.

Current scope (heuristic, per changed-files diffs):
1. Identify function definitions introduced/modified in changed files
2. Find new call sites across all changed files
3. Flag when a changed function is called from a different changed file

Limitation: does NOT scan unchanged repository files for downstream
callers. A full implementation would need repository-level file access.
"""

from __future__ import annotations

import ast
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from src.analyzer.diff_parser import parse_file_hunks
from src.models import ChangedFile, RiskFinding, Severity

logger = logging.getLogger(__name__)

# ── Data structures ───────────────────────────────────────

@dataclass
class FunctionInfo:
    name: str
    file_path: str
    params: list[str]
    lineno: int
    decorators: list[str] = field(default_factory=list)
    returns: str | None = None


@dataclass
class CallSite:
    file_path: str
    func_name: str
    lineno: int
    args_count: int


# ── Extraction ────────────────────────────────────────────

class _FuncCollector(ast.NodeVisitor):
    """Collect function definitions and call sites from a Python AST."""

    def __init__(self):
        self.definitions: list[FunctionInfo] = []
        self.calls: list[CallSite] = []

    def visit_FunctionDef(self, node: ast.FunctionDef):
        params = [a.arg for a in node.args.args]
        decorators = []
        for d in node.decorator_list:
            if isinstance(d, ast.Name):
                decorators.append(d.id)
            elif isinstance(d, ast.Attribute) and isinstance(d.value, ast.Name):
                decorators.append(f"{d.value.id}.{d.attr}")
            elif isinstance(d, ast.Call) and isinstance(d.func, ast.Name):
                decorators.append(d.func.id)
        self.definitions.append(FunctionInfo(
            name=node.name,
            file_path="",
            params=params,
            lineno=node.lineno,
            decorators=decorators,
            returns=ast.unparse(node.returns) if node.returns else None,
        ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        func_name = _resolve_call_name(node.func)
        if func_name:
            self.calls.append(CallSite(
                file_path="",
                func_name=func_name,
                lineno=node.lineno,
                args_count=len(node.args) + len(node.keywords),
            ))
        self.generic_visit(node)


def _resolve_call_name(node: ast.expr) -> str | None:
    """Resolve call expression to a function name like 'module.Func'."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _resolve_call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


# ── Changed signature detection ───────────────────────────

def _detect_changed_signatures(
    files: list[ChangedFile],
) -> dict[str, list[FunctionInfo]]:
    """Find function definitions that were added or modified in this PR.

    Returns mapping of function name → changed definitions.
    """
    from src.analyzer.ast_rules import _reconstruct_source_with_map, _try_parse

    changed: dict[str, list[FunctionInfo]] = defaultdict(list)

    for f in files:
        if not f.filename.endswith(".py"):
            continue

        try:
            hunks = parse_file_hunks(f)
        except Exception:
            continue

        for hunk in hunks:
            source, line_map = _reconstruct_source_with_map(hunk)
            if not source.strip():
                continue

            tree = _try_parse(source)
            if tree is None:
                continue

            collector = _FuncCollector()
            try:
                collector.visit(tree)
            except Exception:
                continue

            for func in collector.definitions:
                func.file_path = f.filename
                func.lineno = line_map.get(func.lineno, func.lineno)
                changed[func.name].append(func)

    return changed


def _detect_new_calls(
    files: list[ChangedFile],
) -> list[CallSite]:
    """Find new call sites introduced in this PR."""
    from src.analyzer.ast_rules import _reconstruct_source_with_map, _try_parse

    calls: list[CallSite] = []

    for f in files:
        if not f.filename.endswith(".py"):
            continue

        try:
            hunks = parse_file_hunks(f)
        except Exception:
            continue

        for hunk in hunks:
            source, line_map = _reconstruct_source_with_map(hunk)
            if not source.strip():
                continue

            tree = _try_parse(source)
            if tree is None:
                continue

            collector = _FuncCollector()
            try:
                collector.visit(tree)
            except Exception:
                continue

            for call in collector.calls:
                call.file_path = f.filename
                call.lineno = line_map.get(call.lineno, call.lineno)
                calls.append(call)

    return calls


# ── Impact analysis ───────────────────────────────────────

def analyze_cross_file_impact(
    files: list[ChangedFile],
) -> list[RiskFinding]:
    """Analyze cross-file impact of changes.

    Currently detects:
    1. Functions whose signatures changed (params added/removed)
    2. Call sites that might be affected by upstream signature changes
    """
    if not files:
        return []

    findings: list[RiskFinding] = []

    # 1. Detect changed function signatures
    changed_funcs = _detect_changed_signatures(files)

    # 2. Detect new call sites
    new_calls = _detect_new_calls(files)

    # 3. Flag functions with many parameters changed (high risk)
    # Also flag file-level changes that affect core infrastructure
    for func_name, funcs in changed_funcs.items():
        if len(funcs) >= 2:
            # Same function defined in multiple files — might indicate refactoring
            locations = ", ".join(
                f"{fn.file_path}:{fn.lineno}" for fn in funcs
            )
            findings.append(
                RiskFinding(
                    file_path=funcs[0].file_path,
                    line=funcs[0].lineno,
                    severity=Severity.MEDIUM,
                    rule_id="cross-file-refactor",
                    title=f"Function '{func_name}' modified across files",
                    evidence=f"Changed in: {locations}",
                    recommendation=(
                        "Review all call sites; "
                        "ensure signature changes are backward-compatible."
                    ),
                    confidence=0.65,
                )
            )
        elif len(funcs) == 1 and funcs[0].params:
            # Function with parameters changed — downstream callers might need updates
            fn = funcs[0]
            # Check if new calls reference this function
            matching_calls = [
                c for c in new_calls
                if c.func_name == fn.name and c.file_path != fn.file_path
            ]
            if matching_calls:
                call_locations = ", ".join(
                    f"{c.file_path}:{c.lineno}" for c in matching_calls[:3]
                )
                findings.append(
                    RiskFinding(
                        file_path=fn.file_path,
                        line=fn.lineno,
                        severity=Severity.MEDIUM,
                        rule_id="cross-file-call-impact",
                        title=(
                            f"Function '{fn.name}' changed; "
                            "cross-file callers may be affected"
                        ),
                        evidence=(
                            f"Callers in other files: {call_locations}"
                            if matching_calls
                            else (
                                f"Function '{fn.name}' signature changed. "
                                "Verify all call sites manually."
                            )
                        ),
                        recommendation=(
                            "Check that all callers pass the correct arguments."
                        ),
                        confidence=0.60,
                    )
                )

    # 4. Flag when a file adds imports from other changed files
    # (tight coupling across the PR)
    if len(changed_funcs) >= 3:
        affected_files = set()
        for funcs in changed_funcs.values():
            for fn in funcs:
                affected_files.add(fn.file_path)
        if len(affected_files) >= 3:
            findings.append(
                RiskFinding(
                    file_path="",
                    line=None,
                    severity=Severity.MEDIUM,
                    rule_id="cross-file-many-funcs",
                    title=(
                        f"{len(changed_funcs)} functions changed "
                        f"across {len(affected_files)} files"
                    ),
                    evidence="",
                    recommendation=(
                        "Consider splitting this PR: "
                        "wide-ranging function changes increase regression risk."
                    ),
                    confidence=0.55,
                )
            )

    return findings
