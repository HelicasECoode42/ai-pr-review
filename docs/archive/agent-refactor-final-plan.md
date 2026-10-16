# Agent 改造执行计划（修订版）

> 整合 `agent-refactor-execution-plan.md` 原始方案 + `agent-refactor-review-and-refined-plan.md` 审阅意见 + 可靠性评价反馈，形成最终可执行版本。

---

## 核心定位（不变）

AI PR Review 不是聊天机器人，而是 **Review Task Agent**：

- 工具集合固定，不是开放式调用
- 策略选择受规则约束，可解释
- 输出经过 Pydantic schema + 本地过滤
- 失败降级，优先保证"有用的报告"
- 同一 Agent Runner 复用至 GitHub Actions / Web Console / VS Code

---

## 三个关键修正

基于可靠性评价反馈，以下三点与原方案不同：

### 修正 1：`--review-mode` 和 `--agent-mode` 分离

**原方案**：建议用 `--review-mode agent/two_stage/rule_only` 或 `--agent-mode`

**修正后**：两个参数职责不同，不能合并：

```
--review-mode full_pr|incremental     # 审查范围（已有，不变）
--agent-mode auto|rule_only|one_shot_ai|two_stage   # Agent 策略（新增）
```

`review_mode` 控制"审查多少 diff"，`agent_mode` 控制"用什么策略审查"。这个区分在代码里也要保持一致，不可混用。

### 修正 2：Agent Trace 不进 ReviewReport Schema

**原方案**：给 `ReviewReport` 增加 `agent_steps` 和 `agent_strategy` 字段

**修正后**：Trace 放在 JSON sidecar 顶层，不污染 report schema：

```json
{
  "report": { "...ReviewReport 内容不变..." },
  "agent": {
    "strategy": "two_stage",
    "degradation_path": ["two_stage", "one_shot_ai"],
    "steps": [
      {"index": 0, "tool": "fetch_pull_request", "status": "success", "duration_ms": 320}
    ]
  }
}
```

**理由**：VS Code 插件和 Web Console 前端依赖 `ReviewReport` JSON shape。Trace 是增量信息，放 sidecar 不影响现有消费者。等前端适配后再考虑合并。

### 修正 3：第一版只做 rule_only + one_shot_ai，two-stage 放第二轮

**原方案**：阶段三/四直接支持 two-stage 策略

**修正后**：

| 轮次 | 范围 |
|---|---|
| 第一轮 | Agent State/Trace + Policy + Registry + rule-only Runner + one-shot Runner + CLI `--agent-mode`（默认不启用） |
| 第二轮 | two-stage 拆分 + two-stage Runner + Web Console Trace 展示 |

**理由**：two-stage 涉及 `two_stage.py` 私有导入修复、triage/deep_review 拆分，变量太多。第一轮先把 Agent 骨架立起来并验证 CLI/Web 兼容性，第二轮再加 two-stage。

---

## 执行步骤

### 阶段一：Agent 基础设施（不接入主流程）

#### 1.1 `src/agent/state.py` — Agent 状态模型

```python
from pydantic import BaseModel, Field
from typing import Any, Literal

AgentStepStatus = Literal["success", "failed", "skipped"]
AgentStrategy = Literal["rule_only", "one_shot_ai", "two_stage", "incremental"]

class AgentStep(BaseModel):
    index: int
    tool: str
    input_summary: dict[str, Any] = Field(default_factory=dict)
    observation_summary: dict[str, Any] = Field(default_factory=dict)
    status: AgentStepStatus
    error: str | None = None
    duration_ms: int | None = None

class ReviewAgentState(BaseModel):
    repo: str
    pr_number: int
    strategy: AgentStrategy | None = None
    degradation_path: list[str] = Field(default_factory=list)
    language: str = "en"
    use_ai: bool = True
    # ... 中间态数据字段 ...
    steps: list[AgentStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
```

关键点：
- `degradation_path` 记录实际降级路径，如 `["two_stage", "one_shot_ai", "rule_only"]`
- 不在此阶段接入 `PullRequest`/`ChangedFile` 等已存在的 Pydantic 模型 —— 它们通过 tool 函数传递，不存入 state 的序列化快照

#### 1.2 `src/agent/trace.py` — Trace 摘要与脱敏

```python
def summarize_input(value: object) -> dict: ...
def summarize_observation(value: object) -> dict: ...
def redact_secrets(value: object) -> object: ...
```

安全要求（强制执行，不可妥协）：
- 不保存完整 API token → 替换为 `<redacted>`
- 不保存完整超长 prompt → 只记录字符数
- 不保存完整模型输出原文 → 只记录 `parse_status`、suggestions 数量、risk_level
- 可记录：文件数量、风险命中数、策略、每步耗时、失败原因

