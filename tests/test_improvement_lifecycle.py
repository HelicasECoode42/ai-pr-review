import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.application.review import ReviewApplication
from src.application.strategy_policy import load_active_policy
from src.evaluation.improvement import (
    ImprovementError,
    budget_fingerprint,
    build_input_manifest,
    current_criteria_fingerprint,
    evaluate_gate,
    generate_candidate,
    promote_candidate,
    protocol_fingerprint,
    replay_candidate,
    rollback_policy,
    sha256_file,
)
from src.evaluation.runtime import source_identity
from src.models import PullRequest
from src.utils.config import Settings

DIFF = (
    "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-return 0\n+return 1\n"
)


class FakeProvider:
    def __init__(self, *, request_budget, max_output_tokens):
        self.usage_records = []
        self._request_budget = request_budget
        self._max_output_tokens = max_output_tokens
        self.http_attempts = 0

    def complete_json(self, system, user):
        self._request_budget.reserve(system, user)
        self.http_attempts = self._request_budget.http_attempts
        self.usage_records.append(
            {"usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}
        )
        return json.dumps({"summary": "No verified issue", "risk_level": "low", "suggestions": []})

    def close(self):
        pass


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def fixed_protocol():
    value = {
        "model": "fake",
        "budget_fingerprint": "",
        "criteria_fingerprint": current_criteria_fingerprint(),
        "runtime_fingerprint": source_identity()["reviewer_source_sha256"],
        "retry_policy": "none",
        "patch_and_repository_tokens": 12000,
        "max_output_tokens": 4096,
        "max_http_attempts_per_arm": 2,
        "max_total_http_attempts": 4,
        "max_input_utf8_bytes_per_arm": 12000,
        "timeout_seconds_per_arm": 120,
    }
    value["budget_fingerprint"] = budget_fingerprint(value)
    value["protocol_fingerprint"] = protocol_fingerprint(value)
    return value


def prepared_case(root: Path, case_id: str, repo: str, number: int):
    case_dir = root / "inputs" / case_id
    case_dir.mkdir(parents=True)
    metadata = {
        "nwo": repo,
        "pr_number": number,
        "title": "Change",
        "body": "",
        "base": "a" * 40,
        "head": "b" * 40,
    }
    write_json(case_dir / "pr.json", metadata)
    (case_dir / "diff.patch").write_text(DIFF, encoding="utf-8")
    return {
        "case_id": case_id,
        "metadata": metadata,
        "status": "ready",
        "input_dir": str(case_dir),
        "diff_sha256": hashlib.sha256(DIFF.encode()).hexdigest(),
    }


@pytest.fixture
def lifecycle(tmp_path):
    root = tmp_path / "dataset"
    dev = prepared_case(root, "dev-case", "org/dev", 1)
    holdout = prepared_case(root, "held-case", "org/held", 2)
    holdout_two = prepared_case(root, "held-case-2", "org/held-two", 3)
    split_registry_path = root / "split.json"
    write_json(
        split_registry_path,
        {
            "split_rule": "by repository",
            "dev": ["dev-case"],
            "holdout": ["held-case", "held-case-2"],
        },
    )
    prepared_path = root / "prepared.json"
    write_json(
        prepared_path,
        {"benchmark_commit": "c" * 40, "records": [dev, holdout, holdout_two]},
    )
    dev_manifest = build_input_manifest(
        prepared_path,
        split="dev",
        case_ids=["dev-case"],
        split_registry_path=split_registry_path,
    )
    dev_manifest_path = root / "dev-manifest.json"
    write_json(dev_manifest_path, dev_manifest)
    run_dir = root / "run"
    (run_dir / "candidates").mkdir(parents=True)
    (run_dir / "audits").mkdir()
    write_json(run_dir / "candidates" / "dev-case.json", {"findings": []})
    write_json(run_dir / "audits" / "dev-case.json", {"cost_usd": None})
    run_path = run_dir / "run.json"
    write_json(
        run_path,
        {
            "records": [{"case_id": "dev-case", "status": "success"}],
            "reviewer_commit": "1" * 40,
            "model": "fake",
        },
    )
    diagnoses_path = root / "diagnoses.json"
    write_json(
        diagnoses_path,
        {
            "split": "dev",
            "diagnoses": [
                {
                    "case_id": "dev-case",
                    "failure_tag": "missing_cross_file_evidence",
                    "run_artifact_sha256": sha256_file(run_path),
                }
            ],
        },
    )
    store = tmp_path / "store"
    candidate = generate_candidate(
        store,
        manifest_path=dev_manifest_path,
        run_path=run_path,
        diagnoses_path=diagnoses_path,
        split_registry_path=split_registry_path,
    )
    hold_manifest_path = root / "holdout-manifest.json"
    write_json(
        hold_manifest_path,
        build_input_manifest(
            prepared_path,
            split="holdout",
            case_ids=["held-case", "held-case-2"],
            split_registry_path=split_registry_path,
        ),
    )
    return store, candidate, hold_manifest_path, split_registry_path


def test_dev_only_allowlisted_candidate_and_replay_then_one_use_holdout(lifecycle):
    store, candidate, holdout_path, split_registry_path = lifecycle
    assert candidate["status"] == "candidate"
    assert candidate["policy"]["playbook_recall_k"] == 5
    assert "cross_file_contract_check_v1" not in json.dumps(candidate["policy"])
    protocol = fixed_protocol()
    attestation = {
        "previously_exposed_to_candidate_design": False,
        "split_by_repository": True,
        "attested_by": "reviewer",
    }
    result = replay_candidate(
        store,
        candidate_id=candidate["candidate_id"],
        manifest_path=holdout_path,
        provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
        protocol=protocol,
        provider_mode="fixture",
        split_registry_path=split_registry_path,
        holdout_attestation=attestation,
    )
    assert result["status"] == "complete"
    assert result["quality_status"] == "unadjudicated"
    assert (
        result["arms"]["baseline"][0]["prompt_sha256"]
        != result["arms"]["candidate"][0]["prompt_sha256"]
    )
    with pytest.raises(ImprovementError, match="already been consumed"):
        replay_candidate(
            store,
            candidate_id=candidate["candidate_id"],
            manifest_path=holdout_path,
            provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
            protocol=protocol,
            provider_mode="fixture",
            split_registry_path=split_registry_path,
            holdout_attestation=attestation,
        )


def test_manifest_cannot_relabel_holdout_as_dev(lifecycle):
    store, candidate, holdout_path, split_registry_path = lifecycle
    manifest = json.loads(holdout_path.read_text(encoding="utf-8"))
    manifest["split"] = "dev"
    unsigned = {key: value for key, value in manifest.items() if key != "input_set_sha256"}
    manifest["input_set_sha256"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    forged_path = holdout_path.with_name("relabelled-manifest.json")
    write_json(forged_path, manifest)

    with pytest.raises(ImprovementError, match="trusted split registry"):
        replay_candidate(
            store,
            candidate_id=candidate["candidate_id"],
            manifest_path=forged_path,
            provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
            protocol=fixed_protocol(),
            provider_mode="fixture",
            split_registry_path=split_registry_path,
        )


@pytest.mark.parametrize(
    ("mutation", "expected_reasons"),
    [
        ("drop", {"baseline_replay_case_set_mismatch", "candidate_replay_case_set_mismatch"}),
        ("duplicate", {"baseline_replay_duplicate_case", "candidate_replay_duplicate_case"}),
    ],
)
def test_gate_requires_exact_unique_manifest_case_sets(lifecycle, mutation, expected_reasons):
    store, candidate, holdout_path, split_registry_path = lifecycle
    result = replay_candidate(
        store,
        candidate_id=candidate["candidate_id"],
        manifest_path=holdout_path,
        provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
        protocol=fixed_protocol(),
        provider_mode="fixture",
        split_registry_path=split_registry_path,
        holdout_attestation={
            "previously_exposed_to_candidate_design": False,
            "split_by_repository": True,
            "attested_by": "reviewer",
        },
    )
    replay_path = store / "evaluations" / result["evaluation_id"] / "replay.json"
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    if mutation == "drop":
        replay["arms"]["baseline"].pop()
        replay["arms"]["candidate"].pop()
    else:
        replay["arms"]["baseline"].append(replay["arms"]["baseline"][0].copy())
        replay["arms"]["candidate"].append(replay["arms"]["candidate"][0].copy())
    write_json(replay_path, replay)

    gate = evaluate_gate(
        store,
        evaluation_id=result["evaluation_id"],
        adjudication={},
        quality={},
        coverage={},
        costs={},
        allow_fixture=True,
        split_registry_path=split_registry_path,
    )
    assert gate["decision"] == "reject"
    assert expected_reasons.issubset(set(gate["reasons"]))


def test_gate_binds_detail_to_its_case_and_arm(lifecycle):
    store, candidate, holdout_path, split_registry_path = lifecycle
    result = replay_candidate(
        store,
        candidate_id=candidate["candidate_id"],
        manifest_path=holdout_path,
        provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
        protocol=fixed_protocol(),
        provider_mode="fixture",
        split_registry_path=split_registry_path,
        holdout_attestation={
            "previously_exposed_to_candidate_design": False,
            "split_by_repository": True,
            "attested_by": "reviewer",
        },
    )
    detail_path = (
        store
        / "evaluations"
        / result["evaluation_id"]
        / "baseline"
        / "held-case.json"
    )
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    detail["arm"] = "candidate"
    write_json(detail_path, detail)
    gate = evaluate_gate(
        store,
        evaluation_id=result["evaluation_id"],
        adjudication={},
        quality={},
        coverage={},
        costs={},
        allow_fixture=True,
        split_registry_path=split_registry_path,
    )
    assert gate["decision"] == "reject"
    assert "baseline_persisted_replay_artifact_mismatch" in gate["reasons"]


def test_gate_needs_semantic_quality_coverage_and_measured_cost_before_promote(lifecycle):
    store, candidate, holdout_path, split_registry_path = lifecycle
    protocol = fixed_protocol()
    result = replay_candidate(
        store,
        candidate_id=candidate["candidate_id"],
        manifest_path=holdout_path,
        provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
        protocol=protocol,
        provider_mode="fixture",
        split_registry_path=split_registry_path,
        holdout_attestation={
            "previously_exposed_to_candidate_design": False,
            "split_by_repository": True,
            "attested_by": "reviewer",
        },
    )
    evaluation_id = result["evaluation_id"]
    replay_path = store / "evaluations" / evaluation_id / "replay.json"
    replay_sha = sha256_file(replay_path)
    identity = {
        "evaluation_id": evaluation_id,
        "replay_sha256": replay_sha,
        "input_set_sha256": result["input_set_sha256"],
        "protocol_fingerprint": protocol["protocol_fingerprint"],
    }
    adjudication = {
        **identity,
        "method": "independent_semantic",
        "raw_line_matching_proxy": False,
        "complete": True,
        "unadjudicated": 0,
        "adjudicator": "human-reviewer",
        "evidence_ref": "private-adjudication-record",
    }
    good = evaluate_gate(
        store,
        evaluation_id=evaluation_id,
        adjudication=adjudication,
        quality={
            **identity,
            "source_adjudication_sha256": hashlib.sha256(
                json.dumps(
                    adjudication, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest(),
            "arms": {
                "baseline": {"tp": 4, "fp": 1, "fn": 2, "failed_cases": 0},
                "candidate": {"tp": 4, "fp": 0, "fn": 2, "failed_cases": 0},
            },
        },
        coverage={
            **identity,
            "method": "measured_input_coverage",
            "evidence_ref": "coverage-review-record",
            "baseline": {
                "covered": 4,
                "total": 6,
                "cases": [
                    {"case_id": "held-case", "covered": 2, "total": 3},
                    {"case_id": "held-case-2", "covered": 2, "total": 3},
                ],
            },
            "candidate": {
                "covered": 5,
                "total": 6,
                "cases": [
                    {"case_id": "held-case", "covered": 2, "total": 3},
                    {"case_id": "held-case-2", "covered": 3, "total": 3},
                ],
            },
        },
        costs={
            **identity,
            "source": "provider_measured",
            "evidence_ref": "provider-usage-record",
            "pricing_basis": "provider_reported_unit_rates",
            "same_model_budget_retry_policy": True,
            "model": "fake",
            "budget_fingerprint": protocol["budget_fingerprint"],
            "retry_policy": "none",
            "arms": {
                "baseline": {
                    "cost_usd": 0.01,
                    "request_count": 2,
                    "prompt_tokens": 20,
                    "completion_tokens": 6,
                },
                "candidate": {
                    "cost_usd": 0.01,
                    "request_count": 2,
                    "prompt_tokens": 20,
                    "completion_tokens": 6,
                },
            },
        },
        allow_fixture=True,
        split_registry_path=split_registry_path,
    )
    assert good["decision"] == "eligible_for_human_promotion"
    version = promote_candidate(store, candidate["candidate_id"], evaluation_id, allow_fixture=True)
    loaded = load_active_policy(store)
    assert loaded.status == "active" and loaded.policy_id == version
    assert loaded.policy.prompt_addendum
    rolled = rollback_policy(store, "baseline")
    assert rolled.status == "baseline"
    candidate_path = store / "candidates" / f"{candidate['candidate_id']}.json"
    altered = json.loads(candidate_path.read_text(encoding="utf-8"))
    altered["policy"]["prompt_addendum"] = ""
    write_json(candidate_path, altered)
    with pytest.raises(ImprovementError, match="canonical policy/provenance"):
        promote_candidate(store, candidate["candidate_id"], evaluation_id, allow_fixture=True)


def test_unknown_cost_or_line_proxy_rejects_gate(lifecycle):
    store, candidate, holdout_path, split_registry_path = lifecycle
    result = replay_candidate(
        store,
        candidate_id=candidate["candidate_id"],
        manifest_path=holdout_path,
        provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
        protocol=fixed_protocol(),
        provider_mode="fixture",
        split_registry_path=split_registry_path,
        holdout_attestation={
            "previously_exposed_to_candidate_design": False,
            "split_by_repository": True,
            "attested_by": "reviewer",
        },
    )
    replay_path = store / "evaluations" / result["evaluation_id"] / "replay.json"
    replay_sha = sha256_file(replay_path)
    identity = {
        "evaluation_id": result["evaluation_id"],
        "replay_sha256": replay_sha,
        "input_set_sha256": result["input_set_sha256"],
        "protocol_fingerprint": result["protocol"]["protocol_fingerprint"],
    }
    gate = evaluate_gate(
        store,
        evaluation_id=result["evaluation_id"],
        adjudication={
            **identity,
            "method": "raw_line_matching",
            "raw_line_matching_proxy": True,
            "complete": True,
            "unadjudicated": 0,
            "adjudicator": "test",
            "evidence_ref": "test",
        },
        quality={
            **identity,
            "source_adjudication_sha256": "0" * 64,
            "arms": {
                "baseline": {"tp": 4, "fp": 1, "fn": 2, "failed_cases": 0},
                "candidate": {"tp": 4, "fp": 0, "fn": 2, "failed_cases": 0},
            },
        },
        coverage={
            **identity,
            "method": "measured_input_coverage",
            "evidence_ref": "test",
            "baseline": {
                "covered": 4,
                "total": 6,
                "cases": [
                    {"case_id": "held-case", "covered": 2, "total": 3},
                    {"case_id": "held-case-2", "covered": 2, "total": 3},
                ],
            },
            "candidate": {
                "covered": 5,
                "total": 6,
                "cases": [
                    {"case_id": "held-case", "covered": 2, "total": 3},
                    {"case_id": "held-case-2", "covered": 3, "total": 3},
                ],
            },
        },
        costs={
            **identity,
            "source": "unknown",
            "arms": {"baseline": {"cost_usd": None}, "candidate": {"cost_usd": None}},
        },
    )
    assert gate["decision"] == "reject"
    assert "semantic_adjudication_not_independent" in gate["reasons"]
    assert "measured_cost_unknown" in gate["reasons"]
    with pytest.raises(ImprovementError, match="passing gate"):
        promote_candidate(store, candidate["candidate_id"], result["evaluation_id"])


def test_promoted_fixture_policy_is_loaded_by_review_application_then_rolled_back(lifecycle):
    store, candidate, holdout_path, split_registry_path = lifecycle
    protocol = fixed_protocol()
    replay = replay_candidate(
        store,
        candidate_id=candidate["candidate_id"],
        manifest_path=holdout_path,
        provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
        protocol=protocol,
        provider_mode="fixture",
        split_registry_path=split_registry_path,
        holdout_attestation={
            "previously_exposed_to_candidate_design": False,
            "split_by_repository": True,
            "attested_by": "test-only-human",
        },
    )
    replay_path = store / "evaluations" / replay["evaluation_id"] / "replay.json"
    replay_sha = sha256_file(replay_path)
    identity = {
        "evaluation_id": replay["evaluation_id"],
        "replay_sha256": replay_sha,
        "input_set_sha256": replay["input_set_sha256"],
        "protocol_fingerprint": protocol["protocol_fingerprint"],
    }
    adjudication = {
        **identity,
        "method": "independent_semantic",
        "raw_line_matching_proxy": False,
        "complete": True,
        "unadjudicated": 0,
        "adjudicator": "test-only-human",
        "evidence_ref": "test-only-case-review",
    }
    gate = evaluate_gate(
        store,
        evaluation_id=replay["evaluation_id"],
        adjudication=adjudication,
        quality={
            **identity,
            "source_adjudication_sha256": hashlib.sha256(
                json.dumps(
                    adjudication, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest(),
            "arms": {
                "baseline": {"tp": 4, "fp": 1, "fn": 2, "failed_cases": 0},
                "candidate": {"tp": 4, "fp": 0, "fn": 2, "failed_cases": 0},
            },
        },
        coverage={
            **identity,
            "method": "measured_input_coverage",
            "evidence_ref": "test-only-coverage",
            "baseline": {
                "covered": 4,
                "total": 6,
                "cases": [
                    {"case_id": "held-case", "covered": 2, "total": 3},
                    {"case_id": "held-case-2", "covered": 2, "total": 3},
                ],
            },
            "candidate": {
                "covered": 5,
                "total": 6,
                "cases": [
                    {"case_id": "held-case", "covered": 2, "total": 3},
                    {"case_id": "held-case-2", "covered": 3, "total": 3},
                ],
            },
        },
        costs={
            **identity,
            "source": "provider_measured",
            "evidence_ref": "test-only-usage",
            "pricing_basis": "provider_reported_unit_rates",
            "same_model_budget_retry_policy": True,
            "model": "fake",
            "budget_fingerprint": protocol["budget_fingerprint"],
            "retry_policy": "none",
            "arms": {
                "baseline": {
                    "cost_usd": 0.01,
                    "request_count": 2,
                    "prompt_tokens": 20,
                    "completion_tokens": 6,
                },
                "candidate": {
                    "cost_usd": 0.01,
                    "request_count": 2,
                    "prompt_tokens": 20,
                    "completion_tokens": 6,
                },
            },
        },
        allow_fixture=True,
        split_registry_path=split_registry_path,
    )
    assert gate["decision"] == "eligible_for_human_promotion"
    version = promote_candidate(
        store, candidate["candidate_id"], replay["evaluation_id"], allow_fixture=True
    )

    prompts = []
    provider = Mock()
    provider.usage_records = []

    def engine(**kwargs):
        prompts.append(kwargs["prompt_addendum"])
        return SimpleNamespace(metrics={}, suggestions=[], analysis_warnings=[])

    application = ReviewApplication(
        Settings(
            _env_file=None,
            openai_api_key="fixture-only",
            review_model="fake",
            strategy_policy_store_dir=str(store),
        ),
        provider_factory=Mock(return_value=provider),
        engine=engine,
    )
    pr = PullRequest(repo="org/held", number=2, title="Change", head_sha="b" * 40)
    application.review(pr, [], [])
    assert prompts[-1] == candidate["policy"]["prompt_addendum"]
    rollback_policy(store, "baseline")
    application.review(pr, [], [])
    assert prompts[-1] == ""
    assert load_active_policy(store).status == "baseline"
    assert version
