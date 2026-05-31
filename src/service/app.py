"""FastAPI web console for AI PR Review — wraps existing analysis logic."""

from __future__ import annotations

import datetime
import json
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.analyzer.risk_rules import scan_risks
from src.github.client import GitHubApiError, GitHubClient
from src.output.markdown import render_markdown
from src.reviewer.engine import build_rule_only_report, review_with_ai
from src.reviewer.provider import OpenAICompatibleProvider
from src.utils.config import detect_language, get_settings

app = FastAPI(title="AI PR Review Console")


_HISTORY_TS_RE = re.compile(r"(\d{8}-\d{6})(?:_full)?$")


def _json_safe_model(model: object) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return {}


def _history_timestamp(stem: str) -> str:
    match = _HISTORY_TS_RE.search(stem)
    return match.group(1) if match else "unknown"


class TrendRequest(BaseModel):
    repo: str
    count: int = 10

class AnalyzeRequest(BaseModel):
    repo: str
    pr_number: int
    language: str | None = None  # auto-detect if not set
    use_ai: bool = True


class AnalyzeResponse(BaseModel):
    report: dict
    markdown: str
    duration_seconds: float


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "ai-pr-review-console"}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> AnalyzeResponse | dict:
    settings = get_settings()
    t0 = time.monotonic()

    # 1. Fetch PR data
    try:
        with GitHubClient(settings.github_token, timeout=settings.request_timeout_seconds) as gh:
            pr = gh.get_pull_request(req.repo, req.pr_number)
            files = gh.get_changed_files(req.repo, req.pr_number)
    except GitHubApiError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    if pr is None or not pr.repo:
        raise HTTPException(status_code=502, detail="Failed to fetch PR information")

    # Auto-detect language if not specified
    if not req.language:
        req.language = detect_language(pr.title or "", pr.body or "")

    # 2. Rule scan
    findings = scan_risks(files)

    # 3. Build report
    report = build_rule_only_report(pr, files, findings, language=req.language)

    # 4. AI review (if enabled)
    if req.use_ai:
        api_key = settings.openai_api_key
        if not api_key:
            report.analysis_warnings.append(
                "OPENAI_API_KEY not set; falling back to rule-only."
            )
        else:
            provider = OpenAICompatibleProvider(
                api_key=api_key,
                model=settings.review_model,
                base_url=settings.openai_base_url,
                timeout=settings.request_timeout_seconds,
            )
            try:
                report = review_with_ai(
                    pr=pr,
                    files=files,
                    findings=findings,
                    provider=provider,
                    max_suggestions=settings.max_suggestions,
                    min_confidence=settings.min_comment_confidence,
                    max_suggestions_per_file=settings.max_suggestions_per_file,
                    language=req.language,
                )
            except Exception as exc:
                report.ai_failure_reason = str(exc)
                report.analysis_warnings.append(
                    f"AI review failed: {exc}. Showing rule-based analysis only."
                )
            finally:
                provider.close()

    # 5. Render output
    md = render_markdown(report, language=req.language)
    elapsed = time.monotonic() - t0

    # Save to history
    history_dir = Path("reports/history")
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    repo_slug = req.repo.replace("/", "_")
    history_file = history_dir / f"{repo_slug}_{req.pr_number}_{ts}.json"
    history_entry = {
        "repo": req.repo,
        "pr_number": req.pr_number,
        "title": pr.title,
        "analyzed_at": ts,
        "risk_level": report.risk_level.value if hasattr(report.risk_level, "value") else str(report.risk_level),
        "files_count": len(files),
        "additions": sum(f.additions for f in files),
        "deletions": sum(f.deletions for f in files),
        "suggestions_count": len(report.suggestions or []),
        "used_ai": report.used_ai,
        "report_confidence": report.report_confidence,
    }
    with open(history_file, "w", encoding="utf-8") as hf:
        json.dump(history_entry, hf, ensure_ascii=False, indent=2)
    # Also save full report
    full_history = history_dir / f"{repo_slug}_{req.pr_number}_{ts}_full.json"
    with open(full_history, "w", encoding="utf-8") as hf:
        json.dump(
            {
                "report": _json_safe_model(report),
                "markdown": md,
                "duration_seconds": round(elapsed, 2),
                "analyzed_at": ts,
            },
            hf,
            ensure_ascii=False,
            indent=2,
        )

    return AnalyzeResponse(
        report=_json_safe_model(report),
        markdown=md,
        duration_seconds=round(elapsed, 2),
    )