新增测试：`tests/test_agent_trace.py`（覆盖脱敏和摘要）

#### 1.3 `src/agent/policy.py` — 策略选择

```python
def choose_agent_strategy(
    *,
    use_ai: bool,
    has_api_key: bool,
    files_count: int,
    additions: int,
    findings_count: int,
    high_severity_count: int,
    requested_mode: str = "auto",
) -> tuple[str, str]:   # (strategy, reason)
```

策略规则：

| 条件 | 策略 | 原因 |
|---|---|---|
| `use_ai=False` 或 `has_api_key=False` | `rule_only` | 不可用 AI |
| `requested_mode` 显式指定 | 使用指定值 | 用户覆盖 |
| `files_count > 20` 且 `high_severity_count >= 3` | `two_stage` | 大 PR + 高风险集中（第二轮实现） |
| 其余 | `one_shot_ai` | 默认 |

第一轮中 `two_stage` 不会出现在自动选择结果里（仅显式指定时可用）。`incremental` 由 `--review-mode` 控制，不进 agent strategy。

新增测试：`tests/test_agent_policy.py`（覆盖所有条件分支）

#### 1.4 `src/agent/registry.py` — 工具注册表

```python
@dataclass
class AgentTool:
    name: str
    execute: Callable[[ReviewAgentState], None]

class AgentToolRegistry:
    def register(self, tool: AgentTool) -> None: ...
    def get(self, name: str) -> AgentTool: ...
```

第一版不需要 `description`/`risk` 字段 —— 策略选择是规则驱动的，不是 LLM 驱动的。

#### 1.5 `src/agent/runner.py` — Rule-Only Runner

```python
class ReviewAgentRunner:
    def run(self, request: ReviewAgentRequest) -> ReviewAgentResult: ...

class ReviewAgentRequest(BaseModel):
    repo: str
    pr_number: int
    language: str | None = None
    use_ai: bool = True
    agent_mode: str = "auto"       # 新增，对应 --agent-mode
    review_mode: str = "full_pr"   # 已有，对应 --review-mode
    # ... 其余 CLI 参数透传 ...

class ReviewAgentResult(BaseModel):
    state: ReviewAgentState
    report: ReviewReport
    markdown: str
    json_report: dict       # report.model_dump(mode="json")
    agent_sidecar: dict     # {"strategy": ..., "steps": [...], "degradation_path": [...]}
```

第一版 Runner 只支持 rule-only 路径：

```python
def run(self, request):
    state = ReviewAgentState(...)

    self._call_tool(state, "fetch_pull_request")       # → state.pr
    self._call_tool(state, "fetch_changed_files")      # → state.files
    self._call_tool(state, "scan_risks")               # → state.findings
    self._call_tool(state, "build_rule_only_report")   # → state.report
    self._call_tool(state, "render_markdown")          # → state.markdown

    return ReviewAgentResult(
        report=state.report,
        markdown=state.markdown,
        json_report=state.report.model_dump(mode="json"),
        agent_sidecar={
            "strategy": state.strategy,
            "degradation_path": state.degradation_path,
            "steps": [s.model_dump() for s in state.steps],
        },
    )
```

鲁棒性保证：
- `fetch_pull_request` 失败 → 生成 minimal failure report，无法降级（数据源没了）
- `build_rule_only_report` 失败 → 调用 `build_diagnostic_report`（Level 3 fallback）
- 每个 tool 失败记录到 `state.steps[i].status = "failed"` + error，不中断整体流程（如果可降级）

新增测试：
- `tests/test_agent_runner_rule_only.py`：mock GitHub client，验证完整跑出 ReviewReport
- 与旧 `build_rule_only_report` 输出逐字段对比，确保 JSON shape 不变

---

### 阶段二：CLI 增加 `--agent-mode`（默认不启用）

#### 2.1 `src/cli/main.py` 变更

新增参数：

```python
agent_mode: str = typer.Option(
    "off", "--agent-mode",
    help="Agent mode: off, auto, rule_only, one_shot_ai. "
         "When 'off' (default), uses legacy review pipeline.",
),
```

逻辑分支：

```python
if agent_mode == "off":
    # 走原有 review 流程（不变）
    report = build_rule_only_report(...)
    if use_ai:
        report = review_with_ai(...)
else:
    # 走 Agent Runner
    runner = ReviewAgentRunner.from_settings(settings)
    result = runner.run(ReviewAgentRequest(
        repo=repo,
        pr_number=pr_number,
        use_ai=use_ai,
        agent_mode=agent_mode,
        ...
    ))
    report = result.report
    content = result.markdown
    # agent_sidecar 写入 JSON sidecar 的顶层
```

