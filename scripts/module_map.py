#!/usr/bin/env python
"""Generate the real module map, call edges and store touchpoints from the code.

Diagrams written by hand drift from the implementation. This derives them from
the AST instead, so the DFD and STD documents can quote machine output:

* every module's public functions with line numbers and parameter counts;
* module-to-module call edges, resolved by definition name;
* which modules actually touch each ledger (evidence, budgets, coverage,
  verdicts, usage, playbook) - the stores the data-flow diagram claims exist;
* functions that return a report or accept a provider, i.e. the real seams.

    .venv/bin/python scripts/module_map.py --json reports/real-run/module-map.json
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STORES = {
    "evidence": ("EvidenceBundle", "bundle.add", "bundle.render", "bundle.execute"),
    "coverage": ("ChunkCoverage", "coverage_summary", "CoverageSummary", "LineSpan"),
    "budget": ("BudgetReport", "BudgetLine", "Omission", "run_budget_report"),
    "verdicts": ("filter_verdicts", "confirmation_verdicts", "FilterVerdict"),
    "criteria": ("CRITERIA_VERSION", "witness", "evaluate_equivalence"),
    "usage": ("usage_records", "summarize_usage", "cache_hit"),
    "playbook": ("playbook", "load_playbook", "recall(", "contract_domains"),
    "units": ("ReviewUnit", "review_by_units", "unit_review", "build_unit_context"),
    "snapshot": ("Snapshot", "repository_snapshot", "GitSnapshot", "GitHubSnapshot"),
    "report": ("ReviewReport", "build_grounded_report", "model_dump"),
}

SEAM_HINTS = ("provider", "snapshot", "report", "options", "defaults")


def module_of(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT).with_suffix("")).replace("/", ".")


def analyse(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    source = path.read_text(encoding="utf-8")
    functions: list[dict] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src"):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names if a.name.startswith("src"))
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            params = [
                a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            ]
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "params": params,
                "lines": (node.end_lineno or node.lineno) - node.lineno + 1,
                "docstring": bool(ast.get_docstring(node)),
            })
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    touches = sorted(
        store for store, needles in STORES.items() if any(needle in source for needle in needles)
    )
    seams = sorted({
        param for fn in functions for param in fn["params"]
        if any(hint in param for hint in SEAM_HINTS)
    })
    return {
        "module": module_of(path),
        "functions": sorted(functions, key=lambda f: f["line"]),
        "imports_src": sorted(set(imports)),
        "calls_out_names": sorted(calls),
        "touches": touches,
        "seam_params": seams,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path,
                        help="write the generated inventory table as markdown")
    args = parser.parse_args(argv)

    modules = [
        analyse(path)
        for path in sorted((REPO_ROOT / "src").rglob("*.py"))
        if "__pycache__" not in path.parts
    ]
    # module-level call edges: caller -> module defining the called name
    owner: dict[str, str] = {}
    for module in modules:
        for fn in module["functions"]:
            owner.setdefault(fn["name"], module["module"])
    edges: dict[str, set[str]] = defaultdict(set)
    for module in modules:
        for name in module["calls_out_names"]:
            target = owner.get(name)
            if target and target != module["module"]:
                edges[module["module"]].add(target)

    print(f"modules: {len(modules)}\n")
    for module in modules:
        funcs = ", ".join(f"{f['name']}():{f['line']}" for f in module["functions"][:8])
        print(f"== {module['module']} ({len(module['functions'])} public fn)")
        print(f"   functions : {funcs or '-'}")
        print(f"   calls out : {', '.join(sorted(edges.get(module['module'], ()))) or '-'}")
        print(f"   stores    : {', '.join(module['touches']) or '-'}")
        if module["seam_params"]:
            print(f"   seams     : {', '.join(module['seam_params'])}")
        print()

    if args.markdown:
        lines = [
            "# 模块清单（由 `scripts/module_map.py` 从 AST 生成，请勿手改）",
            "",
            "| 模块 | 公开函数（行号） | 调用到的 src 模块 | 触及的账目 | 接缝参数 |",
            "|---|---|---|---|---|",
        ]
        for module in modules:
            funcs = "<br>".join(f"`{f['name']}`:{f['line']}" for f in module["functions"]) or "—"
            out = ", ".join(sorted(edges.get(module["module"], ()))) or "—"
            touch = ", ".join(module["touches"]) or "—"
            seams = ", ".join(module["seam_params"]) or "—"
            lines.append(f"| `{module['module']}` | {funcs} | {out} | {touch} | {seams} |")
        lines.append("")
        lines.append(f"共 {len(modules)} 个模块。图与表都来自代码，手画容易与实现脱节。")
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {args.markdown}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"modules": modules,
             "edges": {k: sorted(v) for k, v in edges.items()}},
            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
