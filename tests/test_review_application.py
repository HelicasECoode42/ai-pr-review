from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from src.application.protocol import AgentContext
from src.application.review import ReviewApplication
from src.models import PullRequest
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


def test_primary_is_closed_if_critic_creation_fails():
    provider = Mock()
    factory = Mock(side_effect=[provider, RuntimeError("creation failed")])
    application = ReviewApplication(
        settings(enable_critic=True, critic_model="other"), provider_factory=factory, engine=Mock()
    )
    with pytest.raises(RuntimeError):
        application.review(PullRequest(repo="r", number=1, title="t"), [], [])
    provider.close.assert_called_once()


def test_same_model_reuses_provider_and_engine_failure_closes_once():
    provider = Mock()
    factory = Mock(return_value=provider)
    application = ReviewApplication(
        settings(enable_critic=True, critic_model="deepseek-flash"),
        provider_factory=factory,
        engine=Mock(side_effect=RuntimeError("engine")),
    )
    with pytest.raises(RuntimeError):
        application.review(PullRequest(repo="r", number=1, title="t"), [], [])
    factory.assert_called_once()
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
