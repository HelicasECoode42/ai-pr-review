"""Application boundary for model configuration and provider ownership."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack

from src.models import ChangedFile, PullRequest, ReviewReport, RiskFinding
from src.utils.config import Settings


class ReviewApplication:
    """One configured AI stage shared by CLI, Web and Agent transports.

    Network ownership stays here. The engine and providers are injectable for
    offline tests; callers keep transport-specific metadata and error handling.
    """

    def __init__(self, settings: Settings, *, provider_factory=None, engine=None):
        if provider_factory is None:
            from src.reviewer.provider import OpenAICompatibleProvider

            provider_factory = OpenAICompatibleProvider
        if engine is None:
            from src.reviewer.engine import review_with_ai

            engine = review_with_ai
        self.settings = settings
        self.provider_factory: Callable = provider_factory
        self.engine: Callable = engine

    def _provider(self, model: str):
        s = self.settings
        effort = s.review_reasoning_effort
        if (
            effort is None
            and s.enable_repository_review
            and model.startswith(("deepseek-v4", "deepseek-flash"))
        ):
            effort = "low"
        return self.provider_factory(
            api_key=s.openai_api_key,
            model=model,
            base_url=s.openai_base_url,
            timeout=s.request_timeout_seconds,
            stream=s.enable_repository_review,
            total_timeout=300 if s.enable_repository_review else None,
            max_output_tokens=32768 if s.enable_repository_review else None,
            reasoning_effort=effort,
        )

    def review(
        self, pr: PullRequest, files: list[ChangedFile], findings: list[RiskFinding], **options
    ) -> ReviewReport:
        s = self.settings
        with ExitStack() as resources:
            provider = self._provider(s.review_model)
            resources.callback(provider.close)
            critic = provider
            if s.enable_critic and s.critic_model and s.critic_model != s.review_model:
                critic = self._provider(s.critic_model)
                resources.callback(critic.close)
            defaults = {
                "max_suggestions": s.max_suggestions,
                "follow_changes": s.enable_repository_review,
                "repository_evidence_tokens": s.repository_evidence_tokens,
                "max_input_tokens": s.max_review_input_tokens,
                "min_confidence": s.min_comment_confidence,
                "max_suggestions_per_file": s.max_suggestions_per_file,
                "verify_rule_signals": s.verify_rule_signals,
                "enable_critic": s.enable_critic,
                "critic_provider": critic,
            }
            defaults.update(options)
            if (
                defaults["follow_changes"]
                and not defaults.get("two_stage", False)
                and defaults.get("repository_snapshot") is None
                and pr.base_sha
                and pr.head_sha
            ):
                from src.analyzer.repository_context import GitHubSnapshot
                from src.github.client import GitHubClient

                existing_client = defaults.get("gh_client")
                token = existing_client.token if existing_client is not None else s.github_token
                reader = resources.enter_context(
                    GitHubClient(token, timeout=s.request_timeout_seconds)
                )
                defaults["repository_snapshot"] = GitHubSnapshot(
                    reader, pr.repo, pr.base_sha, pr.head_sha, [f.filename for f in files]
                )
            return self.engine(pr=pr, files=files, findings=findings, provider=provider, **defaults)
