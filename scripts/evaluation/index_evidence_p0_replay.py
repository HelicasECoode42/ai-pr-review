"""Offline host-evidence replay for the bounded Python index; no provider calls."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

from src.analyzer.repository_context import EvidenceBundle, GitSnapshot
from src.evaluation.reviewbench import parse_diff
from src.reviewer.grounded_review import _bounded_python_index_search, _execute_intent_tasks


ROOT = Path(__file__).resolve().parents[2]
MIRROR = ROOT.parent / "eval-data/mirrors/allan-mobley-jr_gimmes"
BASE = "08efd0bbc34fd29de2a4077f07d24f3dc02a63b6"
HEAD = "e1477f8b3f18c1de6ff87dbca9465a7b106dc193"
NORMAL_BUDGET = 6000


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def tracked_reads(snapshot: GitSnapshot, bundle: EvidenceBundle, resolver, requests, changed):
    """Run ordinary snapshot seeding + the existing EvidenceBundle search executor."""
    search_calls = []
    original_search = snapshot.search

    def measured_search(symbol, side="head", limit=6):
        start = time.perf_counter()
        result = original_search(symbol, side=side, limit=limit)
        search_calls.append({"symbol": symbol, "side": side, "limit": limit,
                             "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
                             "hits": result})
        return result

    snapshot.search = measured_search
    bundle.follow(changed)
    seeded = {key: value for key, value in snapshot.cache.items()}
    seeded_search_count = len(search_calls)
    started = time.perf_counter()
    plan = {"hypotheses": [{
        "id": "H1", "priority_eligible": True, "source_refs": [f"diff:{changed[0].filename}"],
        "focus_tags": ["caller_contract"], "missing_evidence": [],
    }], "tasks": []}
    _execute_intent_tasks(bundle, requests, plan, snapshot, set(),
                          {item.filename for item in changed}, search_resolver=resolver)
    request_elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    audit = snapshot.audit()
    new_cache = {key: value for key, value in snapshot.cache.items() if key not in seeded}
    host_indexes = []
    for item in bundle.requests:
        index = item.get("host_index")
        if not index:
            continue
        host_indexes.append({key: index.get(key) for key in (
            "schema", "status", "side", "completeness", "searched_scope",
            "files_parsed", "source_bytes_parsed", "source_bytes_run_total", "scan_ms", "omissions",
        )} | {"relationships": [{key: edge.get(key) for key in (
            "source", "source_start", "source_end", "target", "target_start", "target_end",
            "provenance", "evidence_level", "strength",
        )} for edge in index.get("relationships", [])]})
    excerpt_meta = [{key: item[key] for key in (
        "path", "side", "start_line", "end_line", "evidence_ref", "sha256", "kind"
    )} for item in bundle.audit()["excerpts"]]
    return {
        "outcomes": [{key: item.get(key) for key in (
            "status", "normalized_query", "hits", "evidence_refs", "reused",
            "omission_reason", "index_extra_omitted"
        )} for item in bundle.requests],
        "host_task_statuses": [item.get("status") for item in plan["tasks"]],
        "excerpts": excerpt_meta,
        "renderable_context_chars": len(bundle.render()),
        "evidence_tokens": bundle.tokens,
        "snapshot_reads_before_request": len(seeded),
        "snapshot_reads_added_by_request": len(new_cache),
        "snapshot_bytes_added_by_request": sum(len(text.encode()) for text in new_cache.values() if text),
        "snapshot_cache_files_total": len(snapshot.cache),
        "snapshot_cache_bytes_total": sum(len(text.encode()) for text in snapshot.cache.values() if text),
        "snapshot_search_call_count_total": len(search_calls),
        "snapshot_search_call_count_before_request": seeded_search_count,
        "snapshot_search_call_count_for_request": len(search_calls) - seeded_search_count,
        "snapshot_search_elapsed_ms_for_request": round(sum(
            item["elapsed_ms"] for item in search_calls[seeded_search_count:]
        ), 3),
        "snapshot_errors": audit.get("errors", []),
        "host_index": host_indexes,
        "request_elapsed_ms": request_elapsed_ms,
    }


def replay_real_dev() -> dict:
    root = MIRROR
    diff = git(root, "diff", BASE, HEAD)
    files = parse_diff(diff)
    # Frozen mechanically from the changed Python function declaration in this PR.
    # This is not a prior model request trace and was not selected from its gold.
    requests = [{"hypothesis_id": "H1", "purpose": "Inspect callers of the changed function",
                 "tool": "search", "symbol": "check_staleness", "side": "head"}]
    variants = {}
    for enabled in (False, True):
        snapshot = GitSnapshot(root, BASE, HEAD)
        bundle = EvidenceBundle(snapshot, budget=NORMAL_BUDGET)
        resolver = _bounded_python_index_search(snapshot, files) if enabled else None
        variants["on" if enabled else "off"] = tracked_reads(
            snapshot, bundle, resolver, requests, files
        )
    # Post-hoc source-range scoring only. Gold content is never added to a request,
    # prompt, query, snapshot, or the report; only per-arm covered-range counts persist.
    gold_path = ROOT.parent / "eval-data/golden/allan-mobley-jr_gimmes_468-e1477f8b.json"
    gold_findings = json.loads(gold_path.read_text())["findings"]
    gold_range_score = {}
    for mode, result in variants.items():
        covered = 0
        cross_file_total = 0
        cross_file_covered = 0
        for finding in gold_findings:
            start, end, path = finding.get("start_line"), finding.get("end_line"), finding.get("file")
            if not isinstance(start, int) or not isinstance(end, int) or not isinstance(path, str):
                continue
            is_covered = any(item["path"] == path and item["start_line"] <= start
                              and item["end_line"] >= end for item in result["excerpts"])
            if is_covered:
                covered += 1
            if finding.get("context_required") == "diff-plus-related-files":
                cross_file_total += 1
                cross_file_covered += int(is_covered)
        gold_range_score[mode] = {
            "gold_range_source": "offline post-hoc only",
            "total_ranges": len(gold_findings),
            "full_gold_ranges_in_bundle_excerpts": covered,
            "cross_file_ranges": cross_file_total,
            "full_cross_file_ranges_in_bundle_excerpts": cross_file_covered,
            "semantic_precision_recall": "N/A",
        }
    return {"case": "allan-mobley-jr/gimmes PR 468", "base": BASE, "head": HEAD,
            "changed_paths": [item.filename for item in files],
            "query_source": "mechanically selected changed Python function name; no gold read",
            "budget_tokens": NORMAL_BUDGET, "request_sequence": requests,
            "posthoc_gold_range_score": gold_range_score, "variants": variants}


def make_fixture(files: dict[str, str], changed_path: str):
    root = Path(tempfile.mkdtemp(prefix="index-evidence-replay-"))
    for args in (("-c", "init.defaultBranch=main", "init", "-q"),
                 ("config", "user.email", "offline@example.invalid"),
                 ("config", "user.name", "Offline Fixture")):
        git(root, *args)
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    git(root, "add", ".")
    git(root, "commit", "-m", "base")
    base = git(root, "rev-parse", "HEAD")
    target = root / changed_path
    target.write_text(target.read_text() + "\ndef changed_entry():\n    return 0\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "head")
    head = git(root, "rev-parse", "HEAD")
    return root, base, head, parse_diff(git(root, "diff", base, head))


def replay_fixture(name: str, files: dict[str, str], symbol: str, *, budget=NORMAL_BUDGET):
    root, base, head, changed = make_fixture(files, "entry.py")
    request = [{"hypothesis_id": "H1", "purpose": "Inspect the fixed symbol query",
                "tool": "search", "symbol": symbol, "side": "head"}]
    variants = {}
    for enabled in (False, True):
        snapshot = GitSnapshot(root, base, head)
        bundle = EvidenceBundle(snapshot, budget=budget)
        resolver = _bounded_python_index_search(snapshot, changed) if enabled else None
        variants["on" if enabled else "off"] = tracked_reads(
            snapshot, bundle, resolver, request, changed
        )
    return {"case": name, "budget_tokens": budget, "request_sequence": request,
            "manual_fixture_relation": {
                "alias": "entry.run invokes api.fetch_record through imported alias load_record",
                "ambiguity": "alpha and beta both define fetch_record",
                "no_hit": "the requested identifier is absent",
            }.get(name, "synthetic host evidence-budget boundary"), "variants": variants}


def main() -> None:
    # Keep this replay strictly offline even when the local tiktoken asset is absent.
    from src.analyzer import context_builder

    context_builder._token_encoding = lambda: None
    output = {
        "protocol": "same frozen snapshot, request sequence, order, and EvidenceBundle budget; only resolver flag differs; model prompt is not invoked",
        "provider_calls": 0,
        "cost": {"model_api": 0, "api_usd": 0},
        "real_dev": replay_real_dev(),
        "fixtures": [
            replay_fixture("alias_helpful", {
                "api.py": "def fetch_record():\n    return 1\n",
                "entry.py": "from api import fetch_record as load_record\ndef run():\n    return load_record()\n",
                "unrelated.py": "def idle():\n    return 0\n",
            }, "fetch_record"),
            replay_fixture("no_hit", {
                "api.py": "def fetch_record():\n    return 1\n",
                "entry.py": "def changed_entry():\n    return 0\n",
            }, "never_called"),
            replay_fixture("same_name_ambiguous", {
                "alpha.py": "def fetch_record():\n    return 'a'\n",
                "beta.py": "def fetch_record():\n    return 'b'\n",
                "entry.py": "from alpha import fetch_record as from_alpha\nfrom beta import fetch_record as from_beta\ndef run():\n    return from_alpha() + from_beta()\n",
            }, "fetch_record"),
            replay_fixture("evidence_budget_boundary", {
                "api.py": "def fetch_record():\n    return 1\n",
                "entry.py": "from api import fetch_record as load_record\ndef run():\n    return load_record()\n",
            }, "fetch_record", budget=1),
        ],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
