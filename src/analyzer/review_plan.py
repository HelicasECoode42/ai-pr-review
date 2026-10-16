"""Deterministic source relationships and bounded review units; no model calls.

Edges are syntax-level evidence, not a promise of runtime reachability. Imports
resolve within the supplied snapshot inventory; ambiguous symbols stay weak.
"""

from __future__ import annotations

import ast
import hashlib
import json
import posixpath
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field

from src.analyzer.context_builder import count_tokens
from src.analyzer.diff_parser import parse_file_hunks
from src.analyzer.repository_context import Snapshot, safe_source_path
from src.application.protocol import BudgetLine, BudgetReport, Coverage, FrozenContract, Omission
from src.models import ChangedFile, DiffHunk, FileStatus


class SourceSymbol(FrozenContract):
    name: str
    start: int
    end: int


class SourceFile(FrozenContract):
    path: str
    side: Literal["head", "base"]
    content_hash: str
    parser: str
    symbols: tuple[SourceSymbol, ...] = ()
    imports: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class Relationship(FrozenContract):
    source: str
    target: str
    kind: str
    strength: Literal["strong", "weak"]
    symbol: str | None = None


class EvidenceRef(FrozenContract):
    path: str
    side: Literal["head", "base"]
    start: int
    end: int
    content_hash: str


class LineSpan(FrozenContract):
    """Source lines a fragment covers, on each side.

    ``None`` means the fragment covers no line on that side (for example a
    fragment holding only the ``@@`` header). ``cuts_a_diff_line`` is true when
    the fragment boundary falls inside one diff line, so the fragment must not
    be read as a complete line.
    """

    new_start: int | None = None
    new_end: int | None = None
    old_start: int | None = None
    old_end: int | None = None
    cuts_a_diff_line: bool = False


class ChangeSlice(FrozenContract):
    slice_id: str
    path: str
    side: Literal["head", "base"]
    hunk_id: str
    hunk_index: int
    fragment_index: int
    fragment: str
    lines: LineSpan = Field(default_factory=LineSpan)
    symbols: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    estimated_tokens: int = Field(ge=0)


class ReviewUnit(FrozenContract):
    unit_id: str
    slices: tuple[ChangeSlice, ...]
    estimated_tokens: int = Field(ge=0)
    evidence: tuple[EvidenceRef, ...] = ()
    related_paths: tuple[str, ...] = ()


class SplitSymbol(FrozenContract):
    path: str
    symbol: str
    units: int
    fragments: int


class CutEdge(FrozenContract):
    source: str
    target: str
    kind: str
    strength: Literal["strong", "weak"]
    symbol: str | None = None
    targets_changed_symbol: bool = False


class ExternalCaller(FrozenContract):
    source: str
    target: str
    symbol: str


class CutCounts(FrozenContract):
    units: int = 0
    slices: int = 0
    split_symbols: int = 0
    cut_edges_strong: int = 0
    cut_edges_weak: int = 0
    cut_edges_targets_changed_symbol: int = 0
    external_callers_of_changed: int = 0


class CutReport(FrozenContract):
    """Measurement of what the current split separates; never used for grouping."""

    split_symbols: tuple[SplitSymbol, ...] = ()
    cut_edges: tuple[CutEdge, ...] = ()
    external_callers_of_changed: tuple[ExternalCaller, ...] = ()
    counts: CutCounts = Field(default_factory=CutCounts)


class CoverageSummary(FrozenContract):
    """Whether every changed file and hunk this plan read is actually accounted for.

    ``planned_hunks`` counts hunks represented by at least one slice;
    ``unavailable_evidence_hunks`` counts hunks kept in the plan but without the
    frozen source needed to locate their definitions. The same hunk can appear
    in both, so the two are not expected to sum to ``parsed_hunks``. A hunk with
    neither a slice nor an explanation shows up in ``unaccounted_hunks`` and
    ``complete`` turns false, which is the regression signal for silent drops.
    """

    changed_files: int = 0
    parsed_hunks: int = 0
    planned_hunks: int = 0
    unavailable_evidence_hunks: int = 0
    excluded_files: int = 0
    unaccounted_hunks: tuple[str, ...] = ()
    unaccounted_files: tuple[str, ...] = ()
    complete: bool = True


