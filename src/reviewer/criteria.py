"""The verification filter chain.

Confirmation used to be one conjunction written inline in the review loop, which
made it impossible to answer "why was this candidate not published?" and
impossible to switch a check off. This module turns it into a named chain in
which every filter returns a verdict, including the reason it rejected:

    chain_completeness -> source_provenance -> counter_evidence -> witness_eval
    -> decision_vocabulary

``witness_eval`` also consults the declared contract domains, so a witness built
from an input the interface excludes does not count as a witness.

Rules:

* ``source_provenance`` (H3): a cited line must have been inside the patch text
  we sent or an evidence excerpt we rendered. A quote the model could not have
  read is not evidence, however well it matches the repository.
* ``witness_eval`` (H4/H5): a claim of changed behaviour must be able to show an
  input where base and head differ. No witness on an enumerated domain is a
  refutation of the claim's evidence, so it blocks confirmation - and it is
  recorded as such rather than silently dropped. Expressions outside the safe
  subset are reported as ``unsupported`` and do not block.
* Every verdict is written into the decision record, so a candidate's route is
  auditable and a criteria change can be attributed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.reviewer.witness import WitnessResult, evaluate_equivalence, expression_from_quote

# Version of the confirmation rules. Bump it whenever a filter or a threshold
# changes: a report records which rules produced it (H6).
CRITERIA_VERSION = "grounded-v3: chain+provenance+witness+contract"

_CONFIRMED_CHAIN_FIELDS = (
    "trigger", "expected_behavior", "actual_behavior", "introduced_by", "reason",
)
_VALID_DECISIONS = {"confirmed", "dismissed", "needs_context"}


@dataclass(frozen=True)
class FilterVerdict:
    name: str
    passed: bool
    reason: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"filter": self.name, "passed": self.passed, "reason": self.reason, **self.detail}


def quote_was_shown(quote: str, sent_text: str) -> bool:
    """Was this exact source line part of the diff text we actually sent?"""
    needle = "".join(quote.split())
    if not needle:
        return False
    for line in sent_text.splitlines():
        if line[:1] in {"+", "-", " "} and "".join(line[1:].split()) == needle:
            return True
    return False


def valid_citation(citation, snapshot, bundle, sent_text=None) -> bool:
    """Provenance first, then an exact match against the frozen snapshot."""
    try:
        path = citation["path"]
        side = citation["side"]
        line = int(citation["line"])
        quote = citation["quote"]
        if side not in {"head", "base"} or line < 1:
            return False
        if not isinstance(quote, str) or not quote.strip():
            return False
        seen = any(
            e["path"] == path and e["side"] == side and e["start_line"] <= line <= e["end_line"]
            for e in bundle.excerpts
        )
        if not seen and sent_text is not None:
            seen = quote_was_shown(quote, sent_text)
        if not seen:
            return False
        text = snapshot.read(path, side)
        if not text or line > len(text.splitlines()):
            return False
        return "".join(quote.split()) in "".join(text.splitlines()[line - 1].split())
    except (KeyError, TypeError, ValueError):
        return False


def _evaluable(evidence: list[dict], side: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for citation in evidence:
        if citation.get("side") != side:
            continue
        expression = expression_from_quote(str(citation.get("quote", "")))
        if expression:
            found.append((str(citation.get("path", "")), expression))
    return found


def witness_for_evidence(evidence: list[dict]) -> WitnessResult | None:
    """Evaluate an expression pair the citations quote.

    Quotes are searched on both sides rather than only the first of each, and a
    pair from the same file is preferred: a witness is about one behaviour, and
    comparing two unrelated files would be meaningless.
    """
    bases = _evaluable(evidence, "base")
    heads = _evaluable(evidence, "head")
    if not bases or not heads:
        return None
    pairs = [
        (base, head)
        for base in bases
        for head in heads
        if base[0] and base[0] == head[0]
    ] or [
        (base, head) for base in bases for head in heads
    ]
    for base, head in pairs:
        result = evaluate_equivalence(base[1], head[1])
        if result.status != "unsupported":
            return result
    return evaluate_equivalence(pairs[0][0][1], pairs[0][1][1])


def domain_violation(witness: dict, domains: dict[str, str]) -> str | None:
    """Which declared contract a witness input breaks, if any.

    A witness built from an input the interface excludes is not evidence that the
    code is wrong: it shows the guard handles a case the caller cannot produce.
    """
    from src.knowledge.playbook import violates_domain

    for name, declared in domains.items():
        if name in witness and violates_domain(witness[name], declared):
            return f"{name}={witness[name]!r} is outside the declared contract {name}: {declared}"
    return None


def confirmation_verdicts(
    *,
    decision: str,
    item: dict,
    evidence: list[dict],
    counter: list[dict],
    snapshot,
    bundle,
    files,
    sent_text: str,
    contract_domains: dict[str, str] | None = None,
) -> list[FilterVerdict]:
    """Run the chain in order and return every verdict, pass or fail."""
    chain_ok = all(
        isinstance(item.get(key), str) and item[key].strip() for key in _CONFIRMED_CHAIN_FIELDS
    )
    call_path = item.get("call_path")
    chain_ok = chain_ok and isinstance(call_path, list) and bool(call_path) and all(
        isinstance(step, str) and step.strip() for step in call_path
    )
    verdicts = [
        FilterVerdict(
            "chain_completeness", bool(chain_ok),
            "behaviour chain is complete" if chain_ok else "behaviour chain is incomplete",
            {"missing": [k for k in _CONFIRMED_CHAIN_FIELDS if not str(item.get(k) or "").strip()]},
        )
    ]

    cited = list(evidence)
    source_ok = bool(cited) and all(
        valid_citation(c, snapshot, bundle, sent_text) for c in cited
    )
    has_head = any(c.get("side") == "head" for c in cited)
    provenance_ok = source_ok and has_head
    verdicts.append(
        FilterVerdict(
            "source_provenance", provenance_ok,
            "every citation was sent and matches the snapshot" if provenance_ok
            else "a citation was never shown to the model or does not match the snapshot",
            {"head_citation": has_head, "citations": len(cited)},
        )
    )

    if decision == "confirmed" and provenance_ok and chain_ok:
        result = witness_for_evidence(cited)
        if result is None:
            verdicts.append(FilterVerdict(
                "witness_eval", True, "not applicable: no evaluable expression pair",
                {"status": "not_applicable"},
            ))
        elif result.status == "differs":
            violation = domain_violation(result.witness, contract_domains or {})
            verdicts.append(FilterVerdict(
                "witness_eval", violation is None,
                "witness found: the versions differ on a concrete input" if violation is None
                else f"witness input is outside the declared contract: {violation}",
                {"status": result.status, "domain_size": result.domain_size,
                 "witness": result.witness, "base_value": result.base_value,
                 "head_value": result.head_value,
                 **({"contract_violation": violation} if violation else {})},
            ))
        elif result.status == "no_witness":
            verdicts.append(FilterVerdict(
                "witness_eval", False, result.reason,
                {"status": result.status, "domain_size": result.domain_size},
            ))
        else:
            verdicts.append(FilterVerdict(
                "witness_eval", True, f"not evaluable: {result.reason}",
                {"status": result.status},
            ))

    # Dismissals must rest on counter-evidence that was actually sent and matches
    # the snapshot; this lived outside the chain before, which is why the chain
    # accepted `counter` and never looked at it.
    counter_ok = bool(counter) and all(
        valid_citation(c, snapshot, bundle, sent_text) for c in counter
    )
    # Only a dismissal needs counter-evidence: it claims the scenario cannot
    # happen, so it must cite what disproves it. A confirmation needs none, and
    # "no counter-evidence offered" must not count as a passing check for one.
    if decision != "dismissed":
        counter_passed, counter_reason = True, "not required for this decision"
    elif not counter:
        counter_passed, counter_reason = False, "dismissal has no counter-evidence"
    elif counter_ok:
        counter_passed, counter_reason = True, "counter-evidence is valid"
    else:
        counter_passed = False
        counter_reason = "counter-evidence was never shown or does not match the snapshot"
    verdicts.append(FilterVerdict(
        "counter_evidence", counter_passed, counter_reason, {"citations": len(counter)},
    ))

    verdicts.append(FilterVerdict(
        "decision_vocabulary", decision in _VALID_DECISIONS,
        "decision is one of confirmed/dismissed/needs_context" if decision in _VALID_DECISIONS
        else f"unknown decision {decision!r}",
    ))
    return verdicts


def blocked_by_witness(verdicts: list[FilterVerdict]) -> FilterVerdict | None:
    return next(
        (v for v in verdicts if v.name == "witness_eval" and not v.passed), None
    )
