"""Mechanical witness evaluation for claimed behaviour differences.

The strongest check available for a code-review finding is not a better prompt:
it is running both versions on an input and seeing whether they differ. This
module makes that check available for the class of findings where it is sound —
pure expressions extracted verbatim from the frozen base and head snapshots.

Two outcomes matter, and they are deliberately asymmetric:

* ``differs`` — a concrete input where base and head disagree. This is a
  *witness*: the claim has demonstrated evidence, and it is recorded.
* ``no_witness`` — no input in a systematically enumerated domain distinguishes
  the two expressions. For a claim of a behaviour change this is a refutation of
  the *evidence*, not a proof of universal equivalence, so the caller must not
  treat it as "confirmed" and must say so in the decision ledger.
* ``unsupported`` — inputs, expressions, or intermediate values exceed the
  explicit evaluator bounds. This is not evidence of equivalence or difference.

The evaluator never executes parsed source: expressions are walked against a
whitelist of pure AST nodes with no builtins beyond a small set, no attribute
access and no imports. Resource ceilings are 512 AST nodes, depth 64, 256-bit
integers, 4096 logical sequence items, 4096 domain assignments, and 200,000
evaluation steps. Crossing a ceiling returns ``unsupported``; the caller keeps
the claim pending instead of treating the evaluator's limit as a proof.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field

MAX_DOMAIN_ASSIGNMENTS = 4096
MAX_EVALUATION_STEPS = 200_000
MAX_AST_NODES = 512
MAX_AST_DEPTH = 64
MAX_LITERAL_CHARS = 1024
MAX_INT_BITS = 256
MAX_VALUE_ITEMS = 4096
_CONTAINER_NAMES = {
    "values", "items", "seq", "xs", "arr", "data", "lst", "collection", "rows", "records",
}
_INT_NAMES = {
    "index", "i", "j", "idx", "k", "n", "count", "size", "length", "total", "value", "x", "y",
    "start", "end", "offset", "limit",
}
_LIST_VALUES: tuple[object, ...] = ([1, 2, 3], [], [0], [1, 2])
_INT_VALUES: tuple[object, ...] = (-4, -1, 0, 1, 2, 3, None, "")
_OTHER_VALUES: tuple[object, ...] = (0, 1, -1, None, True, False, "")
_SAFE_CALLS = {"len": len, "abs": abs, "min": min, "max": max, "sum": sum}
_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Is, ast.IsNot, ast.IfExp, ast.Subscript, ast.Slice, ast.Name, ast.Load,
    ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set, ast.Call, ast.keyword,
)


class UnsupportedExpression(Exception):
    """The expression is outside the mechanically evaluable subset."""


class _Raised:
    """A raised exception, compared by type name so two versions can be told apart."""

    def __init__(self, exc: BaseException):
        self.kind = type(exc).__name__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Raised) and other.kind == self.kind

    def __hash__(self) -> int:  # pragma: no cover - only needed for set use
        return hash(self.kind)

    def __repr__(self) -> str:
        return f"raises:{self.kind}"


@dataclass(frozen=True)
class WitnessResult:
    status: str  # differs | no_witness | unsupported | error
    reason: str
    domain_size: int = 0
    witness: dict[str, object] = field(default_factory=dict)
    base_value: str = ""
    head_value: str = ""

    @property
    def blocks_confirmation(self) -> bool:
        """Whether this result cannot support confirming a behavior-change claim."""
        return self.status in {"no_witness", "unsupported", "error"}


def _check_nodes(tree: ast.AST) -> None:
    stack = [(tree, 1)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_AST_NODES:
            raise UnsupportedExpression("AST node count limit exceeded")
        if depth > MAX_AST_DEPTH:
            raise UnsupportedExpression("AST depth limit exceeded")
        if not isinstance(node, _ALLOWED_NODES):
            raise UnsupportedExpression(f"node {type(node).__name__} is not evaluable")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_CALLS
        ):
            raise UnsupportedExpression("only len/abs/min/max/sum calls are evaluable")
        if isinstance(node, ast.Constant) and not isinstance(
            node.value, (int, float, bool, str, type(None))
        ):
            raise UnsupportedExpression("constant of unsupported type")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int) and node.value.bit_length() > MAX_INT_BITS:
                raise UnsupportedExpression("integer literal size limit exceeded")
            if isinstance(node.value, str) and len(node.value) > MAX_LITERAL_CHARS:
                raise UnsupportedExpression("string literal size limit exceeded")
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise UnsupportedExpression("non-finite float literal is not evaluable")
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and len(node.elts) > MAX_VALUE_ITEMS:
            raise UnsupportedExpression("container literal size limit exceeded")
        if isinstance(node, ast.Dict) and len(node.keys) > MAX_VALUE_ITEMS:
            raise UnsupportedExpression("container literal size limit exceeded")
        if isinstance(node, ast.Call) and node.keywords:
            raise UnsupportedExpression("keyword arguments are not evaluable")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))


def parse_expression(text: str) -> ast.Expression:
    """Parse one source expression, refusing anything outside the safe subset."""
    candidate = text.strip().rstrip(":,").strip()
    if candidate.startswith("return "):
        candidate = candidate[len("return "):].strip()
    if not candidate or len(candidate) > 2000:
        raise UnsupportedExpression("empty or oversized expression")
    try:
        tree = ast.parse(candidate, mode="eval")
    except SyntaxError as exc:
        raise UnsupportedExpression(f"not a single expression: {exc.msg}") from exc
    except (MemoryError, RecursionError) as exc:
        raise UnsupportedExpression("parser resource limit exceeded") from exc
    _check_nodes(tree)
    return tree


def free_names(tree: ast.Expression) -> list[str]:
    """Names whose value must be supplied. Call targets are not free variables."""
    return sorted(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in _SAFE_CALLS
    )


def _values_for(name: str) -> tuple[object, ...]:
    lowered = name.lower()
    if lowered in _CONTAINER_NAMES or lowered.endswith(("s", "list", "seq")):
        return _LIST_VALUES
    if lowered in _INT_NAMES:
        return _INT_VALUES
    return _OTHER_VALUES


def _domain(names: list[str]) -> list[dict[str, object]]:
    combinations = 1
    for name in names:
        option_count = len(_values_for(name))
        if combinations > MAX_DOMAIN_ASSIGNMENTS // option_count:
            raise UnsupportedExpression("input domain exceeds assignment limit")
        combinations *= option_count
    assignments: list[dict[str, object]] = [{}]
    for name in names:
        options = _values_for(name)
        expanded = [{**existing, name: value} for existing in assignments for value in options]
        assignments = expanded
    return assignments


class _Budget:
    def __init__(self, limit: int):
        self.remaining = limit

    def spend(self) -> None:
        self.remaining -= 1
        if self.remaining <= 0:
            raise UnsupportedExpression("evaluation budget exhausted")


def _value_weight(value: object, *, stop_after: int | None = None) -> int:
    """Bound logical sequence size before an operation can amplify it."""
    if stop_after is None:
        stop_after = MAX_VALUE_ITEMS
    pending = [value]
    weight = 0
    while pending:
        current = pending.pop()
        if isinstance(current, int) and current.bit_length() > MAX_INT_BITS:
            raise UnsupportedExpression("integer result size limit exceeded")
        if isinstance(current, float) and not math.isfinite(current):
            raise UnsupportedExpression("non-finite float result is not evaluable")
        if isinstance(current, str):
            weight += len(current)
        elif isinstance(current, (list, tuple, set, dict)):
            weight += len(current)
            if isinstance(current, dict):
                pending.extend(current.keys())
                pending.extend(current.values())
            else:
                pending.extend(current)
        if weight > stop_after:
            raise UnsupportedExpression("value size limit exceeded")
    return weight


def _bounded_binop(op: ast.operator, left: object, right: object) -> object:
    """Check growth before invoking Python operators that allocate results."""
    if isinstance(op, ast.Pow):
        if not isinstance(right, int) or isinstance(right, bool) or abs(right) > 8:
            raise UnsupportedExpression("power exponent is outside the supported range")
        if isinstance(left, int) and not isinstance(left, bool):
            estimated_bits = max(1, abs(left).bit_length()) * max(1, right)
            if estimated_bits > MAX_INT_BITS:
                raise UnsupportedExpression("power result size limit exceeded")
        elif not isinstance(left, float):
            raise UnsupportedExpression("power operand types are not evaluable")
        result = left ** right  # type: ignore[operator]
    elif isinstance(op, (ast.Add, ast.Mult)):
        if isinstance(op, ast.Add) and type(left) is type(right) and isinstance(
            left, (str, list, tuple)
        ):
            if _value_weight(left) + _value_weight(right) > MAX_VALUE_ITEMS:
                raise UnsupportedExpression("value size limit exceeded")
        elif isinstance(op, ast.Mult):
            sequence, count = (left, right) if isinstance(right, int) else (right, left)
            if (
                isinstance(sequence, (str, list, tuple))
                and isinstance(count, int)
                and _value_weight(sequence) * max(0, count) > MAX_VALUE_ITEMS
            ):
                raise UnsupportedExpression("value size limit exceeded")
        if isinstance(left, int) and isinstance(right, int):
            if isinstance(op, ast.Add):
                if max(left.bit_length(), right.bit_length()) + 1 > MAX_INT_BITS:
                    raise UnsupportedExpression("integer result size limit exceeded")
            elif left.bit_length() + right.bit_length() > MAX_INT_BITS + 1:
                raise UnsupportedExpression("integer result size limit exceeded")
        result = left + right if isinstance(op, ast.Add) else left * right  # type: ignore[operator]
    elif isinstance(op, ast.Sub):
        if isinstance(left, int) and isinstance(right, int) and max(
            left.bit_length(), right.bit_length()
        ) + 1 > MAX_INT_BITS:
            raise UnsupportedExpression("integer result size limit exceeded")
        result = left - right  # type: ignore[operator]
    elif isinstance(op, ast.Div):
        result = left / right  # type: ignore[operator]
    elif isinstance(op, ast.FloorDiv):
        result = left // right  # type: ignore[operator]
    elif isinstance(op, ast.Mod):
        result = left % right  # type: ignore[operator]
    else:
        raise UnsupportedExpression("unsupported binary operator")
    _value_weight(result)
    return result


def _evaluate(node: ast.AST, env: dict[str, object], budget: _Budget) -> object:
    budget.spend()
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, env, budget)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise UnsupportedExpression(f"unbound name {node.id}")
        return env[node.id]
    if isinstance(node, ast.List):
        return [_evaluate(item, env, budget) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_evaluate(item, env, budget) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {
            _evaluate(key, env, budget): _evaluate(value, env, budget)
            for key, value in zip(node.keys, node.values, strict=True)
        }
    if isinstance(node, ast.Set):
        return {_evaluate(item, env, budget) for item in node.elts}
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, env, budget)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand  # type: ignore[operator]
        return +operand  # type: ignore[operator]
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: object = True
            for value in node.values:
                result = _evaluate(value, env, budget)
                if not result:
                    return result
            return result
        result = False
        for value in node.values:
            result = _evaluate(value, env, budget)
            if result:
                return result
        return result
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, env, budget)
        right = _evaluate(node.right, env, budget)
        return _bounded_binop(node.op, left, right)
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, env, budget)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _evaluate(comparator, env, budget)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return (
            _evaluate(node.body, env, budget)
            if _evaluate(node.test, env, budget)
            else _evaluate(node.orelse, env, budget)
        )
    if isinstance(node, ast.Subscript):
        target = _evaluate(node.value, env, budget)
        index = _evaluate(node.slice, env, budget)
        return target[index]  # type: ignore[index]
    if isinstance(node, ast.Slice):
        return slice(
            _evaluate(node.lower, env, budget) if node.lower else None,
            _evaluate(node.upper, env, budget) if node.upper else None,
        )
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name)
        args = [_evaluate(arg, env, budget) for arg in node.args]
        if node.func.id == "sum":
            if not args:
                return _SAFE_CALLS[node.func.id]()
            if len(args) > 2:
                return _SAFE_CALLS[node.func.id](*args)
            result: object = args[1] if len(args) == 2 else 0
            if not isinstance(result, (int, float)):
                raise UnsupportedExpression("sum start value is outside the supported domain")
            _value_weight(result)
            for arg in args[0]:  # type: ignore[union-attr]
                result = _bounded_binop(ast.Add(), result, arg)
            return result
        result = _SAFE_CALLS[node.func.id](*args)
        _value_weight(result)
        return result
    raise UnsupportedExpression(f"node {type(node).__name__} is not evaluable")


def _compare(op: ast.cmpop, left: object, right: object) -> bool:
    if isinstance(op, ast.Eq):
        return bool(left == right)
    if isinstance(op, ast.NotEq):
        return bool(left != right)
    if isinstance(op, ast.In):
        return bool(left in right)  # type: ignore[operator]
    if isinstance(op, ast.NotIn):
        return bool(left not in right)  # type: ignore[operator]
    if isinstance(op, ast.Is):
        return left is right
    if isinstance(op, ast.IsNot):
        return left is not right
    if isinstance(op, ast.Lt):
        return bool(left < right)  # type: ignore[operator]
    if isinstance(op, ast.LtE):
        return bool(left <= right)  # type: ignore[operator]
    if isinstance(op, ast.Gt):
        return bool(left > right)  # type: ignore[operator]
    if isinstance(op, ast.GtE):
        return bool(left >= right)  # type: ignore[operator]
    raise UnsupportedExpression("unsupported comparison")


def _outcome(node: ast.AST, env: dict[str, object], budget: _Budget) -> object:
    try:
        return _evaluate(node, env, budget)
    except UnsupportedExpression:
        raise
    except (MemoryError, RecursionError) as exc:
        raise UnsupportedExpression("evaluation resource limit exceeded") from exc
    except Exception as exc:  # a raised exception is itself an observable outcome
        return _Raised(exc)


def evaluate_equivalence(base_expression: str, head_expression: str) -> WitnessResult:
    """Look for an input on which the two expressions behave differently."""
    try:
        base_tree = parse_expression(base_expression)
        head_tree = parse_expression(head_expression)
    except UnsupportedExpression as exc:
        return WitnessResult("unsupported", str(exc))
    except (MemoryError, RecursionError):
        return WitnessResult("unsupported", "parser resource limit exceeded")
    try:
        names = sorted(set(free_names(base_tree)) | set(free_names(head_tree)))
    except (MemoryError, RecursionError):
        return WitnessResult("unsupported", "input analysis resource limit exceeded")
    try:
        assignments = _domain(names)
    except UnsupportedExpression as exc:
        return WitnessResult("unsupported", str(exc))
    except (MemoryError, RecursionError):
        return WitnessResult("unsupported", "input domain resource limit exceeded")
    if not assignments:
        return WitnessResult("unsupported", "no evaluable inputs")
    budget = _Budget(MAX_EVALUATION_STEPS)
    try:
        for env in assignments:
            base_value = _outcome(base_tree, env, budget)
            head_value = _outcome(head_tree, env, budget)
            if base_value != head_value:
                return WitnessResult(
                    "differs",
                    "found an input where the two versions behave differently",
                    domain_size=len(assignments),
                    witness=dict(env),
                    base_value=repr(base_value),
                    head_value=repr(head_value),
                )
    except UnsupportedExpression as exc:
        return WitnessResult("unsupported", str(exc))
    except (MemoryError, RecursionError):
        return WitnessResult("unsupported", "evaluation resource limit exceeded")
    return WitnessResult(
        "no_witness",
        f"no behaviour difference on {len(assignments)} enumerated assignment(s)",
        domain_size=len(assignments),
    )


def expression_from_quote(quote: str) -> str | None:
    """The expression a quote contributes, or None if it is not one."""
    stripped = quote.strip()
    if not stripped:
        return None
    # Diff markers are part of the line, not of the expression.
    if stripped[:1] in {"+", "-"} and not stripped[1:2].isdigit():
        stripped = stripped[1:].strip()
    if stripped.startswith("#"):
        return None
    try:
        parse_expression(stripped)
    except UnsupportedExpression:
        return None
    return stripped
