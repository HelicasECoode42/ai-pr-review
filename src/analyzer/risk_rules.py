from __future__ import annotations

import re
import logging

from src.analyzer.diff_parser import parse_file_hunks
from src.models import ChangedFile, RiskFinding, Severity
from src.utils import detect_programming_language
from src.utils.rule_loader import load_rules

logger = logging.getLogger(__name__)

# Files excluded from rule scanning (demo reports, generated artifacts)
_SKIP_SCAN_PREFIXES = (
    "docs/demo/",
    "reports/",
)


def _should_skip_scan(filename: str) -> bool:
    return filename.startswith(_SKIP_SCAN_PREFIXES)




# Rule IDs whose application requires a matching detected language
_LANG_REQUIRED_RULES: dict[str, str] = {
    "swallowed-exception-python": "python",
    "swallowed-exception-js": "javascript",
}

# Runtime rule storage — loaded from YAML via rule_loader
_RISK_PATH_RULES: list = []
_LINE_RULES_RUNTIME: list = []
_DISABLED_RULES: set[str] = set()


def _ensure_rules_loaded() -> None:
    """Load rules from YAML if not already loaded."""
    global _RISK_PATH_RULES, _LINE_RULES_RUNTIME, _DISABLED_RULES
    if not _RISK_PATH_RULES:
        path_rules, line_rules, disabled = load_rules()
        _RISK_PATH_RULES[:] = path_rules
        _LINE_RULES_RUNTIME[:] = line_rules
        _DISABLED_RULES.clear()
        _DISABLED_RULES.update(disabled)


def reload_rules(user_rules_path: str | None = None) -> None:
    """Reload rules from YAML, optionally with a user override file."""
    global _RISK_PATH_RULES, _LINE_RULES_RUNTIME, _DISABLED_RULES
    path_rules, line_rules, disabled = load_rules(user_rules_path)
    _RISK_PATH_RULES[:] = path_rules
    _LINE_RULES_RUNTIME[:] = line_rules
    _DISABLED_RULES.clear()
    _DISABLED_RULES.update(disabled)



def scan_risks(files: list[ChangedFile] | None) -> list[RiskFinding]:
    """Run all risk scans: path rules, line regex rules, test deletions, and AST analysis."""
    _ensure_rules_loaded()
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
        if rule_id in _DISABLED_RULES:
            continue
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

    lang = detect_programming_language(file.filename)
    for hunk in hunks:
        for changed in hunk.added_lines:
            for rule_id, pattern, severity, title, recommendation in _LINE_RULES_RUNTIME:
                if rule_id in _DISABLED_RULES:
                    continue
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
