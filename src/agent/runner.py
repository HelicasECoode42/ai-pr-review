"""Review Agent Runner — orchestrates review tools with trace and degradation.

The Runner is the core of the Agent layer.  It receives a
ReviewAgentRequest, selects a strategy via policy, executes tools in
sequence through the registry, records every step in ReviewAgentState,
and returns a ReviewAgentResult containing the final report, markdown,
JSON, and an agent sidecar for trace inspection.

Round 1 supports: rule_only, one_shot_ai (and explicit two_stage).
Auto-selection always picks one_shot_ai for now.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel

from src.agent.policy import choose_agent_strategy
from src.agent.registry import AgentTool, AgentToolRegistry
from src.agent.state import AgentStep, AgentStepStatus, ReviewAgentState
from src.agent.trace import summarize_input, summarize_observation
from src.models import ReviewReport, Severity
from src.utils.config import Settings, get_settings


class ReviewAgentRequest(BaseModel):
    """Input to the Agent Runner — mirrors CLI/Web parameters."""

    repo: str
    pr_number: int
    language: str | None = None
    use_ai: bool = True
    agent_mode: str = "auto"
    review_mode: str = "full_pr"
    reviewer_version: str = "pr-branch"
    execution_status: str = "success"
    degradation_reason: str | None = None
    report_confidence: str = "normal"
    pr_syntax_ok: bool = True
    reviewed_commit: str | None = None
    trigger_event: str | None = None
    workflow_run_url: str | None = None


class ReviewAgentResult(BaseModel):
    """Output of the Agent Runner — report + trace sidecar."""

    report: ReviewReport
    markdown: str
    json_report: dict[str, Any]
    agent_sidecar: dict[str, Any]
    state: ReviewAgentState


class ReviewAgentRunner:
    """Orchestrates a review run through the tool registry.

    Usage::

        runner = ReviewAgentRunner(settings)
        result = runner.run(request)
        # result.report, result.markdown, result.json_report, result.agent_sidecar
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._registry = AgentToolRegistry()
        self._register_tools()

    # ── tool registration ───────────────────────────────────

    def _register_tools(self) -> None:
        """Register all available agent tools into the registry."""
        from src.analyzer.risk_rules import scan_risks
        from src.github.client import GitHubClient
        from src.output.json_report import render_json
        from src.output.markdown import render_markdown
        from src.reviewer.engine import build_rule_only_report, review_with_ai
        from src.models import ReviewMeta
        from datetime import datetime, timezone
        from src.reviewer.provider import OpenAICompatibleProvider

        settings = self._settings

        # ── fetch_pull_request ──────────────────────────
        def _fetch_pr(ctx: dict[str, Any]) -> None:
            with GitHubClient(settings.github_token, timeout=settings.request_timeout_seconds) as gh:
                ctx["pr"] = gh.get_pull_request(ctx["repo"], ctx["pr_number"])

        self._registry.register(AgentTool(name="fetch_pull_request", execute=_fetch_pr))

        # ── fetch_changed_files ─────────────────────────
        def _fetch_files(ctx: dict[str, Any]) -> None:
            with GitHubClient(settings.github_token, timeout=settings.request_timeout_seconds) as gh:
                ctx["files"] = gh.get_changed_files(ctx["repo"], ctx["pr_number"])

        self._registry.register(AgentTool(name="fetch_changed_files", execute=_fetch_files))

        # ── scan_risks ──────────────────────────────────
        def _scan(ctx: dict[str, Any]) -> None:
            ctx["findings"] = scan_risks(ctx["files"])

        self._registry.register(AgentTool(name="scan_risks", execute=_scan))

        # ── analyze_cross_file ──────────────────────────
        def _cross_file(ctx: dict[str, Any]) -> None:
            from src.analyzer.cross_file import analyze_cross_file_impact
            cross = analyze_cross_file_impact(ctx["files"])
            ctx["cross_file_findings"] = cross
            # Merge into findings for downstream review
            if cross:
                ctx["findings"] = list(ctx["findings"]) + cross

        self._registry.register(AgentTool(name="analyze_cross_file", execute=_cross_file))

        # ── build_rule_only_report ──────────────────────
        def _rule_report(ctx: dict[str, Any]) -> None:
            review_meta = ReviewMeta(
                reviewed_commit=ctx.get("reviewed_commit"),
                trigger_event=ctx.get("trigger_event"),
                workflow_run_url=ctx.get("workflow_run_url"),
                updated_at=datetime.now(timezone.utc).isoformat(),
                review_mode=ctx.get("review_mode", "full_pr"),
            )
            ctx["report"] = build_rule_only_report(
                ctx["pr"],
                ctx["files"],
                ctx["findings"],
                language=ctx.get("language", "en"),
                reviewer_version=ctx.get("reviewer_version", "pr-branch"),
                execution_status=ctx.get("execution_status", "success"),
                degradation_reason=ctx.get("degradation_reason"),
                report_confidence=ctx.get("report_confidence", "normal"),
                pr_syntax_ok=ctx.get("pr_syntax_ok", True),
                review_meta=review_meta,
            )

        self._registry.register(AgentTool(name="build_rule_only_report", execute=_rule_report))

        # ── one_shot_ai_review ──────────────────────────
        def _ai_review(ctx: dict[str, Any]) -> None:
            provider = OpenAICompatibleProvider(
                api_key=settings.openai_api_key,
                model=settings.review_model,
                base_url=settings.openai_base_url,
                timeout=settings.request_timeout_seconds,
            )
            try:
                review_meta = ReviewMeta(
                    reviewed_commit=ctx.get("reviewed_commit"),
                    trigger_event=ctx.get("trigger_event"),
                    workflow_run_url=ctx.get("workflow_run_url"),
                    updated_at=datetime.now(timezone.utc).isoformat(),
                    review_mode=ctx.get("review_mode", "full_pr"),
                )
                ctx["report"] = review_with_ai(
                    pr=ctx["pr"],
                    files=ctx["files"],
                    findings=ctx["findings"],
                    provider=provider,
                    max_suggestions=settings.max_suggestions,
                    min_confidence=settings.min_comment_confidence,
                    max_suggestions_per_file=settings.max_suggestions_per_file,
                    language=ctx.get("language", "en"),
                    reviewer_version=ctx.get("reviewer_version", "pr-branch"),
                    execution_status=ctx.get("execution_status", "success"),
                    degradation_reason=ctx.get("degradation_reason"),
                    report_confidence=ctx.get("report_confidence", "normal"),
                    pr_syntax_ok=ctx.get("pr_syntax_ok", True),
                    review_meta=review_meta,
                    two_stage=(ctx.get("strategy") == "two_stage"),
                )
            finally:
                provider.close()

        self._registry.register(AgentTool(name="one_shot_ai_review", execute=_ai_review))

        # ── render_markdown ─────────────────────────────
        def _render_md(ctx: dict[str, Any]) -> None:
            ctx["markdown"] = render_markdown(
                ctx["report"], language=ctx.get("language", "en")
            )

        self._registry.register(AgentTool(name="render_markdown", execute=_render_md))

        # ── render_json ─────────────────────────────────
        def _render_json(ctx: dict[str, Any]) -> None:
            ctx["json_report"] = render_json(ctx["report"])

        self._registry.register(AgentTool(name="render_json", execute=_render_json))

    # ── main entry point ─────────────────────────────────────

    def run(self, request: ReviewAgentRequest) -> ReviewAgentResult:
        """Execute a full review run and return report + trace.

        Execution order (Batch A):
          1. fetch_pull_request
          2. fetch_changed_files
          3. scan_risks
          4. choose_agent_strategy  ← after real data is available
          5. review tool (based on strategy)
          6. render_markdown
          7. render_json
        """
        # Detect language if not specified
        language = request.language
        if not language:
            language = "en"

        # ── Build initial state ─────────────────────────────
        state = ReviewAgentState(
            repo=request.repo,
            pr_number=request.pr_number,
            language=language,
            use_ai=request.use_ai,
        )

        # ── Build tool context (mutable data carrier) ────────
        ctx: dict[str, Any] = {
            "repo": request.repo,
            "pr_number": request.pr_number,
            "language": language,
            "use_ai": request.use_ai,
            "strategy": None,  # filled after scan_risks
            "reviewer_version": request.reviewer_version,
            "execution_status": request.execution_status,
            "degradation_reason": request.degradation_reason,
            "report_confidence": request.report_confidence,
            "pr_syntax_ok": request.pr_syntax_ok,
            "reviewed_commit": request.reviewed_commit,
            "trigger_event": request.trigger_event,
            "workflow_run_url": request.workflow_run_url,
            "review_mode": request.review_mode,
            "pr": None,
            "files": [],
            "findings": [],
            "report": None,
            "markdown": "",
            "json_report": {},
        }

        # ── Step 1-3: Fetch + Scan (before strategy) ─────────
        self._call_tool(state, ctx, "fetch_pull_request")
        try:
            self._call_tool(state, ctx, "fetch_changed_files")
        except Exception as exc:
            state.warnings.append(f"Failed to fetch changed files: {exc}")
            ctx["files"] = []
        self._call_tool(state, ctx, "scan_risks")
        self._call_tool(state, ctx, "analyze_cross_file")

        # ── Step 4: Choose strategy with real data ───────────
        has_api_key = bool(self._settings.openai_api_key)
        files = ctx["files"]
        findings = ctx["findings"]

        files_count = len(files)
        additions = sum(getattr(f, "additions", 0) for f in files)
        findings_count = len(findings)
        high_severity_count = sum(
            1 for f in findings
            if getattr(f, "severity", None) in (Severity.CRITICAL, Severity.HIGH)
        )

        strategy, strategy_reason = choose_agent_strategy(
            use_ai=request.use_ai,
            has_api_key=has_api_key,
            files_count=files_count,
            additions=additions,
            findings_count=findings_count,
            high_severity_count=high_severity_count,
            requested_mode=request.agent_mode,
        )
        state.strategy = strategy  # type: ignore[assignment]
        state.strategy_reason = strategy_reason
        ctx["strategy"] = strategy

        # ── Degradation path tracking ───────────────────────
        if request.use_ai and strategy == "rule_only" and not has_api_key:
            state.degradation_path.append("one_shot_ai")
            state.warnings.append("AI requested but no API key; degraded to rule_only")

        # ── Step 5: Execute review based on strategy ──────────
        if strategy == "rule_only":
            self._call_tool(state, ctx, "build_rule_only_report")

        elif strategy == "one_shot_ai":
            try:
                self._call_tool(state, ctx, "one_shot_ai_review")
            except Exception:
                state.degradation_path.append("one_shot_ai")
                state.strategy = "rule_only"  # type: ignore[assignment]
                ctx["strategy"] = "rule_only"
                state.warnings.append("AI review failed; degrading to rule_only")
                self._call_tool(state, ctx, "build_rule_only_report")

        elif strategy == "two_stage":
            try:
                self._call_tool(state, ctx, "one_shot_ai_review")  # two_stage=True internally
            except Exception:
                state.degradation_path.append("two_stage")
                state.warnings.append("Two-stage review failed; degrading to rule_only")
                self._call_tool(state, ctx, "build_rule_only_report")

        # ── Step 6-7: Render outputs ─────────────────────────
        self._call_tool(state, ctx, "render_markdown")
        self._call_tool(state, ctx, "render_json")

        # ── Build agent sidecar ─────────────────────────────
        agent_sidecar: dict[str, Any] = {
            "strategy": state.strategy,
            "strategy_reason": state.strategy_reason,
            "degradation_path": state.degradation_path,
            "warnings": state.warnings,
            "steps": [step.model_dump() for step in state.steps],
        }

        # render_json produces a string; convert to dict for the result
        json_report = ctx["json_report"]
        if isinstance(json_report, str):
            import json
            json_report = json.loads(json_report)

        return ReviewAgentResult(
            report=ctx["report"],
            markdown=ctx["markdown"],
            json_report=json_report,
            agent_sidecar=agent_sidecar,
            state=state,
        )

    # ── helpers ──────────────────────────────────────────────

    def _call_tool(
        self,
        state: ReviewAgentState,
        ctx: dict[str, Any],
        tool_name: str,
    ) -> None:
        """Invoke a tool by name, recording the step in agent state."""
        tool = self._registry.get(tool_name)
        step_index = len(state.steps)

        t0 = time.monotonic()
        try:
            tool.execute(ctx)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            step = AgentStep(
                index=step_index,
                tool=tool_name,
                input_summary=summarize_input(ctx, tool_name),
                observation_summary=summarize_observation(
                    ctx.get(self._observation_key(tool_name), ctx), tool_name
                ),
                status="success",
                duration_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            step = AgentStep(
                index=step_index,
                tool=tool_name,
                input_summary=summarize_input(ctx, tool_name),
                observation_summary={},
                status="failed",
                error=str(exc),
                duration_ms=elapsed_ms,
            )
            state.steps.append(step)
            raise

        state.steps.append(step)

    @staticmethod
    def _observation_key(tool_name: str) -> str:
        """Return the ctx key that holds this tool's primary output."""
        mapping: dict[str, str] = {
            "fetch_pull_request": "pr",
            "fetch_changed_files": "files",
            "scan_risks": "findings",
            "analyze_cross_file": "cross_file_findings",
            "build_rule_only_report": "report",
            "one_shot_ai_review": "report",
            "build_review_report": "report",
            "render_markdown": "markdown",
            "render_json": "json_report",
            "triage_hotspots": "hotspots",
            "deep_review_hotspot": "suggestions",
        }
        return mapping.get(tool_name, tool_name)
