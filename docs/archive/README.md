# 文档索引

**唯一的计划是 [`plan.md`](plan.md)。** 任何 `*-plan.md`、`*-development*.md`、`*-summary.md` 若与它冲突，以它为准。

## 权威文档（读这些就够了）

| 文档 | 作用 |
|---|---|
| [`plan.md`](plan.md) | **唯一计划**：P0–P5 + RSI 循环、验收、依赖、进度 |
| [`decisions.md`](decisions.md) | 决策记录（ADR），含定位、知识层、并发、版本契约、RSI |
| [`architecture-and-positioning-review.md`](architecture-and-positioning-review.md) | 项目盘点与诊断：为什么效果差、与别的 harness / 腾讯架构对比 |
| [`review-loop-design.md`](review-loop-design.md) | 审查闭环：发现 → 取证 → 核验 → 判定，指标定义与安全 |
| [`self-improving-plan.md`](self-improving-plan.md) | RSI 详细设计（发布工程 + playbook 增量 + 门禁） |
| [`evaluation/identification-baseline.md`](evaluation/identification-baseline.md) | 错误识别基线（已知答案实测 + 真实运行手工判定） |
| [`evaluation-protocol.md`](evaluation-protocol.md) | 评测协议 |
| [`error-handling-policy.md`](error-handling-policy.md) | 降级与错误处理政策 |
| [`review-guide.md`](review-guide.md) | 使用与审查判据说明 |
| [`architecture.md`](architecture.md) | 技术架构（分层与数据流） |
| [`graph-planning-dataflow.md`](graph-planning-dataflow.md) | 图规划与上下文预算实测数据 |

## 参考文档（仍在维护，按需查）

`code-review-practices-and-prompt-design.md`（外部实践调研）、`contribution-guide.md`、`functions-index.md`、`evaluation/` 下的结果与协议、`reviews/` 下的复盘。

## 已归档（`docs/archive/`，历史记录，不再维护）

以下文档与 `plan.md` 冲突或已被取代，保留仅为追溯：

`agent-refactor-final-plan.md`、`rules-agent-two-phase-plan.md`、`critic-agent-plan.md`、`ai-evidence-first-review-plan.md`、`jd-server-fit.md`、`final-audit-summary.md`、`2026-05-30-summary.md`。

## 待归档（仍被引用或需先合并内容，见 `plan.md` P0）

`agent-refactor-execution-plan.md`、`agent-refactor-review-and-refined-plan.md`、`rules-agent-synergy-plan.md`、`current-development-plan.md`、`review-capability-improvement-plan.md`、`web-console-demo-first-plan.md`、`sse-review-stream.md`、`video-product-brief.md`、`2026-05-29-summary.md`、`evaluation-driven-review.md`、`review-v2-development.md`、`PRD-review-capability-v2.md`、`review-research-and-development-standard.md`、`grouping-boundaries-and-options.md`、`review-slicing-and-boundaries.md`、`rules-agent-synergy-plan.md`、`functions-index.md`。

> 归档标准：① 是否已被 `plan.md` 取代；② 是否仍被其它文档/代码引用（引用者需先改指向）；③ 是否含有唯一的实验数据（有则先并入权威文档）。
