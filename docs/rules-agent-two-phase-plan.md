# Rules → Signals → Agent 两阶段落地方案

## 1. 改造结论

规则引擎保留，但职责从“输出风险结论”调整为“召回候选信号”。Agent 的主审查先独立阅读 PR 文本、Diff 和项目规范，再在独立步骤中验证规则信号。最终报告由合并层生成，任何路径规则都不能单独决定最终 HIGH 风险。

目标链路：

```text
GitHub PR / Diff
       │
       ├───────────────┐
       ▼               ▼
独立语义审查       规则/AST/跨文件扫描
(不看规则结论)       (只产出 signals)
       │               │
       │               ▼
       │         Signal Verifier
       │         确认 / 驳回 / 不确定
       │               │
       └───────┬───────┘
               ▼
          Review Merger
               ▼
     suggestions + audit trail
```

## 2. 总体边界

- 不替换现有 `OpenAICompatibleProvider`，复用统一 Provider 接口。
- 不一次性重写 CLI、FastAPI、GitHub Actions 和报告渲染。
- Phase 1 不新增额外模型调用，先解除规则对主审查的强耦合。
- Phase 2 才增加批量 Signal Verification；不按信号逐条调用模型，避免 N 次请求。
- 原始规则信号保留在 JSON 审计字段中，但不等于最终建议。
- AI 不可用时仍可输出 rule-only 报告，但必须标记“未验证信号”，不能伪装成语义审查结论。

## 3. Phase 1：Context-first 解耦

### 3.1 目标

让 Agent 首先阅读原始文本和 Diff，规则只用于旁路审计，不再给主模型预设 HIGH/MEDIUM 结论。

### 3.2 代码改动

#### A. 去除重复跨文件扫描

当前 `scan_risks()` 已调用 `analyze_cross_file_impact()`，Agent Runner 又单独调用一次。Phase 1 只保留一个入口：

```python
# 推荐：scan_risks 只负责 regex/path/AST
signals = scan_risks(files)
signals.extend(analyze_cross_file_impact(files))
```

由 Runner 统一编排，避免隐藏副作用和重复 findings。

#### B. 主审查上下文不注入规则

为 `build_review_context` 增加显式参数：

```python
def build_review_context(
    pr: PullRequest,
    files: list[ChangedFile],
    findings: list[RiskFinding] | None = None,
    *,
    include_rule_findings: bool = False,
    prioritize_rule_files: bool = False,
    max_patch_tokens: int = 6_000,
) -> ReviewContext:
    ...
```

独立 AI Review 使用：

```python
context = build_review_context(
    pr,
    files,
    findings=None,
    include_rule_findings=False,
    prioritize_rule_files=False,
)
```

rule-only 和 Signal Verification 可以显式传入 signals，不依赖默认行为。

#### C. Agent 策略主要根据任务规模决定

Phase 1 的 `auto` 策略只使用：

- files count；
- additions/deletions；
- patch token estimate；
- 是否有模型和 API Key。

规则数量只记录到 trace，不再直接把主审查切换到高风险模式。CRITICAL 确定性规则可以作为例外，但只能触发“增加审查深度”，不能直接决定最终风险。

#### D. 规则输出改名为 signals

内部逐步从 `findings` 迁移到 `signals`。Phase 1 保留 `RiskFinding` 类型兼容旧代码，但在变量、报告标题和 trace 中使用 signal 语义：

```python
signals: list[RiskFinding] = scan_risks(files)
```

### 3.3 Phase 1 数据流框架

```python
signals = scan_risks(files)
signals.extend(analyze_cross_file_impact(files))

strategy = choose_agent_strategy(
    use_ai=use_ai,
    has_api_key=has_api_key,
    files_count=len(files),
    additions=additions,
    # Phase 1: signals 只进入 trace，不影响默认主审查判断
)

if strategy == "rule_only":
    report = build_rule_only_report(pr, files, signals)
else:
    report = review_with_ai(
        pr=pr,
        files=files,
        findings=[],  # 主审查不看规则结论
        provider=provider,
    )
    report.rule_findings = signals  # 仅审计，不直接合并进 suggestions
```

### 3.4 Phase 1 验收

- 同一个跨文件 signal 只出现一次。
- 主审查 Prompt 中不出现 `Rule findings`。
- 修改纯文档或 CI 注释时，路径规则不能单独将最终风险抬为 HIGH。
- AI 失败时报告明确显示“未验证规则信号”。
- 现有 CLI、FastAPI、Markdown、JSON 入口保持兼容。
- 单测覆盖“有 signal / 无 signal”时主模型收到的 Prompt 内容一致。

## 4. Phase 2：Signal Verification + Merge

### 4.1 目标

增加独立、批量的信号验证步骤，让模型只在第二阶段处理规则候选，并产出结构化决策。

