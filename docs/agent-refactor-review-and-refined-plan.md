# Agent 改造方案审阅与优化建议

> 本文是对 `docs/agent-refactor-execution-plan.md` 的代码级审阅。先对照当前代码验证问题诊断的准确性，再给出执行顺序调整建议和设计层面的补充。

---

## 一、问题诊断验证

对照 `src/cli/main.py`、`src/service/app.py`、`src/reviewer/engine.py`、`src/reviewer/two_stage.py` 四个核心文件，原计划指出的问题全部真实存在：

### 1.1 编排逻辑重复（已确认）

| 步骤 | `cli/main.py` (`analyze()`) | `service/app.py` (`/api/analyze`) |
|---|---|---|
| 获取 PR | L151-153: `GitHubClient` → `get_pull_request` + `get_changed_files` | L73-75: 完全相同 |
| 语言检测 | L184-187: `detect_output_language` | L83-84: 完全相同 |
| 规则扫描 | L275: `scan_risks(files)` | L87: 完全相同 |
| 规则报告 | L276-294: `build_rule_only_report(...)` | L90: 完全相同 |
| AI 审查 | L296-339: `OpenAICompatibleProvider` → `review_with_ai(...)` | L93-123: 完全相同（参数略少） |
| Markdown 渲染 | L400-403: `render_markdown(...)` | L126: 完全相同 |

两处代码合计约 170 行是重复逻辑，只是错误处理策略不同（CLI 写 failure report 到文件 + Step Summary，Web 抛 HTTPException）。Runner 统一后，这些差异可作为 Runner 的 `on_error` 策略注入。

### 1.2 `_filter_suggestions` 私有函数跨模块调用（已确认）

```
src/reviewer/engine.py:587   def _filter_suggestions(...)   # 定义
src/reviewer/two_stage.py:199  from src.reviewer.engine import _filter_suggestions  # 调用1
src/reviewer/two_stage.py:260  from src.reviewer.engine import _filter_suggestions  # 调用2
```

两处都在函数体内做 import，就是为了规避模块级循环导入。这是拆分信号。

### 1.3 `review_with_ai` 职责过重（已确认）

`engine.py:336-485` 的 `review_with_ai` 单函数承担了：

- 构建 review context（L355）
- 两阶段判断与调度（L356-370）
- Provider 调用（L372-374）
- JSON 解析（L375）
- 建议过滤（L377-380）
- Warning 收集（L381-401）
- FixTracking 构建（L402-408）
- Completeness 构建（L410-415）
- ReviewReport 组装（L416-435）
- 三级降级（L442-485：ProviderError → rule-only → diagnostic）

Agent 化后，这些应拆成独立 tool step，每个 step 只做一件事，失败时各自降级。

### 1.4 缺少 Agent Trace（已确认）

当前只有最终 `ReviewReport`，没有结构化的 "如何走到这个结果" 的记录。`analysis_warnings` 和 `completeness` 是雏形，但不够：
- 没有时间戳（不知道哪步耗时多少）
- 没有输入/输出摘要（不知道每步拿到了什么数据）
- 没有降级路径记录（不知道 two-stage 是否 fallback 到了 one-shot）

---

## 二、执行顺序调整建议

原计划六阶段顺序：

```
阶段一(State/Trace) → 阶段二(Tools) → 阶段三(Policy) → 阶段四(Runner) → 阶段五(CLI/Web) → 阶段六(two-stage拆分)
```

**建议调整为：**

```
阶段一(State/Trace) → 阶段六(two-stage拆分) → 阶段二(Tools) → 阶段三(Policy) → 阶段四(Runner) → 阶段五(CLI/Web) → 阶段七(Web Trace)
```

### 调整理由

**把阶段六（two-stage 拆分）提前到阶段二之前**：

1. `_filter_suggestions` 的私有导入是当前代码最脏的部分，修复它不需要引入任何新概念
2. 把 `triage.py`、`deep_review.py`、`suggestion_filter.py` 拆出来是纯重构，不改变行为，风险极低
3. 阶段二注册 tools 时可以复用干净的公开函数，而不是 `from engine import _filter_suggestions`
4. 拆分后的 `suggestion_filter.py` 不依赖 `engine.py`，消除循环导入

### 调整后的阶段详情

