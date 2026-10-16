"""The knowledge playbook.

A wiki-shaped store of review knowledge: contract notes, project rules, retrieval
hints and the false-positive catalogue. Entries are Markdown with YAML
front-matter so they are human-readable, diffable and reviewable, and so a single
entry can be added or amended without rewriting the file - ACE's point being that
whole-context rewrites erode detail (context collapse) while brevity bias drops
domain insight.

Three properties are load-bearing:

* **Delta only.** Entries are addressed by ``id``; loading refuses duplicate ids
  and nothing here regenerates the file.
* **Scope-filtered.** An entry states the repo, languages, paths or symbols it
  applies to, and ``recall`` returns only what matches, ranked by specificity.
* **Mechanically expirable.** An entry that names symbols can be checked against
  the frozen snapshot; when they no longer exist it expires without any model
  judgement being involved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PLAYBOOK = Path("knowledge/playbook.md")

# Scope matching compares language names, so suffixes are normalised here rather
# than at every call site.
_LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
    ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby", ".md": "markdown",
    ".kt": "kotlin",
}


def language_of(path: str) -> str:
    """The scope language name for a path (``src/a.py`` -> ``python``)."""
    return _LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), Path(path).suffix.lstrip(".").lower())
_VALID_KINDS = {"contract", "project_rule", "false_positive_pattern", "retrieval_hint"}
_VALID_STATUS = {"candidate", "authoritative", "expired", "rejected"}
# Declared value domains for a contract entry. A witness input outside the
# declared domain is not evidence that the code is wrong.
_VALID_DOMAINS = {"int", "sequence", "non_empty_str", "number"}
_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class PlaybookEntry:
    id: str
    kind: str
    statement: str
    body: str = ""
    scope: dict = field(default_factory=dict)
    source: str = ""
    intent_symptoms: tuple[str, ...] = ()
    domain: dict = field(default_factory=dict)
    version: int = 1
    status: str = "candidate"
    supersedes: str | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "statement": self.statement,
            "scope": self.scope, "status": self.status, "version": self.version,
            "intent_symptoms": list(self.intent_symptoms), "domain": self.domain,
        }


class PlaybookError(ValueError):
    """The playbook is malformed; refusing to load it silently would hide that."""


def parse_entry(text: str, *, origin: str = "") -> PlaybookEntry:
    match = _FRONT_MATTER.match(text.strip())
    if not match:
        raise PlaybookError(f"{origin}: entry has no YAML front-matter")
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise PlaybookError(f"{origin}: front-matter must be a mapping")
    kind = str(meta.get("kind", ""))
    status = str(meta.get("status", "candidate"))
    if kind not in _VALID_KINDS:
        raise PlaybookError(f"{origin}: unknown kind {kind!r}")
    if status not in _VALID_STATUS:
        raise PlaybookError(f"{origin}: unknown status {status!r}")
    entry_id = str(meta.get("id", "")).strip()
    statement = str(meta.get("statement", "")).strip()
    if not entry_id or not statement:
        raise PlaybookError(f"{origin}: entry needs an id and a statement")
    scope = meta.get("scope") or {}
    if not isinstance(scope, dict):
        raise PlaybookError(f"{origin}: scope must be a mapping")
    symptoms = meta.get("intent_symptoms") or ()
    if isinstance(symptoms, str):
        symptoms = (symptoms,)
    domain = meta.get("domain") or {}
    if not isinstance(domain, dict):
        raise PlaybookError(f"{origin}: domain must be a mapping of name -> type")
    for name, declared in domain.items():
        if declared not in _VALID_DOMAINS:
            raise PlaybookError(f"{origin}: unknown domain type {declared!r} for {name!r}")
    return PlaybookEntry(
        id=entry_id, kind=kind, statement=statement, body=match.group(2).strip(),
        scope=scope, source=str(meta.get("source", "")),
        intent_symptoms=tuple(str(item) for item in symptoms),
        domain={str(name): str(declared) for name, declared in domain.items()},
        version=int(meta.get("version", 1)), status=status,
        supersedes=meta.get("supersedes"),
    )


_ENTRY = re.compile(r"---\s*\n(.*?)\n---\s*\n(.*?)(?=\n---\s*\n|\Z)", re.DOTALL)


def load_text(text: str, *, origin: str = "playbook") -> list[PlaybookEntry]:
    """Parse entries from playbook text, refusing duplicate ids."""
    entries: list[PlaybookEntry] = []
    seen: set[str] = set()
    for index, match in enumerate(_ENTRY.finditer(text)):
        entry = parse_entry(f"---\n{match.group(1)}\n---\n{match.group(2)}",
                            origin=f"{origin}:{index}")
        if entry.id in seen:
            raise PlaybookError(f"{origin}: duplicate entry id {entry.id!r}")
        seen.add(entry.id)
        entries.append(entry)
    return entries


def load_playbook(path: Path | str = DEFAULT_PLAYBOOK) -> list[PlaybookEntry]:
    """Load the playbook from disk; a missing file is an empty playbook."""
    location = Path(path)
    if not location.exists():
        return []
    return load_text(location.read_text(encoding="utf-8"), origin=str(location))


def _matches(
    entry: PlaybookEntry,
    *,
    repo: str,
    paths: list[str],
    languages: list[str],
    symbols_by_path: dict[str, set[str]] | None,
) -> bool:
    scope = entry.scope
    scoped_repo = scope.get("repo")
    if scoped_repo not in (None, "*") and scoped_repo != repo:
        return False
    scope_languages = scope.get("languages")
    scope_paths = scope.get("paths")
    matching_paths = [
        path for path in paths
        if (not scope_paths or any(Path(path).match(pattern) for pattern in scope_paths))
        and (not scope_languages or language_of(path) in scope_languages)
    ]
    # Some callers only know the aggregate language set and do not pass paths.
    # Keep that legacy scope mode; with paths present, check each file directly.
    if ((scope_paths or scope_languages) and not matching_paths
            and (paths or not scope_languages or not set(scope_languages) & set(languages))):
        return False
    scoped_symbols = [str(symbol) for symbol in (scope.get("symbols") or ())]
    if not scoped_symbols or "*" in scoped_symbols:
        return True
    # Missing AST data is unknown, not a wildcard. Match the symbol and the
    # path/language on the same changed file so a mixed-language PR cannot join
    # unrelated scope facts from different files.
    if symbols_by_path is None:
        return False
    scoped_paths = matching_paths or paths
    return any(
        path in symbols_by_path and bool(set(scoped_symbols) & symbols_by_path[path])
        for path in scoped_paths
    )


def recall(entries: list[PlaybookEntry], *, repo: str = "", paths: list[str] | None = None,
           languages: list[str] | None = None, symbols_by_path: dict[str, set[str]] | None = None,
           k: int = 5) -> list[PlaybookEntry]:
    """Scope-filtered top-k. Only authoritative entries are offered to the model."""
    paths = paths or []
    languages = languages or []
    usable = [e for e in entries if e.status == "authoritative"]
    matched = [e for e in usable if _matches(
        e, repo=repo, paths=paths, languages=languages, symbols_by_path=symbols_by_path
    )]
    matched.sort(key=lambda e: (len(e.scope), len(e.statement)), reverse=True)
    return matched[:k]


def expired_by_symbols(entries: list[PlaybookEntry], symbol_exists) -> list[PlaybookEntry]:
    """Entries whose named symbols vanished from the frozen snapshot.

    Mechanical on purpose: no model decides whether knowledge is still true.
    """
    expired: list[PlaybookEntry] = []
    for entry in entries:
        symbols = [str(s) for s in (entry.scope.get("symbols") or ())]
        if symbols and not any(symbol_exists(symbol) for symbol in symbols):
            expired.append(entry)
    return expired


def contract_domains(entries: list[PlaybookEntry]) -> dict[str, str]:
    """Return only run-global contract domains without narrower path/symbol scope.

    The current verifier accepts one flat name-to-domain map. Passing a scoped
    contract through it would apply that contract to unrelated candidates in the
    same PR. Such entries need candidate-location binding before they can become
    mechanical domains. Conflicting global declarations fail closed.
    """
    domains: dict[str, str] = {}
    for entry in entries:
        if entry.kind == "contract" and entry.status == "authoritative":
            scope = entry.scope
            if scope.get("symbols") or scope.get("paths") or scope.get("languages"):
                continue
            for name, declared in entry.domain.items():
                previous = domains.get(name)
                if previous is not None and previous != declared:
                    raise PlaybookError(
                        f"conflicting contract domain for {name!r}: {previous!r} vs {declared!r}"
                    )
                domains[name] = declared
    return domains


def violates_domain(value: object, declared: str) -> bool:
    """Whether a witness value falls outside a declared contract domain."""
    if declared == "int":
        return isinstance(value, bool) or not isinstance(value, int)
    if declared == "number":
        return isinstance(value, bool) or not isinstance(value, (int, float))
    if declared == "sequence":
        return not isinstance(value, (list, tuple, str))
    if declared == "non_empty_str":
        return not isinstance(value, str) or not value
    return False


def render(entries: list[PlaybookEntry], *, limit: int = 2000) -> str:
    """Render recalled entries for the prompt, stating they are guidance."""
    if not entries:
        return ""
    lines = [
        "## Project knowledge (accumulated guidance, not an authority over this diff)",
    ]
    for entry in entries:
        lines.append(f"- [{entry.kind}] {entry.statement}")
        if entry.body:
            lines.append(f"  {entry.body.splitlines()[0]}")
    return "\n".join(lines)[:limit]
