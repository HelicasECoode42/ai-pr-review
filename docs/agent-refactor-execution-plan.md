# AI PR Review Agent 改造计划

## 1. 当前事实

### 已有的 pipeline（不变）

- CLI（`src/cli/main.py`）：Typer 入口，获取 PR → 规则扫描 → AI 审查 → 报告
- Web Console（`src/service/app.py`）：FastAPI，`/api/analyze`
- GitHub Actions（`.github/workflows/ai-pr-review.yml`）：base 分支 reviewer + PR head 语法诊断
- VS Code 插件：读 `pr-review.json`，Problems / Panel / CodeLens

### 已有的 Agent 初版

| 文件 | 状态 |
|---|---|
| `src/agent/state.py` | `ReviewAgentState`, `AgentStep` — 只记录 trace 所需字段，不存大对象 |
| `src/agent/trace.py` | `summarize_input`, `summarize_observation`, `redact_secrets` |
| `src/agent/policy.py` | `choose_agent_strategy` — auto 默认 one_shot_ai，two_stage 需显式指定 |
| `src/agent/registry.py` | `AgentTool`, `AgentToolRegistry` |
| `src/agent/runner.py` | `ReviewAgentRunner` — rule-only 端到端可跑，含 one-shot/two-stage 骨架 |

### 已有的测试

```
tests/test_agent_policy.py          11 tests
tests/test_agent_trace.py           20 tests
tests/test_agent_runner_rule_only.py  6 tests
已有测试（engine/diff/context/rules） 39 tests
────────────────────────────────────────────
总计                                76 tests 全部通过
```

### 目标

不是推倒重写。在现有三层（GitHub Actions / Web Console / VS Code）之上增加 Agent 编排层，让每次审查输出结构化 Trace，但不破坏任何现有接口。

---

## 2. 当前不做什么

- **不接 CLI/Web 默认路径**：`--agent-mode` 新增但默认 `"off"`，走旧逻辑
- **不继续扩展 two-stage**：骨架留在 runner 里，但 triage/deep_review 拆分放后面
- **不改 GitHub Actions artifact**：workflow 文件、路径、名称不变
- **不改 VS Code JSON 兼容字段**：`review_meta` / `pr` / `files` / `suggestions` 字段保留
- **不引入复杂多 Agent 框架**：不装 LangChain/AutoGen，工具注册表只做薄封装

---

## 3. Stage 1：Hardening（当前阶段）

分两批执行。第一批是必须修的（影响 trace 正确性和可观测性），第二批是改善项（不影响正确性，可延后）。

### Batch A（优先）

#### 3.1 拆 `fetch_pull_request`

当前 `runner.py` 的 `fetch_pull_request` tool 同时调了 `get_pull_request` 和 `get_changed_files`。

改为两个独立 tool：

```
fetch_pull_request      → ctx["pr"]       （失败 → 无法继续）
fetch_changed_files     → ctx["files"]    （失败 → 记录 warning，可降级）
```

#### 3.2 策略选择移到 fetch + scan 之后

当前 `choose_agent_strategy` 在 fetch 之前调用，`files_count`/`additions`/`findings_count` 全为 0。

改为：

```
fetch_pull_request → fetch_changed_files → scan_risks → choose_agent_strategy → review/render
```

#### 3.3 `strategy_reason` 进入 state 和 sidecar

`choose_agent_strategy` 已返回 `(strategy, reason)`，但 runner 只用了 `strategy`。

- `ReviewAgentState` 新增 `strategy_reason: str | None = None`
- `agent_sidecar` 输出 `strategy_reason`

#### 3.4 `agent_sidecar` 加 `warnings`

当前 sidecar 有 `strategy`、`degradation_path`、`steps`，漏了 `warnings`。

改为：

```json
{
  "strategy": "rule_only",
  "strategy_reason": "OPENAI_API_KEY not set",
  "degradation_path": ["one_shot_ai"],
  "warnings": ["AI requested but no API key; degraded to rule_only"],
  "steps": [...]
}
```

#### 补测试

覆盖拆分后的 tool 顺序、strategy_reason 非空、warnings 包含降级原因。

### Batch B（可延后）

#### 3.5 `AgentTool` 加 `description` / `risk`

当前 registry 的 `AgentTool` 只有 `name` + `execute`。

```python
@dataclass
class AgentTool:
    name: str
    execute: Callable[..., Any]
    description: str = ""   # 人类可读，出现在 trace 和错误信息中
    risk: str = "read"      # "read" | "write" — 标识是否有副作用
```

`risk` 当前不触发校验，仅记录。未来 `write` 类 tool（如发布 GitHub comment）可在此做额外审计。

#### 3.6 `ReviewAgentRequest` / `ReviewAgentResult` 改为 Pydantic BaseModel

当前是手写 `__init__` 的普通 class。改为 Pydantic 后：

- 可以直接作为 FastAPI 请求体
- 可以 `.model_dump()` 输出到 JSON sidecar
- 测试中构造更简洁

```python
class ReviewAgentRequest(BaseModel):
    repo: str
    pr_number: int
    language: str | None = None
    use_ai: bool = True
    strategy_mode: str = "auto"           # Runner 内部策略选择
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
    report: ReviewReport                   # 保持强类型，对外输出时再 model_dump
    markdown: str
    json_report: dict[str, Any]
    agent_sidecar: dict[str, Any]
```

