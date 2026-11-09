"""Raw discovery: prompts and per-item validation of model findings."""
from __future__ import annotations

import json


def _raw_discovery_prompt(pr, files):
    """PR metadata plus every file's full patch, matching the raw-model baseline.

    The raw lane deliberately bypasses `context.text`: its patch budget clips and
    reorders hunks, and on CrabTrap that clipped form cut candidates from 6-9 to
    1-5 with the same model and evidence. No budget is applied here.
    """
    parts = [
        f"# Pull request {getattr(pr, 'repo', '')}#{getattr(pr, 'number', '')}",
        f"Title: {getattr(pr, 'title', '') or ''}",
        "## Description",
        str(getattr(pr, "body", None) or "(empty)"),
        "## Diff",
    ]
    for file in files:
        if not file.patch:
            continue
        old = file.previous_filename or file.filename
        parts.append(f"--- a/{old}\n+++ b/{file.filename}\n{file.patch}")
    return "\n".join(parts)


def _excerpt(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)[:200]


def _collect_raw_findings(response, origin, parse_errors) -> list[dict]:
    """Keep the usable findings of one discovery sample; record each other one.

    Only what the pool and verifier read is required: a file path (the anchor
    the verifier starts from) and an integer line. A missing title or reason is
    defaulted downstream, so it is not a reason to drop a finding. The id is the
    sample name plus the item's position in the model's own list, so the same
    response always yields the same ids.
    """
    findings = response.get("findings", []) if isinstance(response, dict) else []
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        parse_errors.append({"origin": origin, "index": None,
                             "reason": "findings_not_a_list", "excerpt": _excerpt(findings)})
        return []
    items = []
    for index, item in enumerate(findings):
        line = item.get("line") if isinstance(item, dict) else None
        if not isinstance(item, dict):
            reason = "not_an_object"
        elif not isinstance(item.get("file") or item.get("file_path"), str):
            reason = "missing_file"
        elif isinstance(line, bool) or not (
                isinstance(line, int) or (isinstance(line, str) and line.strip().isdigit())):
            reason = "line_not_integer"
        else:
            reason = None
        if reason:
            parse_errors.append({"origin": origin, "index": index,
                                 "reason": reason, "excerpt": _excerpt(item)})
            continue
        line = item["line"]
        items.append({**item, "line": int(line) if isinstance(line, str) else line,
                      "origin": origin, "raw_candidate_id": f"{origin}:{index}"})
    return items


def _raw_findings_payload(response, max_suggestions):
    """Normalise the single-call discovery response into the review payload shape."""
    findings = response.get("findings") if isinstance(response, dict) else None
    suggestions = []
    for item in (findings or [])[:max_suggestions]:
        if not isinstance(item, dict):
            continue
        # Doubts stay out of the reason the verifier reads: it had dismissed
        # gold findings because "the candidate itself concedes" a hedge.
        reason = str(item.get("reason") or "").strip()
        suggestions.append({
            "file_path": item.get("file") or item.get("file_path"),
            "line": item.get("line"),
            "severity": "medium",
            "confidence": 0.6,
            "title": str(item.get("title") or "Possible defect"),
            "reason": reason or "Raw discovery candidate",
            "recommendation": str(item.get("recommendation") or ""),
            "evidence": [str(entry) for entry in (item.get("evidence") or [])],
            "failure_scenario": str(item.get("failure_scenario") or reason),
            "raw_candidate_id": item.get("raw_candidate_id"),
            "candidate_origin": item.get("origin"),
            "merged_candidates": list(item.get("merged_candidates") or []),
        })
    return {
        "summary": str(response.get("summary") or "Raw single-call discovery"),
        "risk_level": "medium",
        "suggestions": suggestions,
    }
