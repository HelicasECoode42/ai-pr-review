"""Bounded, syntax-only Python symbol bindings for review-plan navigation.

The index describes parser evidence, not runtime reachability.  Callers and
callees are always scoped to the frozen side and the paths actually indexed.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections import defaultdict
from typing import Literal

from pydantic import Field, model_validator

from src.models import FrozenContract


class SymbolRef(FrozenContract):
    base: str = ""
    head: str = ""
    side: Literal["head", "base"] = "head"
    path: str = ""
    language: str = "python"
    name: str = ""
    qualified_scope: str = ""
    kind: Literal["function", "class"] = "function"
    start: int
    end: int
    content_hash: str = ""
    symbol_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def fill_legacy_name(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            scope = value.get("qualified_scope") or value.get("name", "")
            value.setdefault("qualified_scope", scope)
            value.setdefault("name", scope.rsplit(".", 1)[-1])
        return value


class CallSite(FrozenContract):
    name: str
    caller_symbol_id: str | None = None
    caller_scope: str = ""
    receiver: str | None = None
    start: int
    end: int


class ImportBinding(FrozenContract):
    local_name: str
    module: str
    imported_name: str | None = None
    is_module: bool = False
    module_alias_is_full: bool = False


class SourceFile(FrozenContract):
    path: str
    side: Literal["head", "base"]
    base: str = ""
    head: str = ""
    content_hash: str
    parser: str
    symbols: tuple[SymbolRef, ...] = ()
    imports: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    call_sites: tuple[CallSite, ...] = ()
    import_bindings: tuple[ImportBinding, ...] = ()
    local_names: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    module_assignments: tuple[str, ...] = ()
    module_definitions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class Relationship(FrozenContract):
    source: str
    target: str
    kind: str
    strength: Literal["strong", "weak"]
    symbol: str | None = None
    provenance: Literal["import_binding", "lexical_binding", "lexical_search"] = "lexical_search"
    evidence_level: Literal["exact_binding", "resolved_binding", "ambiguous", "lexical"] = "lexical"
    source_symbol_id: str | None = None
    target_symbol_id: str | None = None
    source_start: int | None = None
    source_end: int | None = None
    target_start: int | None = None
    target_end: int | None = None
    side: Literal["head", "base"] = "head"
    base: str = ""
    head: str = ""


class UnresolvedReference(FrozenContract):
    source_symbol_id: str | None = None
    path: str
    side: Literal["head", "base"]
    name: str
    start: int
    end: int
    reason: Literal["dynamic_attribute", "dynamic_binding", "ambiguous_binding", "unresolved_name"]
    possible_target_symbol_ids: tuple[str, ...] = ()


class SymbolQuery(FrozenContract):
    """Bounded navigation result; complete means no known gaps in searched_scope only.

    It never establishes that a symbol or caller does not exist elsewhere in the
    repository, because paths outside this finite scope were not searched.
    """

    relationships: tuple[Relationship, ...] = ()
    searched_scope: tuple[str, ...] = ()
    completeness: Literal["complete", "partial"] = "complete"
    omissions: tuple[str, ...] = ()


def _symbol_id(
    base: str,
    head: str,
    side: str,
    path: str,
    scope: str,
    kind: str,
    start: int,
    end: int,
    content_hash: str,
) -> str:
    payload = json.dumps(
        [base, head, side, path, scope, kind, start, end, content_hash],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def parse_python(path: str, text: str, side: str, base: str, head: str) -> SourceFile:
    """Parse Python declarations, lexical calls, imports, and local shadows."""
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return SourceFile(
            path=path,
            side=side,
            base=base,
            head=head,
            content_hash=content_hash,
            parser="python-ast",
            limitations=("parse_failed",),
        )

    package = path.split("/")[:-1]
    refs: list[SymbolRef] = []
    calls: list[CallSite] = []
    imports: set[str] = set()
    bindings: list[ImportBinding] = []
    locals_by_scope: dict[str, tuple[str, ...]] = {}
    scope_stack: list[str] = []
    scope_symbols: list[SymbolRef | None] = [None]

    def local_names(node: ast.AST) -> set[str]:
        args_node = getattr(node, "args", None)
        names: set[str] = (
            {arg.arg for arg in ast.walk(args_node) if isinstance(arg, ast.arg)}
            if args_node is not None
            else set()
        )
        declared_global: set[str] = set()

        def target_ids(target: ast.AST) -> set[str]:
            if isinstance(target, ast.Name):
                return {target.id}
            if isinstance(target, (ast.Tuple, ast.List)):
                return set().union(*(target_ids(item) for item in target.elts))
            return set()

        stack = list(getattr(node, "body", ()))
        while stack:
            child = stack.pop()
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                names.add(child.name) if hasattr(child, "name") else None
                continue
            if isinstance(child, ast.arg):
                names.add(child.arg)
            if isinstance(child, (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    names.update(target_ids(target))
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                names.update(target_ids(child.target))
            elif isinstance(child, (ast.Global, ast.Nonlocal)):
                declared_global.update(child.names)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    names.add(
                        alias.asname
                        or (
                            alias.name.split(".")[0]
                            if isinstance(child, ast.Import)
                            else alias.name
                        )
                    )
            stack.extend(ast.iter_child_nodes(child))
        return names - declared_global

    parse_limits: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            self._visit_scope(node, "function")

        def visit_AsyncFunctionDef(self, node):
            self._visit_scope(node, "function")

        def visit_ClassDef(self, node):
            self._visit_scope(node, "class")

        def _visit_scope(self, node, kind):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    self.visit(decorator)
                for default in [*node.args.defaults, *[d for d in node.args.kw_defaults if d]]:
                    self.visit(default)
            elif isinstance(node, ast.ClassDef):
                for base_expr in [*node.bases, *node.keywords]:
                    self.visit(base_expr)
            scope = ".".join([*scope_stack, node.name])
            start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
            ref = SymbolRef(
                base=base,
                head=head,
                side=side,
                path=path,
                qualified_scope=scope,
                kind=kind,
                start=start,
                end=node.end_lineno,
                content_hash=content_hash,
                symbol_id=_symbol_id(
                    base, head, side, path, scope, kind, start, node.end_lineno, content_hash
                ),
            )
            refs.append(ref)
            scope_stack.append(node.name)
            names = local_names(node)
            scope_symbols.append(ref)
            locals_by_scope[scope] = tuple(sorted(names))
            for child in node.body:
                self.visit(child)
            scope_symbols.pop()
            scope_stack.pop()

        def visit_Import(self, node):
            if scope_stack:
                parse_limits.add("function_local_imports_unresolved")
                return
            for alias in node.names:
                imports.add(alias.name.replace(".", "/"))
                bindings.append(
                    ImportBinding(
                        local_name=alias.asname or alias.name.split(".")[0],
                        module=alias.name.replace(".", "/"),
                        is_module=True,
                        module_alias_is_full=alias.asname is not None
                        or "/" not in alias.name.replace(".", "/"),
                    )
                )

        def visit_ImportFrom(self, node):
            if scope_stack:
                parse_limits.add("function_local_imports_unresolved")
                return
            prefix = package[: len(package) - node.level + 1] if node.level else []
            stem = "/".join([*prefix, *(node.module or "").split(".")]).strip("/")
            imports.add(stem)
            for alias in node.names:
                if alias.name == "*":
                    parse_limits.add("star_import_unresolved")
                    continue
                target = f"{stem}/{alias.name}".strip("/")
                imports.add(target)
                bindings.append(
                    ImportBinding(
                        local_name=alias.asname or alias.name, module=stem, imported_name=alias.name
                    )
                )

        def visit_Call(self, node):
            callee = node.func
            if isinstance(callee, ast.Name):
                name, receiver = callee.id, None
            elif isinstance(callee, ast.Attribute):
                name = callee.attr
                receiver = callee.value.id if isinstance(callee.value, ast.Name) else None
            else:
                name, receiver = ast.unparse(callee), "<dynamic>"
            current = scope_symbols[-1]
            calls.append(
                CallSite(
                    name=name,
                    caller_symbol_id=current.symbol_id if current else None,
                    caller_scope=".".join(scope_stack),
                    receiver=receiver,
                    start=node.lineno,
                    end=node.end_lineno or node.lineno,
                )
            )
            self.generic_visit(node)

    Visitor().visit(tree)
    module_assignments: set[str] = set()
    module_definitions: set[str] = set()
    module_stack = list(tree.body)
    while module_stack:
        statement = module_stack.pop()
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                module_definitions.add(statement.name)
            continue
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    module_assignments.add(target.id)
        elif isinstance(statement, (ast.For, ast.AsyncFor)) and isinstance(
            statement.target, ast.Name
        ):
            module_assignments.add(statement.target.id)
        elif isinstance(statement, ast.Delete):
            module_assignments.update(
                target.id for target in statement.targets if isinstance(target, ast.Name)
            )
        module_stack.extend(ast.iter_child_nodes(statement))
    limitations = ["references_are_syntactic_not_runtime_calls", *sorted(parse_limits)]
    return SourceFile(
        path=path,
        side=side,
        base=base,
        head=head,
        content_hash=content_hash,
        parser="python-ast",
        symbols=tuple(sorted(refs, key=lambda s: (s.start, s.qualified_scope))),
        imports=tuple(sorted(imports)),
        calls=tuple(sorted({c.name for c in calls})),
        call_sites=tuple(calls),
        import_bindings=tuple(bindings),
        local_names=locals_by_scope,
        module_assignments=tuple(sorted(module_assignments)),
        module_definitions=tuple(sorted(module_definitions)),
        limitations=tuple(limitations),
    )


class PythonSymbolIndex:
    """Read-only call navigation over an already bounded set of parsed files."""

    def __init__(
        self,
        files: tuple[SourceFile, ...],
        relationships: tuple[Relationship, ...],
        *,
        side: str,
        max_depth: int = 3,
        max_results: int = 100,
        index_omissions: tuple[str, ...] = (),
        unresolved_references: tuple[UnresolvedReference, ...] = (),
    ):
        self.files = tuple(f for f in files if f.side == side and f.parser == "python-ast")
        self.relationships = tuple(r for r in relationships if r.side == side)
        self.side = side
        self.max_depth = max_depth
        self.max_results = max_results
        inferred = [
            limitation
            for f in self.files
            for limitation in f.limitations
            if limitation != "references_are_syntactic_not_runtime_calls"
        ]
        self.index_omissions = tuple(sorted(set(index_omissions) | set(inferred)))
        self.unresolved_references = tuple(ref for ref in unresolved_references if ref.side == side)
        self.by_id = {symbol.symbol_id: symbol for file in self.files for symbol in file.symbols}

    @classmethod
    def from_plan(
        cls,
        plan,
        *,
        side: Literal["head", "base"],
        max_depth: int = 3,
        max_results: int = 100,
    ) -> PythonSymbolIndex:
        """Build from a ReviewPlan while carrying its known coverage gaps."""
        omissions = list(plan.index_omissions)
        if side == "base":
            omissions.append("base_inventory_not_enumerated")
        return cls(
            plan.files,
            plan.relationships,
            side=side,
            max_depth=max_depth,
            max_results=max_results,
            index_omissions=tuple(omissions),
            unresolved_references=plan.unresolved_references,
        )

    def query(
        self,
        symbol_id: str,
        *,
        direction: Literal["callers", "callees"],
        depth: int = 1,
        limit: int = 50,
    ) -> SymbolQuery:
        if depth < 1 or limit < 1:
            raise ValueError("depth and limit must be positive")
        bounded_depth = min(depth, self.max_depth)
        bounded_limit = min(limit, self.max_results)
        if symbol_id not in self.by_id:
            return SymbolQuery(
                searched_scope=tuple(sorted(f.path for f in self.files)),
                completeness="partial",
                omissions=("unknown_symbol_id",),
            )
        seen_symbols = {symbol_id}
        found = []
        frontier = {symbol_id}
        for _ in range(bounded_depth):
            next_frontier = set()
            for edge in self.relationships:
                src = edge.source_symbol_id
                dst = edge.target_symbol_id
                if direction == "callers" and dst in frontier:
                    found.append(edge)
                    next_frontier.add(src)
                elif direction == "callees" and src in frontier:
                    found.append(edge)
                    next_frontier.add(dst)
            frontier = next_frontier - seen_symbols
            seen_symbols.update(next_frontier)
            if len(found) > bounded_limit:
                break
        omissions = list(self.index_omissions)
        searched_scope = tuple(sorted(f.path for f in self.files))
        relevant_unresolved = [
            ref
            for ref in self.unresolved_references
            if (direction == "callees" and ref.source_symbol_id in seen_symbols)
            or (direction == "callers" and ref.path in searched_scope)
        ]
        omissions.extend(f"unresolved:{ref.reason}" for ref in relevant_unresolved)
        if depth > self.max_depth:
            omissions.append("depth_limit")
        if len(found) > bounded_limit:
            omissions.append("result_limit")
        return SymbolQuery(
            relationships=tuple(found[:bounded_limit]),
            searched_scope=searched_scope,
            completeness="partial" if omissions else "complete",
            omissions=tuple(omissions),
        )


def resolve_python_relationships(
    files: tuple[SourceFile, ...], inventory: set[str], *, side: str, max_edges: int = 5000
) -> tuple[tuple[Relationship, ...], tuple[UnresolvedReference, ...], tuple[str, ...]]:
    """Bind statically provable Python calls; retain unresolved/dynamic call gaps."""
    active = [f for f in files if f.side == side and f.parser == "python-ast"]
    by_path = {f.path: f for f in active}
    definitions: dict[tuple[str, str], list[SymbolRef]] = defaultdict(list)
    all_named_definitions: dict[str, list[SymbolRef]] = defaultdict(list)
    for file in active:
        for symbol in file.symbols:
            if "." not in symbol.qualified_scope:
                definitions[(file.path, symbol.qualified_scope)].append(symbol)
                all_named_definitions[symbol.qualified_scope].append(symbol)
    edges: list[Relationship] = []
    unresolved: list[UnresolvedReference] = []

    def candidates(module: str, path: str) -> tuple[str, ...]:
        if module.startswith("."):
            return ()
        choices = [module, module + ".py", module + "/__init__.py"]
        if module.endswith((".js", ".jsx")):
            choices += [module.rsplit(".", 1)[0] + ".py"]
        return tuple(p for p in choices if p in inventory and p != path)

    for file in active:
        binding_targets: dict[str, tuple[str, str | None, bool]] = {}
        ambiguous_imports: set[str] = set()
        unsupported_module_aliases: set[str] = set()
        for binding in file.import_bindings:
            targets = candidates(binding.module, file.path)
            if len(targets) == 1:
                target = (
                    targets[0],
                    binding.imported_name,
                    binding.is_module,
                )
                previous = binding_targets.get(binding.local_name)
                if previous is not None and previous != target:
                    ambiguous_imports.add(binding.local_name)
                else:
                    binding_targets[binding.local_name] = target
                if binding.is_module and "/" in binding.module and not binding.module_alias_is_full:
                    unsupported_module_aliases.add(binding.local_name)
        refs_by_id = {s.symbol_id: s for s in file.symbols}
        symbols_by_scope = {s.qualified_scope: s for s in file.symbols}

        def imported_candidates(
            local_name: str,
            called_name: str,
            binding_targets=binding_targets,
            unsupported_module_aliases=unsupported_module_aliases,
        ) -> list[SymbolRef]:
            binding = binding_targets.get(local_name)
            if binding is None:
                return []
            target_path, imported_name, is_module = binding
            if is_module and local_name in unsupported_module_aliases:
                return []
            target_name = called_name if is_module else (imported_name or local_name)
            return definitions.get((target_path, target_name), [])

        def lexical_shadowed(
            name: str, scope: str, symbols_by_scope=symbols_by_scope, file=file
        ) -> bool:
            parts = scope.split(".") if scope else []
            for end in range(len(parts), 0, -1):
                qualified = ".".join(parts[:end])
                current = symbols_by_scope.get(qualified)
                if current and current.kind == "class":
                    continue
                if name in file.local_names.get(qualified, ()):
                    return True
            return False

        for call in file.call_sites:
            source = refs_by_id.get(call.caller_symbol_id or "")
            name = call.name
            target_symbols: list[SymbolRef] = []
            provenance = "lexical_binding"
            reason = "unresolved_name"
            shadows = False
            possible_targets = imported_candidates(call.receiver if call.receiver else name, name)
            if call.receiver and call.receiver in binding_targets:
                alias_shadowed = (
                    lexical_shadowed(call.receiver, call.caller_scope)
                    or call.receiver in file.module_assignments
                    or call.receiver in file.module_definitions
                    or call.receiver in ambiguous_imports
                )
                if alias_shadowed:
                    reason = "dynamic_binding"
                else:
                    target_path, imported_name, is_module = binding_targets[call.receiver]
                    if is_module and call.receiver in unsupported_module_aliases:
                        reason = "dynamic_binding"
                    elif is_module:
                        target_symbols = definitions.get((target_path, name), [])
                        provenance = "import_binding"
            elif call.receiver:
                reason = "dynamic_attribute"
            else:
                # A local parameter/assignment shadows a module or imported name.
                source_scope = source.qualified_scope if source else ""
                scope_parts = source_scope.split(".") if source_scope else []
                for n in range(len(scope_parts), 0, -1):
                    scope_name = ".".join(scope_parts[:n])
                    scope_symbol = symbols_by_scope.get(scope_name)
                    if scope_symbol and scope_symbol.kind == "class":
                        continue
                    local_def = [
                        s for s in file.symbols if s.qualified_scope == f"{scope_name}.{name}"
                    ]
                    if local_def:
                        target_symbols = local_def
                        break
                    if name in by_path[file.path].local_names.get(scope_name, ()):
                        shadows = True
                        reason = "dynamic_binding"
                        break
                if not shadows and not target_symbols and name not in file.module_assignments:
                    target_symbols = definitions.get((file.path, name), [])
                elif name in file.module_assignments:
                    shadows = True
                    reason = "dynamic_binding"
                if not shadows and not target_symbols and name in binding_targets:
                    if name in ambiguous_imports or name in file.module_definitions:
                        shadows = True
                        reason = "ambiguous_binding"
                    else:
                        target_path, imported_name, _ = binding_targets[name]
                        target_symbols = definitions.get((target_path, imported_name or name), [])
                        provenance = "import_binding"
                if not shadows and not target_symbols and name not in binding_targets:
                    target_symbols = [
                        s for s in all_named_definitions.get(name, ()) if s.path != file.path
                    ]
                    if target_symbols:
                        reason = "ambiguous_binding"
                        provenance = "lexical_search"
            target_symbols = list({s.symbol_id: s for s in target_symbols}.values())
            if len(target_symbols) == 1 and source is not None and provenance != "lexical_search":
                target = target_symbols[0]
                edges.append(
                    Relationship(
                        source=file.path,
                        target=target.path,
                        kind="call",
                        strength="strong",
                        symbol=name,
                        provenance=provenance,
                        evidence_level="exact_binding"
                        if provenance == "import_binding"
                        else "resolved_binding",
                        source_symbol_id=source.symbol_id,
                        target_symbol_id=target.symbol_id,
                        source_start=call.start,
                        source_end=call.end,
                        target_start=target.start,
                        target_end=target.end,
                        side=side,
                        base=file.base,
                        head=file.head,
                    )
                )
            else:
                if len(target_symbols) > 1:
                    reason = "ambiguous_binding"
                unresolved.append(
                    UnresolvedReference(
                        source_symbol_id=call.caller_symbol_id,
                        path=file.path,
                        side=side,
                        name=name,
                        start=call.start,
                        end=call.end,
                        reason=reason,
                        possible_target_symbol_ids=tuple(
                            sorted({target.symbol_id for target in possible_targets})
                        ),
                    )
                )
                if target_symbols and source is not None:
                    for target in target_symbols[:20]:
                        edges.append(
                            Relationship(
                                source=file.path,
                                target=target.path,
                                kind="call",
                                strength="weak",
                                symbol=name,
                                provenance="lexical_search",
                                evidence_level="ambiguous",
                                source_symbol_id=source.symbol_id,
                                target_symbol_id=target.symbol_id,
                                source_start=call.start,
                                source_end=call.end,
                                target_start=target.start,
                                target_end=target.end,
                                side=side,
                                base=file.base,
                                head=file.head,
                            )
                        )
    omissions = ("relationship_edge_limit",) if len(edges) > max_edges else ()
    return tuple(edges[:max_edges]), tuple(unresolved), omissions
