# Critic Agent：对 Evidence-grounded 建议做反驳式验证

## 目的

Evidence Gate 只能证明“模型引用的代码真实存在”，不能证明因果推理正确。Critic Agent 只审查已通过 Evidence Gate 的中高风险建议，主动寻找反例、已有保护和设计意图，输出 `confirmed`、`dismissed` 或 `uncertain`。

```text
Reviewer → Evidence Gate → Critic（中高风险） → 最终建议
```

## 调用与模型策略

- 默认 `ENABLE_CRITIC=false`，不改变现有调用成本。
- 启用后每个 PR 最多额外 1 次批量模型调用，不按建议数量线性调用。
- `CRITIC_MODEL` 为空时复用 Reviewer 模型但使用独立反驳 Prompt；设置为不同模型名时，Agent Runner 建立独立 Provider。
- 推荐起步配置：Reviewer=`deepseek-v4-flash`，Critic=`deepseek-v4-pro`。不同模型降低共同偏差，但不是正确性的替代品。

## 判定规则

| Critic 结果 | 最终处理 |
|---|---|
| `confirmed` | 以 `ai_critic_confirmed` 来源展示 |
| `dismissed` | 不展示，写入 `critic_decisions` |
| `uncertain` | 不展示，写入 `critic_decisions`，等待人工或测试证据 |
| Provider 不可用 | 保留已通过 Evidence Gate 的建议，但写入 `unverified` 审计记录 |

## 验收数据

`ReviewReport.metrics` 新增：

- `critic_request_count`
- `critic_confirmed_count`
- `critic_dismissed_count`
- `critic_uncertain_count`
- `critic_unverified_count`

单测必须证明：Critic 能驳回一个已通过 evidence 的建议、最终风险随之降低，且仅发生一次批量调用。

## 复盘问题

对每次人工标注记录 Reviewer、Critic、最终标签是否一致；重点统计“Reviewer 误报被 Critic 驳回”的比例，以及 Critic 错误驳回真问题的比例。
