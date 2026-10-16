"""Two-tier scanning: full sweep, selective deep review, rule hits never missed."""

from __future__ import annotations

from src.analyzer.diff_parser import parse_file_hunks
from src.analyzer.tiered_scan import TriagePolicy, triage
from src.models import ChangedFile, RiskFinding

PLAIN = "@@ -10,3 +10,4 @@ def helper(x):\n ctx\n-old\n+new\n"
RISKY = "@@ -10,3 +10,4 @@ def run(cmd):\n ctx\n-old\n+exec(cmd)\n"
TOPIC = "@@ -10,3 +10,4 @@ def call(client):\n ctx\n-old\n+token = 1\n"


def bundle(*patches: tuple[str, str]):
    files = [ChangedFile(filename=path, patch=patch) for path, patch in patches]
    return files, {f.filename: parse_file_hunks(f) for f in files}


def finding(path: str, line: int) -> RiskFinding:
    # A suspicion rule: structural and path rules are context, not triggers.
    return RiskFinding(file_path=path, line=line, severity="high", rule_id="ast-broad-except",
                       title="t", evidence="e", recommendation="r", confidence=0.9)


def test_every_hunk_is_swept_whatever_is_flagged():
    files, hunks = bundle(("src/a.py", PLAIN), ("src/b.py", PLAIN))
    scan = triage(files, hunks, [])
    assert len(scan.swept) == 2
    assert scan.summary()["swept_share"] == 1.0


def test_a_rule_hit_always_reaches_deep_review():
    files, hunks = bundle(("src/a.py", PLAIN), ("src/b.py", PLAIN))
    scan = triage(files, hunks, [finding("src/b.py", 11)])
    assert "src/b.py" in {path for path, _h, _s, _r in scan.suspicious}


def test_a_rule_hit_survives_a_tight_budget():
    files, hunks = bundle(("src/a.py", PLAIN), ("src/b.py", PLAIN))
    scan = triage(files, hunks, [finding("src/b.py", 11)], budget=1)
    assert [path for path, _h, _s, _r in scan.suspicious] == ["src/b.py"]


def test_a_dangerous_operation_flags_but_a_topic_word_does_not():
    risky_files, risky_hunks = bundle(("src/run.py", RISKY))
    assert triage(risky_files, risky_hunks, []).summary()["hunks_deep_review"] == 1
    # "token" is a topic word in this repository, not a risky operation.
    topic_files, topic_hunks = bundle(("src/deep/call.py", TOPIC))
    assert triage(topic_files, topic_hunks, []).summary()["hunks_deep_review"] == 0


def test_churn_and_publicness_alone_never_flag_a_hunk():
    big = "@@ -1,200 +1,200 @@\n" + " ctx\n" * 200
    files, hunks = bundle(("src/api.py", big))
    summary = triage(files, hunks, []).summary()
    assert summary["hunks_deep_review"] == 0
    assert summary["not_deep_reasons"] == {"below_threshold": 1}


def test_budget_trimming_is_counted_not_hidden():
    files, hunks = bundle(("src/a.py", RISKY), ("src/b.py", RISKY))
    summary = triage(files, hunks, [], budget=1).summary()
    assert summary["hunks_deep_review"] == 1
    assert summary["not_deep_reasons"]["budget_exhausted"] == 1


def test_the_policy_is_declared_not_magic():
    policy = TriagePolicy()
    assert policy.score(rule_hits=0, risky=False, churn=0, public_interface=False) == 0.0
    assert policy.score(rule_hits=0, risky=True, churn=0, public_interface=False) == 10.0
    assert policy.score(rule_hits=1, risky=False, churn=0, public_interface=False) == 10.0


def test_a_diff_that_fits_the_budget_is_reviewed_entirely():
    """Triage chooses when the diff does not fit; if it fits, nothing is skipped."""
    files, hunks = bundle(("src/a.py", PLAIN), ("src/b.py", PLAIN))
    summary = triage(files, hunks, [], budget=1000).summary()
    assert summary["hunks_deep_review"] == summary["hunks_swept"] == 2
    assert "fits_budget" in {reason for _p, _h, _s, rs in
                             triage(files, hunks, [], budget=1000).suspicious for reason in rs}


def test_structural_and_path_rules_are_context_not_triggers():
    from src.analyzer.tiered_scan import is_suspicion_finding

    assert is_suspicion_finding("cross-file-call-impact") is False
    assert is_suspicion_finding("risk-path-infra-reviewer") is False
    assert is_suspicion_finding("ast-broad-except") is True
    assert is_suspicion_finding("sql-string-concat") is True


def test_a_context_rule_does_not_pull_a_hunk_into_deep_review():
    files, hunks = bundle(("src/infra/deep.py", PLAIN))
    context_rule = RiskFinding(file_path="src/infra/deep.py", line=11, severity="medium",
                               rule_id="risk-path-infra-reviewer", title="t", evidence="e",
                               recommendation="r", confidence=0.5)
    summary = triage(files, hunks, [context_rule]).summary()
    assert summary["hunks_deep_review"] == 0
    assert summary["not_deep_reasons"]["context_signal_not_a_trigger"] == 1
