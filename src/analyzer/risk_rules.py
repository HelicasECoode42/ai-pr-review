from __future__ import annotations

import re

from src.analyzer.diff_parser import parse_file_hunks
from src.models import ChangedFile, RiskFinding, Severity


RISK_PATH_PATTERNS = [
    re.compile(r"auth|permission|rbac|acl|login|session|jwt", re.IGNORECASE),
    re.compile(r"payment|billing|invoice|migration", re.IGNORECASE),
]

LINE_RULES: list[tuple[str, re.Pattern[str], Severity, str, str]] = [
    (
        "sql-string-concat",
        re.compile(r"(SELECT|INSERT|UPDATE|DELETE).*(\+|f\"|format\()", re.IGNORECASE),
        Severity.HIGH,
        "Possible SQL injection risk",
        "Use parameterized queries or the ORM query builder instead of string interpolation.",
    ),
    (
        "shell-execution",
        re.compile(r"(subprocess|os\.system|child_process\.exec|Runtime\.getRuntime)", re.IGNORECASE),
        Severity.HIGH,
        "Shell command execution changed",
        "Validate user-controlled input and avoid shell=True or string-built commands.",
    ),
    (
        "dynamic-execution",
        re.compile(r"\b(eval|exec)\s*\(", re.IGNORECASE),
        Severity.CRITICAL,
        "Dynamic code execution introduced",
        "Avoid dynamic execution or strictly sandbox and validate the input.",
    ),
    (
        "secret-logging",
        re.compile(r"(log|print|console\.).*(token|password|passwd|secret|api[_-]?key)", re.IGNORECASE),
        Severity.HIGH,
        "Potential secret logging",
        "Do not write credentials or secrets to logs. Mask sensitive values before logging.",
    ),
    (
        "swallowed-exception",
        re.compile(r"(except\s+.*:\s*$|catch\s*\(.*\)\s*\{?\s*$|pass\s*$)", re.IGNORECASE),
        Severity.MEDIUM,
        "Exception handling may hide failures",
        "Log enough context, rethrow when appropriate, or return an explicit error.",
    ),
    (
        "test-skip",
        re.compile(r"(pytest\.mark\.skip|describe\.skip|it\.skip|test\.skip|@Disabled)", re.IGNORECASE),
        Severity.MEDIUM,
        "Test skip introduced",
        "Avoid skipping tests in production branches unless the reason and follow-up are explicit.",
    ),
]


def scan_risks(files: list[ChangedFile]) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    for file in files:
        findings.extend(_scan_path_risk(file))
        findings.extend(_scan_line_rules(file))
        findings.extend(_scan_test_deletions(file))
    return findings


def _scan_path_risk(file: ChangedFile) -> list[RiskFinding]:
    for pattern in RISK_PATH_PATTERNS:
        if pattern.search(file.filename):
            return [
                RiskFinding(
                    file_path=file.filename,
                    severity=Severity.MEDIUM,
                    rule_id="risk-path",
                    title="High-risk area changed",
                    evidence=f"File path `{file.filename}` matches `{pattern.pattern}`.",
                    recommendation="Review authorization, data integrity, and rollback behavior carefully.",
                    confidence=0.6,
                )
            ]
    return []


def _is_test_file(filename: str) -> bool:
    return "test" in filename.lower() or filename.lower().startswith("tests/")


def _scan_line_rules(file: ChangedFile) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    for hunk in parse_file_hunks(file):
        for changed in hunk.added_lines:
            for rule_id, pattern, severity, title, recommendation in LINE_RULES:
                if not pattern.search(changed.content):
                    continue
                # Lower severity for test fixtures / mock data
                effective_severity = severity
                effective_confidence = 0.75
                if _is_test_file(file.filename) and rule_id == "secret-logging":
                    effective_severity = Severity.LOW
                    effective_confidence = 0.4
                findings.append(
                    RiskFinding(
                        file_path=file.filename,
                        line=changed.line,
                        severity=effective_severity,
                        rule_id=rule_id,
                        title=title,
                        evidence=changed.content.strip(),
                        recommendation=recommendation,
                        confidence=effective_confidence,
                    )
                )
    return findings


def _scan_test_deletions(file: ChangedFile) -> list[RiskFinding]:
    if "test" not in file.filename.lower() and "spec" not in file.filename.lower():
        return []
    deleted_assertions = 0
    for hunk in parse_file_hunks(file):
        deleted_assertions += sum(
            1
            for line in hunk.removed_lines
            if "assert" in line or "expect(" in line or "should" in line
        )
    if deleted_assertions == 0:
        return []
    return [
        RiskFinding(
            file_path=file.filename,
            severity=Severity.MEDIUM,
            rule_id="test-assertion-removed",
            title="Test assertions removed",
            evidence=f"{deleted_assertions} assertion-like deleted lines detected.",
            recommendation="Confirm coverage is replaced elsewhere or explain why the assertion is obsolete.",
            confidence=0.7,
        )
    ]
