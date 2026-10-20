"""Deterministic test-quality rules.

The review funnel has a class of gold findings that are structural rather than
semantic: a test that mocks the function it claims to test, a patch that cannot
reach a local import binding, global state reset without teardown, or a test that
uses pytest plugins the project does not declare. A model can find some of them,
but they are decidable from the AST at zero model cost, so they belong in the
deterministic lane.

The rules are intentionally narrow: each one reports a structural fact and a
reason, and callers decide whether to surface the result. Nothing here publishes
a comment or claims a defect is confirmed.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TestQualityFinding:
    rule_id: str
    path: str
    line: int
    title: str
    reason: str
    confidence: float

    def as_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "path": self.path,
            "line": self.line,
            "title": self.title,
            "reason": self.reason,
            "confidence": self.confidence,
        }


def _module_constants(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    names.add(target.id)
    return names


def _patch_targets(tree: ast.Module) -> dict[str, int]:
    """Map the last component of a patched dotted target to its patch line."""
    targets: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = None
        if ((isinstance(function, ast.Attribute) and function.attr == "patch")
                or (isinstance(function, ast.Name) and function.id == "patch")):
            name = "patch"
        if name is None or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        target = first.value.split(".")[-1]
        if target:
            targets[target] = node.lineno
    return targets


def _imported_names(tree: ast.Module) -> dict[str, tuple[str, int]]:
    names: dict[str, tuple[str, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = (node.module, node.lineno)
    return names


def _fixture_teardown_lines(tree: ast.Module) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorators = {
                getattr(dec, "attr", None) or getattr(dec, "id", None)
                for dec in node.decorator_list
            }
            if "fixture" in decorators and any(
                isinstance(item, ast.Yield) for item in ast.walk(node)
            ):
                lines.append(node.lineno)
    return lines


def _global_clear_without_teardown(tree: ast.Module) -> list[tuple[str, int]]:
    globals_ = _module_constants(tree) | {
        name for name in _imported_names(tree) if name.isupper()
    }
    teardown_present = bool(_fixture_teardown_lines(tree))
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        func = call.func
        if not isinstance(func, ast.Attribute) or func.attr != "clear":
            continue
        if not isinstance(func.value, ast.Name) or func.value.id not in globals_:
            continue
        if not any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and item.lineno <= node.lineno for item in tree.body):
            continue
        if not teardown_present:
            hits.append((func.value.id, node.lineno))
    return hits


def _mock_call_arity(tree: ast.Module) -> list[tuple[str, int, int]]:
    """Calls of a patched function whose positional arity does not fit the mock.

    Without the real signature this rule cannot compare to the production
    function, so it reports the structural inconsistency instead of a verdict.
    """
    patched = _patch_targets(tree)
    hits: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None
        )
        if name in patched:
            hits.append((name, node.lineno, len(node.args)))
    return hits


def _function_signatures(sources: dict[str, str] | None) -> dict[str, tuple[int, str | None]]:
    """Map a function name to (positional parameter count, return annotation)."""
    signatures: dict[str, tuple[int, str | None]] = {}
    for text in (sources or {}).values():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = [argument for argument in node.args.args if argument.arg not in {"self", "cls"}]
            if node.args.vararg is not None:
                continue
            annotation = None
            if node.returns is not None:
                annotation = ast.unparse(node.returns)
            signatures.setdefault(node.name, (len(args), annotation))
    return signatures


def _patch_side_effect_targets(tree: ast.Module) -> dict[str, str]:
    """Map a side_effect function name to the dotted target it stands in for."""
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not ((isinstance(function, ast.Attribute) and function.attr == "patch")
                or (isinstance(function, ast.Name) and function.id == "patch")):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        for keyword in node.keywords:
            if keyword.arg == "side_effect" and isinstance(keyword.value, ast.Name):
                mapping[keyword.value.id] = first.value
    return mapping


def _side_effect_return(
    path: str, tree: ast.Module, signatures: dict[str, tuple[int, str | None]]
) -> list[TestQualityFinding]:
    """Mock side_effect return shape/global mutation that make the test self-testing."""
    findings: list[TestQualityFinding] = []
    targets = _patch_side_effect_targets(tree)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        returns = [item for item in ast.walk(node) if isinstance(item, ast.Return) and item.value]
        mutation = None
        for child in ast.walk(node):
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr in {"append", "clear", "extend", "pop"}
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id.isupper()):
                mutation = child.func.value.id
        if mutation and node.name in targets:
            findings.append(TestQualityFinding(
                rule_id="TQ008", path=path, line=node.lineno,
                title="Mock side_effect mutates module-global state",
                reason=(
                    f"`{node.name}` mutates `{mutation}` from inside a mock side_effect; "
                    "the assertion then checks the mock's own side effect, not production logic."
                ),
                confidence=0.7,
            ))
        if not returns:
            continue
        target = targets.get(node.name)
        if not target:
            continue
        expected = signatures.get(target.split(".")[-1])
        if expected is None or not expected[1]:
            continue
        annotation = expected[1].lower()
        literal_types = {type(item.value).__name__.lower() for item in returns}
        if "tuple" in literal_types and "tuple" not in annotation:
            findings.append(TestQualityFinding(
                rule_id="TQ006", path=path, line=node.lineno,
                title="Mock side_effect return shape does not match the real function",
                reason=(
                    f"`{node.name}` returns a tuple but the real target returns `{expected[1]}`; "
                    "if the production path ran against this mock, the result would be "
                    "the wrong type."
                ),
                confidence=0.7,
            ))
    return findings


def _magic_index_uses(path: str, tree: ast.Module) -> list[TestQualityFinding]:
    """Integer subscripts on a local tuple-like value are brittle and hard to read."""
    findings: list[TestQualityFinding] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        index = node.slice
        if not isinstance(index, ast.Constant) or isinstance(index.value, bool):
            continue
        if not isinstance(index.value, int) or index.value < 4:
            continue
        target = node.value
        if not isinstance(target, ast.Name):
            continue
        identity = (target.id, node.lineno)
        if identity in seen:
            continue
        seen.add(identity)
        findings.append(TestQualityFinding(
            rule_id="TQ007", path=path, line=node.lineno,
            title="Test indexes a tuple-like value with a magic number",
            reason=(
                f"`{target.id}[{index.value}]` relies on positional tuple layout; a named "
                "tuple or data class would document the field and survive reordering."
            ),
            confidence=0.55,
        ))
    return findings


def scan_test_file(
    path: str, text: str, *, project_text: str | None = None,
    known_sources: dict[str, str] | None = None,
) -> list[TestQualityFinding]:
    """Scan one Python test file for structural test-quality problems."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    findings: list[TestQualityFinding] = []
    patched = _patch_targets(tree)
    imported = _imported_names(tree)
    teardown = bool(_fixture_teardown_lines(tree))

    for name, (_module, line) in imported.items():
        if name in patched and patched[name] != line:
            findings.append(TestQualityFinding(
                rule_id="TQ001", path=path, line=line,
                title="Patched target is bound locally before the patch is applied",
                reason=(
                    f"`{name}` is imported by name, so a patch on the module "
                    "attribute does not replace this local binding; calling "
                    f"`{name}()` may still run the real function."
                ),
                confidence=0.8,
            ))

    seen_calls: set[tuple[str, int]] = set()
    for name, line, args in _mock_call_arity(tree):
        if (name, line) in seen_calls:
            continue
        seen_calls.add((name, line))
        findings.append(TestQualityFinding(
            rule_id="TQ002", path=path, line=line,
            title="Test calls the function it mocked",
            reason=(
                f"`{name}` is patched at line {patched.get(name)} and then called "
                f"at line {line} with {args} positional argument(s); assertions on "
                "the patched call verify the mock, not the production logic."
            ),
            confidence=0.7,
        ))

    if not teardown:
        for name, line in _global_clear_without_teardown(tree):
            findings.append(TestQualityFinding(
                rule_id="TQ003", path=path, line=line,
                title="Module-level global is cleared without teardown",
                reason=(
                    f"`{name}.clear()` mutates module-global state at the start of "
                    "a test and there is no yield-fixture teardown; a failure after "
                    "the mutation leaks state into later tests."
                ),
                confidence=0.75,
            ))

    signatures = _function_signatures(known_sources)
    for name, line, args in _mock_call_arity(tree):
        expected = signatures.get(name)
        if expected is None or args < 1 or args == expected[0]:
            continue
        findings.append(TestQualityFinding(
            rule_id="TQ005", path=path, line=line,
            title="Mocked call shape does not match the real function signature",
            reason=(
                f"`{name}` is called with {args} positional argument(s) but its frozen "
                f"definition takes {expected[0]}; if the patch ever missed this call "
                "would raise, and the mock hides the contract mismatch."
            ),
            confidence=0.8,
        ))
    findings.extend(_magic_index_uses(path, tree))
    findings.extend(_side_effect_return(path, tree, signatures))

    if project_text is not None:
        uses_mocker = re.search(r"\bmocker\b", text) is not None
        uses_asyncio = "pytest.mark.asyncio" in text or "pytest_asyncio" in text
        missing = []
        if uses_mocker and "pytest-mock" not in project_text:
            missing.append("pytest-mock")
        if uses_asyncio and "pytest-asyncio" not in project_text:
            missing.append("pytest-asyncio")
        if missing:
            findings.append(TestQualityFinding(
                rule_id="TQ004", path=path, line=1,
                title="Test dependencies are not declared by the project",
                reason=(
                    "The test relies on " + ", ".join(missing)
                    + " but the project metadata does not declare them, so the "
                    "test cannot collect in a clean environment."
                ),
                confidence=0.9,
            ))
    return findings
