"""Admission of discovered candidates into verification."""
from __future__ import annotations

from src.reviewer.criteria import (
    quote_was_shown,
)
from src.reviewer.grounded.evidence import (
    _add_callee_contracts,
    _add_contract_callers,
    _add_serialization_contract,
)
from src.reviewer.grounded.rule_candidates import (
    _suggestion_rule_candidate_key,
)

# Aliases kept for existing importers; the implementations live in criteria.py.
_quote_was_shown = quote_was_shown
from src.reviewer.grounded.state import GroundedRun


def admit_candidates(run: GroundedRun, payload):
    """Record every candidate and select those with a valid anchor for verification."""
    seen = set()
    paths = {f.filename: f for f in run.files}
    for index, suggestion in enumerate(payload.suggestions):
        if _suggestion_rule_candidate_key(suggestion) in run.host_rule_candidate_keys:
            run.host_rule_candidate_ids.add(index)
        identity = (suggestion.file_path, suggestion.line, suggestion.title.casefold())
        item = {"candidate_id": index, "suggestion": suggestion.model_dump(mode="json")}
        if identity in seen:
            item.update(status="filtered", reason="duplicate_identity")
        elif suggestion.file_path not in paths:
            item.update(status="needs_context", reason="anchor_not_in_changed_files")
            run.pending.append(suggestion.model_copy(update={"source": "ai_needs_context"}))
        elif len(run.eligible) >= run.max_suggestions:
            item.update(status="needs_context", reason="candidate_verification_budget")
            run.pending.append(suggestion.model_copy(update={"source": "ai_needs_context"}))
        else:
            source = run.snapshot.read(suggestion.file_path)
            if not source or not suggestion.line or not 1 <= suggestion.line <= len(source.splitlines()):
                item.update(status="needs_context", reason="invalid_head_anchor")
                run.pending.append(suggestion.model_copy(update={"source": "ai_needs_context"}))
            else:
                item.update(status="awaiting_verification")
                run.eligible.append((index, suggestion))
                # Candidate-specific complete definition before behavioral verification.
                run.bundle.add(suggestion.file_path, suggestion.line, kind="candidate_definition")
                # One hop along the contract: where else is this symbol used?
                _add_contract_callers(run.bundle, run.snapshot, run.files, suggestion)
                _add_serialization_contract(run.bundle, run.snapshot, suggestion)
                if run.raw_discovery:
                    _add_callee_contracts(run.bundle, run.snapshot, suggestion)
        seen.add(identity); run.candidates.append(item)
