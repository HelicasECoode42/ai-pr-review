from __future__ import annotations

import re
import logging

from src.analyzer.diff_parser import parse_file_hunks
from src.models import ChangedFile, RiskFinding, Severity

logger = logging.getLogger(__name__)

# Files excluded from rule scanning (demo reports, generated artifacts)
_SKIP_SCAN_PREFIXES = (
    "docs/demo/",
    "reports/",
)


def _should_skip_scan(filename: str) -> bool:
    return filename.startswith(_SKIP_SCAN_PREFIXES)


def _detect_language(filename: str) -> str:
    """Detect programming language from file extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    _LANG_MAP = {
        "py": "python",
        "pyw": "python",
        "js": "javascript",
        "jsx": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "mts": "typescript",
        "cts": "typescript",
    }
    return _LANG_MAP.get(ext, "other")


# Rule IDs whose application requires a matching detected language
_LANG_REQUIRED_RULES: dict[str, str] = {
    "swallowed-exception-python": "python",
    "swallowed-exception-js": "javascript",
}


# Path risk patterns: each entry is (rule_id, severity, regex, title, recommendation, confidence)
_RISK_PATH_RULES: list[tuple[str, Severity, re.Pattern[str], str, str, float]] = [
    (
        "risk-path-auth",
        Severity.MEDIUM,
        re.compile(r"auth|permission|rbac|acl|login|session|jwt", re.IGNORECASE),
        "Auth/permission code changed",
        "Review authorization, data integrity, and rollback behavior carefully.",
        0.6,
    ),
    (
        "risk-path-payment",
        Severity.MEDIUM,
        re.compile(r"payment|billing|invoice|migration", re.IGNORECASE),
        "Payment/migration code changed",
        "Review financial logic, rollback behavior, and data integrity carefully.",
        0.6,
    ),
    (
        "risk-path-infra-workflow",
        Severity.HIGH,
        re.compile(r"^\.github/workflows/", re.IGNORECASE),
        "CI/CD workflow changed",
        "Workflow changes affect review pipeline stability. Verify fallback and gate logic.",
        0.7,
    ),
    (
        "risk-path-infra-reviewer",
        Severity.HIGH,
        re.compile(r"^src/cli/|^src/reviewer/|^src/github/", re.IGNORECASE),
        "Review tool infrastructure changed",
        "Changes to reviewer code may affect review stability, fallback behavior, "
        "or workflow gating. Request a second reviewer.",
        0.7,
    ),
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
        "swallowed-exception-python",
        re.compile(r"except\s+\w*\s*:\s*pass\s*$", re.IGNORECASE),
        Severity.MEDIUM,
        "Exception handling may hide failures (Python)",
        "Log enough context, rethrow when appropriate, or return an explicit error.",
    ),
    (
        "swallowed-exception-js",
        re.compile(r"catch\s*\(.*?\)\s*\{\s*\}"),
        Severity.MEDIUM,
        "Exception handling may hide failures (JS/TS)",
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


def scan_risks(files: list[ChangedFile] | None) -> list[RiskFinding]:
    """Run all risk scans: path rules, line regex rules, test deletions, and AST analysis."""
    if not files:
        return []
    findings: list[RiskFinding] = []
    for file in files:
        if _should_skip_scan(file.filename):
            continue
        try:
            findings.extend(_scan_path_risk(file))
            findings.extend(_scan_line_rules(file))
            findings.extend(_scan_test_deletions(file))
        except Exception as e:
            logger.warning(f"Failed to scan file {file.filename}: {e}")
            continue

    # ── AST rules (imported lazily to avoid circular deps) ──
    try:
        from src.analyzer.ast_rules import scan_ast_risks
        findings.extend(scan_ast_risks(files))
    except ImportError:
        logger.debug("AST rules module not available; skipping.")
    except Exception as e:
        logger.warning(f"AST rules scan failed: {e}")

    # ── Cross-file impact analysis ──
    try:
        from src.analyzer.cross_file import analyze_cross_file_impact
        findings.extend(analyze_cross_file_impact(files))
    except ImportError:
        logger.debug("Cross-file analysis module not available; skipping.")
    except Exception as e:
        logger.warning(f"Cross-file analysis failed: {e}")

    return findings


def _scan_path_risk(file: ChangedFile) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    for rule_id, severity, pattern, title, recommendation, confidence in _RISK_PATH_RULES:
        if pattern.search(file.filename):
            findings.append(
                RiskFinding(
                    file_path=file.filename,
                    severity=severity,
                    rule_id=rule_id,
                    title=title,
                    evidence=f"File path `{file.filename}` matches `{pattern.pattern}`.",
                    recommendation=recommendation,
                    confidence=confidence,
                )
            )
    return findings


def _is_test_file(filename: str) -> bool:
    return "test" in filename.lower() or filename.lower().startswith("tests/")


def _scan_line_rules(file: ChangedFile) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    try:
        hunks = parse_file_hunks(file)
    except Exception as e:
        logger.warning(f"Failed to parse hunks for {file.filename}: {e}")
        return []

    lang = _detect_language(file.filename)
    for hunk in hunks:
        for changed in hunk.added_lines:
            for rule_id, pattern, severity, title, recommendation in LINE_RULES:
                # Skip language-specific rules that don't match the detected language
                if rule_id in _LANG_REQUIRED_RULES and _LANG_REQUIRED_RULES[rule_id] != lang:
                    continue
                try:
                    if not pattern.search(changed.content):
                        continue
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
                except Exception:
                    # Single rule matching failed, skip this rule
                    continue
    return findings


def _scan_test_deletions(file: ChangedFile) -> list[RiskFinding]:
    if "test" not in file.filename.lower() and "spec" not in file.filename.lower():
        return []
    try:
        hunks = parse_file_hunks(file)
    except Exception as e:
        logger.warning(f"Failed to parse test file {file.filename}: {e}")
        return []

    deleted_assertions = 0
    for hunk in hunks:
        for line in hunk.removed_lines:
            try:
                if "assert" in line or "expect(" in line or "should" in line:
                    deleted_assertions += 1
            except Exception:
                continue
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