| 阶段 | 内容 | 新增/修改文件 | 测试 |
|---|---|---|---|
| 一 | Agent State + Trace 数据结构 | `src/agent/state.py`, `src/agent/trace.py` | `tests/test_agent_trace.py` |
| 六 | two-stage 拆分 + 公开 filter | `src/reviewer/suggestion_filter.py`, `src/reviewer/triage.py`, `src/reviewer/deep_review.py` | `tests/test_suggestion_filter.py` |
| 二 | Tool Registry + 薄封装 | `src/agent/registry.py`, `src/agent/tools.py` | — |
| 三 | 策略选择 | `src/agent/policy.py` | `tests/test_agent_policy.py` |
| 四 | Runner（先 rule-only，再接入 AI） | `src/agent/runner.py` | `tests/test_agent_runner_*.py` |
| 五 | CLI/Web 改用 Runner | `src/cli/main.py`, `src/service/app.py` | 现有全量测试 |
| 七 | Web Console 展示 Trace | `src/service/static/app.js` | 手动验收 |

每一步都独立可测、独立可合，避免一次大改。

---

## 三、设计层面补充建议

### 3.1 降级链显式化

当前降级逻辑隐藏在 try/except 中（如 `engine.py:442-485`），不够可观测。建议 Runner 内部定义显式降级路径：

```python
DEGRADATION_PATHS: dict[str, list[str]] = {
    "two_stage":     ["two_stage", "one_shot_ai", "rule_only"],
    "one_shot_ai":   ["one_shot_ai", "rule_only"],
    "rule_only":     ["rule_only"],
    "incremental":   ["incremental", "one_shot_ai", "rule_only"],
}
```

Runner 执行时：
1. 按当前 strategy 找到对应降级路径
2. 依次尝试每个 strategy
3. 每次降级都写入 `state.degradation_path`
4. 所有步骤和降级原因记录到 `state.steps`

### 3.2 Agent State 增加 `degradation_path` 字段

```python
class ReviewAgentState(BaseModel):
    # ... 原计划字段 ...
    degradation_path: list[str] = Field(default_factory=list)
    # 例如: ["two_stage", "one_shot_ai", "rule_only"]
    # 空列表 = 未降级，按首选策略成功完成
```

比从 `steps` 中反推更直观，Web Console 可以直接展示降级链路。

### 3.3 Policy 函数签名需要更丰富的信号

原计划 `choose_review_strategy` 的参数不够。建议改为：

```python
def choose_review_strategy(
    *,
    use_ai: bool,
    has_api_key: bool,
    files_count: int,
    additions: int,
    deletions: int,
    findings_count: int,
    high_severity_findings_count: int,      # 新增：critical+high 命中数
    hotspot_files_count: int,                # 新增：风险命中涉及的文件数
    provider_context_limit: int | None,      # 新增：模型上下文窗口大小
    requested_mode: str = "auto",
) -> tuple[str, str]:   # 返回 (strategy, reason)
```

新增参数的决策逻辑：
- `high_severity_findings_count >= 3` → two-stage（高风险集中，需要分层分析）
- `hotspot_files_count <= 2` → one-shot（热点少，triage 轮次浪费）
- `provider_context_limit < 32000` → one-shot（小上下文窗口不适合多轮调用）
- `additions > 800` → two-stage（大 PR）

### 3.4 two-stage 拆分边界

当前 `two_stage.py` (278行) 建议拆为：

| 新文件 | 内容 | 依赖 |
|---|---|---|
| `src/reviewer/suggestion_filter.py` | `filter_suggestions()`（公开）+ 去重辅助函数 | `models.py`, `analyzer/diff_parser.py` |
| `src/reviewer/triage.py` | `run_triage()` + triage prompt | `provider.py`, `analyzer/context_builder.py` |
| `src/reviewer/deep_review.py` | `run_deep_review()` + deep-dive prompt | `provider.py` |
| `src/reviewer/two_stage.py` | `two_stage_review()` 入口函数（组合上面三个） | 上面三个模块 |

注意：`suggestion_filter.py` 建议放在 `src/reviewer/` 而非 `src/analyzer/`，因为它处理的是模型输出（`ReviewSuggestion`），属于 reviewer 层职责。它只依赖 `diff_parser.py` 的 `changed_line_map`，不依赖 `engine.py`，没有循环导入风险。

