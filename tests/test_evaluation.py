import json

import pytest

from src.evaluation.improvement import ReviewStrategy, improve
from src.evaluation.reviewbench import parse_diff, run_review
from src.evaluation.scoring import GoldenCase, accept_candidate, score, validate_splits

DIFF = (
    "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
    "@@ -1 +1 @@\n-return None\n+return token\n"
)
META = {
    "nwo": "fixture/demo",
    "pr_number": 1,
    "title": "Change",
    "base": "a" * 40,
    "head": "b" * 40,
}


class Provider:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def complete_json(self, system_prompt, user_prompt):
        self.prompts.append(user_prompt)
        return json.dumps(self.payload)


def test_maximum_matching_and_duplicate_predictions():
    result = score(3, ["a", "b"], [(0, "a"), (0, "b"), (1, "a"), (2, "a")])
    assert (result["tp"], result["fp"], result["fn"]) == (2, 1, 0)
    assert result["precision"] == 2 / 3
    assert score(0, ["a"], [])["recall"] == 0
    assert score(0, [], [])["precision"] is None
    with pytest.raises(ValueError):
        score(1, ["a"], [(1, "a")])


def test_diff_paths_and_counts():
    file = parse_diff(DIFF)[0]
    assert (file.filename, file.additions, file.deletions) == ("app.py", 1, 1)
    with pytest.raises(ValueError):
        parse_diff(DIFF.replace("b/app.py", "b/../app.py"))
    with pytest.raises(ValueError):
        parse_diff("diff --git a/logo.png b/logo.png\nBinary files differ")
    quoted = DIFF.replace("--- a/app.py", '--- "a/\\303\\251.py"').replace(
        "+++ b/app.py", '+++ "b/\\303\\251.py"'
    )
    assert parse_diff(quoted)[0].filename == "é.py"


def test_success_contract_and_no_reviewer_repository_context():
    provider = Provider({"summary": "No bugs", "risk_level": "low", "suggestions": []})
    output, audit = run_review(META, DIFF, provider)
    assert output["findings"] == []
    assert output["pr"]["head"] == META["head"]
    assert "Function Index" not in provider.prompts[0]
    assert audit["cost_usd"] is None


def test_failure_is_not_empty_success():
    class Broken:
        def complete_json(self, *args):
            raise RuntimeError("unavailable")

    with pytest.raises(RuntimeError, match="failed or degraded"):
        run_review(META, DIFF, Broken())


def test_splits_and_negative_labels():
    data = {
        "case_id": "one",
        "source": "synthetic",
        "split": "dev",
        "group": "repo",
        "metadata": META,
        "diff": DIFF,
        "findings": [],
        "label_provenance": "reviewed",
    }
    with pytest.raises(ValueError):
        GoldenCase(**data)
    case = GoldenCase(**data, negative_verified=True)
    with pytest.raises(ValueError, match="leaks"):
        validate_splits([case, case.model_copy(update={"case_id": "two", "split": "test"})])


def test_improvement_cannot_trade_recall_for_precision():
    baseline = {"precision": 0.6, "recall": 0.6, "f1": 0.6, "latency_seconds": 1, "failed_cases": 0}
    worse = {"precision": 0.9, "recall": 0.4, "f1": 0.7, "latency_seconds": 1, "failed_cases": 0}
    assert not accept_candidate(baseline, worse)
    assert not accept_candidate(baseline, {**baseline, "f1": float("nan")})
    better = {**baseline, "precision": 0.7, "f1": 0.65}
    assert accept_candidate(baseline, better)
    strategy = ReviewStrategy()
    result = improve(
        strategy, [], Provider(strategy.model_dump()), lambda candidate, split: baseline
    )
    assert result["decision"] == "rollback"
    with pytest.raises(ValueError):
        improve(strategy, [], Provider({}), lambda *args: better, split="test")
    with pytest.raises(ValueError):
        ReviewStrategy.model_validate({"judge_prompt": "cheat"})


def test_evidence_ablation_changes_only_gate():
    suggestion = {
        "file_path": "app.py",
        "line": 1,
        "severity": "high",
        "confidence": 0.9,
        "title": "Leak",
        "reason": "Returns a token",
        "recommendation": "Redact",
        "failure_scenario": "Return exposes a secret",
        "evidence": ["not in diff"],
    }
    payload = {"summary": "Risk", "risk_level": "high", "suggestions": [suggestion]}
    gated, _ = run_review(META, DIFF, Provider(payload), evidence=True)
    ungated, _ = run_review(META, DIFF, Provider(payload), evidence=False)
    assert gated["findings"] == []
    assert len(ungated["findings"]) == 1


def test_target_context_is_bounded_and_not_external_symlink(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use bounded retries")
    provider = Provider({"summary": "No bugs", "risk_level": "low", "suggestions": []})
    run_review(META, DIFF, provider, context_pack=True, project_root=tmp_path)
    assert "Use bounded retries" in provider.prompts[0]
    provider.prompts.clear()
    run_review(META, DIFF, provider, context_pack=False, project_root=tmp_path)
    assert "Use bounded retries" not in provider.prompts[0]


def test_critic_unverified_is_failed_experiment():
    payload = {
        "summary": "Risk",
        "risk_level": "high",
        "suggestions": [
            {
                "file_path": "app.py",
                "line": 1,
                "severity": "high",
                "confidence": 0.9,
                "title": "Leak",
                "reason": "Returns a token",
                "recommendation": "Redact",
                "failure_scenario": "Return exposes a secret",
                "evidence": ["return token"],
            }
        ],
    }

    class MissingCritic:
        calls = 0

        def complete_json(self, *args):
            self.calls += 1
            return json.dumps(payload if self.calls == 1 else {"items": []})

    with pytest.raises(RuntimeError, match="Critic degraded"):
        run_review(META, DIFF, MissingCritic(), critic=True)


def test_failed_and_unadjudicated_runs_do_not_publish_partial_quality():
    from src.evaluation.adjudicate import summarize

    dataset = [{"case_id": "one", "findings": [{"id": "bug"}]}]
    experiment = {"records": [{"case_id": "one", "variant": "baseline", "status": "failed"}]}
    assert summarize(dataset, experiment, [])["variants"]["baseline"]["precision"] is None
    experiment["records"][0].update(status="success", output={"findings": [{}]})
    assert summarize(dataset, experiment, [])["variants"]["baseline"]["recall"] is None
    annotations = [
        {
            "case_id": "one",
            "variant": "baseline",
            "reviewer": "independent",
            "exhaustive": True,
            "matches": [[0, "bug"]],
        }
    ]
    assert summarize(dataset, experiment, annotations)["variants"]["baseline"]["f1"] == 1
