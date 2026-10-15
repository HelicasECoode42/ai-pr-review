"""Two-tier scanning: sweep everything, deep-review only what is flagged."""

from __future__ import annotations

from src.analyzer.diff_parser import parse_file_hunks
from src.analyzer.tiered_scan import TriagePolicy, triage
from src.models import ChangedFile, RiskFinding

PLAIN = "@@ -10,3 +10,4 @@ def helper(x):\n ctx\n-old\n+new\n"
SENSITIVE = "@@ -10,3 +10,4 @@ def login(user):\n ctx\n-token = 1\n+token = load_secret()\n"


def bundle(*patches: tuple[str, str]):
    files = [ChangedFile(filename=path, patch=patch) for path, patch in patches]
    return files, {f.filename: parse_file_hunks(f) for f in files}


def finding(path: str, line: int) -> RiskFinding:
    return RiskFinding(file_path=path, line=line, severity="high", rule_id="r1",
                       title="t", evidence="e", recommendation="r", confidence=0.9)


def test_every_hunk_is_swept_whatever_is_flagged():
    files, hunks = bundle(("src/a.py", PLAIN), ("src/b.py", PLAIN))
    scan = triage(files, hunks, [])
    assert len(scan.swept) == 2
    assert scan.summary()["swept_share"] == 1.0


def test_a_rule_hit_reaches_deep_review():
    files, hunks = bundle(("src/a.py", PLAIN), ("src/b.py", PLAIN))
    scan = triage(files, hunks, [finding("src/b.py", 11)])
    flagged = {path for path, _hunk, _score, _reasons in scan.suspicious}
    assert "src/b.py" in flagged
    reasons = [r for _p, _h, _s, rs in scan.suspicious for r in rs]
    assert any(r.startswith("rule_hits") for r in reasons)


def test_sensitive_tokens_raise_a_hunk_above_the_threshold():
    files, hunks = bundle(("src/login.py", SENSITIVE))
    scan = triage(files, hunks, [])
    assert scan.summary()["hunks_deep_review"] == 1


def test_budget_caps_deep_review_and_the_rest_is_counted():
    files, hunks = bundle(("src/a.py", PLAIN), ("src/b.py", PLAIN))
    findings = [finding("src/a.py", 11), finding("src/b.py", 11)]
    scan = triage(files, hunks, findings, budget=1)
    summary = scan.summary()
    assert summary["hunks_deep_review"] == 1
    assert summary["not_deep_reasons"]["budget_exhausted"] == 1


def test_low_signal_hunks_are_accounted_for_not_ignored():
    files, hunks = bundle(("src/deep/inner.py", PLAIN))
    scan = triage(files, hunks, [])
    summary = scan.summary()
    assert summary["hunks_deep_review"] == 0
    assert summary["not_deep_reasons"] == {"below_threshold": 1}


def test_threshold_is_a_declared_policy_not_a_magic_number():
    policy = TriagePolicy()
    assert policy.score(rule_hits=0, sensitive=False, churn=0, public_interface=False) == 0.0
    assert policy.score(rule_hits=1, sensitive=False, churn=0, public_interface=False) == 10.0
