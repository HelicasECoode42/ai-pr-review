"""Feed deterministic test-quality findings into review as candidates.

`scan_test_file` only sees one file. This adapter gives it what it needs from the
frozen snapshot: the changed Python test files, the nearest project manifest, and
the source of modules the tests patch (one import hop further, so a function
re-exported by the patched module still resolves to its real signature). The
results are candidates, not conclusions: callers route them through the same
verification and criteria as model candidates.
"""

from __future__ import annotations

import ast
from pathlib import PurePosixPath

from src.analyzer.test_quality_rules import scan_test_file

_MANIFESTS = ("pyproject.toml", "requirements-dev.txt", "requirements.txt", "setup.cfg")


def is_python_test(path: str) -> bool:
    pure = PurePosixPath(path)
    if pure.suffix != ".py":
        return False
    return pure.name.startswith("test_") or pure.name.endswith("_test.py") or "tests" in pure.parts


def _ancestors(path: str) -> list[str]:
    parents = [str(parent) for parent in PurePosixPath(path).parents]
    return ["" if parent == "." else parent for parent in parents]


def _join(root: str, relative: str) -> str:
    return f"{root}/{relative}" if root else relative


def _project_text(snapshot, path: str) -> str | None:
    texts = []
    for root in _ancestors(path):
        for name in _MANIFESTS:
            text = snapshot.read(_join(root, name), "head")
            if text:
                texts.append(text)
        if texts:
            return "\n".join(texts)
    return None


def _dotted_modules(tree: ast.Module) -> set[str]:
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif (isinstance(node, ast.Call) and node.args
              and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
              and getattr(node.func, "attr", getattr(node.func, "id", None)) == "patch"):
            target = node.args[0].value
            if "." in target:
                modules.add(target.rsplit(".", 1)[0])
    return modules


def _resolve(snapshot, test_path: str, module: str) -> tuple[str, str] | None:
    relative = module.replace(".", "/")
    for root in _ancestors(test_path):
        for candidate in (_join(root, relative + ".py"), _join(root, relative + "/__init__.py")):
            text = snapshot.read(candidate, "head")
            if text:
                return candidate, text
    return None


def _known_sources(snapshot, test_path: str, tree: ast.Module) -> dict[str, str]:
    sources: dict[str, str] = {}
    frontier = _dotted_modules(tree)
    for _hop in range(2):
        following = set()
        for module in frontier:
            resolved = _resolve(snapshot, test_path, module)
            if resolved is None or resolved[0] in sources:
                continue
            sources[resolved[0]] = resolved[1]
            try:
                following |= _dotted_modules(ast.parse(resolved[1]))
            except SyntaxError:
                continue
        frontier = following
    return sources


def collect_test_quality_candidates(snapshot, files) -> list[dict]:
    """Raw-discovery shaped findings for every changed Python test file."""
    findings = []
    for file in files:
        if not is_python_test(file.filename):
            continue
        text = snapshot.read(file.filename, "head")
        if not text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for item in scan_test_file(
            file.filename, text,
            project_text=_project_text(snapshot, file.filename),
            known_sources=_known_sources(snapshot, file.filename, tree),
        ):
            source_lines = text.splitlines()
            quote = source_lines[item.line - 1] if 0 < item.line <= len(source_lines) else ""
            findings.append({
                "file": item.path, "line": item.line,
                "title": f"[{item.rule_id}] {item.title}",
                "reason": item.reason,
                "evidence": [quote] if quote.strip() else [],
                "rule_id": item.rule_id,
            })
    return findings