### 3.5 Agent 层保持极简

原计划的 `AgentTool` dataclass 包含 `description` 和 `risk` 字段——这些是给 LLM 看的，当前阶段不需要（策略选择是规则驱动的，不是 LLM 驱动的）。建议第一版：

```python
@dataclass
class AgentTool:
    name: str
    execute: Callable[[ReviewAgentState], None]  # 原地修改 state
```

`description` 和 `risk` 留待将来如果有 LLM-driven tool selection 再加。

### 3.6 CLI 参数统一

当前已有 `--review-mode full_pr/incremental`，原计划建议新增 `--agent-mode`。两个 mode 参数容易让用户困惑。建议统一为：

```
--strategy auto|rule_only|one_shot|two_stage|incremental
```

- `rule_only` → 不调用 AI
- `one_shot` → 当前默认行为（一次 LLM 调用审查全部 diff）
- `two_stage` → triage + deep-dive
- `incremental` → 当前 `--review-mode incremental` 语义
- `auto` → 由 policy 自动选择（默认）

旧参数 `--review-mode` 保留为 deprecated alias，指向 `--strategy`。

### 3.7 Trace 安全要求

`trace.py` 的 `summarize_input` / `summarize_observation` 必须保证：

- **不落盘 API token**：输入中的 `GITHUB_TOKEN`、`OPENAI_API_KEY` 替换为 `<redacted>`
- **不落盘完整 prompt**：只记录 prompt 字符数，不存原文
- **不落盘完整模型输出**：只记录 `parse_status`（success/failed）、suggestions 数量、risk_level
- **可记录的内容**：文件数量、风险命中数、策略选择、每步耗时、失败原因

---

## 四、风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|---|---|---|---|
| Runner 替换 CLI/Web 后 CI workflow 行为差异 | 中 | 高 | 阶段五前跑全量测试，对比 artifact JSON 输出 |
| two-stage 拆分引入循环导入 | 低 | 中 | `suggestion_filter.py` 不依赖 `engine.py`，`triage.py`/`deep_review.py` 不互相依赖 |
| Agent State 序列化膨胀（trace JSON 过大） | 中 | 低 | `trace.py` 的 summarize 函数严格裁剪，不存完整 PullRequest/ChangedFile |
| Web Console 前端适配 Trace 展示工作量超预期 | 中 | 低 | 阶段七是纯增量，不影响已有功能，可延后 |
| 降级链复杂化导致某些组合不可达 | 低 | 中 | 阶段四的 `test_agent_runner_ai_fallback.py` 覆盖所有降级路径 |

---

## 五、可立即执行的准备工作

以下工作不需要等 Agent 改造，可以现在做，对后续阶段有帮助：

### 5.1 拆分 `_filter_suggestions` 为公开函数（预做阶段六）

```bash
# 新增 src/reviewer/suggestion_filter.py
# 内容：从 engine.py 搬出 _filter_suggestions 及相关辅助函数
# engine.py 和 two_stage.py 改为 from src.reviewer.suggestion_filter import filter_suggestions
```

这是最安全的改动——函数行为完全不改，只是搬家。这样做完之后，阶段二的 tools 注册就可以直接引用干净的公开函数。

### 5.2 给 `ReviewReport` 增加预留字段（预做阶段一）

```python
# src/models.py
class ReviewReport(BaseModel):
    # ... 已有字段 ...
    agent_steps: list[dict] = Field(default_factory=list)   # 预留
    agent_strategy: str | None = None                         # 预留
    degradation_path: list[str] = Field(default_factory=list) # 预留
```

三个字段默认空，不影响现有序列化和测试。Agent 改造时直接写入，不做 schema 迁移。

---

## 六、总结

原计划的六个阶段方向正确、可执行性强。本审阅的核心建议只有三点：

1. **执行顺序微调**：把 two-stage 拆分提前，先清理私有导入债务
2. **降级链显式化**：不用 try/except 隐式驱动，用 Runner 内的显式降级路径表
3. **Agent 层极简起步**：不需要 tool description/risk，当前策略选择是规则驱动的

如果达成共识，下一步可以从 **5.1（拆分 suggestion_filter）** 或 **阶段一（Agent State + Trace 数据结构）** 开始写代码。这两步互不依赖，可以并行准备。