> **命名约定**：CLI 参数叫 `--agent-mode`（默认 `"off"`），表示是否启用 Runner。`ReviewAgentRequest.strategy_mode`（默认 `"auto"`）是 Runner 内部的策略选择字段。CLI 中 `agent_mode != "off"` 时才构造 `ReviewAgentRequest` 并把 `agent_mode` 的值映射到 `strategy_mode`。

---

## 4. Stage 1 验收

### Batch A 验收

```bash
uv sync --extra dev
uv run pytest tests/test_agent_policy.py tests/test_agent_trace.py tests/test_agent_runner_rule_only.py
```

- [ ] agent step 列表中包含 `fetch_pull_request` 和 `fetch_changed_files`（已拆分）
- [ ] `scan_risks` 在 `choose_agent_strategy` 之前执行
- [ ] `agent_sidecar.strategy_reason` 非空
- [ ] `agent_sidecar.warnings` 包含降级原因（当 API key 为空时）
- [ ] 所有已有 76 个测试仍通过
- [ ] CLI（`--agent-mode off`）/ Web Console 不受影响

### Batch B 验收

- [ ] `AgentTool` 有 `description` 和 `risk` 字段（默认值 `""` / `"read"`）
- [ ] `ReviewAgentRequest` / `ReviewAgentResult` 为 Pydantic BaseModel
- [ ] `ReviewAgentResult.report` 类型为 `ReviewReport`（非 dict）

---

## 5. Stage 2：接入 CLI / Web

### 5.1 CLI 新增 `--agent-mode`

```python
agent_mode: str = typer.Option(
    "off", "--agent-mode",
    help="Agent mode: off, auto, rule_only, one_shot_ai, two_stage.",
)
```

参数分离（不复用 `--review-mode`）：

```
--review-mode full_pr|incremental     # 审查范围（已有，不变）
--agent-mode off|auto|rule_only|one_shot_ai|two_stage   # Agent 策略（新增）
```

逻辑分支：

```python
if agent_mode == "off":
    # 走原有 pipeline（与当前完全一致）
    report = build_rule_only_report(...)
    if use_ai:
        report = review_with_ai(...)
else:
    runner = ReviewAgentRunner(settings)
    result = runner.run(ReviewAgentRequest(
        repo=repo,
        pr_number=pr_number,
        use_ai=use_ai,
        strategy_mode=agent_mode,   # CLI --agent-mode 值映射到 request.strategy_mode
        # ... 其余参数透传 ...
    ))
    report = result.report
    content = result.markdown
    # agent_sidecar 写入 JSON sidecar 顶层
```

### 5.2 Web Console `/api/analyze` 接入

```python
if agent_mode == "off":
    # 走原有逻辑（不变）
else:
    runner = ReviewAgentRunner(settings)
    result = runner.run(ReviewAgentRequest(
        repo=req.repo,
        pr_number=req.pr_number,
        use_ai=req.use_ai,
        strategy_mode=agent_mode,
        language=req.language,
    ))
    return {
        "report": result.json_report,
        "markdown": result.markdown,
        "duration_seconds": elapsed,
        "agent": result.agent_sidecar.model_dump(),   # ★ 新增
    }
```

### 5.3 验收

- `--agent-mode off`（默认）行为 = 当前完全一致
- `--agent-mode rule_only` 生成正确 report
- `--agent-mode one_shot_ai` 调用 AI 成功
- 模型 401/429/timeout/5xx 降级为 rule_only
- JSON sidecar 顶层 `agent` 字段不影响 `report` 字段 shape
- Web Console 原有前端不因新增字段崩溃

---

## 6. Stage 3：Two-Stage 正式化（第二轮）

### 6.1 拆分 `_filter_suggestions`

`src/reviewer/engine.py` → `src/reviewer/suggestion_filter.py`（公开函数）

### 6.2 拆分 triage / deep_review

`src/reviewer/two_stage.py` → `triage.py` + `deep_review.py` + 保留 `two_stage.py` 入口

### 6.3 Runner 接入 two-stage

```
triage_hotspots → deep_review_hotspot (×n) → filter_suggestions → build_report
降级链：two_stage → one_shot_ai → rule_only
```

### 6.4 Web Console Trace Tab

后端响应已有 `agent` 字段，前端新增轻量 Trace 展示。

---

## 7. 鲁棒性验收清单（全阶段）

- [ ] `uv run pytest` 全部通过
- [ ] `--no-ai` 无模型 key 可运行（rule-only 报告）
- [ ] `--agent-mode off` 行为 = 当前默认行为
- [ ] `--agent-mode rule_only` / `one_shot_ai` 正确
- [ ] 模型 401 / 429 / timeout / 5xx 降级为 rule-only
- [ ] LLM 返回非法 JSON → 重试一次 → 仍失败则降级
- [ ] Agent trace 不泄露 token / prompt / raw model output
- [ ] JSON sidecar 顶层 `agent` 字段不影响 `report` 字段
- [ ] Web Console 前端不崩溃
- [ ] GitHub Actions artifact 路径不变
- [ ] 报告生成 != 合并门禁（坏 PR 仍阻止合并）
- [ ] `--review-mode full_pr/incremental` 语义不变
