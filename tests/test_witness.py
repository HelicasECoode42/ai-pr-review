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
    assert result.blocks_confirmation is False


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
