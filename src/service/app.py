"""FastAPI web console for AI PR Review — wraps existing analysis logic."""

from __future__ import annotations

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
from src.utils.config import get_settings

app = FastAPI(title="AI PR Review Console")


class AnalyzeRequest(BaseModel):
    repo: str
    pr_number: int
    language: str = "zh"
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

    return AnalyzeResponse(
        report=report.model_dump(),
        markdown=md,
        duration_seconds=round(elapsed, 2),
    )


# Serve static frontend at root
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