### 4.2 核心契约

代码接口骨架位于 `src/reviewer/signal_contracts.py`：

```python
class SignalDecision(str, Enum):
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    UNSURE = "unsure"

class SignalVerification(BaseModel):
    key: SignalKey
    decision: SignalDecision
    adjusted_severity: Severity | None
    confidence: float
    reason: str

class SignalVerifier(Protocol):
    def verify_batch(
        self,
        signals: list[SignalEnvelope],
    ) -> SignalVerificationBatch: ...
```

### 4.3 批量验证流程

```python
independent_report = review_diff_without_signals(...)

envelopes = build_signal_envelopes(
    signals=signals,
    files=files,
    max_excerpt_tokens=2_000,
)

verification = verifier.verify_batch(envelopes)

final_report = merger.merge(
    independent_suggestions=independent_report.suggestions,
    signals=signals,
    verification=verification,
)
```

Signal Verifier 的输入只包含：

- rule id、原始严重度和置信度；
- 文件路径与具体行；
- 对应 patch excerpt；
- 项目 Review Guide 中与该信号相关的规则。

不传整个 PR，避免重复消耗主审查 Token。

### 4.4 合并规则

| 决策 | 最终处理 |
|---|---|
| `confirmed` | 转成候选 suggestion，使用模型调整后的严重度和理由 |
| `dismissed` | 不进入 suggestions；写入 `dismissed_signals` |
| `unsure` | 不进入主建议；写入 `unresolved_signals` 供人工查看 |
| Verifier 调用失败 | 不猜测；全部记为 `unverified`，不提升最终 AI 风险 |
| 确定性 CRITICAL | 保留安全阻断提示，但必须附具体代码证据 |

去重键：

```text
(file_path, line, normalized_root_cause)
```

当独立 AI 建议和 confirmed signal 指向同一根因时，保留证据更具体、置信度更高的一条，并记录 provenance：

```text
source = ai | rule_confirmed | ai_and_rule
```

### 4.5 Phase 2 Runner 框架

```python
scan_risks
analyze_cross_file
choose_agent_strategy
independent_ai_review
build_signal_envelopes
verify_signals
merge_review_results
render_markdown
render_json
```

每一步进入 Agent Trace，记录耗时、输入数量、确认/驳回数量和降级原因，不记录 API Key 或完整敏感 Diff。

### 4.6 Phase 2 验收

- Signal Verification 使用单次批量请求，不随 signal 数量线性增加 HTTP 请求。
- 每条规则信号最终都有 `confirmed/dismissed/unsure/unverified` 状态。
- 被驳回信号不出现在用户建议和总体风险统计中。
- Agent 独立建议在规则引擎关闭时仍能正常生成。
- 对固定 PR 集合输出 before/after 表：规则命中数、确认数、驳回数、人工认可数。
- 没有人工标注集前，不声称“误报率下降 X%”。

## 5. 文件级实施清单

### Phase 1

| 文件 | 改动 |
|---|---|
| `src/analyzer/risk_rules.py` | 移除内部跨文件扫描，保持单一职责 |
| `src/agent/runner.py` | 统一扫描编排；独立 Review 不传规则 findings |
| `src/analyzer/context_builder.py` | 增加规则注入与规则优先排序开关 |
| `src/agent/policy.py` | 默认策略与规则严重度解耦 |
| `src/reviewer/engine.py` | AI 报告与原始 signal 审计字段分离 |
| `tests/` | Prompt 隔离、扫描一次、降级语义测试 |

### Phase 2

| 文件 | 改动 |
|---|---|
| `src/reviewer/signal_contracts.py` | 数据契约和 Protocol；本轮已提供骨架 |
| `src/reviewer/signal_verifier.py` | 批量 Prompt、Provider 调用和解析 |
| `src/reviewer/review_merger.py` | 独立建议与信号决策合并、去重、provenance |
| `src/agent/runner.py` | 注册 verify/merge tools，完善 trace |
| `src/models.py` | 增加 unresolved/unverified/provenance 输出字段 |
| `tests/` | verifier、merge、Provider 失败、重复根因测试 |

## 6. 合并策略

- Phase 1 单独 PR：只做解耦，不引入新模型请求。
- Phase 2 单独 PR：在 Phase 1 稳定后接入 verifier 和 merger。
- 每个 Phase 都必须保持 rule-only 可运行。
- 不在同一 PR 中同时改 Provider、多入口 UI 和报告样式。

## 7. 面试表达边界

Phase 1 完成后可以讲“将规则扫描从最终结论降级为旁路信号，解除其对主模型 Prompt 和风险等级的强耦合”。

Phase 2 完成并有标注评测后，才能讲“通过独立语义审查、批量信号验证和结果合并建立规则 + Agent 协同链路”。没有人工对照数据前，不写具体误报率提升。
