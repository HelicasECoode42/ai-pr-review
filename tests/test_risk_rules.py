from src.analyzer.risk_rules import scan_risks
from src.models import ChangedFile, FileStatus, Severity


def test_scan_risks_detects_secret_logging() -> None:
    file = ChangedFile(
        filename="src/auth/session.py",
        status=FileStatus.MODIFIED,
        patch="""@@ -1,2 +1,3 @@
 def login(token):
+    print("token", token)
     return True""",
    )

    findings = scan_risks([file])

    assert any(f.rule_id == "secret-logging" for f in findings)
    assert any(f.severity == Severity.HIGH for f in findings)


def test_scan_risks_lowers_secret_logging_in_tests() -> None:
    file = ChangedFile(
        filename="tests/test_session.py",
        status=FileStatus.MODIFIED,
        patch="""@@ -1,2 +1,3 @@
def test_login(token):
+    print("token", token)
     assert True""",
    )

    findings = scan_risks([file])
    secret_findings = [f for f in findings if f.rule_id == "secret-logging"]

    assert secret_findings
    assert secret_findings[0].severity == Severity.LOW
    assert secret_findings[0].confidence == 0.4


def test_evaluation_quotes_are_not_executable_secret_logging() -> None:
    patch = '@@ -0,0 +1 @@\n+print("token", token)'
    files = [
        ChangedFile(filename="docs/evaluation/run/candidates.json", status=FileStatus.ADDED, patch=patch),
        ChangedFile(filename="docs/evaluation/check.py", status=FileStatus.ADDED, patch=patch),
    ]
    findings = scan_risks(files)
    assert not any(f.file_path.endswith(".json") for f in findings)
    assert any(f.file_path.endswith("check.py") and f.rule_id == "secret-logging" for f in findings)