class ReviewPlan(FrozenContract):
    version: str = "syntax-plan-v2"
    base: str = Field(pattern=r"^[0-9a-f]{40}$")
    head: str = Field(pattern=r"^[0-9a-f]{40}$")
    token_budget: int = Field(gt=0)
    units: tuple[ReviewUnit, ...]
    files: tuple[SourceFile, ...]
    relationships: tuple[Relationship, ...]
    coverage: tuple[Coverage, ...]
    limitations: tuple[str, ...]
    cuts: CutReport = Field(default_factory=CutReport)
    coverage_summary: CoverageSummary = Field(default_factory=CoverageSummary)
    budgets: BudgetReport = Field(default_factory=lambda: BudgetReport(scope="build_review_plan"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _python(path: str, text: str, side: str) -> SourceFile:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return SourceFile(
            path=path,
            side=side,
            content_hash=_hash(text),
            parser="python-ast",
            limitations=("parse_failed",),
        )
    symbols, imports, calls = [], set(), set()
    package = path.split("/")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(
                SourceSymbol(
                    name=node.name,
                    start=min([node.lineno, *[d.lineno for d in node.decorator_list]]),
                    end=node.end_lineno,
                )
            )
        elif isinstance(node, ast.Import):
            imports.update(a.name.replace(".", "/") for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else []
            stem = "/".join([*prefix, *(node.module or "").split(".")]).strip("/")
            imports.add(stem)
            imports.update(stem + "/" + a.name for a in node.names if a.name != "*")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
    return SourceFile(
        path=path,
        side=side,
        content_hash=_hash(text),
        parser="python-ast",
        symbols=tuple(sorted(symbols, key=lambda s: (s.start, s.name))),
        imports=tuple(sorted(imports)),
        calls=tuple(sorted(calls)),
        limitations=("references_are_syntactic_not_runtime_calls",),
    )


def _typescript(path: str, text: str, side: str) -> SourceFile:
    # Grammar is optional: absence is explicit, never a regex pretending to be AST.
    try:
        import tree_sitter_typescript
        from tree_sitter import Language, Parser
    except ImportError:
        return SourceFile(
            path=path,
            side=side,
            content_hash=_hash(text),
            parser="unavailable",
            limitations=("typescript_grammar_not_installed",),
        )
    grammar = (
        tree_sitter_typescript.language_tsx
        if path.endswith(".tsx")
        else tree_sitter_typescript.language_typescript
    )
    parser = Parser(Language(grammar()))
    raw = text.encode()
    tree = parser.parse(raw)
    symbols, imports, calls = [], set(), set()
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        stack.extend(reversed(n.children))
        if n.type in {
            "function_declaration",
            "method_definition",
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
        }:
            name = n.child_by_field_name("name")
            if name:
                symbols.append(
                    SourceSymbol(
                        name=raw[name.start_byte : name.end_byte].decode(),
                        start=n.start_point.row + 1,
                        end=n.end_point.row + 1,
                    )
                )
        elif n.type in {"import_statement", "export_statement"}:
            source = n.child_by_field_name("source")
            if source:
                imports.add(raw[source.start_byte : source.end_byte].decode()[1:-1])
        elif n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn and fn.type == "identifier":
                calls.add(raw[fn.start_byte : fn.end_byte].decode())
    limits = ["no_typescript_type_resolution", "javascript_dynamic_imports_not_resolved"]
    if tree.root_node.has_error:
        limits.append("parse_errors")
    return SourceFile(
        path=path,
        side=side,
        content_hash=_hash(text),
        parser="tree-sitter-typescript",
        symbols=tuple(sorted(symbols, key=lambda s: (s.start, s.name))),
        imports=tuple(sorted(imports)),
        calls=tuple(sorted(calls)),
        limitations=tuple(limits),
    )


def _index(path: str, text: str, side: str) -> SourceFile:
    suffix = PurePosixPath(path).suffix
    if suffix == ".py":
        return _python(path, text, side)
    if suffix in {".ts", ".tsx"}:
        return _typescript(path, text, side)
    return SourceFile(
        path=path,
        side=side,
        content_hash=_hash(text),
        parser="unavailable",
        limitations=("language_relationships_not_supported",),
    )


class SourceIndexCache:
    """Run-scoped, bounded cache of immutable parse results, never model results."""

    def __init__(self, max_entries: int = 5000):
        if max_entries < 1:
            raise ValueError("Index cache must have positive capacity")
        self.max_entries = max_entries
        self.entries: dict[tuple[str, str, str], SourceFile] = {}
        self.hits = 0

    def parse(self, path: str, text: str, side: str) -> SourceFile:
        key = (path, side, _hash(text))
        if key in self.entries:
            self.hits += 1
            return self.entries[key]
        parsed = _index(path, text, side)
        if len(self.entries) >= self.max_entries:
            del self.entries[next(iter(self.entries))]
        self.entries[key] = parsed
        return parsed


def _resolve(stem: str, path: str, inventory: set[str]) -> tuple[str, ...]:
    if stem.startswith("."):
        stem = posixpath.normpath(posixpath.join(posixpath.dirname(path), stem))
    candidates = [
        stem,
        stem + ".py",
        stem + "/__init__.py",
        stem + ".ts",
        stem + ".tsx",
        stem + "/index.ts",
        stem + "/index.tsx",
    ]
    if stem.endswith((".js", ".jsx")):
        candidates += [stem.rsplit(".", 1)[0] + ".ts", stem.rsplit(".", 1)[0] + ".tsx"]
    return tuple(p for p in candidates if p in inventory and p != path)


def _hunk_line_meta(hunk: DiffHunk) -> list[tuple[str, int | None, int | None]]:
    """Per raw hunk line: (kind, old line, new line). Header and markers consume nothing.

    The prefix rules mirror diff_parser._build_hunk, so the line span a fragment
    reports cannot disagree with the added/removed lines extracted from the hunk.
    """
    meta: list[tuple[str, int | None, int | None]] = []
    old_cursor, new_cursor = hunk.old_start, hunk.new_start
    for index, line in enumerate(hunk.raw.splitlines()):
        if index == 0:
            meta.append(("header", None, None))
        elif line.startswith("\\"):
            meta.append(("marker", None, None))
        elif line.startswith("+") and not line.startswith("+++"):
            meta.append(("added", None, new_cursor))
            new_cursor += 1
        elif line.startswith("-") and not line.startswith("---"):
            meta.append(("removed", old_cursor, None))
            old_cursor += 1
        else:
            meta.append(("context", old_cursor, new_cursor))
            old_cursor += 1
            new_cursor += 1
    return meta


def _span_for(
    pieces: list[tuple[int, bool]], meta: list[tuple[str, int | None, int | None]]
) -> LineSpan:
    new_numbers = [meta[index][2] for index, _ in pieces if meta[index][2] is not None]
    old_numbers = [meta[index][1] for index, _ in pieces if meta[index][1] is not None]
    return LineSpan(
        new_start=min(new_numbers) if new_numbers else None,
        new_end=max(new_numbers) if new_numbers else None,
        old_start=min(old_numbers) if old_numbers else None,
        old_end=max(old_numbers) if old_numbers else None,
        cuts_a_diff_line=any(not whole_line for _, whole_line in pieces),
    )


def _split_hunk(
    hunk: DiffHunk, token_budget: int, limitations: set[str]
) -> list[tuple[str, LineSpan]]:
    """Split one hunk into budget-sized fragments, byte-preserving and in order.

    Every fragment also reports the source line span it covers. A pathological
    line longer than the whole budget is cut by characters; both pieces are then
    marked as cutting a diff line rather than pretending to be whole lines.
    """
    meta = _hunk_line_meta(hunk)
    out: list[tuple[str, LineSpan]] = []
    buffer = ""
    pieces: list[tuple[int, bool]] = []

    def flush() -> None:
        nonlocal buffer, pieces
        out.append((buffer, _span_for(pieces, meta)))
        buffer = ""
        pieces = []

    for line_index, chunk in enumerate(hunk.raw.splitlines(keepends=True)):
        if buffer and count_tokens(buffer + chunk) > token_budget:
            flush()
        split_line = False
        while count_tokens(chunk) > token_budget:
            split_line = True
            limitations.add("oversized_hunk_fragmented")
            lo, hi = 1, len(chunk)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if count_tokens(chunk[:mid]) <= token_budget:
                    lo = mid
                else:
                    hi = mid - 1
            pieces.append((line_index, False))
            buffer += chunk[:lo]
            chunk = chunk[lo:]
            flush()
        if chunk:
            buffer += chunk
            pieces.append((line_index, not split_line))
    if buffer:
        flush()
    return out


def _cut_report(units: list[ReviewUnit], relationships: tuple[Relationship, ...]) -> CutReport:
    unit_of = defaultdict(set)
    changed_symbols = defaultdict(set)
    occurrences = defaultdict(list)
    slice_count = 0
    for index, unit in enumerate(units):
        for s in unit.slices:
            slice_count += 1
            unit_of[s.path].add(index)
            changed_symbols[s.path].update(s.symbols)
            for name in set(s.symbols):
                occurrences[(s.path, name)].append((index, s.hunk_id))
    split = []
    for (path, name), occ in sorted(occurrences.items()):
        unit_count = len({i for i, _ in occ})
        fragment_count = len(occ)
        if unit_count > 1 or fragment_count > len({h for _, h in occ}):
            split.append(
                SplitSymbol(path=path, symbol=name, units=unit_count, fragments=fragment_count)
            )
    cut, external = [], []
    for e in relationships:
        if e.source not in unit_of or e.target not in unit_of:
            continue
        symbol_hit = e.kind == "symbol_reference" and e.symbol in changed_symbols[e.target]
        if not unit_of[e.source] & unit_of[e.target]:
            cut.append(
                CutEdge(
                    source=e.source,
                    target=e.target,
                    kind=e.kind,
                    strength=e.strength,
                    symbol=e.symbol,
                    targets_changed_symbol=bool(symbol_hit),
                )
            )
    for e in relationships:
        if (
            e.kind == "symbol_reference"
            and e.strength == "weak"
            and e.target in unit_of
            and e.symbol in changed_symbols[e.target]
            and e.source not in unit_of
        ):
            external.append(ExternalCaller(source=e.source, target=e.target, symbol=e.symbol))
    cut.sort(key=lambda c: (c.source, c.target, c.kind, c.strength, c.symbol or ""))
    external.sort(key=lambda c: (c.source, c.target, c.symbol))
    return CutReport(
        split_symbols=tuple(split),
        cut_edges=tuple(cut),
        external_callers_of_changed=tuple(external[:50]),
        counts=CutCounts(
            units=len(units),
            slices=slice_count,
            split_symbols=len(split),
            cut_edges_strong=sum(c.strength == "strong" for c in cut),
            cut_edges_weak=sum(c.strength == "weak" for c in cut),
            cut_edges_targets_changed_symbol=sum(c.targets_changed_symbol for c in cut),
            external_callers_of_changed=len(external),
        ),
    )


def build_review_plan(
    snapshot: Snapshot,
    files: list[ChangedFile],
    paths: list[str],
    *,
    token_budget: int = 6000,
    index_file_limit: int = 5000,
    index_char_limit: int = 20_000_000,
    public_degree: int = 8,
    index_cache: SourceIndexCache | None = None,
) -> ReviewPlan:
    """Plan every eligible hunk; extra evidence is referenced, not silently charged.

    Token budget bounds rendered diff fragments. Evidence references are lazy and
    consume the later retrieval budget (D3). Output makes that limitation explicit.
    """
    if token_budget < 128 or index_file_limit < 1 or index_char_limit < 1 or public_degree < 2:
        raise ValueError("Invalid planning budget")
    coverage, limitations, indexed, contents = [], set(), {}, {}
    hunk_ids_by_file: dict[str, list[str]] = defaultdict(list)
    cache = index_cache if index_cache is not None else SourceIndexCache(index_file_limit)
    inventory = {p for p in paths if safe_source_path(p)}
    inventory.update(f.filename for f in files if safe_source_path(f.filename))
    # Changed code first; finite index never suppresses its diff coverage.
    changed_paths = {f.filename for f in files}
    ordered = sorted(inventory, key=lambda p: (p not in changed_paths, p))
    chars = 0
    for i, path in enumerate(ordered):
        if i >= index_file_limit or chars >= index_char_limit:
            limitations.add("repository_index_budget_exhausted")
            coverage.append(Coverage(path=path, status="unavailable", reason="index_budget"))
            continue
        text = snapshot.read(path)
        if text is None:
            coverage.append(Coverage(path=path, status="unavailable", reason="head_unavailable"))
            continue
        if chars + len(text) > index_char_limit:
            limitations.add("repository_index_budget_exhausted")
            coverage.append(Coverage(path=path, status="unavailable", reason="index_budget"))
            continue
        chars += len(text)
        contents[(path, "head")] = text
        indexed[path] = cache.parse(path, text, "head")
    edges = set()
    definitions = defaultdict(set)
    for path, source in indexed.items():
        for symbol in source.symbols:
            definitions[symbol.name].add(path)
        for stem in source.imports:
            targets = _resolve(stem, path, set(indexed))
            for target in targets:
                edges.add((path, target, "import", "strong" if len(targets) == 1 else "weak", None))
    for path, source in indexed.items():
        for call in source.calls:
            targets = definitions[call] - {path}
            if len(targets) > 20:
                limitations.add("ambiguous_symbol_reference_limit")
                targets = set(sorted(targets)[:20])
            for target in sorted(targets):
                # Bare matching is always weak without binding/type resolution.
                edges.add((path, target, "symbol_reference", "weak", call))
    relationships = tuple(
        Relationship(source=e[0], target=e[1], kind=e[2], strength=e[3], symbol=e[4])
        for e in sorted(edges, key=lambda e: tuple(str(v) for v in e))
    )
    slices = []
    for file in sorted(files, key=lambda f: f.filename):
        if not safe_source_path(file.filename):
            coverage.append(Coverage(path=file.filename, status="excluded", reason="source_policy"))
            continue
        hunks = parse_file_hunks(file)
        if not hunks:
            coverage.append(
                Coverage(
                    path=file.filename, status="unavailable", reason="missing_or_invalid_patch"
                )
            )
            continue
        side = "base" if file.status == FileStatus.REMOVED else "head"
        source_path = (file.previous_filename or file.filename) if side == "base" else file.filename
        text = contents.get((source_path, side))
        old_path = file.previous_filename or file.filename
        old_source = None
        if file.status != FileStatus.ADDED:
            if len(indexed) >= index_file_limit or chars >= index_char_limit:
                limitations.add("repository_index_budget_exhausted")
            else:
                old_text = snapshot.read(old_path, "base")
                if old_text is not None and chars + len(old_text) <= index_char_limit:
                    chars += len(old_text)
                    old_source = cache.parse(old_path, old_text, "base")
                    indexed["base:" + old_path] = old_source
                    if side == "base":
                        text = old_text
                elif old_text is not None:
                    limitations.add("repository_index_budget_exhausted")
        source = old_source if side == "base" else indexed.get(file.filename)
        for number, hunk in enumerate(hunks):
            hid = _hash(file.filename + "\0" + str(number) + "\0" + hunk.raw)
            hunk_ids_by_file[file.filename].append(hid)
            line = hunk.old_start if side == "base" else hunk.new_start
            span = hunk.old_count if side == "base" else hunk.new_count
            symbols = (
                tuple(s for s in source.symbols if s.start < line + max(span, 1) and s.end >= line)
                if source
                else ()
            )
            refs = tuple(
                EvidenceRef(
                    path=source_path,
                    side=side,
                    start=s.start,
                    end=s.end,
                    content_hash=source.content_hash,
                )
                for s in symbols
            )
            if old_source is not None and side == "head":
                refs += tuple(
                    EvidenceRef(
                        path=old_path,
                        side="base",
                        start=s.start,
                        end=s.end,
                        content_hash=old_source.content_hash,
                    )
                    for s in old_source.symbols
                    if s.start < hunk.old_start + max(hunk.old_count, 1) and s.end >= hunk.old_start
                )
            elif file.status != FileStatus.ADDED and old_source is None:
                coverage.append(
                    Coverage(
                        path=old_path,
                        hunk_id=hid,
                        status="unavailable",
                        reason="base_definition_unavailable",
                    )
                )
            # Oversized hunks split without dropping a byte. Each fragment keeps
            # the source lines it covers and whether its edge cuts a diff line.
            for part, (fragment, span) in enumerate(_split_hunk(hunk, token_budget, limitations)):
                slices.append(
                    ChangeSlice(
                        slice_id=_hash(hid + ":" + str(part)),
                        path=file.filename,
                        side=side,
                        hunk_id=hid,
                        hunk_index=number,
                        fragment_index=part,
                        fragment=fragment,
                        lines=span,
                        symbols=tuple(s.name for s in symbols),
                        evidence=refs,
                        estimated_tokens=count_tokens(fragment),
                    )
                )
            coverage.append(Coverage(path=file.filename, hunk_id=hid, status="planned"))
            if text is None:
                coverage.append(
                    Coverage(
                        path=file.filename,
                        hunk_id=hid,
                        status="unavailable",
                        reason="definition_source_unavailable",
                    )
                )
    adjacency = defaultdict(set)
    for edge in relationships:
        if edge.strength == "strong":
            adjacency[edge.source].add(edge.target)
            adjacency[edge.target].add(edge.source)
    # Greedy bounded traversal avoids giant connected components. Common hubs
    # remain related evidence, never force all their consumers into one unit.
    remaining = {s.slice_id: s for s in slices}
    units = []
    while remaining:
        first = min(remaining.values(), key=lambda s: (s.path, s.hunk_index, s.fragment_index))
        chosen, cost = [], 0
        frontier = {first.path}
        while True:
            eligible = sorted(
                (s for s in remaining.values() if s.path in frontier),
                key=lambda s: (s.path != first.path, s.path, s.hunk_index, s.fragment_index),
            )
            fit = next(
                (
                    s
                    for s in eligible
                    if count_tokens("\n".join([*[c.fragment for c in chosen], s.fragment]))
                    <= token_budget
                ),
                None,
            )
            if fit is None:
                break
            chosen.append(fit)
            del remaining[fit.slice_id]
            cost = count_tokens("\n".join(c.fragment for c in chosen))
            if len(adjacency[fit.path]) < public_degree:
                frontier.update(p for p in adjacency[fit.path] if len(adjacency[p]) < public_degree)
        if not chosen:
            raise ValueError("Diff fragment cannot fit planning budget")
        members = {s.path for s in chosen}
        related = sorted(
            {e.target for e in relationships if e.source in members}
            | {e.source for e in relationships if e.target in members}
        )
        evidence = {r.model_dump_json(): r for s in chosen for r in s.evidence}
        identity = json.dumps(
            [
                snapshot.base,
                snapshot.head,
                token_budget,
                [s.slice_id for s in chosen],
                "syntax-plan-v2",
            ],
            separators=(",", ":"),
        )
        units.append(
            ReviewUnit(
                unit_id=_hash(identity),
                slices=tuple(chosen),
                estimated_tokens=cost,
                evidence=tuple(evidence[k] for k in sorted(evidence)),
                related_paths=tuple(p for p in related if p not in members),
            )
        )
    limitations.add("evidence_refs_are_lazy_and_not_in_diff_token_budget")
    limitations.update(limit for f in indexed.values() for limit in f.limitations)
    accounted_hunks = {c.hunk_id for c in coverage if c.hunk_id is not None}
    covered_paths = {c.path for c in coverage}
    coverage_summary = CoverageSummary(
        changed_files=len(files),
        parsed_hunks=sum(len(ids) for ids in hunk_ids_by_file.values()),
        planned_hunks=sum(c.status == "planned" for c in coverage),
        unavailable_evidence_hunks=sum(
            c.status == "unavailable" and c.hunk_id is not None for c in coverage
        ),
        excluded_files=len({c.path for c in coverage if c.status == "excluded"}),
        unaccounted_hunks=tuple(
            sorted(
                hid
                for ids in hunk_ids_by_file.values()
                for hid in ids
                if hid not in accounted_hunks
            )
        ),
        unaccounted_files=tuple(
            sorted(f.filename for f in files if f.filename not in covered_paths)
        ),
    )
    coverage_summary = coverage_summary.model_copy(
        update={
            "complete": not coverage_summary.unaccounted_hunks
            and not coverage_summary.unaccounted_files
        }
    )
    head_files = [key for key in indexed if not key.startswith("base:")]
    budgets = BudgetReport(
        scope="build_review_plan",
        lines=(
            BudgetLine(
                name="diff_fragments_per_unit",
                limit=token_budget,
                used=max((u.estimated_tokens for u in units), default=0),
                covers=(
                    "rendered diff fragments in the largest ReviewUnit; excludes lazy "
                    "EvidenceRef source, planner prompts and any model output"
                ),
            ),
            BudgetLine(
                name="diff_fragments_total",
                limit=None,
                used=sum(u.estimated_tokens for u in units),
                covers=(
                    "sum over units; the cap is applied per unit, so this total is"
                    " measured rather than bounded"
                ),
            ),
            BudgetLine(
                name="indexed_files",
                limit=index_file_limit,
                used=len(head_files),
                covers="head files parsed for relationships; base copies are not extra inventory",
            ),
            BudgetLine(
                name="indexed_source_chars",
                limit=index_char_limit,
                used=chars,
                covers="head and base characters read for parsing; not a model context budget",
            ),
        ),
        omissions=tuple(
            Omission(path=c.path, reason=c.reason or c.status, hunk_id=c.hunk_id)
            for c in coverage
            if c.status != "planned"
        ),
    )
    return ReviewPlan(
        base=snapshot.base,
        head=snapshot.head,
        token_budget=token_budget,
        units=tuple(units),
        files=tuple(indexed[k] for k in sorted(indexed)),
        relationships=relationships,
        coverage=tuple(coverage),
        limitations=tuple(sorted(limitations)),
        cuts=_cut_report(units, relationships),
        coverage_summary=coverage_summary,
        budgets=budgets,
    )
