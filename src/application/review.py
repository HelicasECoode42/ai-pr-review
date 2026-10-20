"""Application boundary for model configuration and provider ownership."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
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
from src.reviewer.provider import ReviewRequestBudget
from src.reviewer.usage import summarize_usage
from src.utils.config import Settings, is_deepseek_chat_model

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

    def _provider(self, model: str, request_budget: ReviewRequestBudget):
        s = self.settings
        effort = s.review_reasoning_effort
        if (
            effort is None
            and s.enable_repository_review
            and model.startswith(("deepseek-v4", "deepseek-flash"))
        ):
            effort = "low"
        provider_options = {
            "api_key": s.openai_api_key,
            "model": model,
            "base_url": s.openai_base_url,
            "timeout": s.request_timeout_seconds,
            "connect_timeout": s.model_connect_timeout_seconds,
            "read_timeout": s.model_read_timeout_seconds,
            "stream": s.enable_repository_review,
            "total_timeout": s.max_review_timeout_seconds if s.enable_repository_review else None,
            "max_output_tokens": s.model_output_token_limit,
            "reasoning_effort": effort,
            "request_budget": request_budget,
        }
        if (
            s.review_thinking_enabled is not None
            and is_deepseek_chat_model(model, s.openai_base_url)
        ):
            provider_options["thinking_enabled"] = s.review_thinking_enabled
        return self.provider_factory(**provider_options)

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

    def _recall_playbook(
        self, files: list[ChangedFile], defaults: dict, *, repo: str, k: int | None = None
    ):
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
                          k=self.settings.playbook_recall_k if k is None else k)
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
            request_budget = ReviewRequestBudget(
                s.max_input_token_upper_bound,
                s.max_review_http_attempts,
                s.max_review_timeout_seconds,
            )
            provider = self._provider(s.review_model, request_budget)
            resources.callback(provider.close)
            critic = provider
            if s.enable_critic and s.critic_model and s.critic_model != s.review_model:
                critic = self._provider(s.critic_model, request_budget)
                resources.callback(critic.close)
            defaults = {
                "max_suggestions": s.max_suggestions,
                "follow_changes": s.enable_repository_review,
                "grounded_missing_evidence_followup": s.grounded_missing_evidence_followup,
                "repository_evidence_tokens": s.repository_evidence_tokens,
                "documentation_patch_tokens": s.documentation_patch_tokens,
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
                "unit_provider_factory",
                lambda: self._provider(s.review_model, request_budget),
            )
            from src.application.strategy_policy import load_active_policy

            loaded_policy = load_active_policy(s.strategy_policy_store_dir)
            policy_bytes = json.dumps(
                loaded_policy.policy.model_dump(), sort_keys=True,
                separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")
            effective_policy_sha256 = hashlib.sha256(policy_bytes).hexdigest()
            defaults["strategy_policy_id"] = loaded_policy.policy_id
            defaults["strategy_policy_status"] = loaded_policy.status
            defaults["strategy_policy_content_sha256"] = effective_policy_sha256
            # Knowledge layer (P3): recall scope-matched playbook entries and offer
            # them as guidance. Entries never override the criteria, and the ids
            # used are recorded so a report says which knowledge was in play.
            recalled, knowledge_warnings, unavailable_contract_domain_ids = self._recall_playbook(
                files, defaults, repo=pr.repo,
                k=loaded_policy.policy.playbook_recall_k,
            )
            prompt_entries = [
                entry for entry in recalled
                if entry.id not in unavailable_contract_domain_ids
            ]
            addendum_parts = [str(defaults.get("prompt_addendum") or "").strip()]
            if loaded_policy.policy.prompt_addendum:
                addendum_parts.append(loaded_policy.policy.prompt_addendum)
            if prompt_entries:
                addendum_parts.append(render(prompt_entries))
            defaults["prompt_addendum"] = "\n\n".join(
                part for part in addendum_parts if part
            )
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
            if not isinstance(report, ReviewReport):
                # Tests and embedding callers may inject a lightweight engine
                # with its own return type. Candidate lifecycle applies only to
                # the stable production report contract.
                return report
            from src.reviewer.decision_ledger import (
                append_candidate_ledger,
                build_candidate_lifecycle,
            )
            from src.reviewer.grounded_review import CRITERIA_VERSION

            run_id = uuid.uuid4().hex
            lifecycle = build_candidate_lifecycle(
                report,
                run_id=run_id,
                criteria_version=CRITERIA_VERSION,
                publish_min_confidence=s.publish_min_confidence,
            )
            # Stamp what this run actually cost and which single threshold governs
            # publishing, at the boundary that owns both the providers and the
            # settings. The workflow reads these instead of keeping its own copy.
            # A provider that exposes no usage records contributes nothing rather
            # than a fabricated zero.
            metrics = getattr(report, "metrics", None)
            fingerprints = {}
            if isinstance(metrics, dict):
                records = _usage_records(provider)
                if critic is not provider:
                    records.extend(_usage_records(critic))
                if records:
                    metrics.update(summarize_usage(records))
                metrics["experience_ids"] = ",".join(entry.id for entry in recalled)
                metrics["experience_count"] = len(recalled)
                metrics.update(request_budget.snapshot())
                metrics["strategy_policy_id"] = loaded_policy.policy_id
                metrics["strategy_policy_status"] = loaded_policy.status
                metrics["strategy_policy_effective_sha256"] = effective_policy_sha256
                if unavailable_contract_domain_ids:
                    metrics["contract_scope_unavailable_count"] = len(
                        unavailable_contract_domain_ids
                    )
                metrics["publish_min_confidence"] = s.publish_min_confidence
                # Which verification rules produced this report, so a change in
                # behaviour can be attributed to a criteria change (ADR-006).
                metrics["criteria_version"] = CRITERIA_VERSION
                trace = getattr(report, "repository_context", {}).get("review_trace")
                if isinstance(trace, dict):
                    trace["policy"] = {
                        "policy_id": loaded_policy.policy_id,
                        "status": loaded_policy.status,
                        "effective_policy_sha256": effective_policy_sha256,
                        "grounded_missing_evidence_followup": s.grounded_missing_evidence_followup,
                        "effective_review_strategy_sha256": hashlib.sha256(json.dumps(
                            {
                                "effective_policy_sha256": effective_policy_sha256,
                                "grounded_missing_evidence_followup": (
                                    s.grounded_missing_evidence_followup
                                ),
                            }, sort_keys=True, separators=(",", ":"),
                        ).encode("utf-8")).hexdigest(),
                    }
                    usage_keys = (
                        "usage_responses_known", "usage_responses_unknown",
                        "usage_prompt_tokens", "usage_completion_tokens", "usage_total_tokens",
                        "cache_hit_tokens", "cache_miss_tokens", "cache_hit_rate",
                        "request_http_attempts", "request_http_attempt_cap",
                        "request_input_upper_bound_utf8_bytes",
                        "request_input_upper_bound_cap_utf8_bytes",
                        "request_input_upper_bound_unit",
                        "request_input_estimated_method", "request_budget_stop_reason",
                        "request_budget_elapsed_seconds",
                    )
                    usage = dict(trace.get("usage") or {})
                    usage.update({key: metrics[key] for key in usage_keys if key in metrics})
                    trace["usage"] = usage
                    trace["usage"]["request_total_timeout_seconds"] = (
                        s.max_review_timeout_seconds
                    )
                    trace["usage_status"] = (
                        "reported" if metrics.get("usage_responses_known", 0) else
                        "partial" if metrics.get("usage_responses_unknown", 0) else "unknown"
                    )
                    trace.pop("trace_sha256", None)
                    trace_body = json.dumps(
                        trace, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")
                    trace_sha256 = hashlib.sha256(trace_body).hexdigest()
                    trace["trace_sha256"] = trace_sha256
                    metrics["review_trace_path"] = "embedded:repository_context.review_trace"
                    metrics["review_trace_sha256"] = trace_sha256
                if pr.repo and pr.number and pr.head_sha:
                    from src.reviewer.publish import (
                        review_comment_body,
                        review_publication_fingerprint,
                    )

                    fingerprints = {
                        str(index): review_publication_fingerprint(
                            repo=pr.repo,
                            pr_number=pr.number,
                            head_sha=pr.head_sha,
                            criteria_version=CRITERIA_VERSION,
                            file_path=suggestion.file_path,
                            line=suggestion.line,
                            body=review_comment_body(suggestion),
                        )
                        for index, suggestion in enumerate(report.suggestions)
                    }
                    metrics["publish_fingerprints"] = json.dumps(
                        fingerprints, sort_keys=True, separators=(",", ":")
                    )
                else:
                    metrics["publish_fingerprint_status"] = "unavailable_missing_identity"
                metrics["candidate_ledger_run_id"] = run_id
                metrics["candidate_lifecycle_inventory_status"] = lifecycle[
                    "inventory_status"
                ]
                metrics["suggestion_candidate_ids"] = json.dumps(
                    lifecycle["suggestion_candidate_ids"],
                    sort_keys=True,
                    separators=(",", ":"),
                )
                fingerprint_by_candidate_id = {}
                for suggestion_index, candidate_id in lifecycle[
                    "suggestion_candidate_ids"
                ].items():
                    fingerprint = fingerprints.get(suggestion_index)
                    if fingerprint:
                        fingerprint_by_candidate_id[candidate_id] = fingerprint
                for candidate in lifecycle["candidates"]:
                    fingerprint = fingerprint_by_candidate_id.get(candidate["candidate_id"])
                    if fingerprint:
                        candidate["publish_fingerprint"] = fingerprint
            if not s.decision_ledger_enabled:
                persistence_status = "not_persisted"
                report.analysis_warnings.append(
                    "Candidate lifecycle was not persisted because "
                    "local ledger writing is disabled."
                )
            else:
                try:
                    append_candidate_ledger(s.decision_ledger_path, lifecycle)
                    persistence_status = "persisted"
                except Exception as exc:
                    persistence_status = "failed"
                    error_type = type(exc).__name__
                    if not error_type.isidentifier():
                        error_type = "OSError"
                    report.analysis_warnings.append(
                        "Candidate lifecycle persistence failed "
                        f"({error_type}); the report is partial."
                    )
                    if report.execution_status == "success":
                        report.execution_status = "degraded"
                    if report.report_confidence not in {"failed", "partial"}:
                        report.report_confidence = "partial"
                    if report.degradation_reason is None:
                        report.degradation_reason = "candidate_ledger_persistence_failed"
            lifecycle["persistence_status"] = persistence_status
            report.candidate_lifecycle = lifecycle
            if isinstance(metrics, dict):
                metrics["candidate_ledger_persistence_status"] = persistence_status
            if knowledge_warnings:
                report.analysis_warnings.extend(knowledge_warnings)
            return report
