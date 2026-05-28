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
