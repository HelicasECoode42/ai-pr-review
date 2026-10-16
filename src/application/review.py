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

_SYMBOL_SCOPE_MAX_FILES = 12
_SYMBOL_SCOPE_MAX_FILE_BYTES = 100_000
_SYMBOL_SCOPE_MAX_TOTAL_BYTES = 400_000


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

    @staticmethod
    def _changed_symbols_by_path(
        files: list[ChangedFile], snapshot, *, relevant_paths: set[str]
    ) -> tuple[dict[str, set[str]], bool]:
        """Index only frozen changed files and return symbols containing changed lines.

        Missing snapshot text, unsupported syntax and parse failures remain absent
        from the map. ``recall`` treats that absence as unknown, never as a match.
        """
        if snapshot is None:
            return {}, False
        from src.analyzer.diff_parser import parse_file_hunks
        from src.analyzer.review_plan import SourceIndexCache

        index = SourceIndexCache(max_entries=max(1, len(files) * 2))
        symbols_by_path: dict[str, set[str]] = {}
        limited = len(relevant_paths) > _SYMBOL_SCOPE_MAX_FILES
        total_bytes = 0
        budget_exhausted = False
        relevant_files = [file for file in files if file.filename in relevant_paths]
        bounded_files = relevant_files[:_SYMBOL_SCOPE_MAX_FILES]
        for file_index, file in enumerate(bounded_files):
            hunks = parse_file_hunks(file)
            if not hunks:
                continue
            has_added = any(hunk.added_lines for hunk in hunks)
            has_removed = any(
                raw.startswith("-") and not raw.startswith("---")
                for hunk in hunks for raw in hunk.raw.splitlines()[1:]
            )
            sides = []
            if has_added:
                sides.append(("head", file.filename))
            if has_removed:
                sides.append(("base", file.previous_filename or file.filename))
            file_symbols: set[str] = set()
            file_complete = bool(sides)
            for side, path in sides:
                try:
                    text = snapshot.read(path, side)
                except Exception as exc:
                    logger.warning("Could not read frozen %s source %s: %s", side, path, exc)
                    file_complete = False
                    continue
                if text is None:
                    file_complete = False
                    continue
                source_bytes = len(text.encode("utf-8"))
                if total_bytes + source_bytes > _SYMBOL_SCOPE_MAX_TOTAL_BYTES:
                    limited = True
                    file_complete = False
                    budget_exhausted = True
                    break
                total_bytes += source_bytes
                if source_bytes > _SYMBOL_SCOPE_MAX_FILE_BYTES:
                    limited = True
                    file_complete = False
                    continue
                parsed = index.parse(path, text, side)
                if (parsed.parser == "unavailable"
                        or {"parse_failed", "parse_errors"} & set(parsed.limitations)):
                    file_complete = False
                    continue
                changed_lines: set[int] = set()
                for hunk in hunks:
                    if side == "head":
                        changed_lines.update(line.line for line in hunk.added_lines)
                    else:
                        old_line = hunk.old_start
                        for raw in hunk.raw.splitlines()[1:]:
                            if raw.startswith("-"):
                                changed_lines.add(old_line)
                                old_line += 1
                            elif raw.startswith(" "):
                                old_line += 1
                file_symbols.update(
                    symbol.name for symbol in parsed.symbols
                    if any(symbol.start <= line <= symbol.end for line in changed_lines)
                )
            if file_complete:
                # Keep the result keyed by the current changed path even for a
                # deletion read from the old filename, so recall tests the same
                # path that its scope matched.
                symbols_by_path[file.filename] = file_symbols
            if budget_exhausted:
                break
            if total_bytes >= _SYMBOL_SCOPE_MAX_TOTAL_BYTES:
                limited = limited or file_index + 1 < len(relevant_files)
                break
        return symbols_by_path, limited

    def _recall_playbook(self, files: list[ChangedFile], defaults: dict, *, repo: str):
        """Return scope-matched entries and explicit limits on symbol resolution."""
        path = Path(self.settings.playbook_path or DEFAULT_PLAYBOOK)
        try:
            entries = load_playbook(path)
        except Exception as exc:  # a broken playbook must not break a review
            logger.warning("Playbook at %s could not be loaded: %s", path, exc)
            return [], [], []
        paths = [file.filename for file in files]
        languages = sorted({language_of(path) for path in paths})
        snapshot = defaults.get("repository_snapshot")
        symbol_scoped = [
            entry for entry in entries
            if entry.status == "authoritative"
            and (symbols := [str(value) for value in (entry.scope.get("symbols") or ())])
            and "*" not in symbols
            and entry.scope.get("repo") in (None, "*", repo)
            and any(
                (not entry.scope.get("languages")
                 or language_of(path) in entry.scope.get("languages", []))
                and (not entry.scope.get("paths") or any(
                    Path(path).match(pattern) for pattern in entry.scope.get("paths", [])
                ))
                for path in paths
            )
        ]
        relevant_symbol_paths = {
            path for entry in symbol_scoped for path in paths
            if (not entry.scope.get("languages")
                or language_of(path) in entry.scope.get("languages", []))
            and (not entry.scope.get("paths") or any(
                Path(path).match(pattern) for pattern in entry.scope.get("paths", [])
            ))
        }
        symbols_by_path, symbol_index_limited = (
            self._changed_symbols_by_path(
                files, snapshot, relevant_paths=relevant_symbol_paths
            ) if symbol_scoped else ({}, False)
        )
        recalled = recall(entries, repo=repo, paths=paths,
                          languages=languages, symbols_by_path=symbols_by_path,
                          k=self.settings.playbook_recall_k)
        warnings = []
        recalled_ids = {entry.id for entry in recalled}
        unresolved_symbol_scoped = [
            entry.id for entry in symbol_scoped
            if entry.id not in recalled_ids and any(
                   path not in symbols_by_path for path in relevant_symbol_paths
                   if (not entry.scope.get("languages")
                       or language_of(path) in entry.scope.get("languages", []))
                   and (not entry.scope.get("paths") or any(
                       Path(path).match(pattern) for pattern in entry.scope.get("paths", [])
                   )))
        ]
        if unresolved_symbol_scoped and (
            snapshot is None or relevant_symbol_paths - set(symbols_by_path)
        ):
            warnings.append(
                "Symbol-scoped playbook entries were not recalled because changed symbols "
                "could not be resolved from every relevant frozen file: "
                + ", ".join(sorted(unresolved_symbol_scoped))
            )
        if symbol_index_limited:
            warnings.append(
                "Symbol-scoped playbook resolution reached its changed-source file or size "
                "budget; unindexed paths remain unknown and cannot widen recall."
            )
        unavailable_contract_domain_ids = [
            entry.id for entry in recalled
            if entry.kind == "contract" and (
                entry.scope.get("symbols") or entry.scope.get("paths")
                or entry.scope.get("languages")
            ) and entry.domain
        ]
        if unavailable_contract_domain_ids:
            warnings.append(
                "Scoped contract entries with declared domains were recalled but withheld "
                "from the run-wide prompt and witness constraints: their values are not "
                "verified repository API facts, and candidate-specific contract binding is "
                "unavailable: "
                + ", ".join(unavailable_contract_domain_ids)
            )
        return recalled, warnings, unavailable_contract_domain_ids

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
            defaults.setdefault("unit_review_cost_ceiling_tokens",
                                s.unit_review_cost_ceiling_tokens)
            defaults.setdefault("unit_evidence_tokens", s.unit_evidence_tokens)
            if s.unit_review_max_units and pr.head_sha:
                # One checkpoint per frozen head: a crashed or cancelled run can be
                # resumed, and finished units are never paid for twice.
                defaults.setdefault(
                    "unit_checkpoint_path",
                    str(Path(s.unit_checkpoint_dir) / f"units-{pr.head_sha[:12]}.jsonl"),
                )
            defaults.setdefault(
                "unit_provider_factory", lambda: self._provider(s.review_model)
            )
            # Knowledge layer (P3): recall scope-matched playbook entries and offer
            # them as guidance. Entries never override the criteria, and the ids
            # used are recorded so a report says which knowledge was in play.
            recalled, knowledge_warnings, unavailable_contract_domain_ids = self._recall_playbook(
                files, defaults, repo=pr.repo
            )
            prompt_entries = [
                entry for entry in recalled
                if entry.id not in unavailable_contract_domain_ids
            ]
            if prompt_entries and "prompt_addendum" not in defaults:
                defaults["prompt_addendum"] = render(prompt_entries)
            if recalled and "contract_domains" not in defaults:
                try:
                    defaults["contract_domains"] = contract_domains(recalled)
                except ValueError as exc:
                    defaults["contract_domains"] = {}
                    knowledge_warnings.append(
                        f"Conflicting global playbook contract domains were disabled: {exc}"
                    )
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
                metrics["experience_ids"] = ",".join(entry.id for entry in recalled)
                metrics["experience_count"] = len(recalled)
                if unavailable_contract_domain_ids:
                    metrics["contract_scope_unavailable_count"] = len(
                        unavailable_contract_domain_ids
                    )
                metrics["publish_min_confidence"] = s.publish_min_confidence
                # Which verification rules produced this report, so a change in
                # behaviour can be attributed to a criteria change (ADR-006).
                from src.reviewer.grounded_review import CRITERIA_VERSION

                metrics["criteria_version"] = CRITERIA_VERSION
            if knowledge_warnings:
                report.analysis_warnings.extend(knowledge_warnings)
            return report
