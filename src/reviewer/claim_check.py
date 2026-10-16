"""Check what a pull request says it does against what it actually does.

The description is a claim about behaviour; the diff is the implementation. When
they disagree, the disagreement is itself the defect - it is exactly the shape of a
gold finding this project missed on a real PR ("the description promises a shared
single-thread executor, but four entry points still spawn their own thread").

This prototype is deterministic and costs nothing: it lifts the claim-bearing
sentences out of the description, reduces each to the terms that carry meaning, and
looks for those terms in the added lines. A claim whose mechanism never appears in
the diff becomes a candidate. A model can be asked about the hard cases later; this
step only needs to be right about the obvious ones, and it never guesses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.models import ChangedFile

# Sentences that claim a behaviour change. A description also contains headings,
# issue links and prose, and treating those as claims would drown the signal.
_CLAIM = re.compile(
    r"\b(?:add|added|adds|implement|implemented|introduc|replace|replaced|reject|"
    r"rejects|allow|allowlist|allow-list|serialis|serializ|guard|validate|validates|"
    r"enforce|limit|limits|strip|remov|migrat|switch|share|shared|lock|use|uses|"
    r"now|instead)\b",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{3,}")
_REPLACEMENT = re.compile(r"\b(?:replace[sd]?|replacing|instead of|switch(?:es|ed)?\s+(?:to|from)|"
                          r"migrat(?:e|es|ed|ing)|remov(?:e|es|ed|ing))\b", re.IGNORECASE)
_QUOTED = re.compile(r"`([^`]+)`|\"([^\"]+)\"")
_STOP = frozenset({
    "that", "this", "with", "from", "have", "has", "been", "will", "also", "into",
    "when", "than", "then", "they", "their", "there", "which", "while", "would",
    "should", "could", "only", "more", "most", "some", "such", "both", "each",
    "about", "after", "before", "because", "between", "during", "through", "using",
    "used", "uses", "make", "makes", "made", "does", "doing", "done", "ensure",
    "allow", "allows", "prevent", "prevents", "avoid", "avoids", "code", "case",
    "cases", "path", "paths", "value", "values", "added", "removed", "instead",
    "rather", "same", "other", "these", "those", "user", "users",
})


# Words that appear in almost any diff. A claim is not implemented merely because
# the diff contains the word "thread" or "push": the mechanism term - executor,
# allowlist, serialise - is what the claim is actually about, and that is the term
# that has to be found. Without this the prototype called the jadx claim supported
# because its diff happened to contain pull, push and thread.
_GENERIC = frozenset({
    "thread", "threads", "pull", "push", "operations", "operation", "value", "values",
    "data", "code", "file", "files", "engine", "feature", "pattern", "patterns",
    "length", "string", "strings", "list", "lists", "item", "items", "object",
    "objects", "request", "requests", "response", "server", "client", "config",
    "state", "result", "results", "error", "errors", "input", "output", "function",
    "method", "class", "module", "field", "fields", "update", "updates", "change",
    "changes", "character", "characters", "flag", "flags", "call", "calls",
})


@dataclass
class Claim:
    text: str
    terms: list[str]
    evidence: list[str] = field(default_factory=list)
    # A replacement claim names the thing it replaces. If that thing is still being
    # written in the added lines, the replacement did not happen - the description
    # and the code disagree, which is the defect. The jadx gold finding is exactly
    # this: "replaces ad-hoc kotlin.concurrent.thread calls" while the diff adds
    # them.
    replaced_terms: list[str] = field(default_factory=list)
    still_added: list[str] = field(default_factory=list)

    @property
    def contradicted(self) -> bool:
        return bool(self.still_added)

    @property
    def supported(self) -> bool:
        if self.contradicted:
            return False
        distinctive = [term for term in self.evidence if term not in _GENERIC]
        if distinctive:
            return True
        # Only generic terms matched: fall back to coverage rather than credit the
        # claim for words that appear in any diff.
        return bool(self.terms) and len(self.evidence) >= max(2, len(self.terms) // 2)

    @property
    def status(self) -> str:
        if self.contradicted:
            return "contradicted"
        return "supported" if self.supported else "unimplemented"


def _terms(sentence: str) -> list[str]:
    """The words that carry meaning, longest first so mechanism names survive."""
    seen: list[str] = []
    for match in _IDENTIFIER.findall(sentence):
        lowered = match.lower()
        if lowered in _STOP or lowered in seen:
            continue
        seen.append(lowered)
    seen.sort(key=len, reverse=True)
    return seen[:8]


def extract_claims(body: str | None) -> list[Claim]:
    """Claim-bearing sentences, with the terms that would evidence them."""
    claims: list[Claim] = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", body or ""):
        line = raw.strip().lstrip("*-• \t")
        if len(line) < 12 or line.startswith("#") or not _CLAIM.search(line):
            continue
        replaced: list[str] = []
        if _REPLACEMENT.search(line):
            for quoted in _QUOTED.findall(line):
                inner = quoted[0] or quoted[1]
                replaced.extend(part.strip() for part in inner.split() if part.strip())
            for term in _terms(line):
                if "." in term or "_" in term:
                    replaced.append(term)
        claims.append(Claim(text=line[:300], terms=_terms(line), replaced_terms=replaced))
    return claims


def added_text(files: list[ChangedFile]) -> str:
    """Everything the diff adds, which is where a claim must be visible."""
    chunks: list[str] = []
    for file in files:
        chunks.append(file.filename)
        for line in (file.patch or "").splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                chunks.append(line[1:])
    return "\n".join(chunks)


def check_claims(body: str | None, files: list[ChangedFile]) -> list[Claim]:
    """Each claim, marked with the terms the diff actually contains."""
    haystack = added_text(files).lower()
    claims = extract_claims(body)
    for claim in claims:
        claim.evidence = [term for term in claim.terms if term in haystack]
        claim.still_added = [
            term for term in claim.replaced_terms
            if term.lower().replace("`", "") in haystack and term.lower() not in _GENERIC
        ]
    return claims


def unimplemented(body: str | None, files: list[ChangedFile]) -> list[Claim]:
    """Claims whose mechanism appears nowhere in the added lines.

    A model later decides whether that disagreement is a defect; this only reports
    that the description and the code do not mention the same thing, which is a
    fact about the text and needs no judgement.
    """
    return [claim for claim in check_claims(body, files) if not claim.supported]
