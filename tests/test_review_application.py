import json
from unittest.mock import Mock

import httpx
import pytest
import respx
from pydantic import ValidationError

from src.application.protocol import AgentContext
from src.application.review import ReviewApplication
from src.models import PullRequest, ReviewReport, ReviewSuggestion, Severity
from src.utils.config import Settings


def settings(**kwargs):
    return Settings(
        _env_file=None, openai_api_key="fake-key", review_model="deepseek-flash", **kwargs
    )


def test_shared_factory_and_critic_model_effort_follow_actual_model():
    providers = []

    def factory(**kwargs):
        p = Mock()
        p.config = kwargs
        providers.append(p)
        return p

    engine = Mock(return_value="result")
    application = ReviewApplication(
        settings(enable_critic=True, critic_model="other-model"),
        provider_factory=factory,
        engine=engine,
    )
    assert application.review(PullRequest(repo="r", number=1, title="t"), [], []) == "result"
    assert providers[0].config["reasoning_effort"] == "low"
    assert providers[1].config["reasoning_effort"] is None
    assert engine.call_args.kwargs["critic_provider"] is providers[1]
    for p in providers:
        p.close.assert_called_once()


def test_missing_evidence_followup_setting_reaches_production_engine():
    providers = []

    def factory(**kwargs):
        provider = Mock()
        provider.config = kwargs
        providers.append(provider)
        return provider

    engine = Mock(return_value="result")
    application = ReviewApplication(
        settings(grounded_missing_evidence_followup=True),
        provider_factory=factory,
        engine=engine,
    )

    assert application.review(PullRequest(repo="r", number=1, title="t"), [], []) == "result"
    assert engine.call_args.kwargs["grounded_missing_evidence_followup"] is True
    unit_provider = engine.call_args.kwargs["unit_provider_factory"]()
    assert unit_provider.config["request_budget"] is providers[0].config["request_budget"]


def test_primary_is_closed_if_critic_creation_fails():
    provider = Mock()
    factory = Mock(side_effect=[provider, RuntimeError("creation failed")])
    application = ReviewApplication(
        settings(enable_critic=True, critic_model="other"), provider_factory=factory, engine=Mock()
    )
    with pytest.raises(RuntimeError):
        application.review(PullRequest(repo="r", number=1, title="t"), [], [])
    provider.close.assert_called_once()


def test_same_model_uses_critic_output_cap_and_closes_each_provider_on_failure():
    providers = []

    def factory(**kwargs):
        provider = Mock()
        provider.config = kwargs
        providers.append(provider)
        return provider

    application = ReviewApplication(
        settings(enable_critic=True, critic_model="deepseek-flash"),
        provider_factory=factory,
        engine=Mock(side_effect=RuntimeError("engine")),
    )
    with pytest.raises(RuntimeError):
        application.review(PullRequest(repo="r", number=1, title="t"), [], [])
    assert len(providers) == 2
    assert providers[0].config["model"] == providers[1].config["model"]
    assert providers[0].config["max_output_tokens"] == 32_768
    assert providers[1].config["max_output_tokens"] == 1_024
    assert providers[0].config["request_budget"] is providers[1].config["request_budget"]
    for provider in providers:
        provider.close.assert_called_once()


def test_shared_provider_instance_is_closed_once_for_review_and_critic():
    provider = Mock()
    factory = Mock(return_value=provider)
    application = ReviewApplication(
        settings(enable_critic=True, critic_model="deepseek-flash"),
        provider_factory=factory,
        engine=Mock(side_effect=RuntimeError("engine")),
    )
    with pytest.raises(RuntimeError):
        application.review(PullRequest(repo="r", number=1, title="t"), [], [])
    assert factory.call_count == 2
    provider.close.assert_called_once()


def test_agent_carrier_rejects_unknown_fields_and_wrong_stage_data():
    context = AgentContext(repo="r", pr_number=1)
    with pytest.raises(ValidationError):
        context.files = "not-files"
    with pytest.raises(ValidationError):
        context.typo_field = "x"