# ── History endpoints ──

@app.get("/api/history")
def list_history() -> list[dict]:
    """List all past review results with summary metadata."""
    history_dir = Path("reports/history")
    if not history_dir.exists():
        return []
    entries = []
    for f in sorted(history_dir.glob("*_full.json"), reverse=True):
        try:
            with open(f, "r", encoding="utf-8") as hf:
                payload = json.load(hf)
            report = payload.get("report", payload)
            pr = report.get("pr", {})
            files = report.get("files", [])
            entries.append({
                "id": f.stem,
                "repo": pr.get("repo", ""),
                "pr_number": pr.get("number", 0),
                "title": pr.get("title", ""),
                "html_url": pr.get("html_url", ""),
                "risk_level": report.get("risk_level", "unknown"),
                "files_count": len(files),
                "additions": sum(f.get("additions", 0) for f in files),
                "deletions": sum(f.get("deletions", 0) for f in files),
                "suggestions_count": len(report.get("suggestions", [])),
                "used_ai": report.get("used_ai", False),
                "report_confidence": report.get("report_confidence", "unknown"),
                "analyzed_at": payload.get("analyzed_at") or _history_timestamp(f.stem),
            })
        except Exception:
            continue
    return entries

@app.get("/api/history/{entry_id}")
def get_history_entry(entry_id: str) -> dict:
    """Retrieve a full historical review report."""
    history_file = Path(f"reports/history/{entry_id}.json")
    if not history_file.exists():
        raise HTTPException(status_code=404, detail="History entry not found")
    with open(history_file, "r", encoding="utf-8") as hf:
        payload = json.load(hf)
    if "report" in payload:
        return payload
    return {"report": payload, "markdown": "", "duration_seconds": 0}

# ── Repo Trend ──

class TrendRequest(BaseModel):
    repo: str
    count: int = 10  # number of recent PRs to fetch

@app.post("/api/repo-trend")
def repo_trend(req: TrendRequest) -> dict:
    """Fetch recent PRs for a repo and run rule-only analysis on each."""
    settings = get_settings()
    count = max(1, min(req.count, 20))

    try:
        with GitHubClient(settings.github_token, timeout=settings.request_timeout_seconds) as gh:
            pr_list = gh.list_pull_requests(req.repo, count=count)
    except GitHubApiError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    if not pr_list:
        return {"repo": req.repo, "prs": [], "note": "No open PRs found"}

    results = []
    for pr in pr_list:
        try:
            with GitHubClient(settings.github_token, timeout=settings.request_timeout_seconds) as gh:
                files = gh.get_changed_files(req.repo, pr.number)
            findings = scan_risks(files)
            additions = sum(f.additions for f in files)
            deletions = sum(f.deletions for f in files)
            risk = "low"
            if findings:
                sevs = [f.severity for f in findings]
                if any(s.value == "critical" for s in sevs):
                    risk = "critical"
                elif any(s.value == "high" for s in sevs):
                    risk = "high"
                elif any(s.value == "medium" for s in sevs):
                    risk = "medium"
            results.append({
                "pr_number": pr.number,
                "title": pr.title,
                "author": pr.author,
                "html_url": pr.html_url,
                "risk_level": risk,
                "files_count": len(files),
                "additions": additions,
                "deletions": deletions,
                "findings_count": len(findings),
                "top_findings": [
                    {"rule_id": f.rule_id, "title": f.title, "severity": f.severity.value}
                    for f in findings[:3]
                ],
            })
        except Exception:
            # Individual PR fetch failure — skip this PR, continue with others
            continue

    return {"repo": req.repo, "count": len(results), "prs": results}

# Serve static frontend at root
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