不变保证：
- `--agent-mode` 不指定时 = `"off"`，行为与现在完全一致
- `--no-ai` 依然有效
- `--format markdown/json` 依然有效
- `--output` 依然有效
- artifact 路径不变
- 所有已有测试继续通过

#### 2.2 验证清单

```bash
# 原有行为不变
uv run python -m src.cli.main owner/repo 123 --no-ai --output reports/pr-123.md
uv run python -m src.cli.main owner/repo 123 --format json

# 新 agent 路径
uv run python -m src.cli.main owner/repo 123 --no-ai --agent-mode rule_only
uv run python -m src.cli.main owner/repo 123 --agent-mode one_shot_ai

# 全量测试
uv run pytest
```

---

### 阶段三（第二轮）：Two-Stage 正式化

#### 3.1 拆分 `_filter_suggestions` 为公开函数

新增 `src/reviewer/suggestion_filter.py`：

```python
def filter_suggestions(
    suggestions: list[ReviewSuggestion],
    files: list[ChangedFile],
    max_suggestions: int,
    min_confidence: float = 0.0,
    max_suggestions_per_file: int = 5,
) -> list[ReviewSuggestion]: ...
```

`engine.py` 和 `two_stage.py` 改为 `from src.reviewer.suggestion_filter import filter_suggestions`。消除私有函数跨模块调用。

新增测试：`tests/test_suggestion_filter.py`

#### 3.2 拆分 triage / deep_review

| 新文件 | 内容 |
|---|---|
| `src/reviewer/triage.py` | `run_triage()`, triage prompt |
| `src/reviewer/deep_review.py` | `run_deep_review()`, deep-dive prompt |
| `src/reviewer/two_stage.py` | `two_stage_review()` 入口，组合上面两个 + `filter_suggestions` |

#### 3.3 Runner 接入 One-Shot AI

Runner 增加 `one_shot_ai` tool，内部调用 `review_with_ai`（或拆开的步骤）。

#### 3.4 Runner 接入 Two-Stage

Runner 增加 `triage_hotspots` 和 `deep_review_hotspot` tools。降级链：`two_stage → one_shot_ai → rule_only`。

---

### 阶段四（第二轮）：Web Console 展示 Agent Trace

后端 `/api/analyze` 响应增加 `agent` 字段（在顶层，不进 report）：

```json
{
  "report": { ... },
  "markdown": "...",
  "duration_seconds": 12.3,
  "agent": {
    "strategy": "two_stage",
    "degradation_path": [],
    "steps": [...]
  }
}
```

前端新增轻量 Trace Tab，展示每步工具名、状态、耗时、错误。

---

## 目录结构

```
src/
  agent/
    __init__.py
    state.py          # ReviewAgentState, AgentStep, AgentStepStatus
    trace.py          # summarize_input, summarize_observation, redact_secrets
    policy.py         # choose_agent_strategy
    registry.py       # AgentTool, AgentToolRegistry
    runner.py         # ReviewAgentRunner, ReviewAgentRequest, ReviewAgentResult
    tools.py          # 薄封装现有函数为 AgentTool
  analyzer/
  cli/
  github/
  output/
  reviewer/
    suggestion_filter.py   # 公开的 filter_suggestions（从 engine.py 迁出）
    triage.py              # run_triage（从 two_stage.py 拆出）
    deep_review.py         # run_deep_review（从 two_stage.py 拆出）
    two_stage.py           # two_stage_review 入口
    engine.py              # review_with_ai, build_rule_only_report（简化后）
    ...
  service/
  utils/
tests/
  test_agent_policy.py
  test_agent_runner_rule_only.py
  test_agent_runner_ai_fallback.py
  test_agent_trace.py
  test_suggestion_filter.py
```

---

## 鲁棒性验收清单

- [ ] `uv run pytest` 全部通过
- [ ] `--no-ai` 无模型 key 可运行
- [ ] `--agent-mode off` 行为 = 当前默认行为
- [ ] `--agent-mode rule_only` 生成正确 report
- [ ] `--agent-mode one_shot_ai` 生成 AI report
- [ ] 模型 401、429、timeout、5xx 降级为 rule-only
- [ ] LLM 返回非法 JSON 时重试一次，失败后降级
- [ ] Agent trace 不泄露 token
- [ ] JSON sidecar 顶层 `agent` 字段不影响 `report` 字段 shape
- [ ] Web Console 原有前端不因新增字段崩溃
- [ ] GitHub Actions artifact 路径不变
- [ ] "报告是否生成" 和 "PR 是否可合并" 继续分离
- [ ] `--review-mode full_pr/incremental` 参数语义不变
