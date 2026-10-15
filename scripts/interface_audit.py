#!/usr/bin/env python
"""Interface audit: find features that are wired to nothing.

Every integration bug in this project's history had the same shape - a flag or
argument was added in one layer and never consumed in the next:

* ``prompt_addendum`` reached the legacy path but not the grounded one;
* ``min_confidence`` was accepted by the grounded path and never read;
* ``witness_blocked`` was initialised inside a branch, so an empty candidate list
  crashed the audit;
* knowledge recall matched ``py`` against a scope that said ``python``.

Unit tests do not catch those, because each side is individually correct. This
script checks the seams statically, with no model and no network:

1. **unused parameters** - a function accepts an argument and never references it,
   which is how a feature becomes inert while reporting itself as active;
2. **unknown keyword arguments** - a call passes a keyword the callee does not
   accept (a hard TypeError at runtime, or silently swallowed by **kwargs);
3. **flag-like parameters with no reader** - the same as (1) restricted to the
   names that carry switches, budgets and factories.

Run it as a CI gate:

    .venv/bin/python scripts/interface_audit.py
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FLAG_HINTS = (
    "enabled", "enable", "rounds", "tokens", "budget", "max_", "_max", "path",
    "factory", "addendum", "domains", "workers", "units", "threshold", "mode",
    "confidence", "recall", "workers", "ceiling",
)
SKIP_PARAMS = {"self", "cls", "kwargs", "args"}


@dataclass(frozen=True)
class DefRecord:
    name: str
    qualname: str
    path: str
    line: int
    params: tuple[str, ...]
    has_var_keyword: bool
    body_names: frozenset[str]


def functions(tree: ast.AST, path: str) -> list[DefRecord]:
    found: list[DefRecord] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            args = node.args
            params = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
            if args.vararg:
                params.append(args.vararg.arg)
            if args.kwarg:
                params.append(args.kwarg.arg)
            used = {
                child.id for child in ast.walk(node)
                if isinstance(child, ast.Name)
            }
            used |= {
                child.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
            }
            found.append(DefRecord(
                name=node.name,
                qualname=".".join([*self.stack, node.name]),
                path=path,
                line=node.lineno,
                params=tuple(params),
                has_var_keyword=args.kwarg is not None,
                body_names=frozenset(used),
            ))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

    Visitor().visit(tree)
    return found


def calls(tree: ast.AST) -> list[tuple[str, set[str], int]]:
    out: list[tuple[str, set[str], int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            keywords = {kw.arg for kw in node.keywords if kw.arg}
            if keywords:
                out.append((node.func.id, keywords, node.lineno))
    return out


def audit(paths: list[Path]) -> tuple[list[str], list[str], list[str]]:
    definitions: dict[str, list[DefRecord]] = {}
    call_sites: list[tuple[str, set[str], str, int]] = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # a file that cannot parse is its own failure
            raise SystemExit(f"{path}: {exc}")
        rel = str(path.relative_to(REPO_ROOT))
        for record in functions(tree, rel):
            definitions.setdefault(record.name, []).append(record)
        for name, keywords, line in calls(tree):
            call_sites.append((name, keywords, rel, line))

    unused: list[str] = []
    flags: list[str] = []
    for records in definitions.values():
        if len(records) > 1:
            continue  # same name in several modules: cannot attribute reliably
        record = records[0]
        for param in record.params:
            if param in SKIP_PARAMS or param.startswith("_"):
                continue
            if param in record.body_names:
                continue
            # A recv-style name may only appear as an attribute base; body_names
            # already includes attribute names, so a genuine miss stays a miss.
            message = f"{record.path}:{record.line} {record.qualname}({param}) is accepted and never used"
            unused.append(message)
            if any(hint in param.lower() for hint in FLAG_HINTS):
                flags.append(message)

    unknown: list[str] = []
    for name, keywords, rel, line in call_sites:
        records = definitions.get(name)
        if not records:
            continue
        params = set()
        for record in records:
            params |= set(record.params)
        if any(record.has_var_keyword for record in records):
            continue
        for keyword in sorted(keywords - params):
            unknown.append(f"{rel}:{line} {name}(... {keyword}=...) is not a parameter")
    return sorted(set(unused)), sorted(set(flags)), sorted(set(unknown))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-tests", action="store_true",
                        help="also audit tests/ and scripts/")
    args = parser.parse_args(argv)
    roots = ["src"]
    if args.include_tests:
        roots += ["tests", "scripts"]
    paths: list[Path] = []
    for root in roots:
        paths.extend(sorted((REPO_ROOT / root).rglob("*.py")))
    paths = [p for p in paths if "__pycache__" not in p.parts]

    unused, flags, unknown = audit(paths)
    print(f"audited {len(paths)} file(s)\n")
    sections = (
        ("unknown keyword arguments (call/definition mismatch)", unknown),
        ("flag-like parameters with no reader", flags),
        ("parameters accepted and never used", unused),
    )
    for title, items in sections:
        print(f"== {title}: {len(items)}")
        for item in items:
            print(f"   {item}")
        print()
    failing = len(unknown) + len(flags)
    if failing:
        print(f"interface audit FAILED: {failing} seam problem(s)")
        return 1
    print("interface audit passed: no inert flags, no call/definition mismatch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
