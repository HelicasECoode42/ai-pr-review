"""The two review prompt families must not drift through text patching."""

from __future__ import annotations

import inspect

from src.reviewer import grounded_review
from src.reviewer.prompt import (
    CROSS_FILE_ANCHOR_CONSTRAINT,
    CROSS_FILE_EVIDENCE_CONSTRAINT,
    DIFF_HORIZON_CLAUSE,
    EVIDENCE_EXCERPT_CONSTRAINT,
    SYSTEM_PROMPT,
    UNCHANGED_LINES_CONSTRAINT,
    UNTRUSTED_INPUT_CLAUSE,
    build_user_prompt,
)

# The exact wording the grounded path used to patch in with str.replace().
OLD_ANCHOR_LINE = "- Do not comment on unchanged lines or deletion-only lines."
OLD_EVIDENCE_LINE = (
    "- `evidence` must contain an exact non-empty excerpt from the suggestion's cited added line."
)


def test_cross_file_flag_selects_the_evidence_constraints():
    legacy = build_user_prompt("ctx", 3, "en")
    grounded = build_user_prompt("ctx", 3, "en", cross_file_evidence=True)

    assert UNCHANGED_LINES_CONSTRAINT in legacy
    assert EVIDENCE_EXCERPT_CONSTRAINT in legacy
    assert UNCHANGED_LINES_CONSTRAINT not in grounded
    assert EVIDENCE_EXCERPT_CONSTRAINT not in grounded
    assert CROSS_FILE_ANCHOR_CONSTRAINT in grounded
    assert CROSS_FILE_EVIDENCE_CONSTRAINT in grounded


def test_grounded_prompt_matches_the_replace_chain_it_replaced():
    """The refactor must not change one byte of what the model sees."""
    legacy = build_user_prompt("ctx", 5, "en")
    expected = legacy.replace(OLD_ANCHOR_LINE, CROSS_FILE_ANCHOR_CONSTRAINT).replace(
        OLD_EVIDENCE_LINE, CROSS_FILE_EVIDENCE_CONSTRAINT
    )
    assert build_user_prompt("ctx", 5, "en", cross_file_evidence=True) == expected


def test_language_and_max_suggestions_still_interpolate():
    prompt = build_user_prompt("the-context", 9, "zh")
    assert "the-context" in prompt
    assert "at most 9 suggestions" in prompt
    assert "Chinese (Simplified)" in prompt


def test_grounded_review_no_longer_patches_prompt_text():
    """A str.replace() patch silently no-ops once the wording changes."""
    source = inspect.getsource(grounded_review)
    assert "prompt.replace(" not in source


def test_both_families_declare_untrusted_input():
    assert UNTRUSTED_INPUT_CLAUSE in SYSTEM_PROMPT
    assert "untrusted data" in grounded_review.DISCOVERY_SYSTEM
    assert "untrusted data" in grounded_review.VERIFY_SYSTEM


def test_legacy_prompt_states_the_diff_horizon():
    assert DIFF_HORIZON_CLAUSE in SYSTEM_PROMPT
    # The grounded prompts state the same limits in their own words.
    assert "Never invent missing code" in grounded_review.DISCOVERY_SYSTEM
    assert "Absence of a check in a short excerpt does not prove absence" in (
        grounded_review.VERIFY_SYSTEM
    )
