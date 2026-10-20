#!/usr/bin/env python3
"""One-off offline complexity matrix through production review paths."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import unified_diff
from importlib.metadata import version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.analyzer.cross_file import analyze_cross_file_impact  # noqa: E402
from src.analyzer.diff_parser import parse_file_hunks  # noqa: E402
from src.analyzer.repository_context import safe_source_path  # noqa: E402
from src.analyzer.risk_rules import scan_signals  # noqa: E402
from src.application.review import ReviewApplication  # noqa: E402
from src.models import ChangedFile, FileStatus, PullRequest, ReviewMeta  # noqa: E402
from src.reviewer.engine import build_rule_only_report  # noqa: E402
from src.reviewer.provider import ProviderError  # noqa: E402
from src.utils.config import Settings  # noqa: E402

BASE, HEAD = "a" * 40, "b" * 40
MODEL, CRITIC = "offline-recording-provider", "offline-recording-critic"
BUDGET = {"evidence_tokens": 6000, "input_bytes": 200_000, "attempts": 16,
          "timeout_s": 60, "output_tokens": 128, "patch_tokens": 6000}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def digest(value: object) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode())

@dataclass
class Fixture:
    name: str
    files: list[ChangedFile]
    base_sources: dict[str, str]
    head_sources: dict[str, str]
    anchor: str
    line: int
    patch_sha: str
    snapshot_sha: str
    additions: int

def fixtures() -> dict[str, Fixture]:
    result = {}
    for name, (count, added) in {"small": (1, 4), "medium": (8, 12),
                                  "large": (24, 65)}.items():
        files, base, head, patches = [], {}, {}, []
        line = 2
        for i in range(count):
            path = f"src/fixture_{i:02d}.py"
            fn = f"process_fixture_{i:02d}"
            old = f"def {fn}(value):\n    return value\n"
            new = (f"def {fn}(value):\n" + "\n".join(
                f"    stage_{j:03d} = value" for j in range(added)
            ) + "\n    return value\n")
            body = list(unified_diff(old.splitlines(), new.splitlines(),
                                     fromfile=f"a/{path}", tofile=f"b/{path}",
                                     n=3, lineterm=""))
            patch = (f"diff --git a/{path} b/{path}\nindex {i+1:07x}..{i+2:07x} 100644\n"
                     f"--- a/{path}\n+++ b/{path}\n" + "\n".join(body) + "\n")
            file = ChangedFile(filename=path, status=FileStatus.MODIFIED, patch=patch,
                               additions=added, deletions=0, changes=added)
            if i == 0 and parse_file_hunks(file):
                line = parse_file_hunks(file)[0].added_lines[0].line
            files.append(file)
            base[path], head[path] = old, new
            patches.append(patch)
        head["src/consumer.py"] = (
            "from src.fixture_00 import process_fixture_00\n\n"
            "def consume(value):\n    return process_fixture_00(value)\n"
        )
        head["src/independent_contract.py"] = (
            "def normalize(value):\n    if value is None:\n        return 0\n    return value\n"
        )
        base.update({p: head[p] for p in ("src/consumer.py", "src/independent_contract.py")})
        patch_bytes = "\n".join(patches).encode()
        snapshot = {"base": base, "head": head}
        result[name] = Fixture(name, files, base, head, files[0].filename, line,
                               sha(patch_bytes), digest(snapshot), count * added)
    return result

class Snapshot:
    """Small in-memory equivalent of the frozen production snapshot interface."""

    def __init__(self, fixture: Fixture):
        self.base, self.head = BASE, HEAD
        self.sources = {"base": fixture.base_sources, "head": fixture.head_sources}
        self.cache: dict[tuple[str, str], str | None] = {}
        self.reads = self.hits = self.searches = 0
        self.search_limited = False

    def read(self, path: str, side: str = "head") -> str | None:
        self.reads += 1
        key = side, path
        if key not in self.cache:
            self.cache[key] = (
                self.sources.get(side, {}).get(path) if safe_source_path(path) else None
            )
        else:
            self.hits += 1
        return self.cache[key]

    def search(self, symbol: str, side: str = "head", limit: int = 6) -> list[tuple[str, int]]:
        self.searches += 1
        if side not in self.sources or not re.fullmatch(r"[A-Za-z_]\w{2,79}", symbol):
            return []
        matches = []
        for path in sorted(self.sources[side]):
            text = self.read(path, side) or ""
            for number, line in enumerate(text.splitlines(), 1):
                if re.search(r"\b" + re.escape(symbol) + r"\b", line):
                    matches.append((path, number))
                    break
            if len(matches) >= limit:
                break
        return matches

    def counts(self) -> dict[str, int]:
        return {"read_invocations": self.reads, "unique_read_keys": len(self.cache),
                "read_cache_hits": self.hits, "search_invocations": self.searches}

    def audit(self) -> dict:
        files = [{"side": s, "path": p, "available": t is not None}
                 for (s, p), t in sorted(self.cache.items())]
        return {"backend": "recording_fixture_snapshot", "base": self.base,
                "head": self.head, "search_limited": False, "read_files": files, "errors": []}
def call_kind(system: str) -> str:
    text = system.lower()
    if "rapid first-pass review" in text:
        return "triage"
    if "specific risk hotspot" in text:
        return "deep_review"
    if "frozen base/head repository snapshot" in text:
        return "discovery"
    if "verify code-review candidates" in text:
        return "verification"
    return "one_shot"

class FakeProvider:
    def __init__(self, model: str, fixture: Fixture, scenario: str,
                 calls: list[dict[str, Any]], budget):
        self.model, self.fixture, self.scenario = model, fixture, scenario
        self.calls, self.budget, self.index = calls, budget, 0

    def complete_json(self, system: str, prompt: str) -> str:
        self.index += 1
        kind, started = call_kind(system), time.perf_counter()
        record = {"model": self.model, "kind": kind,
                  "input_bytes": len(system.encode()) + len(prompt.encode()),
                  "input_char_div_4": math.ceil((len(system) + len(prompt)) / 4),
                  "output_bytes": 0, "output_char_div_4": 0, "admitted": False}
        try:
            self.budget.reserve(system, prompt)
        except ProviderError as exc:
            record.update(outcome="budget_rejected", error=exc.kind)
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.calls.append(record)
            raise
        record["admitted"] = True
        if self.scenario == "provider_failure":
            record.update(outcome="scripted_timeout", error="timeout")
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.calls.append(record)
            raise ProviderError("Scripted offline provider timeout.", kind="timeout")
        response = self.answer(kind)
        record.update(outcome="scripted_response", output_bytes=len(response.encode()),
                      output_char_div_4=math.ceil(len(response) / 4),
                      elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
        self.calls.append(record)
        return response

    def answer(self, kind: str) -> str:
        f = self.fixture
        if kind == "triage":
            hotspots = []
            if self.scenario == "legacy_hotspots":
                hotspots = [{"file_path": x.filename, "start_line": 1,
                             "end_line": x.additions + 2, "reason": "Probe only.",
                             "risk_category": "correctness"} for x in f.files[:5]]
            return json.dumps({"hotspots": hotspots})
        if kind == "deep_review":
            return '{"suggestions": []}'
        if (kind == "discovery" and self.scenario in {"forced_evidence", "budget_exhausted"}
                and self.index == 1):
            request = {"hypothesis_id": "h", "purpose": "Exercise one bounded read.",
                       "tool": "read", "path": "src/independent_contract.py",
                       "line": 1, "side": "head"}
            plan = {"id": "h", "goal": "Probe routing only.", "constraints": [],
                    "risk_hypotheses": ["No finding assertion."], "unknowns": [],
                    "source_refs": [f"diff:{f.anchor}"], "focus_tags": ["caller_contract"]}
            return json.dumps({"intent_hypotheses": [plan], "context_requests": [request]})
        if kind == "discovery" and self.scenario == "forced_evidence" and self.index == 2:
            return json.dumps({"summary": "Probe only.", "risk_level": "low",
                "intent_hypotheses": [{"id": "h", "goal": "Probe routing only.",
                    "constraints": [], "risk_hypotheses": ["No finding assertion."],
                    "unknowns": [], "source_refs": [f"diff:{f.anchor}"],
                    "focus_tags": ["caller_contract"]}],
                "suggestions": [{"file_path": f.anchor, "line": f.line,
                    "severity": "medium", "confidence": 0.5, "title": "Probe candidate",
                    "reason": "Routing exercise only.", "recommendation": "None.",
                    "evidence": ["fixture"], "failure_scenario": "none"}]})
        if kind == "verification":
            return json.dumps({"items": [{"candidate_id": 0, "decision": "needs_context",
                "confidence": 0.1, "trigger": "Probe only.", "call_path": "n/a",
                "expected_behavior": "n/a", "actual_behavior": "n/a",
                "introduced_by": "n/a", "reason": "No adjudication.",
                "evidence": [], "counter_evidence": []}]})
        return json.dumps({"summary": "Scripted empty response; no quality claim.",
                           "risk_level": "low", "suggestions": [], "intent_hypotheses": []})

    def close(self) -> None:
        pass
def make_settings(directory: Path, grounded: bool, critic: bool, evidence: int) -> Settings:
    return Settings(_env_file=None, openai_api_key="offline-fake", github_token=None,
        review_model=MODEL, critic_model=CRITIC if critic else None,
        enable_repository_review=grounded, enable_critic=critic,
        repository_evidence_tokens=evidence, unit_review_max_units=0,
        max_input_token_upper_bound=BUDGET["input_bytes"],
        max_review_http_attempts=BUDGET["attempts"],
        max_review_timeout_seconds=BUDGET["timeout_s"],
        model_output_token_limit=BUDGET["output_tokens"],
        playbook_path=str(ROOT / "knowledge/playbook.md"),
        decision_ledger_enabled=True,
        decision_ledger_path=str(directory / "ledger.jsonl"),
        strategy_policy_store_dir=str(directory / "policy"))

def scan(fixture: Fixture, snapshot: Snapshot) -> tuple[list, dict]:
    started = time.perf_counter()
    signals = scan_signals(fixture.files, include_cross_file=False, snapshot=snapshot)
    risk_ms = (time.perf_counter() - started) * 1000
    cross_warnings: list[str] = []
    started = time.perf_counter()
    cross = analyze_cross_file_impact(fixture.files, cross_warnings)
    cross_ms = (time.perf_counter() - started) * 1000
    findings = [*signals.findings, *cross]
    warnings = [*signals.warnings, *cross_warnings]
    lint = next((w for w in signals.warnings if w.startswith("Ruff status=")), "")
    match = re.search(r"Ruff status=([a-z_]+)", lint)
    version = re.search(r"version=(.+?)\s+base=", lint)
    return findings, {"elapsed_ms": round(risk_ms + cross_ms, 3),
        "risk_scan_ms": round(risk_ms, 3), "cross_file_ms": round(cross_ms, 3),
        "signals": len(findings), "risk_and_ast_included": True,
        "cross_file_included": True, "ruff_status": match.group(1) if match else "not_reported",
        "ruff_version": version.group(1) if version else None, "warnings": warnings,
        "snapshot_counts_after_scan": snapshot.counts()}
def pull_request(f: Fixture) -> PullRequest:
    return PullRequest(repo="synthetic/complexity-probe", number=101,
        title=f"Fixed {f.name} complexity fixture", body="Offline route measurement.",
        base_ref=BASE, base_sha=BASE, head_ref=HEAD, head_sha=HEAD)
def rule_only(f: Fixture) -> dict:
    snapshot = Snapshot(f)
    started = time.perf_counter()
    findings, scanned = scan(f, snapshot)
    report = build_rule_only_report(pull_request(f), f.files, findings,
        language="en", reviewer_version="complexity-probe",
        review_meta=ReviewMeta(review_mode="full_pr"))
    return {"fixture": f.name, "scan": scanned, "report_builder": "build_rule_only_report",
        "report_ms": round((time.perf_counter() - started) * 1000 - scanned["elapsed_ms"], 3),
        "findings": len(report.rule_findings), "suggestions": len(report.suggestions),
        "provider_calls": 0, "snapshot_counts": snapshot.counts()}
def run_arm(f: Fixture, arm: str, scenario: str = "empty",
            evidence: int = 6000) -> dict:
    grounded = arm in {"current_grounded", "grounded_plus_critic"}
    critic = arm == "grounded_plus_critic"
    snapshot, calls, providers = Snapshot(f), [], []
    started = time.perf_counter()
    findings, scan_record = scan(f, snapshot)
    scan_ms = scan_record["elapsed_ms"]
    with tempfile.TemporaryDirectory(prefix="complexity-probe-") as temp:
        settings = make_settings(Path(temp), grounded, critic, evidence)
        def provider_factory(**kw):
            provider = FakeProvider(kw["model"], f, scenario, calls, kw["request_budget"])
            providers.append(provider)
            return provider
        app = ReviewApplication(settings, provider_factory=provider_factory)
        app_started = time.perf_counter()
        report = app.review(pull_request(f), f.files, findings, language="en",
            two_stage=arm == "legacy_two_stage", repository_snapshot=snapshot)
        app_ms = (time.perf_counter() - app_started) * 1000
        budget = providers[0].budget.snapshot() if providers else {}
        trace = (report.repository_context or {}).get("review_trace", {})
        requests = (report.repository_context or {}).get("requests", [])
        omissions = (report.repository_context or {}).get("omitted", [])
        status = report.metrics.get("configuration_status")
        def size(key):
            return sum(c[key] or 0 for c in calls)
        return {"arm": arm, "scenario": scenario, "fixture": f.name,
            "composition": "unsupported" if status == "unsupported" else "measured",
            "configuration_status": status, "execution_status": report.execution_status,
            "failure": report.ai_failure_reason, "used_ai": report.used_ai,
            "critic_requested": critic,
            "calls": len(calls), "calls_by_stage": {k: sum(c["kind"] == k for c in calls)
                for k in sorted({c["kind"] for c in calls})},
            "call_records": calls, "prompt_bytes": size("input_bytes"),
            "output_bytes": size("output_bytes"),
            "logical_input_char_div_4": size("input_char_div_4"),
            "logical_output_char_div_4": size("output_char_div_4"),
            "billing_tokens": "unknown; fake provider has no usage response",
            "provider_local_ms": round(sum(c["elapsed_ms"] for c in calls), 3),
            "application_local_ms": round(app_ms, 3),
            "free_scan_ms": scan_ms,
            "end_to_end_local_ms": round((time.perf_counter() - started) * 1000, 3),
            "request_budget": budget, "scan": scan_record,
            "snapshot_counts_total": snapshot.counts(),
            "evidence_requests": [{"status": x.get("status"), "executed": x.get("executed"),
                "reused": x.get("reused", False)} for x in requests],
            "evidence_omissions": len(omissions),
            "evidence_omission_reasons": sorted(
                {str(x.get("reason", "unknown")) for x in omissions}
            ),
            "coverage": trace.get("coverage"), "context_truncated": report.context_truncated,
            "raw_candidates": len(report.candidate_audit), "published": len(report.suggestions),
            "decision_ledger_isolated": True}

def protocol_identity() -> dict:
    protocol = {"budgets": BUDGET, "arms": ["one_shot_control", "current_grounded",
        "legacy_two_stage", "grounded_plus_critic"], "scenarios": ["empty", "forced_evidence",
        "budget_exhausted", "provider_failure", "legacy_hotspots"],
        "provider": "recording fake; no network", "dotenv": False,
        "ledger": "enabled; isolated temp path",
        "token_estimator": "rough prompt chars/4; billing unknown"}
    # Hash source/config bytes, not symbols; captures dependencies of the route.
    runtime_paths = list((ROOT / "src").rglob("*.py")) + [
        ROOT / p for p in ("src/utils/default_rules.yml", "knowledge/playbook.md",
                            "pyproject.toml", "uv.lock", ".ai-pr-review-rules.yml",
                            ".ai-pr-review-rules.yaml") if (ROOT / p).is_file()
    ]
    paths = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in sorted(runtime_paths)}
    protocol["environment"] = {"python": platform.python_version(),
        **{name: version(name) for name in ("pydantic", "httpx", "ruff", "tiktoken")}}
    paths["scripts/evaluation/complexity_probe.py"] = sha(Path(__file__).read_bytes())
    return {"protocol": protocol, "protocol_sha256": digest(protocol),
            "runtime_files_sha256": paths, "runtime_sha256": digest(paths)}
def run_probe() -> dict:
    fs = fixtures()
    manifest = {n: {"files": len(f.files), "additions": f.additions,
        "patch_sha256": f.patch_sha, "snapshot_sha256": f.snapshot_sha}
        for n, f in fs.items()}
    runs = [run_arm(f, arm) for f in fs.values() for arm in
            ("one_shot_control", "current_grounded", "legacy_two_stage", "grounded_plus_critic")]
    runs += [run_arm(fs["medium"], "current_grounded", "forced_evidence"),
             run_arm(fs["medium"], "current_grounded", "budget_exhausted", evidence=1),
             run_arm(fs["medium"], "one_shot_control", "provider_failure"),
             run_arm(fs["large"], "legacy_two_stage", "legacy_hotspots")]
    measured = [x for x in runs if x["composition"] == "measured"]
    highest = max(measured, key=lambda x: x["calls"])
    return {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Offline complexity only; no P/R or quality conclusion.",
        "identity": {"fixture_manifest": manifest, "fixture_sha256": digest(manifest),
                     **protocol_identity()}, "budgets": BUDGET,
        "limits": {"provider": "fake/no network", "dotenv_read": False,
            "billing_tokens": "unknown", "provider latency": "not measured",
            "timing": "local scan/application/fake serialization only"},
        "rule_only_baselines": [rule_only(f) for f in fs.values()], "runs": runs,
        "reviewbench_inventory_status": "not measured by this synthetic probe",
        "next_live_step": (
            "Verify prepared hash; smoke all supported arms on the cached dev pair. "
            "No P/R until trusted dev snapshots and blinded labels; no holdout access."
        ),
        "summary": {"runs": len(runs), "measured_compositions": len(measured),
            "unsupported_grounded_critic": sum(x["composition"] == "unsupported" for x in runs),
            "provider_calls_measured": sum(x["calls"] for x in measured),
            "max_calls_in_scripted_scenarios": {
                "arm": highest["arm"], "fixture": highest["fixture"],
                          "scenario": highest["scenario"], "calls": highest["calls"]},
            "bottleneck": (
                "Legacy repeats full-context deep review per hotspot; grounded records "
                "snapshot-read fan-out and large-diff omissions."
            )}}
def main() -> int:
    target = ROOT / "reports/evaluation/complexity-probe-2026-10-19.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = run_probe()
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
