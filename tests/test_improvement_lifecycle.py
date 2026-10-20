import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import src.evaluation.improvement as improvement_module
from src.application.review import ReviewApplication
from src.application.strategy_policy import PROMPT_TEMPLATES, load_active_policy
from src.evaluation.improvement import (
    ImprovementError,
    budget_fingerprint,
    build_input_manifest,
    current_criteria_fingerprint,
    derive_trace_diagnoses,
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
    prepared_path = root / "prepared.json"
    write_json(
        prepared_path,
        {"benchmark_commit": "c" * 40, "records": [dev, holdout, holdout_two]},
    )
    split_registry_path = root / "split.json"
    write_json(
        split_registry_path,
        {
            "split_rule": "by repository",
            "prepared_sha256": sha256_file(prepared_path),
            "holdout_status": "fresh_authorized",
            "test_status": "not_authorized",
            "dev": ["dev-case"],
            "holdout": ["held-case", "held-case-2"],
        },
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
    (run_dir / "traces").mkdir()
    write_json(run_dir / "candidates" / "dev-case.json", {"findings": []})
    write_json(run_dir / "audits" / "dev-case.json", {"cost_usd": None})
    dev_record = dev_manifest["records"][0]
    trace = {
        "schema_version": 1,
        "identity": {
            "benchmark_case_id": "dev-case",
            "repository": "org/dev",
            "pr_number": 1,
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "prepared_metadata_sha256": dev_record["metadata_sha256"],
            "prepared_diff_sha256": dev_record["diff_sha256"],
            "input_set_sha256": dev_manifest["input_set_sha256"],
        },
        "policy": {
            "policy_id": "baseline",
            "status": "baseline",
            "effective_policy_sha256": hashlib.sha256(
                json.dumps(
                    {"prompt_addendum": "", "playbook_recall_k": 5},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
        },
        "criteria_sha256": current_criteria_fingerprint(),
        "runtime_sha256": "1" * 64,
        "prompt_sha256": "2" * 64,
        "intent": {"hypotheses": [], "tasks": []},
        "evidence_refs": [
            {
                "evidence_ref_id": "code-ref",
                "status": "available_in_report",
                "repo_sha": "b" * 40,
                "path": "src/app.py",
                "start_line": 1,
                "end_line": 2,
                "content_sha256": "3" * 64,
            }
        ],
        "mechanical_failures": [],
        "omitted_tasks": [],
    }
    trace["trace_sha256"] = hashlib.sha256(
        json.dumps(trace, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    trace_path = run_dir / "traces" / "dev-case.json"
    write_json(trace_path, trace)
    run_path = run_dir / "run.json"
    write_json(
        run_path,
        {
            "records": [
                {
                    "case_id": "dev-case",
                    "status": "success",
                    "review_trace_path": "traces/dev-case.json",
                    "review_trace_sha256": sha256_file(trace_path),
                }
            ],
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
                    "trace_sha256": sha256_file(trace_path),
                    "source": "human_adjudication",
                    "adjudicator": "test-reviewer",
                    "evidence_ref": "code-ref",
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


def test_rsi_rejects_missing_evidence_followup_source_trace(lifecycle, tmp_path):
    _store, _candidate, _holdout, split_registry_path = lifecycle
    dataset = tmp_path / "dataset"
    run_dir = dataset / "run"
    trace_path = run_dir / "traces" / "dev-case.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace["policy"]["grounded_missing_evidence_followup"] = True
    unsigned = {key: value for key, value in trace.items() if key != "trace_sha256"}
    trace["trace_sha256"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        .encode("utf-8")
    ).hexdigest()
    write_json(trace_path, trace)
    run_path = run_dir / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["records"][0]["review_trace_sha256"] = sha256_file(trace_path)
    write_json(run_path, run)

    diagnosis = derive_trace_diagnoses(
        run_path=run_path,
        manifest_path=dataset / "dev-manifest.json",
        split_registry_path=split_registry_path,
    )

    assert diagnosis["diagnoses"] == []
    assert diagnosis["unavailable"][0]["reason"].startswith("unsupported_source_strategy")


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


def test_manifest_cannot_self_sign_a_different_repository_group(lifecycle):
    store, candidate, holdout_path, split_registry_path = lifecycle
    manifest = json.loads(holdout_path.read_text(encoding="utf-8"))
    manifest["records"][0]["group"] = "attacker/other-repository"
    unsigned = {key: value for key, value in manifest.items() if key != "input_set_sha256"}
    manifest["input_set_sha256"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    forged_path = holdout_path.with_name("forged-group-manifest.json")
    write_json(forged_path, manifest)

    with pytest.raises(ImprovementError, match="trusted prepared input records"):
        replay_candidate(
            store,
            candidate_id=candidate["candidate_id"],
            manifest_path=forged_path,
            provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
            protocol=fixed_protocol(),
            provider_mode="fixture",
            split_registry_path=split_registry_path,
        )


def test_overlapping_holdout_subset_consumption_is_rejected(lifecycle):
    store, candidate, holdout_path, split_registry_path = lifecycle
    manifest = json.loads(holdout_path.read_text(encoding="utf-8"))
    one_case = dict(manifest)
    one_case["records"] = [manifest["records"][0]]
    one_case["case_count"] = 1
    unsigned = {key: value for key, value in one_case.items() if key != "input_set_sha256"}
    one_case["input_set_sha256"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    subset_path = holdout_path.with_name("one-case-holdout.json")
    write_json(subset_path, one_case)
    attestation = {
        "previously_exposed_to_candidate_design": False,
        "split_by_repository": True,
        "attested_by": "reviewer",
    }
    replay_candidate(
        store,
        candidate_id=candidate["candidate_id"],
        manifest_path=subset_path,
        provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
        protocol=fixed_protocol(),
        provider_mode="fixture",
        split_registry_path=split_registry_path,
        holdout_attestation=attestation,
    )
    with pytest.raises(ImprovementError, match="already been consumed"):
        replay_candidate(
            store,
            candidate_id=candidate["candidate_id"],
            manifest_path=holdout_path,
            provider_factory=lambda *_, **kwargs: FakeProvider(**kwargs),
            protocol=fixed_protocol(),
            provider_mode="fixture",
            split_registry_path=split_registry_path,
            holdout_attestation=attestation,
        )


def test_host_recorded_budget_omission_derives_bounded_prompt_candidate(lifecycle):
    store, _candidate, holdout_path, split_registry_path = lifecycle
    dataset = holdout_path.parent
    run_path = dataset / "run" / "run.json"
    trace_path = dataset / "run" / "traces" / "dev-case.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace["intent"]["hypotheses"] = [
        {"id": "h1", "source_status": "known", "source_refs": ["diff:src/app.py"]}
    ]
    trace["intent"]["omitted_tasks"] = [
        {
            "task_id": "task-1",
            "hypothesis_id": "h1",
            "reason": "task_limit",
            "omission_reason": "host_task_cap",
            "request_kind": "search",
            "status": "not_executed_budget",
            "valid_source_refs": ["diff:src/app.py"],
        }
    ]
    trace["mechanical_failures"] = [
        {
            "failure_id": "task-1",
            "failure_tag": "cross_file_task_budget_omitted",
            "reason": "task_limit",
            "hypothesis_id": "h1",
            "task_id": "task-1",
            "valid_source_refs": ["diff:src/app.py"],
            "omission_reason": "host_task_cap",
        }
    ]
    trace.pop("trace_sha256")
    trace["trace_sha256"] = hashlib.sha256(
        json.dumps(trace, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    write_json(trace_path, trace)
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["records"][0]["review_trace_sha256"] = sha256_file(trace_path)
    write_json(run_path, run)

    diagnosis = derive_trace_diagnoses(
        run_path=run_path,
        manifest_path=dataset / "dev-manifest.json",
        split_registry_path=split_registry_path,
    )
    assert diagnosis["status"] == "ready"
    assert diagnosis["diagnoses"][0]["failure_tag"] == "cross_file_task_budget_omitted"
    diagnosis_path = dataset / "derived-diagnoses.json"
    write_json(diagnosis_path, diagnosis)
    generated = generate_candidate(
        store,
        manifest_path=dataset / "dev-manifest.json",
        run_path=run_path,
        diagnoses_path=diagnosis_path,
        split_registry_path=split_registry_path,
    )
    assert generated["policy"]["prompt_addendum"] == PROMPT_TEMPLATES[
        "bounded_cross_file_evidence_v1"
    ]


def test_production_paired_runner_trace_drives_offline_cycle_cli(tmp_path, monkeypatch, capsys):
    # Exercise paired's real run_review, frozen GitSnapshot, trace writer, and cycle consumer.
    import sys
    from types import SimpleNamespace

    sys.path.insert(0, str(Path(__file__).parent))
    import test_grounded_review as grounded_test

    import src.evaluation.paired as paired_module

    mirrors = tmp_path / "mirrors"
    repo = mirrors / "fixture_repo"
    repo.mkdir(parents=True)

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE
        ).strip()

    git("init")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (repo / "users.py").write_text('def get_profile():\n    return ("name", 42)\n')
    (repo / "caller.py").write_text(
        "from users import get_profile\n"
        "def render():\n    name, age = get_profile()\n    return age + 1\n"
    )
    git("add", ".")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    (repo / "users.py").write_text(
        'def get_profile():\n    return {"name": "name", "age": 42}\n'
    )
    git("add", ".")
    git("commit", "-m", "head")
    head = git("rev-parse", "HEAD")
    diff = git("diff", base, head)
    case = f"fixture_repo_1-{head[:8]}"
    input_dir = tmp_path / "inputs" / case
    input_dir.mkdir(parents=True)
    metadata = {
        "nwo": "fixture/repo", "pr_number": 1, "title": "Return contract",
        "body": "", "base": base, "head": head,
    }
    write_json(input_dir / "pr.json", metadata)
    (input_dir / "diff.patch").write_text(diff, encoding="utf-8")
    prepared_path = tmp_path / "prepared.json"
    task = {
        "case_id": case,
        "metadata": metadata,
        "status": "ready",
        "input_dir": str(input_dir),
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
    }
    write_json(prepared_path, {"benchmark_commit": "c" * 40, "records": [task]})
    split_path = tmp_path / "split.json"
    write_json(
        split_path,
        {"split_rule": "by repository", "prepared_sha256": sha256_file(prepared_path),
         "dev": [case], "holdout": []},
    )
    manifest = build_input_manifest(
        prepared_path, split="dev", case_ids=[case], split_registry_path=split_path
    )
    manifest_path = tmp_path / "dev-manifest.json"
    write_json(manifest_path, manifest)

    requests = [
        {"hypothesis_id": "H1", "purpose": f"inspect caller {i}", "tool": "search",
         "symbol": f"caller_{i}"}
        for i in range(5)
    ]

    def assess(system, prompt):
        refs = grounded_test.task_evidence_refs(prompt)
        return {**grounded_test.verdict(), "intent_assessments": [{
            "hypothesis_id": "H1", "status": "supported", "evidence_refs": refs,
            "rationale": "Host-run evidence is linked to the changed contract.", "unknowns": [],
        }]}

    class FakeProvider:
        def __init__(self, *args, request_attempt_hook, max_output_tokens, **kwargs):
            self.request_attempt_hook = request_attempt_hook
            self.max_output_tokens = max_output_tokens
            self.http_attempts = 0
            self.usage_records = []
            self.responses = iter([
                {"intent_hypotheses": [grounded_test.planned_hypotheses()[0]],
                 "context_requests": requests},
                grounded_test.candidate(),
                assess,
            ])

        def complete_json(self, system, prompt):
            self.request_attempt_hook()
            self.http_attempts += 1
            response = next(self.responses)
            if callable(response):
                response = response(system, prompt)
            self.usage_records.append({"usage": {
                "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
            }})
            return json.dumps(response)

        def close(self):
            pass

    monkeypatch.setattr(
        paired_module, "get_settings",
        lambda: SimpleNamespace(
            openai_api_key="fake-test-key", review_model="fixture-model", openai_base_url=None
        ),
    )
    monkeypatch.setattr(
        paired_module, "source_identity",
        lambda: {"reviewer_commit": "d" * 40, "working_tree_dirty": False,
                 "reviewer_source_sha256": "e" * 64},
    )
    monkeypatch.setattr(improvement_module, "_default_split_registry_path", lambda: split_path)
    monkeypatch.setattr(paired_module, "OpenAICompatibleProvider", FakeProvider)
    output = tmp_path / "paired-run"
    records = paired_module.paired(
        {"benchmark_commit": "c" * 40, "records": [task]},
        mirrors,
        output,
        arms=("grounded",),
        concurrency=1,
        limit=1,
        max_attempts=1,
        max_http_attempts=6,
        trace_manifest=manifest,
    )
    assert records["grounded"][case]["status"] == "success"
    assert records["grounded"][case]["arm"] == "grounded"
    run_path = output / "grounded" / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run_record = run["records"][0]
    assert run_record["case_id"] == case
    assert run_record["arm"] == "grounded"
    assert run_record["attempts"][0]["http_attempts"] == 3
    assert run_record["attempts"][0]["usage_status"]["complete_for_attempts"] is True
    trace_path = output / "grounded" / run_record["review_trace_path"]
    assert sha256_file(trace_path) == run_record["review_trace_sha256"]
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["identity"]["head_sha"] == head
    assert trace["usage_status"] == "reported"
    assert trace["usage"]["request_http_attempts"] == 3
    assert trace["usage"]["usage_total_tokens"] == 36

    store = tmp_path / "strategy-store"
    monkeypatch.setattr(improvement_module, "_default_split_registry_path", lambda: split_path)

    def forbidden_provider(*args, **kwargs):
        raise AssertionError("offline cycle must never construct a model provider")

    import scripts.review_improvement as improvement_cli
    import src.reviewer.provider as provider_module

    monkeypatch.setattr(provider_module, "OpenAICompatibleProvider", forbidden_provider)
    monkeypatch.setattr(improvement_cli, "replay_candidate", forbidden_provider)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_improvement.py", "cycle", "--store", str(store),
            "--dev-manifest", str(manifest_path), "--run", str(run_path),
        ],
    )
    improvement_cli.main()
    output_line = capsys.readouterr().out.strip().splitlines()[-1]
    output_record = json.loads(output_line)
    assert output_record["status"] == "evaluation_required"
    assert output_record["diagnosis_count"] == 1
    assert output_record["candidate_id"]
    cycle_path = store / "cycles" / output_record["cycle_id"] / "cycle.json"
    saved_cycle = json.loads(cycle_path.read_text(encoding="utf-8"))
    assert saved_cycle == output_record
    diagnoses = json.loads(
        (cycle_path.parent / "diagnoses.json").read_text(encoding="utf-8")
    )
    assert diagnoses["diagnoses"][0]["failure_tag"] == "cross_file_task_budget_omitted"
    candidate = json.loads(
        (store / "candidates" / f"{output_record['candidate_id']}.json").read_text(
            encoding="utf-8"
        )
    )
    assert candidate["policy"]["prompt_addendum"] == PROMPT_TEMPLATES[
        "bounded_cross_file_evidence_v1"
    ]
    assert load_active_policy(store).status == "baseline"
    assert not (store / "evaluations").exists()


def test_policy_with_stale_criteria_falls_back_to_baseline(tmp_path):
    import src.application.strategy_policy as strategy_policy

    store = tmp_path / "store"
    version_id = "a" * 64
    version_path = store / "versions" / f"{version_id}.json"
    version = {
        "schema_version": 1,
        "version_id": version_id,
        "status": "accepted",
        "policy": {"prompt_addendum": "", "playbook_recall_k": 5},
        "criteria_fingerprint": "0" * 64,
    }
    write_json(version_path, version)
    write_json(
        store / "active.json",
        {
            "schema_version": 1,
            "version_id": version_id,
            "content_sha256": sha256_file(version_path),
        },
    )
    loaded = load_active_policy(store)
    assert loaded.status == "invalid_fallback"
    assert loaded.policy == strategy_policy.BASELINE_POLICY


def test_event_write_failure_reports_policy_state_after_rollback(tmp_path, monkeypatch):
    store = tmp_path / "store"

    def fail_event(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(improvement_module, "_append_event", fail_event)
    with pytest.raises(ImprovementError, match="baseline is active.*event was not recorded"):
        rollback_policy(store, "baseline")
    loaded = load_active_policy(store)
    assert loaded.status == "baseline"


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
    assert good["decision"] == "eligible_for_human_promotion", good
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