def test_application_owns_snapshot_client_and_releases_it_on_engine_failure():
    from unittest.mock import patch

    provider = Mock()
    pr = PullRequest(repo="r", number=1, title="t", base_sha="1" * 40, head_sha="2" * 40)
    engine = Mock(side_effect=RuntimeError("review failed"))
    with patch("src.github.client.GitHubClient") as client:
        application = ReviewApplication(
            settings(github_token="fake-github"),
            provider_factory=Mock(return_value=provider),
            engine=engine,
        )
        with pytest.raises(RuntimeError):
            application.review(pr, [], [])
        client.assert_called_once_with("fake-github", timeout=45.0)
        client.return_value.__exit__.assert_called_once()
        snapshot = engine.call_args.kwargs["repository_snapshot"]
        assert snapshot.base == pr.base_sha and snapshot.head == pr.head_sha
    provider.close.assert_called_once()


def test_original_review_cli_keeps_positional_arguments_and_workflow_options():
    from typer.main import get_command

    from src.cli.main import app

    command = get_command(app)
    assert [p.name for p in command.params][:2] == ["repo", "pr_number"]
    assert any("--reviewer-version" in p.opts for p in command.params)
    assert not hasattr(command, "commands")


def test_all_review_providers_share_one_hard_request_budget_and_report_it():
    created = []

    class Provider:
        def __init__(self, **kwargs):
            self.config = kwargs
            self.usage_records = []
            self.closed = False
            created.append(self)

        def close(self):
            self.closed = True

    pr = PullRequest(repo="owner/repo", number=7, title="t", head_sha="a" * 40)
    report = ReviewReport(
        pr=pr, files=[], summary="ok", risk_level=Severity.LOW,
        suggestions=[ReviewSuggestion(
            file_path="src/a.py", line=3, severity=Severity.HIGH, confidence=0.9,
            title="Broken", reason="The call fails", recommendation="Keep behavior",
        )],
    )
    engine = Mock(return_value=report)
    app = ReviewApplication(
        settings(
            enable_critic=True,
            critic_model="critic-model",
            max_input_token_upper_bound=12_345,
            max_review_http_attempts=3,
            max_review_timeout_seconds=12,
        ),
        provider_factory=Provider,
        engine=engine,
    )
    app.review(pr, [], [])

    assert len(created) == 2
    primary_budget = created[0].config["request_budget"]
    assert created[1].config["request_budget"] is primary_budget
    unit_provider = engine.call_args.kwargs["unit_provider_factory"]()
    assert unit_provider.config["request_budget"] is primary_budget
    unit_provider.close()
    assert report.metrics["request_http_attempt_cap"] == 3
    assert report.metrics["request_input_upper_bound_cap_utf8_bytes"] == 12_345
    assert report.metrics["request_input_upper_bound_unit"] == "utf8_bytes"
    assert report.metrics["request_input_estimated_method"].startswith("json-utf8")
    fingerprints = json.loads(report.metrics["publish_fingerprints"])
    assert set(fingerprints) == {"0"}
    assert len(fingerprints["0"]) == 24
    assert report.metrics["strategy_policy_status"] == "baseline"
    assert all(provider.closed for provider in created[:2])


def test_settings_keep_legacy_context_cap_and_expose_full_request_cap(monkeypatch):
    monkeypatch.setenv("MAX_REVIEW_INPUT_TOKENS", "6000")
    monkeypatch.setenv("MAX_INPUT_TOKEN_UPPER_BOUND", "12000")
    configured = Settings(_env_file=None)
    assert configured.max_review_input_tokens == 6000
    assert configured.max_input_token_upper_bound == 12_000


