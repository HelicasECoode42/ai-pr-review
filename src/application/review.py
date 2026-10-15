"""Application boundary for model configuration and provider ownership."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from src.knowledge.playbook import (
    DEFAULT_PLAYBOOK,
    contract_domains,
    language_of,
    load_playbook,
    recall,
    render,
)
from src.models import ChangedFile, PullRequest, ReviewReport, RiskFinding
from src.reviewer.usage import summarize_usage
from src.utils.config import Settings

logger = logging.getLogger(__name__)


def _usage_records(provider: object) -> list[dict]:
    """Return a provider's usage records, or nothing when it exposes none."""
    records = getattr(provider, "usage_records", None)
    return list(records) if isinstance(records, list) else []


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

    def _recall_playbook(self, files: list[ChangedFile], defaults: dict):
        """Scope-matched playbook entries, or nothing when the store is absent."""
        path = Path(self.settings.playbook_path or DEFAULT_PLAYBOOK)
        try:
            entries = load_playbook(path)
        except Exception as exc:  # a broken playbook must not break a review
            logger.warning("Playbook at %s could not be loaded: %s", path, exc)
            return []
        paths = [file.filename for file in files]
        languages = sorted({language_of(path) for path in paths})
        return recall(entries, repo=defaults.get("repo") or "", paths=paths,
                      languages=languages, k=self.settings.playbook_recall_k)

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
            defaults.setdefault("unit_review_max_units", s.unit_review_max_units)
            defaults.setdefault("unit_review_workers", s.unit_review_workers)
            defaults.setdefault(
                "unit_provider_factory", lambda: self._provider(s.review_model)
            )
            # Knowledge layer (P3): recall scope-matched playbook entries and offer
            # them as guidance. Entries never override the criteria, and the ids
            # used are recorded so a report says which knowledge was in play.
            recalled = self._recall_playbook(files, defaults)
            if recalled and "contract_domains" not in defaults:
                defaults["contract_domains"] = contract_domains(recalled)
            if recalled and "prompt_addendum" not in defaults:
                defaults["prompt_addendum"] = render(recalled)
            report = self.engine(
                pr=pr, files=files, findings=findings, provider=provider, **defaults
            )
            # Stamp what this run actually cost and which single threshold governs
            # publishing, at the boundary that owns both the providers and the
            # settings. The workflow reads these instead of keeping its own copy.
            # A provider that exposes no usage records contributes nothing rather
            # than a fabricated zero.
            metrics = getattr(report, "metrics", None)
            if isinstance(metrics, dict):
                records = _usage_records(provider)
                if critic is not provider:
                    records.extend(_usage_records(critic))
                if records:
                    metrics.update(summarize_usage(records))
                if recalled:
                    metrics["experience_ids"] = ",".join(entry.id for entry in recalled)
                    metrics["experience_count"] = len(recalled)
                metrics["publish_min_confidence"] = s.publish_min_confidence
                # Which verification rules produced this report, so a change in
                # behaviour can be attributed to a criteria change (ADR-006).
                from src.reviewer.grounded_review import CRITERIA_VERSION

                metrics["criteria_version"] = CRITERIA_VERSION
            return report
