"""Host-selected evidence added around a candidate."""
from __future__ import annotations

import re

# How many caller locations to pull in for one candidate. Bounded because the
# evidence budget, not the search backend, is the scarce resource.
CALLER_EVIDENCE_LIMIT = 3


def _add_contract_callers(bundle, snapshot, files, suggestion) -> None:
    """Add bounded caller evidence for a candidate's enclosing symbol.

    A changed definition is rarely wrong on its own: the defect usually shows up
    where it is called. Searching the frozen snapshot one hop is cheap and lands
    in the same `omitted`/`evicted` accounting as every other excerpt, so a
    failed lookup is recorded rather than assumed to mean "no caller exists".
    """
    from src.reviewer.suggestion_filter import locate_symbol

    symbol, status, _ = locate_symbol(files, suggestion.file_path, suggestion.line)
    if status != "resolved" or not symbol:
        return
    changed = {f.filename for f in files}
    try:
        hits = snapshot.search(symbol, side="head", limit=CALLER_EVIDENCE_LIMIT)
    except Exception as exc:  # a search backend must not break the review
        bundle.omitted.append(
            {"path": suggestion.file_path, "side": "head", "reason": "caller_search_failed",
             "detail": f"{type(exc).__name__}: {exc}"}
        )
        return
    for path, line in hits:
        if path in changed or path == suggestion.file_path:
            continue  # callers already in the diff are visible without help
        bundle.add(path, line, kind="candidate_caller")


_SERIALIZATION_CLAIM = re.compile(
    r"\b(json|omit(s|ted|empty)?|serializ\w*|marshal\w*|response (field|key)s?|"
    r"(fields?|keys?) (are |is )?(present|absent|included))\b",
    re.IGNORECASE,
)


SERIALIZATION_FIELDS_LIMIT = 3


# Search hits come back sorted by path, so the type file can sit behind tests,
# SQL and UI files; only tagged lines are added, so a wider net costs nothing.
SERIALIZATION_SEARCH_LIMIT = 12


def _add_serialization_contract(bundle, snapshot, suggestion) -> None:
    """Add the tag that decides whether a claimed field is serialized.

    A claim that a response "still contains" or "omits" a field depends on the
    field's serialization tag (Go `json:"x,omitempty"`, and similar), which is
    usually in a type file the diff does not touch. Only lines that declare a
    tag for a field named in the claim are added.
    """
    text = suggestion.title + "\n" + suggestion.reason
    if not _SERIALIZATION_CLAIM.search(text):
        return
    names = []
    for name in re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", text):
        if name not in names:
            names.append(name)
    for name in names[:SERIALIZATION_FIELDS_LIMIT]:
        try:
            hits = snapshot.search(name, side="head", limit=SERIALIZATION_SEARCH_LIMIT)
        except Exception as exc:  # a search backend must not break the review
            bundle.omitted.append({"path": suggestion.file_path, "side": "head",
                                   "reason": "serialization_search_failed",
                                   "detail": f"{type(exc).__name__}: {exc}"})
            return
        for path, line in hits:
            source = snapshot.read(path, "head") or ""
            lines = source.splitlines()
            if 0 < line <= len(lines) and re.search(rf"""json:\"{re.escape(name)}[,\"]""", lines[line - 1]):
                # A "candidate_*" kind may evict initial windows under budget
                # pressure; without it the tag line was silently dropped as
                # evidence_budget_exhausted on CrabTrap. A short window is enough.
                bundle.add(path, line, kind="candidate_serialization_contract", max_lines=12)
                break


_CALL_NAME = re.compile(r"\.([a-z_][A-Za-z0-9_]{2,})\b|\b([a-z_][A-Za-z0-9_]{2,})\s*\(")


_NOT_CALLEES = frozenset({
    "if", "for", "while", "return", "print", "len", "str", "int", "list", "dict", "set", "map",
    "new", "self", "this", "super", "then", "else", "elif", "case", "when", "and", "not",
    "function", "lambda", "await", "async", "typeof", "require", "import", "assert", "raise",
})


CALLEE_LIMIT = 4


def _add_callee_contracts(bundle, snapshot, suggestion) -> None:
    """Add definitions of what the anchor lines call, one hop down.

    A claim like "this loop spins forever on truncated input" depends on how the
    called read primitive behaves at end of input; on fastimage it raises, and
    the verifier confirmed eight such claims without seeing that contract.
    """
    text = (snapshot.read(suggestion.file_path, "head") or "").splitlines()
    line = suggestion.line or 0
    window = "\n".join(text[max(0, line - 3):line + 2])
    names = []
    for dotted, called in _CALL_NAME.findall(window):
        name = dotted or called
        if name not in _NOT_CALLEES and name not in names:
            names.append(name)
    for name in names[:CALLEE_LIMIT]:
        try:
            hits = snapshot.search(name, side="head", limit=8)
        except Exception:  # a search backend must not break the review
            return
        for path, number in hits:
            source = (snapshot.read(path, "head") or "").splitlines()
            current = source[number - 1] if 0 < number <= len(source) else ""
            if re.search(rf"\b(def|func|fun|function)\s+(self\.)?{re.escape(name)}\b"
                         rf"|\bfunc\s+\([^)]*\)\s*{re.escape(name)}\b", current):
                bundle.add(path, number, kind="candidate_callee_contract", max_lines=25)
                break


VERIFY_EVIDENCE_EXTRA_TOKENS = 4000


_CAMEL_SYMBOL = re.compile(r"\b[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*\b|\b[A-Z]{2,}[a-z]\w*\b")


def _fallback_missing_reads(bundle, snapshot, outcomes) -> None:
    """A read of a guessed path that does not exist falls back to a symbol search.

    The verifier asks for files by name ("io_stream.rb") when the class lives
    elsewhere; the class name in its stated purpose ("IOStream#read") is a
    better key. Only definitions (class/def/func/fun/struct/interface) are added.
    """
    for outcome in outcomes:
        request = outcome.get("request") if isinstance(outcome.get("request"), dict) else {}
        if request.get("tool", "read") != "read" or outcome.get("status") == "executed_with_evidence":
            continue
        if outcome.get("omission_reason") not in (None, "source_unavailable"):
            continue
        purpose = str(request.get("purpose") or "")
        for symbol in dict.fromkeys(_CAMEL_SYMBOL.findall(purpose)):
            try:
                hits = snapshot.search(symbol, side=request.get("side", "head"), limit=6)
            except Exception:  # a search backend must not break the review
                return
            added = None
            for path, line in hits:
                text = (snapshot.read(path, request.get("side", "head")) or "").splitlines()
                current = text[line - 1] if 0 < line <= len(text) else ""
                if re.search(rf"\b(class|module|def|func|fun|struct|interface|type)\s+{re.escape(symbol)}\b",
                             current):
                    added = bundle.add(path, line, request.get("side", "head"),
                                       "requested_definition_fallback")
                    break
            if added:
                outcome["fallback"] = {"symbol": symbol, "evidence_ref": added}
                break
