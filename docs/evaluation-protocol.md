# Rules → Signals → Agent 评测与数据留档

## 每次审查自动记录的指标

`ReviewReport.metrics` 会写入 JSON 报告：

| 指标 | 含义 | 验证目的 |
|---|---|---|
| `raw_signal_count` | 规则、AST、跨文件分析产出的原始 signal 数 | 观察召回规模 |
| `main_review_prompt_signal_count` | 主 Agent Prompt 中包含的 signal 数 | Phase 1 必须为 0 |
| `independent_suggestion_count` | 独立语义审查后保留的建议数 | 观察 Agent 自主发现能力 |
| `signal_verification_request_count` | Signal Verifier 发起的模型请求数 | 有 signal 时必须为 1，不得随 signal 数线性增长 |
| `confirmed_signal_count` | 被语义验证确认的 signal 数 | 有效规则证据数 |
| `dismissed_signal_count` | 被驳回的 signal 数 | 可疑规则噪声数量 |
| `unresolved_signal_count` | 模型无法确定的 signal 数 | 需要人工复核的数量 |
| `unverified_signal_count` | Provider 不可用或未返回结果的 signal 数 | 降级覆盖范围 |
| `context_token_count` | 主审查上下文 token 数 | 观察上下文预算 |

## 代码级验收数据

当前自动化测试覆盖的可复现实验：

| 场景 | 断言 | 数据意义 |
|---|---|---|
| 主审查上下文有/无 rule signals | Prompt 不出现 `Rule findings` | `main_review_prompt_signal_count = 0` |
| 大量 HIGH/CRITICAL signals | 策略仍为 `one_shot_ai` | 规则不再劫持主 Agent 策略 |
| 多个 signal | Verifier 只调用一次 Provider | 请求数保持 1 |
| confirmed signal | 转为 `rule_confirmed` 建议 | 可追溯合并 |
| 缺失 verifier 结果 | 进入 `unverified` | 不把未知误写成缺陷 |
| 同一函数多 hunk | 不重复生成跨文件风险 | 去重正确性 |

## 真实 PR 评测集

要证明“质量提升”，至少准备 10 个已关闭或已审查 PR，并由人工为每条候选建议标注：

```json
{
  "pr": "owner/repo#123",
  "suggestion_key": "src/app.py:42:secret-logging",
  "human_label": "true_positive | false_positive | unclear",
  "reviewer_note": "为什么确认或驳回"
}
```

不要先追求大样本；先覆盖以下 PR：

- CI workflow 变更；
- reviewer/core engine 变更；
- 真实安全风险；
- 重构导致的跨文件变更；
- 测试和文档修改；
- AI Provider 失败降级。

## before / after 表

| 指标 | Before | After | 计算方式 |
|---|---:|---:|---|
| 原始规则命中 | 待采集 | 自动生成 | `raw_signal_count` 均值 |
| 用户可见规则建议 | 待采集 | 自动生成 | rule-only / confirmed 建议数 |
| 人工认可建议 | 待采集 | 待标注 | `true_positive` 数 |
| 规则误报率 | 待标注 | 待标注 | false_positive / 已标注规则建议 |
| Agent 独立建议数 | 待采集 | 自动生成 | `independent_suggestion_count` |
| 验证请求数 | 0 | 自动生成 | `signal_verification_request_count` |
| 单次审查耗时 | 待采集 | 待采集 | Agent trace 各步骤耗时之和 |

`待采集` 不等于 0。没有真实 Provider、真实 PR 和人工标签时，只能说明结构与测试通过，不能宣称误报率已经下降。

## 本地真实差异快照

无需 API Key 的结构评测命令：

```bash
uv run python scripts/evaluate_local_diff.py --base main --head HEAD
```

它只读取本地 Git diff，不调用 Provider 或 GitHub API，并将 JSON 写入
`reports/evaluation/`。2026-07-26 对 `main...HEAD`（PR #36 的 12 个文件）
的快照见 `docs/evaluation/main-head-structural-2026-07-26.json`。该快照证明
Phase 1 的输入隔离，不代表语义质量或误报率已经提升。

已完成的真实 Provider 样本见
`docs/evaluation/pr-36-provider-run-2026-07-26.json`。它记录模型实际运行、
Verifier 决策计数和耗时；仍需要人工标签，才可计算质量指标。
