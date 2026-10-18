"""Mechanical witness evaluation: what it proves and what it refuses to prove."""

from __future__ import annotations

import pytest

from src.reviewer.witness import (
    UnsupportedExpression,
    evaluate_equivalence,
    expression_from_quote,
    free_names,
    parse_expression,
)

EQUIVALENT_BASE = "return values[index] if 0 <= index < len(values) else None"
EQUIVALENT_HEAD = "return None if index < 0 or index >= len(values) else values[index]"


def test_equivalent_guard_rewrite_has_no_witness():
    result = evaluate_equivalence(EQUIVALENT_BASE, EQUIVALENT_HEAD)
    assert result.status == "no_witness"
    assert result.blocks_confirmation is True
    assert result.domain_size > 0


def test_removed_guard_produces_a_witness():
    result = evaluate_equivalence(
        "return total / count if count else 0", "return total / count"
    )
    assert result.status == "differs"
    assert result.witness.get("count") == 0
    assert "ZeroDivision" in result.head_value
    assert result.blocks_confirmation is False


def test_negative_index_guard_removal_produces_a_witness():
    result = evaluate_equivalence(
        EQUIVALENT_BASE, "return values[index] if index < len(values) else None"
    )
    assert result.status == "differs"
    assert result.witness.get("index") == -4


def test_algebraically_equal_expressions_have_no_witness():
    assert evaluate_equivalence("return x + 1", "return 1 + x").status == "no_witness"


def test_raising_the_same_exception_on_both_sides_is_not_a_witness():
    """`0 <= None` raises in Python; both versions raising is equivalence."""
    result = evaluate_equivalence("return x < 1", "return not x >= 1")
    assert result.status == "no_witness"


def test_unsupported_expression_is_reported_not_guessed():
    result = evaluate_equivalence("return obj.method()", "return other.method()")
    assert result.status == "unsupported"
    assert result.blocks_confirmation is True


def test_unsupported_pow_is_not_silently_rewritten_to_zero():
    result = evaluate_equivalence("x ** 9", "0")
    assert result.status == "unsupported"
    assert "exponent" in result.reason


def test_sequence_multiplication_is_rejected_before_large_result_allocation(monkeypatch):
    import src.reviewer.witness as witness

    monkeypatch.setattr(witness, "MAX_VALUE_ITEMS", 32)
    result = evaluate_equivalence('"x" * (2 ** 6)', '"x" * 32')
    assert result.status == "unsupported"
    assert "value size" in result.reason


def test_ast_depth_and_node_count_are_bounded_iteratively(monkeypatch):
    import src.reviewer.witness as witness

    monkeypatch.setattr(witness, "MAX_AST_DEPTH", 12)
    with pytest.raises(UnsupportedExpression, match="depth"):
        parse_expression("not " * 20 + "x")

    monkeypatch.setattr(witness, "MAX_AST_DEPTH", 100)
    monkeypatch.setattr(witness, "MAX_AST_NODES", 10)
    with pytest.raises(UnsupportedExpression, match="node count"):
        parse_expression("x + x + x + x + x + x")


def test_domain_product_over_limit_is_unsupported_not_truncated(monkeypatch):
    import src.reviewer.witness as witness

    monkeypatch.setattr(witness, "MAX_DOMAIN_ASSIGNMENTS", 7)
    result = evaluate_equivalence("x + y", "y + x")
    assert result.status == "unsupported"
    assert "domain" in result.reason


def test_small_supported_arithmetic_and_sequence_witnesses_remain_available():
    assert evaluate_equivalence("x + 1", "x + 2").status == "differs"
    assert evaluate_equivalence('"a" * 2', '"a" * 3').status == "differs"
    assert evaluate_equivalence("x ** 2", "x ** 3").status == "differs"
    assert evaluate_equivalence("1 / x", "2 / x").status == "differs"
    assert evaluate_equivalence("sum([1, 2], 3)", "sum([1, 2], 4)").status == "differs"
    assert evaluate_equivalence("sum([1], 10)", "11").status == "no_witness"
    assert evaluate_equivalence("sum()", "0").status == "differs"
    assert evaluate_equivalence('"a" * 0', "''").status == "no_witness"


def test_nested_sequence_repetition_counts_logical_elements_before_multiplying(monkeypatch):
    import src.reviewer.witness as witness

    monkeypatch.setattr(witness, "MAX_VALUE_ITEMS", 19)
    result = evaluate_equivalence("[[0] * 4] * 4", "[[0] * 4] * 3")
    assert result.status == "unsupported"
    assert "value size" in result.reason


def test_non_finite_float_results_are_unsupported_not_false_witnesses():
    result = evaluate_equivalence("1e999 - 1e999", "1e999 - 1e999")
    assert result.status == "unsupported"
    assert "non-finite float" in result.reason
    assert evaluate_equivalence("sum([], '')", "''").status == "unsupported"


@pytest.mark.parametrize("resource_error", [MemoryError, RecursionError])
def test_evaluator_resource_errors_become_unsupported(monkeypatch, resource_error):
    import src.reviewer.witness as witness

    def exhaust(*_args, **_kwargs):
        raise resource_error("synthetic bounded evaluator failure")

    monkeypatch.setattr(witness, "_evaluate", exhaust)
    result = evaluate_equivalence("1", "2")
    assert result.status == "unsupported"
    assert result.blocks_confirmation is True


def test_oversized_integer_literal_is_unsupported(monkeypatch):
    import src.reviewer.witness as witness

    monkeypatch.setattr(witness, "MAX_INT_BITS", 8)
    result = evaluate_equivalence("256", "0")
    assert result.status == "unsupported"
    assert "integer literal" in result.reason


def test_imports_and_attributes_are_refused():
    for source in ("__import__('os').system('x')", "obj.attr", "eval('1')"):
        with pytest.raises(UnsupportedExpression):
            parse_expression(source)


def test_call_targets_are_not_free_variables():
    names = free_names(parse_expression("0 <= index < len(values)"))
    assert names == ["index", "values"]


def test_expression_from_quote_strips_diff_markers():
    assert expression_from_quote("+    return total / count") == "return total / count"
    assert expression_from_quote("-return values[index] if index < len(values) else None") == (
        "return values[index] if index < len(values) else None"
    )


def test_expression_from_quote_rejects_prose():
    assert expression_from_quote("the caller always passes an int here") is None
    assert expression_from_quote("# a comment") is None
