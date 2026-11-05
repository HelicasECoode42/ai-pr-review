# 归档文档索引（历史）

本目录保留旧计划、决策和设计材料，供追溯历史背景；这里的内容不是当前规范、执行计划或状态报告。现行文档分工见 [`../README.md`](../README.md)：规范约束由 [`../HARNESS.md`](../HARNESS.md) 维护，当前执行与验收见 [`../review-v2-development.md`](../review-v2-development.md)，带日期的状态与实测边界见 [`../STATE.md`](../STATE.md)。

## 旧索引中的方案与依据（历史分类）

| 文档 | 作用 |
|---|---|
| [`plan.md`](plan.md) | 早期 P0–P5 + RSI 路线快照（仅历史背景） |
| [`decisions.md`](decisions.md) | 决策记录（ADR），含定位、知识层、并发、版本契约、RSI |
| [`architecture-and-positioning-review.md`](architecture-and-positioning-review.md) | 项目盘点与诊断：为什么效果差、与别的 harness / 腾讯架构对比 |
| [`review-loop-design.md`](review-loop-design.md) | 审查闭环：发现 → 取证 → 核验 → 判定，指标定义与安全 |
| [`self-improving-plan.md`](self-improving-plan.md) | RSI 详细设计（发布工程 + playbook 增量 + 门禁） |
| [`../evaluation/identification-baseline.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/identification-baseline.md) | 错误识别基线（已知答案实测 + 真实运行手工判定） |
| [`../evaluation-protocol.md`](../evaluation-protocol.md) | 评测协议 |
| [`../error-handling-policy.md`](../error-handling-policy.md) | 降级与错误处理政策 |
| [`../review-guide.md`](../review-guide.md) | 使用与审查判据说明 |
| [`../architecture.md`](../architecture.md) | 技术架构（分层与数据流） |
| [`../graph-planning-dataflow.md`](../graph-planning-dataflow.md) | 图规划与上下文预算实测数据 |

## 旧索引中的参考分类（仅作历史记录）

`code-review-practices-and-prompt-design.md`（外部实践调研）、`contribution-guide.md`、`functions-index.md`、`evaluation/` 下的结果与协议、`reviews/` 下的复盘。

## 已归档（`docs/archive/`，历史记录，不再维护）

以下文档与 `plan.md` 冲突或已被取代，保留仅为追溯：

`agent-refactor-final-plan.md`、`rules-agent-two-phase-plan.md`、`critic-agent-plan.md`、`ai-evidence-first-review-plan.md`、`jd-server-fit.md`、`final-audit-summary.md`、`2026-05-30-summary.md`。

## 旧材料待逐项复核（状态不代表当前归档排期）

`agent-refactor-execution-plan.md`、`agent-refactor-review-and-refined-plan.md`、`rules-agent-synergy-plan.md`、`current-development-plan.md`、`review-capability-improvement-plan.md`、`web-console-demo-first-plan.md`、`video-product-brief.md`、`2026-05-29-summary.md`、`evaluation-driven-review.md`、`review-research-and-development-standard.md`、`grouping-boundaries-and-options.md`、`review-slicing-and-boundaries.md`。

当前 [`review-v2-development.md`](../review-v2-development.md) 与 [`PRD-review-capability-v2.md`](../PRD-review-capability-v2.md) 是现行文档，不在此清单中；[`sse-review-stream.md`](../sse-review-stream.md) 描述仍维护的 Web SSE 接口，也不作为过时材料归档。

[`functions-index.md`](../functions-index.md) 是仍维护的生成索引，按当前源码刷新，不在旧材料复核清单中。

> 历史归档复核原则：确认仍有无引用、是否含唯一证据，再决定归档；本页不构成当前执行计划。
