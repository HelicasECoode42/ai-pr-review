"""Canonical ReviewBench gold scope: one source of truth for the recall denominator.

The official gold JSON labels each finding with ``tp_fp`` (``tp``/``fp``) and
``scope`` (``introduced-by-pr`` / ``exacerbated-by-pr`` / ``pre-existing`` /
``unrelated``). Evaluation docs previously hand-wrote the split and drifted
(``11+3+2`` in the JSON versus ``12+2+2`` in an adjudication note). This module
derives the split from the labels themselves; callers must not hard-code it.

The project recall denominator is ``tp`` findings whose scope is
``introduced-by-pr`` or ``exacerbated-by-pr``. ``pre-existing`` findings are
reported separately and ``unrelated`` findings only exist on ``fp`` labels.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

INTRODUCED_BY_PR = "introduced-by-pr"
EXACERBATED_BY_PR = "exacerbated-by-pr"
PRE_EXISTING = "pre-existing"
UNRELATED = "unrelated"

PR_SCOPE_POSITIVE = (INTRODUCED_BY_PR, EXACERBATED_BY_PR)
KNOWN_SCOPES = (INTRODUCED_BY_PR, EXACERBATED_BY_PR, PRE_EXISTING, UNRELATED)
KNOWN_LABELS = ("tp", "fp")


class GoldContractError(ValueError):
    """The gold artifact does not match the fixed label contract."""


def _findings(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        value = value.get("findings")
    if not isinstance(value, list):
        raise GoldContractError("gold artifact must be a list or contain a findings list")
    return [item for item in value if isinstance(item, Mapping)]


def _label(finding: Mapping[str, Any], key: str, allowed: Iterable[str]) -> str:
    value = finding.get(key)
    if not isinstance(value, str):
        raise GoldContractError(f"gold finding is missing a string {key!r}")
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise GoldContractError(f"unsupported gold {key!r}: {value!r}")
    return normalized


def validate_gold_findings(value: Any) -> list[Mapping[str, Any]]:
    """Return the findings after checking every label against the contract."""
    findings = _findings(value)
    for index, finding in enumerate(findings):
        finding_label = _label(finding, "tp_fp", KNOWN_LABELS)
        scope = _label(finding, "scope", KNOWN_SCOPES)
        if finding_label == "tp" and scope == UNRELATED:
            raise GoldContractError(
                f"gold finding {index} is a true positive with scope 'unrelated'"
            )
    return findings


def summarize_gold_scope(value: Any) -> dict[str, Any]:
    """Count official labels and the PR-scope positive denominator."""
    findings = validate_gold_findings(value)
    labels = {"tp": 0, "fp": 0}
    scopes = {
        "introduced_by_pr": 0,
        "exacerbated_by_pr": 0,
        "pre_existing": 0,
        "unrelated": 0,
    }
    scope_keys = {
        INTRODUCED_BY_PR: "introduced_by_pr",
        EXACERBATED_BY_PR: "exacerbated_by_pr",
        PRE_EXISTING: "pre_existing",
        UNRELATED: "unrelated",
    }
    tp_scopes = {
        "introduced_by_pr": 0,
        "exacerbated_by_pr": 0,
        "pre_existing": 0,
        "unrelated": 0,
    }
    for finding in findings:
        label = _label(finding, "tp_fp", KNOWN_LABELS)
        scope = _label(finding, "scope", KNOWN_SCOPES)
        labels[label] += 1
        scopes[scope_keys[scope]] += 1
        if label == "tp":
            tp_scopes[scope_keys[scope]] += 1
    denominator = tp_scopes["introduced_by_pr"] + tp_scopes["exacerbated_by_pr"]
    return {
        "findings_total": len(findings),
        "official_labels": labels,
        "all_label_scope": scopes,
        "tp_scope": tp_scopes,
        "pr_scope_positive_denominator": denominator,
        "pre_existing_excluded": tp_scopes["pre_existing"],
        "scope_source": "official gold tp_fp/scope fields",
    }


def pr_scope_positive_indexes(value: Any) -> list[int]:
    """Indexes (in gold order) of true positives that PR-scope recall counts."""
    findings = validate_gold_findings(value)
    return [
        index
        for index, finding in enumerate(findings)
        if _label(finding, "tp_fp", KNOWN_LABELS) == "tp"
        and _label(finding, "scope", KNOWN_SCOPES) in PR_SCOPE_POSITIVE
    ]
