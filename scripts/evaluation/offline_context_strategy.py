#!/usr/bin/env python3
"""Zero-model offline comparison of first-pass patch-context selection policies.

Selectors run before the gold directory is read. Gold is used only in the final
post-hoc evaluator section. Target PR code is never checked out or executed.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import subprocess
import os
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT.parent / "eval-data")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--source-revision", default=None)
    parser.add_argument(
        "--counter", "--tokenizer", dest="tokenizer",
        choices=("bytebound", "cl100k"), default="cl100k",
        help="token-count layer; bytebound is a UTF-8 byte upper bound, cl100k requires the verified local cache",
    )
    parser.add_argument(
        "--cache-dir", "--token-cache", dest="token_cache", type=Path,
        default=Path("/private/tmp/codex-cl100k-cache"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=None,
    )
    parser.add_argument("--case-ids", nargs="*", default=None)
    args = parser.parse_args()
    if args.output is None:
        args.output = ROOT / f"docs/evaluation/offline-context-{args.tokenizer}-2026-10-18.json"
    data_root = args.data.resolve()
    source_root = args.source_root.resolve()
    if args.tokenizer == "cl100k":
        os.environ["TIKTOKEN_CACHE_DIR"] = str(args.token_cache.resolve())
    prepared_path = data_root / "prepared/prepared.json"
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))

    import sys

    sys.path.insert(0, str(source_root))
    from src.analyzer import context_builder
    from src.analyzer.context_builder import (
        _documentation_reason,
        _has_binary_extension,
        _patch_has_encoding_issues,
        _secret_adjacent_reason,
        _skip_patch_reason,
        build_review_context,
    )
    from src.analyzer.diff_parser import parse_file_hunks
    from src.analyzer.risk_rules import scan_signals
    from src.analyzer.tiered_scan import triage
    from src.evaluation.reviewbench import parse_diff
    from src.models import ChangedFile, FileStatus, PullRequest
    from src.utils import detect_programming_language
    from src.analyzer.risk_rules import _should_skip_scan

    if args.tokenizer == "bytebound":
        context_builder._token_encoding = lambda: None  # type: ignore[assignment]
    count_tokens = context_builder.count_tokens
    token_mode = context_builder.tokenizer_name()
    if args.tokenizer == "cl100k" and token_mode != "cl100k_base":
        raise RuntimeError("cl100k cache did not load; refusing tokenizer fallback")
    tokenizer_asset = None
    if args.tokenizer == "cl100k":
        asset_url = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
        expected_asset_sha256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
        asset_path = args.token_cache.resolve() / hashlib.sha1(asset_url.encode()).hexdigest()
        asset_sha256 = sha(asset_path.read_bytes())
        if asset_sha256 != expected_asset_sha256:
            raise RuntimeError("cached cl100k asset hash differs from installed official expected_sha256")
        tokenizer_asset = {
            "source_host": "openaipublic.blob.core.windows.net",
            "size_bytes": asset_path.stat().st_size,
            "sha256": asset_sha256,
            "installed_expected_sha256_verified": True,
        }
    budget = 6_000
    arm_names = ("A0", "A1", "B", "C", "D_full", "D_prefix")
    budget_units = (
        f"{token_mode} token count"
        if token_mode == "cl100k_base"
        else "UTF-8 byte upper bound, not exact model tokens"
    )

    signature = re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|type|enum)\s+"
        r"[A-Za-z_$][\w$]*|^\s*(?:export\s+)?(?:const|let|var)\s+"
        r"[A-Za-z_$][\w$]*\s*[:=(]|^\s*func\s+(?:\([^)]*\)\s*)?"
        r"[A-Za-z_]\w*|^\s*type\s+[A-Za-z_]\w*\s+(?:struct|interface)"
    )

    def patch_marker(text: str) -> int:
        return text.rfind("## Patches\n")

    def patch_section(text: str) -> str:
        marker = patch_marker(text)
        if marker < 0:
            return "## Patches\n"
        section = text[marker:]
        # build_review_context appends omission/coverage notes after the last
        # patch fence. Keep them outside this patch-only budget measurement.
        closing_fences = list(re.finditer(r"(?m)^```\s*$", section))
        if not closing_fences:
            return "## Patches\n"
        return section[:closing_fences[-1].end()] + "\n"

    def render_patch(selected: dict[str, list[str]]) -> str:
        parts = ["## Patches"]
        for path, hunks in selected.items():
            if not hunks:
                continue
            parts.extend(["", f"### {path}", "```diff", "\n".join(hunks), "```"])
        return "\n".join(parts) + "\n"

    def extract_patch_chunks(text: str) -> list[tuple[str, list[str]]]:
        section = patch_section(text)
        markers = list(re.finditer(r"(?m)^### (.+)$", section))
        chunks: list[tuple[str, list[str]]] = []
        for idx, match in enumerate(markers):
            end = markers[idx + 1].start() if idx + 1 < len(markers) else len(section)
            block = section[match.end():end]
            opening = block.find("```diff\n")
            if opening < 0:
                continue
            body_start = opening + len("```diff\n")
            closing = list(re.finditer(r"(?m)^```\s*$", block[body_start:]))
            body_end = body_start + closing[-1].start() if closing else len(block)
            body = block[body_start:body_end].rstrip("\n")
            chunks.append((match.group(1), body.splitlines()))
        return chunks

    def prefix_fit_a1(text: str) -> tuple[str, int, bool]:
        """Keep the original production-rendered line prefix, with legal fences."""
        prefix = text[:patch_marker(text)] + "## Patches\n"
        selected: dict[str, list[str]] = {}
        stopped = False
        for path, body_lines in extract_patch_chunks(text):
            for line in body_lines:
                trial = {key: value[:] for key, value in selected.items()}
                trial.setdefault(path, []).append(line)
                rendered = render_patch(trial)
                if count_tokens(rendered) > budget:
                    stopped = True
                    break
                selected = trial
            if stopped:
                break
        rendered = render_patch(selected)
        result = prefix + rendered[len("## Patches\n"):]
        return result, count_tokens(rendered), stopped

    def eligible(file: ChangedFile) -> tuple[bool, str | None]:
        reason = _skip_patch_reason(file.filename)
        if reason is None:
            reason = _secret_adjacent_reason(file.filename)
        if reason is None:
            reason = _documentation_reason(file.filename)
        if reason is None and _has_binary_extension(file.filename):
            reason = "encoding_skipped"
        if reason is None and _patch_has_encoding_issues(file.filename, file.patch):
            reason = "encoding_skipped"
        if reason is None and not file.patch:
            reason = "patch_unavailable"
        if reason is None and not parse_file_hunks(file):
            reason = "patch_without_hunks"
        return reason is None, reason

    def selected_context(
        preamble: str, selected_ids: set[tuple[str, int]],
        files: list[ChangedFile], hunks_by_file: dict[str, list],
    ) -> tuple[str, int, dict[str, list[str]]]:
        selected: dict[str, list[str]] = {}
        for file in files:
            for idx, hunk in enumerate(hunks_by_file[file.filename]):
                if (file.filename, idx) in selected_ids:
                    selected.setdefault(file.filename, []).append(hunk.raw)
        rendered = render_patch(selected)
        return preamble + rendered, count_tokens(rendered), selected

    def choose_greedy(
        preamble: str, files: list[ChangedFile], hunks_by_file: dict[str, list],
        order: list[tuple[str, int]],
    ) -> tuple[str, int, set[tuple[str, int]], list[tuple[str, int]]]:
        selected: set[tuple[str, int]] = set()
        no_fit: list[tuple[str, int]] = []
        for key in order:
            trial = selected | {key}
            _, used, _ = selected_context(preamble, trial, files, hunks_by_file)
            if used <= budget:
                selected = trial
            else:
                no_fit.append(key)
        text, used, _ = selected_context(preamble, selected, files, hunks_by_file)
        return text, used, selected, no_fit

    def round_robin_order(files: list[ChangedFile], hunks_by_file: dict[str, list]):
        indices = {file.filename: 0 for file in files}
        order: list[tuple[str, int]] = []
        while True:
            advanced = False
            for file in files:
                index = indices[file.filename]
                if index < len(hunks_by_file[file.filename]):
                    order.append((file.filename, index))
                    indices[file.filename] += 1
                    advanced = True
            if not advanced:
                return order

    def new_side_lines(hunk) -> dict[int, str]:
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", hunk.header)
        if not match:
            return {}
        line = int(match.group(3))
        output: dict[int, str] = {}
        for raw in hunk.raw.splitlines()[1:]:
            if raw.startswith("\\"):
                continue
            if raw.startswith("+") and not raw.startswith("+++"):
                output[line] = raw
                line += 1
            elif raw.startswith("-") and not raw.startswith("---"):
                continue
            else:
                output[line] = raw
                line += 1
        return output

    def prompt_patch(text: str, path: str) -> str | None:
        marker = f"### {path}\n```diff\n"
        start = text.find(marker)
        if start < 0 or text.find(marker, start + len(marker)) >= 0:
            return None
        start += len(marker)
        end = text.find("\n### ", start)
        end = end if end >= 0 else len(text)
        body = text[start:end]
        fences = list(re.finditer(r"(?m)^```\s*$", body))
        return body[:fences[-1].start()].rstrip("\n") if fences else body

    def hunk_visibility(files: list[ChangedFile], text: str) -> tuple[Counter, int]:
        counts: Counter = Counter()
        stubs = 0
        for file in files:
            patch = prompt_patch(text, file.filename)
            visible = parse_file_hunks(ChangedFile(
                filename=file.filename, status=FileStatus.MODIFIED, patch=patch or ""
            )) if patch is not None else []
            by_start: dict[int, list] = defaultdict(list)
            for hunk in visible:
                by_start[hunk.new_start].append(hunk)
            if patch is not None and re.search(r"(?m)^@@\s*$", patch):
                stubs += 1
            for source in parse_file_hunks(file):
                if patch is not None and source.raw in patch:
                    counts["complete_body"] += 1
                elif by_start.get(source.new_start):
                    counts["partial_body"] += 1
                else:
                    counts["no_body"] += 1
        return counts, stubs

    def gold_mapping(case_id: str, gold: list[dict], files: list[ChangedFile], contexts: dict[str, str]):
        by_path = {file.filename: parse_file_hunks(file) for file in files}
        result: dict[str, list[dict]] = {arm: [] for arm in contexts}
        for idx, finding in enumerate(gold):
            if str(finding.get("tp_fp", "")).lower() != "tp":
                continue
            raw_path = str(finding.get("file", "")).replace("\\", "/").removeprefix("b/")
            start, end = finding.get("start_line"), finding.get("end_line")
            category = str(finding.get("category", "unknown")).strip().lower()
            finding_id = sha(json.dumps(
                [case_id, raw_path, start, end, category, str(finding.get("message", ""))],
                ensure_ascii=False, separators=(",", ":"),
            ).encode())
            row = {
                "finding_id_sha256": finding_id,
                "raw_tp_ordinal": idx + 1,
                "file": raw_path,
                "start_line": start,
                "end_line": end,
                "category": category,
            }
            source_hunks = by_path.get(raw_path, [])
            if not isinstance(start, int) or not isinstance(end, int) or end < start:
                status, hunk = "unknown", None
                outside_reason = "invalid_line_range"
            else:
                overlaps = [h for h in source_hunks if (
                    h.new_start <= end and h.new_start + max(h.new_count, 1) - 1 >= start
                )]
                if not overlaps:
                    old_overlaps = [h for h in source_hunks if (
                        h.old_start <= end and h.old_start + max(h.old_count, 1) - 1 >= start
                    )]
                    status, hunk = "outside_diff", None
                    outside_reason = "base_side_only_range" if old_overlaps else "no_head_side_hunk_overlap"
                elif len(overlaps) != 1:
                    status, hunk = "ambiguous", None
                    outside_reason = None
                else:
                    hunk = overlaps[0]
                    source_lines = new_side_lines(hunk)
                    target = set(range(start, end + 1))
                    if not target.issubset(source_lines):
                        status, hunk = "unknown", hunk
                        outside_reason = "range_not_represented_by_hunk_lines"
                    else:
                        status = "mapped"
                        outside_reason = None
            for arm, text in contexts.items():
                item = {**row, "mapping_status": status}
                if status in ("outside_diff", "unknown") and outside_reason:
                    item["mapping_reason"] = outside_reason
                if hunk is not None:
                    item["hunk_id"] = sha(
                        f"{raw_path}:{hunk.new_start}:{hunk.new_count}".encode()
                    )[:16]
                if status == "mapped":
                    patch = prompt_patch(text, raw_path)
                    visible = parse_file_hunks(ChangedFile(
                        filename=raw_path, status=FileStatus.MODIFIED, patch=patch or ""
                    )) if patch is not None else []
                    candidate = next((v for v in visible if v.new_start == hunk.new_start), None)
                    source_lines = new_side_lines(hunk)
                    visible_lines = new_side_lines(candidate) if candidate is not None else {}
                    target = set(range(start, end + 1))
                    visible_target = {
                        line for line in target
                        if line in visible_lines and visible_lines[line] == source_lines[line]
                    }
                    item.update({
                        "target_line_count": len(target),
                        "visible_target_line_count": len(visible_target),
                        "visibility": (
                            "all" if len(visible_target) == len(target)
                            else "some" if visible_target else "none"
                        ),
                        "context_sha256": sha(text.encode()),
                    })
                result[arm].append(item)
        return result

    # First run all selectors and freeze their contexts/hashes. Gold has not been
    # opened at this point.
    arms_by_case: list[dict] = []
    scan_by_case: dict[str, dict] = {}
    d_prefix_injection_sha256: str | None = None
    selected_case_ids = set(args.case_ids or [])
    input_records = [
        row for row in prepared.get("records", [])
        if not selected_case_ids or row.get("case_id") in selected_case_ids
    ]
    for entry in input_records:
        case_id = entry["case_id"]
        if entry.get("status") != "ready":
            arms_by_case.append({"case_id": case_id, "status": "not_ready"})
            continue
        input_dir = Path(entry["input_dir"])
        pr_path, diff_path = input_dir / "pr.json", input_dir / "diff.patch"
        metadata_raw, diff_raw = pr_path.read_bytes(), diff_path.read_bytes()
        metadata = json.loads(metadata_raw)
        if sha(diff_raw) != entry.get("diff_sha256") or metadata != entry.get("metadata"):
            raise ValueError(f"prepared input hash mismatch: {case_id}")
        files = parse_diff(diff_raw.decode("utf-8"))
        pr = PullRequest(
            repo=metadata["nwo"], number=int(metadata["pr_number"]),
            title=metadata["title"], body=metadata.get("body"),
            base_ref=metadata.get("base"), base_sha=metadata.get("base"),
            head_ref=metadata.get("head"), head_sha=metadata.get("head"),
        )
        a0 = build_review_context(
            pr, files, include_context_pack=False, prioritize_rule_files=False,
            allow_remote_file_fetch=False, max_patch_tokens=budget,
        )
        a0_text = a0.text
        marker = patch_marker(a0_text)
        preamble = a0_text[:marker] if marker >= 0 else a0_text
        a1_text, a1_rendered_tokens, a1_stopped = prefix_fit_a1(a0_text)

        eligible_files: list[ChangedFile] = []
        eligibility: dict[str, str] = {}
        hunks_by_file: dict[str, list] = {}
        for file in files:
            ok, reason = eligible(file)
            if ok:
                eligible_files.append(file)
                hunks_by_file[file.filename] = parse_file_hunks(file)
            else:
                eligibility[file.filename] = reason or "excluded"

        b_order = round_robin_order(eligible_files, hunks_by_file)
        b_text, b_tokens, b_selected, b_no_fit = choose_greedy(
            preamble, eligible_files, hunks_by_file, b_order,
        )

        signals = scan_signals(files, include_cross_file=False, include_linters=False)
        triage_result = triage(files, {f.filename: parse_file_hunks(f) for f in files}, list(signals.findings))
        risk_by_hunk = {
            (path, hunk.new_start): (score, reasons)
            for path, hunk, score, reasons in triage_result.suspicious
        }
        candidate_order: list[tuple[str, int]] = []
        for file in eligible_files:
            for index, hunk in enumerate(hunks_by_file[file.filename]):
                changed_lines = [
                    line[1:] for line in hunk.raw.splitlines()
                    if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
                ]
                declaration = any(signature.search(line) for line in changed_lines)
                candidate_order.append((file.filename, index, declaration))
        candidate_order.sort(key=lambda item: (
            -int((item[0], hunks_by_file[item[0]][item[1]].new_start) in risk_by_hunk),
            -risk_by_hunk.get(
                (item[0], hunks_by_file[item[0]][item[1]].new_start), (0.0, ())
            )[0],
            -int(item[2]), item[0], hunks_by_file[item[0]][item[1]].new_start,
        ))
        c_order = [(path, index) for path, index, _decl in candidate_order]
        c_text, c_tokens, c_selected, c_no_fit = choose_greedy(
            preamble, eligible_files, hunks_by_file, c_order,
        )

        # D keeps the production file order and per-file cap but avoids always
        # consuming a file's budget on its earliest small hunks. Every file's
        # first source hunk gets first priority; remaining hunks are ranked by
        # descending cl100k raw-hunk size with source index as a stable tie-break.
        production_order = {entry.path: i for i, entry in enumerate(a0.coverage)}
        d_files = sorted(
            eligible_files,
            key=lambda file: production_order.get(file.filename, len(production_order)),
        )
        per_file_budget = max(1, budget // min(8, max(1, len(files))))
        d_selected: set[tuple[str, int]] = set()
        d_no_fit: list[tuple[str, int]] = []
        d_selection_rank: dict[tuple[str, int], int] = {}
        for file in d_files:
            path = file.filename
            file_hunks = hunks_by_file[path]
            if not file_hunks:
                continue
            priority = [0] + sorted(
                range(1, len(file_hunks)),
                key=lambda idx: (-count_tokens(file_hunks[idx].raw), idx),
            )
            selected_here: set[int] = set()
            for index in priority:
                trial_here = selected_here | {index}
                file_body = "\n".join(file_hunks[idx].raw for idx in sorted(trial_here))
                key = (path, index)
                trial_ids = d_selected | {key}
                _trial_text, rendered_units, _selected = selected_context(
                    preamble, trial_ids, d_files, hunks_by_file,
                )
                if count_tokens(file_body) > per_file_budget or rendered_units > budget:
                    d_no_fit.append(key)
                    continue
                selected_here = trial_here
                d_selected = trial_ids
                d_selection_rank[key] = len(d_selection_rank)
        d_text, d_tokens, _d_rendered = selected_context(
            preamble, d_selected, d_files, hunks_by_file,
        )
        # D_full is the separately defined complete-hunk greedy selector above.
        # D_prefix is a controlled probe of the actual builder: only the parser
        # return order changes, and it is restored even if building fails.
        original_builder_parser = context_builder.parse_file_hunks
        def prefix_reordered_parser(file):
            parsed = original_builder_parser(file)
            if len(parsed) < 2:
                return parsed
            indexed = list(enumerate(parsed))
            return [indexed[0][1], *[
                hunk for _index, hunk in sorted(
                    indexed[1:], key=lambda pair: (-count_tokens(pair[1].raw), pair[0])
                )
            ]]
        injection_sha256 = sha(inspect.getsource(prefix_reordered_parser).encode("utf-8"))
        if d_prefix_injection_sha256 is None:
            d_prefix_injection_sha256 = injection_sha256
        elif d_prefix_injection_sha256 != injection_sha256:
            raise AssertionError("D_prefix injection function changed during run")
        try:
            context_builder.parse_file_hunks = prefix_reordered_parser
            d_prefix_builder = build_review_context(
                pr, files, include_context_pack=False, prioritize_rule_files=False,
                allow_remote_file_fetch=False, max_patch_tokens=budget,
            )
        finally:
            context_builder.parse_file_hunks = original_builder_parser
        d_prefix_text, d_prefix_rendered_tokens, d_prefix_stopped = prefix_fit_a1(
            d_prefix_builder.text
        )
        d_trace = []
        for file in d_files:
            for index, hunk in enumerate(hunks_by_file[file.filename]):
                key = (file.filename, index)
                if key not in d_selected:
                    continue
                changed_lines = [
                    line[1:] for line in hunk.raw.splitlines()
                    if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
                ]
                d_trace.append({
                    "path": file.filename,
                    "source_hunk_index_0based": index,
                    "source_hunk_new_start": hunk.new_start,
                    "raw_hunk_token_count": count_tokens(hunk.raw),
                    "is_first_source_hunk": index == 0,
                    "selection_priority_rank": d_selection_rank[key],
                    "render_order_rank": len(d_trace),
                    "declaration_signature_proxy": any(
                        signature.search(line) for line in changed_lines
                    ),
                })

        contexts = {"A0": a0_text, "A1": a1_text, "B": b_text, "C": c_text, "D_full": d_text, "D_prefix": d_prefix_text}
        for name in ("B", "C", "D_full", "D_prefix"):
            if contexts[name].count("## Patches\n") != 1:
                raise AssertionError(f"{case_id} {name}: expected exactly one patch-section marker")
            if patch_marker(contexts[name]) < 0:
                raise AssertionError(f"{case_id} {name}: patch-section marker is missing")
            if count_tokens(patch_section(contexts[name])) > budget:
                raise AssertionError(f"{case_id} {name}: rendered patch exceeds tokenizer budget")
        if not extract_patch_chunks(d_prefix_text):
            raise AssertionError(f"{case_id} D_prefix: empty patch after builder and strict fit")
        scan_by_case[case_id] = {
            "files": files,
            "contexts": contexts,
            "signals": signals,
            "a0_coverage": {entry.path: entry.model_dump(mode="json") for entry in a0.coverage},
            "eligibility": eligibility,
            "hunks_by_file": {f.filename: parse_file_hunks(f) for f in files},
        }
        arm_metrics = {}
        for name, text in contexts.items():
            section = patch_section(text)
            visible = extract_patch_chunks(text)
            hunk_counts, header_stubs = hunk_visibility(files, text)
            payload_units = sum(count_tokens("\n".join(lines)) for _path, lines in visible)
            arm_metrics[name] = {
                "context_sha256": sha(text.encode()),
                "context_count_units": count_tokens(text),
                "patch_section_count_units": count_tokens(section),
                "patch_body_only_count_units": payload_units,
                "source_hunks": sum(len(parse_file_hunks(file)) for file in files),
                "source_hunk_body_visibility": dict(hunk_counts),
                "header_only_stub_sections": header_stubs,
                "selected_files": len(visible),
                "selected_rendered_hunk_bodies": sum(
                    len(parse_file_hunks(ChangedFile(
                        filename=path, status=FileStatus.MODIFIED, patch="\n".join(lines)
                    ))) for path, lines in visible
                ),
                "selected_hunk_atoms": (
                    len(b_selected) if name == "B" else
                    len(c_selected) if name == "C" else
                    len(d_selected) if name == "D_full" else None
                ),
            }
            if name in ("B", "C", "D_full") and arm_metrics[name]["selected_hunk_atoms"]:
                if arm_metrics[name]["selected_files"] == 0 or arm_metrics[name]["selected_rendered_hunk_bodies"] == 0:
                    raise AssertionError(f"{case_id} {name}: selected hunks absent from rendered output")
        scan_by_case[case_id]["arm_metrics"] = arm_metrics
        scan_by_case[case_id]["selection_detail"] = {
            "a0_builder_patch_budget": a0.patch_token_budget,
            "a0_builder_count_units": a0.token_count,
            "a1_rendered_budget_units": a1_rendered_tokens,
            "a1_stopped_at_line_boundary": a1_stopped,
            "b_selected_hunks": len(b_selected), "b_no_fit_hunks": len(b_no_fit),
            "c_selected_hunks": len(c_selected), "c_no_fit_hunks": len(c_no_fit),
            "d_full_selected_hunks": len(d_selected), "d_full_no_fit_hunks": len(d_no_fit),
            "d_full_per_file_budget": per_file_budget,
            "d_prefix_builder_patch_budget": d_prefix_builder.patch_token_budget,
            "d_prefix_builder_count_units": d_prefix_builder.token_count,
            "d_prefix_strict_rendered_budget_units": d_prefix_rendered_tokens,
            "d_prefix_stopped_at_line_boundary": d_prefix_stopped,
            "d_hunk_priority": "first source hunk; then descending cl100k hunk tokens, stable original index",
            "d_selected_hunk_trace": d_trace,
            "c_scanner_findings": len(signals.findings),
            "c_scan_warnings": len(signals.warnings),
            "c_risky_hunks": len(risk_by_hunk),
            "c_declaration_hunks": sum(1 for _, _, declaration in candidate_order if declaration),
            "eligible_files": len(eligible_files),
            "filtered_files": eligibility,
        }

    # Post-hoc only: load gold after all four context arms and their hashes exist.
    per_case = []
    confusion = {arm: Counter() for arm in arm_names}
    source_categories = Counter()
    a0_missing_causes = Counter()
    rule_input_totals = Counter()
    for entry in input_records:
        case_id = entry["case_id"]
        if case_id not in scan_by_case:
            continue
        case = scan_by_case[case_id]
        files = case["files"]
        signals = case["signals"]
        contexts = case["contexts"]
        gold_path = data_root / "golden" / f"{case_id}.json"
        gold_doc = json.loads(gold_path.read_text(encoding="utf-8"))
        raw_gold = [g for g in gold_doc.get("findings", []) if str(g.get("tp_fp", "")).lower() == "tp"]
        mappings = gold_mapping(case_id, raw_gold, files, contexts)
        line_findings_by_file: dict[str, list] = defaultdict(list)
        file_findings_by_file: dict[str, list] = defaultdict(list)
        for finding in signals.findings:
            if finding.line is None:
                file_findings_by_file[finding.file_path.replace("\\", "/")].append(finding)
            else:
                line_findings_by_file[finding.file_path.replace("\\", "/")].append(finding)
        file_by_path = {file.filename: file for file in files}
        coverage = case["a0_coverage"]
        rule_input_totals["files_total"] += len(files)
        rule_input_totals["files_with_patch"] += sum(bool(file.patch) for file in files)
        rule_input_totals["files_line_path_scan_input"] += sum(
            bool(file.patch) and not _should_skip_scan(file.filename) for file in files
        )
        rule_input_totals["files_line_path_skipped_by_config"] += sum(
            _should_skip_scan(file.filename) for file in files
        )
        rule_input_totals["files_python_ast_eligible"] += sum(
            file.filename.endswith(".py") and bool(file.patch) for file in files
        )
        rule_input_totals["warnings"] += len(signals.warnings)

        per_finding_rows = []
        for gold_row in mappings["A0"]:
            finding = next(
                g for g in raw_gold
                if sha(json.dumps([
                    case_id,
                    str(g.get("file", "")).replace("\\", "/").removeprefix("b/"),
                    g.get("start_line"), g.get("end_line"),
                    str(g.get("category", "unknown")).strip().lower(),
                    str(g.get("message", "")),
                ], ensure_ascii=False, separators=(",", ":")).encode()) == gold_row["finding_id_sha256"]
            )
            path = gold_row["file"]
            start, end = gold_row["start_line"], gold_row["end_line"]
            line_signals = [
                f for f in line_findings_by_file.get(path, [])
                if isinstance(start, int) and isinstance(end, int) and start <= f.line <= end
            ]
            file_signals = file_findings_by_file.get(path, [])
            category = "ast_line" if any(x.rule_id.startswith("ast-") for x in line_signals) else "line" if line_signals else "file_only" if file_signals else "none"
            source_categories[category] += 1
            source_categories["line_ast_rule_signals"] += sum(x.rule_id.startswith("ast-") for x in line_signals)
            source_categories["line_non_ast_rule_signals"] += sum(not x.rule_id.startswith("ast-") for x in line_signals)
            source_categories["file_only_signal_records"] += len(file_signals)
            base_row = {
                key: gold_row[key]
                for key in ("finding_id_sha256", "raw_tp_ordinal", "file", "start_line", "end_line", "category", "mapping_status", "mapping_reason", "hunk_id")
                if key in gold_row
            }
            base_row["rule_signal_class"] = category
            base_row["matching_line_rule_ids"] = sorted({x.rule_id for x in line_signals})
            base_row["matching_line_signal_anchors"] = [
                {
                    "rule_id": x.rule_id,
                    "line": x.line,
                    "scan_kind": "ast" if x.rule_id.startswith("ast-") else "added_line_rule",
                    "source_side": "added_line",
                }
                for x in sorted(line_signals, key=lambda x: (x.line or 0, x.rule_id))
            ]
            base_row["same_file_only_rule_ids"] = sorted({x.rule_id for x in file_signals})
            changed_file = file_by_path.get(path)
            skipped_for_rules = _should_skip_scan(path)
            base_row["rule_input_coverage"] = {
                "file_in_cached_diff": changed_file is not None,
                "patch_present": bool(changed_file and changed_file.patch),
                "line_path_rules_input_eligible": bool(changed_file and changed_file.patch and not skipped_for_rules),
                "line_path_rules_skip_reason": "configured_skip_prefix" if skipped_for_rules else None,
                "ast_language": detect_programming_language(path),
                "ast_eligible": bool(
                    changed_file and changed_file.patch and not skipped_for_rules
                    and detect_programming_language(path) == "python"
                ),
            }
            for arm in contexts:
                row = mappings[arm][len(per_finding_rows)]
                if row["mapping_status"] == "mapped":
                    visibility = row["visibility"]
                    confusion[arm][f"{category}_{visibility}"] += 1
                    if arm == "A0" and visibility == "none":
                        cov = coverage.get(path, {})
                        reason = cov.get("reason") or cov.get("status", "unknown")
                        a0_missing_causes[str(reason)] += 1
                    base_row[arm] = {
                        "visibility": visibility,
                        "visible_target_lines": row["visible_target_line_count"],
                        "target_line_count": row["target_line_count"],
                        "context_sha256": row["context_sha256"],
                    }
                else:
                    confusion[arm][row["mapping_status"]] += 1
                    base_row[arm] = {"mapping_status": row["mapping_status"]}
            per_finding_rows.append(base_row)

        file_scope = Counter()
        for file in files:
            if file.patch:
                file_scope["patch_present"] += 1
            else:
                file_scope["patch_missing"] += 1
            if file.filename.endswith(".py") and file.patch:
                file_scope["python_ast_attempted"] += 1
        for warning in signals.warnings:
            if "skipped" in warning.lower():
                file_scope["warning_skipped"] += 1

        per_case.append({
            "case_id": case_id,
            "pr_sha256": sha((Path(entry["input_dir"]) / "pr.json").read_bytes()),
            "diff_sha256": sha((Path(entry["input_dir"]) / "diff.patch").read_bytes()),
            "gold_file_sha256": sha(gold_path.read_bytes()),
            "gold_raw_tp_count": len(raw_gold),
            "arm_metrics": case["arm_metrics"],
            "selection_detail": case["selection_detail"],
            "rule_input_coverage": dict(file_scope),
            "rule_warning_count": len(signals.warnings),
            "finding_mappings": per_finding_rows,
        })

    # Raw TP counts and line visibility per arm; no same-position deduplication.
    summary = {
        "cases": len(per_case),
        "raw_tp_findings": sum(c["gold_raw_tp_count"] for c in per_case),
        "tokenizer": token_mode,
        "budget_units": budget_units,
        "patch_section_budget": budget,
        "arms": {arm: dict(confusion[arm]) for arm in arm_names},
        "rule_signal_classes": dict(source_categories),
        "a0_no_visible_target_causes": dict(a0_missing_causes),
        "rule_input_totals": dict(rule_input_totals),
        "tokenizer_asset": tokenizer_asset,
    }
    gold_visibility_by_arm = {}
    for arm in arm_names:
        counts = Counter()
        for case in per_case:
            for finding in case["finding_mappings"]:
                counts[finding[arm].get(
                    "visibility", finding[arm].get("mapping_status", "unknown")
                )] += 1
        gold_visibility_by_arm[arm] = {
            **dict(counts),
            "any_target_line_visible": counts["all"] + counts["some"],
            "mapped_target_line_findings": counts["all"] + counts["some"] + counts["none"],
        }
        if sum(counts.values()) != summary["raw_tp_findings"]:
            raise AssertionError(f"{arm}: visibility outcomes do not sum to all raw TP records")
        if gold_visibility_by_arm[arm]["mapped_target_line_findings"] != 303:
            raise AssertionError(f"{arm}: mapped finding denominator changed unexpectedly")
    rule_signal_visibility = Counter()
    for case in per_case:
        for finding in case["finding_mappings"]:
            outcome = finding["A0"].get(
                "visibility", finding["A0"].get("mapping_status", "unknown")
            )
            has_line = bool(finding["matching_line_rule_ids"])
            has_file = bool(finding["same_file_only_rule_ids"])
            signal_class = (
                "line_plus_file" if has_line and has_file else
                "line_only" if has_line else
                "file_only" if has_file else "no_signal"
            )
            rule_signal_visibility[f"{signal_class}:{outcome}"] += 1
    split_manifest_path = source_root / "datasets/dev/split.json"
    split_manifest = json.loads(split_manifest_path.read_text(encoding="utf-8"))
    dev_ids = set(split_manifest["dev"])
    holdout_ids = set(split_manifest["holdout"])
    excluded_ids = {row["case_id"] for row in split_manifest["excluded"]}
    all_case_ids = {case["case_id"] for case in per_case}
    if dev_ids & holdout_ids or dev_ids & excluded_ids or holdout_ids & excluded_ids:
        raise ValueError("split manifest has overlapping case IDs")
    if dev_ids | holdout_ids | excluded_ids != all_case_ids:
        missing = (dev_ids | holdout_ids | excluded_ids) - all_case_ids
        unassigned = all_case_ids - (dev_ids | holdout_ids | excluded_ids)
        raise ValueError(f"split/input mismatch; missing={sorted(missing)}, unassigned={sorted(unassigned)}")
    split_ids = {
        "dev9": dev_ids,
        "holdout_regression15": holdout_ids,
        "excluded1": excluded_ids,
    }
    per_split = {}
    for split_name, case_ids in split_ids.items():
        split_cases = [case for case in per_case if case["case_id"] in case_ids]
        arm_outcomes = {}
        for arm in arm_names:
            counts = Counter()
            for case in split_cases:
                for finding in case["finding_mappings"]:
                    row = finding[arm]
                    counts[row.get("visibility", row.get("mapping_status", "unknown"))] += 1
            arm_outcomes[arm] = {
                **dict(counts),
                "any_target_line_visible": counts["all"] + counts["some"],
                "mapped_target_line_findings": counts["all"] + counts["some"] + counts["none"],
            }
        per_split[split_name] = {
            "cases": len(split_cases),
            "raw_tp_findings": sum(case["gold_raw_tp_count"] for case in split_cases),
            "arms": arm_outcomes,
        }
    if sum(row["raw_tp_findings"] for row in per_split.values()) != summary["raw_tp_findings"]:
        raise AssertionError("split raw TP counts do not sum to all cases")
    if {name: row["raw_tp_findings"] for name, row in per_split.items()} != {
        "dev9": 117, "holdout_regression15": 224, "excluded1": 11,
    }:
        raise AssertionError("split raw TP counts differ from the frozen 9/15/1 manifest")
    for split_name, split_result in per_split.items():
        expected_raw = split_result["raw_tp_findings"]
        for arm, outcomes in split_result["arms"].items():
            outcome_sum = sum(
                outcomes.get(key, 0)
                for key in ("all", "some", "none", "outside_diff", "ambiguous", "unknown")
            )
            if outcome_sum != expected_raw:
                raise AssertionError(f"{split_name}/{arm}: visibility counts do not sum to raw TP")
    dev_comparison_to_a1 = {}
    dev_cases = [case for case in per_case if case["case_id"] in dev_ids]
    for candidate_arm in ("C", "D_prefix"):
        total = {key: [] for key in ("all_gained", "all_lost", "any_gained", "any_lost")}
        per_case_comparison = []
        for case in dev_cases:
            changes = {key: [] for key in total}
            for finding in case["finding_mappings"]:
                if finding.get("mapping_status") != "mapped":
                    continue
                before = finding["A1"]["visibility"]
                after = finding[candidate_arm]["visibility"]
                finding_id = finding["finding_id_sha256"]
                if before != "all" and after == "all":
                    changes["all_gained"].append(finding_id)
                if before == "all" and after != "all":
                    changes["all_lost"].append(finding_id)
                if before == "none" and after in ("some", "all"):
                    changes["any_gained"].append(finding_id)
                if before in ("some", "all") and after == "none":
                    changes["any_lost"].append(finding_id)
            for key, values in changes.items():
                total[key].extend(values)
            if any(changes.values()):
                per_case_comparison.append({
                    "case_id": case["case_id"],
                    **changes,
                    "counts": {key: len(values) for key, values in changes.items()},
                })
        dev_comparison_to_a1[candidate_arm] = {
            "split": "dev9",
            "mapped_findings_only": True,
            "totals": {key: len(values) for key, values in total.items()},
            "finding_id_sha256": total,
            "per_case": per_case_comparison,
        }

    source_files = (
        "src/analyzer/context_builder.py",
        "src/analyzer/risk_rules.py",
        "src/analyzer/tiered_scan.py",
        "src/analyzer/diff_parser.py",
        "src/analyzer/ast_rules.py",
        "src/evaluation/reviewbench.py",
        "src/utils/rule_loader.py",
    )
    source_hashes = {path: sha((source_root / path).read_bytes()) for path in source_files}
    revision = args.source_revision or subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    out = {
        "experiment": "offline-context-selection-and-rule-signal-coverage",
        "method": (
            "No model/API/target code execution; the tokenizer asset was pre-cached and network access was disabled for this run. "
            "All selectors run before gold is read. A0 is actual build_review_context output; "
            "A1 is strict rendered-patch prefix fit; B is cross-file hunk round-robin; "
            "C is global existing rule/AST risk plus declaration tie-break; D_prefix "
            "is an actual-builder parser-order injection restored in finally, then strict-fit."
        ),
        "input_manifest": {
            "schema_version": 2,
            "counter": token_mode,
            "runtime_git_revision": revision,
            "runtime_source_sha256": source_hashes,
            "evaluation_script_sha256": sha(Path(__file__).read_bytes()),
            "d_prefix_injection_sha256": d_prefix_injection_sha256,
            "d_prefix_injection_parameters": {
                "preserve_first_source_hunk": True,
                "remaining_order": "descending cl100k raw-hunk token count",
                "stable_tie_break": "original source hunk index ascending",
                "shared_selector_and_builder_parser": True,
                "restore_with_finally": True,
                "strict_final_rendered_patch_token_budget": 6000,
            },
            "prepared_sha256": sha(prepared_path.read_bytes()),
            "split_manifest_path": "datasets/dev/split.json",
            "split_manifest_sha256": sha(split_manifest_path.read_bytes()),
            "benchmark_commit": prepared.get("benchmark_commit"),
            "split_definition": "datasets/dev/split.json; 9 dev, 15 previously used holdout regressions, 1 excluded; all input cases must map to exactly one split member",
            "budget_units": summary["budget_units"],
            "tokenizer_cache_metadata": tokenizer_asset,
            "patch_section_budget": budget,
            "context_pack": False,
            "remote_fetch": False,
            "rule_scanner": "scan_signals(include_cross_file=False, include_linters=False); warnings retained as counts",
            "rule_line_match": "same normalized file and inclusive gold [start_line,end_line]; position-only proxy, never semantic TP",
            "secondary_strategy_factors": {
                "B": "round-robin by original diff file order and hunk order; complete hunks only; skip non-fitting hunk and continue",
                "C": "global complete-hunk sort key (-flagged, -triage_score, -declaration_signature_regex, path, new_start); triage_score=10*rule_hits + 10*risky + 0.02*churn + 0.5*public_interface; flagged when rule_hits>0 or score>=10; uses production scan_signals; removes per-file cap",
                "D_full": "custom complete-hunk greedy selector; production file order and per-file cap; first source hunk, then descending cl100k raw-hunk token count with stable original-index ties; fit uses complete hunks and per-file/final rendered budgets",
            "D_prefix": "controlled actual-builder probe; temporarily reorder parse_file_hunks to first source hunk then descending cl100k raw-hunk token count, stable original-index ties; restore parser in finally; run actual build_review_context; then strict rendered-patch prefix fit to 6000 tokens",
            },
            "case_ids": [row.get("case_id") for row in input_records],
        },
        "summary": summary,
        "gold_target_line_visibility_by_arm": gold_visibility_by_arm,
        "rule_signal_visibility_A0": dict(rule_signal_visibility),
        "per_split": per_split,
        "dev_comparison_to_A1": dev_comparison_to_a1,
        "per_case": per_case,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": summary["cases"], "raw_tp": summary["raw_tp_findings"], "arms": summary["arms"], "rule_signals": summary["rule_signal_classes"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