@respx.mock
def test_production_review_cap_denial_returns_partial_report_without_sending():
    from src.reviewer.provider import OpenAICompatibleProvider

    route = respx.post("https://provider.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    pr = PullRequest(repo="owner/repo", number=9, title="t")
    app = ReviewApplication(
        settings(
            openai_base_url="https://provider.test/v1",
            enable_repository_review=False,
            max_input_token_upper_bound=1,
        ),
        provider_factory=OpenAICompatibleProvider,
    )

    report = app.review(pr, [], [])

    assert route.call_count == 0
    assert report.report_confidence == "partial"
    assert report.metrics["request_http_attempts"] == 0
    assert report.metrics["request_budget_stop_reason"] == "input_budget_exhausted"
    assert any("input upper-bound budget exhausted" in warning
               for warning in report.analysis_warnings)


def test_production_review_applies_verified_policy_and_observes_rollback(tmp_path):
    import hashlib
    import json

    from src.application.strategy_policy import (
        BASELINE_CONTENT_SHA256,
        PROMPT_TEMPLATES,
        current_criteria_fingerprint,
    )

    pr = PullRequest(repo="owner/repo", number=10, title="t")
    report = ReviewReport(
        pr=pr, files=[], summary="ok", risk_level=Severity.LOW,
        repository_context={"review_trace": {"schema_version": 1}},
    )

    class Provider:
        usage_records = []

        def close(self):
            pass

    engine = Mock(return_value=report)
    policy_dir = tmp_path / "store"
    app = ReviewApplication(
        settings(
            enable_repository_review=False,
            strategy_policy_store_dir=str(policy_dir),
            playbook_path=str(tmp_path / "empty-playbook.md"),
        ),
        provider_factory=lambda **_: Provider(),
        engine=engine,
    )
    (tmp_path / "empty-playbook.md").write_text("", encoding="utf-8")

    app.review(pr, [], [])
    assert engine.call_args.kwargs["prompt_addendum"] == ""
    assert report.metrics["strategy_policy_status"] == "baseline"
    assert engine.call_args.kwargs["strategy_policy_id"] == "baseline"
    baseline_policy_sha = engine.call_args.kwargs["strategy_policy_content_sha256"]
    assert len(baseline_policy_sha) == 64
    baseline_trace = report.repository_context["review_trace"]
    assert baseline_trace["policy"]["policy_id"] == "baseline"
    assert report.metrics["review_trace_sha256"] == baseline_trace["trace_sha256"]

    version_id = "a" * 64
    version = {
        "schema_version": 1,
        "version_id": version_id,
        "status": "accepted",
        "criteria_fingerprint": current_criteria_fingerprint(),
        "policy": {
            "prompt_addendum": PROMPT_TEMPLATES["cross_file_contract_check_v1"],
            "playbook_recall_k": 5,
        },
    }
    version_bytes = json.dumps(
        version, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    (policy_dir / "versions").mkdir(parents=True)
    (policy_dir / "versions" / f"{version_id}.json").write_bytes(version_bytes)
    (policy_dir / "active.json").write_text(json.dumps({
        "schema_version": 1,
        "version_id": version_id,
        "content_sha256": hashlib.sha256(version_bytes).hexdigest(),
    }), encoding="utf-8")

    trace_input_identity = {
        "benchmark_case_id": "fixture-case-10",
        "prepared_metadata_sha256": "1" * 64,
        "prepared_diff_sha256": "2" * 64,
        "input_set_sha256": "3" * 64,
    }
    app.review(pr, [], [], trace_input_identity=trace_input_identity)
    assert engine.call_args.kwargs["prompt_addendum"] == version["policy"]["prompt_addendum"]
    assert report.metrics["strategy_policy_id"] == version_id
    assert report.metrics["strategy_policy_status"] == "active"
    assert engine.call_args.kwargs["trace_input_identity"] == trace_input_identity
    assert engine.call_args.kwargs["strategy_policy_id"] == version_id
    assert engine.call_args.kwargs["strategy_policy_content_sha256"] != baseline_policy_sha
    assert report.metrics["strategy_policy_effective_sha256"] == engine.call_args.kwargs[
        "strategy_policy_content_sha256"
    ]
    active_trace = report.repository_context["review_trace"]
    assert active_trace["policy"]["policy_id"] == version_id
    assert active_trace["policy"]["effective_policy_sha256"] == report.metrics[
        "strategy_policy_effective_sha256"
    ]
    assert report.metrics["review_trace_sha256"] == active_trace["trace_sha256"]

    (policy_dir / "active.json").write_text(json.dumps({
        "schema_version": 1,
        "version_id": "baseline",
        "content_sha256": BASELINE_CONTENT_SHA256,
    }), encoding="utf-8")
    app.review(pr, [], [])
    assert engine.call_args.kwargs["prompt_addendum"] == ""
    assert report.metrics["strategy_policy_id"] == "baseline"
    assert report.metrics["strategy_policy_status"] == "baseline"
    assert report.repository_context["review_trace"]["policy"]["policy_id"] == "baseline"
