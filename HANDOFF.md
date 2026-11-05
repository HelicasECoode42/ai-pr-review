# 当前交接

更新：2026-10-20。HEAD 提交说明提到六个 dev case、三路共 145 条 finding，但仓库中没有可复核的完整协议、逐候选裁决与原始结果，尚未完成质量验收；目前没有可用于简历的质量提升指标。功能说明应以代码和测试为准，不能把历史模型实验写成当前版本收益。当前实现、测试边界及未完成事项见 [`docs/STATE.md`](docs/STATE.md)。

仓库仍保留一份较早的 raw-model 机制观察（两个已暴露 dev PR、4 次请求、非盲人工裁决、候选未核验）：两变体并集 7/23 语义命中，对照 harness 为 2/23。它不是 precision、发布召回、独立泛化或当前 HEAD 的质量结论。数据与限制见 [`评测证据入口`](docs/evaluation/README.md)；黄金集与 dev split 仍保留，旧跟踪报告可从 Git 历史恢复。

## 必读

- [唯一执行计划](docs/review-v2-development.md)
- [当前状态与证据边界](docs/STATE.md)
- [数据集与知识库审计](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/dataset-audit-2026-10-16.md)
- [Harness 规范](docs/HARNESS.md)

VS Code 扩展冻结。此前两组 thinking 请求因输出截断失败；最后一组 non-thinking 开发集评测已按下文及[试跑记录](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/real-dev-pair-2026-10-19.md)完成。评测仅为一个已暴露 PR 的回归观察，不代表独立泛化或晋升证据。soak 是单个缓存 PR fixture 的低负载离线稳定性探针。本轮完整测试/gates结果见 STATE；fixture CLI 不能授权晋升旧 holdout 或候选策略。

最后一组真实 non-thinking 开发集评测已完成：one-shot 成功但零 finding；grounded 先在 2 HTTP 上限失败，随后按新预算单独重跑 grounded 并复用 control，成功完成 1 discovery + 1 verification，最终零 finding。grounded 3 个候选中 2 个 pending、1 个 partial root-cause overlap；没有 Critic。金标人工裁决、P/R边界、费用和余额状态见 [最终报告](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/real-dev-pair-2026-10-19.md) 与[人工裁决 JSON](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/real-dev-pair-2026-10-19-adjudication.json)。当前单 case PR-scope Recall 为 0/14、Precision 不可计算；样本已暴露，不支持独立泛化或晋升。

本轮复杂度测试与后续实验顺序见 [复杂度测量记录](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/complexity-2026-10-19.md)。manifest/split hash 已核验相符；不可将失败响应或模拟调用数当成召回/模型能力。

本次默认关闭 missing-evidence follow-up 的 A/B 真实配对已完成，结果详见 [预算内 A/B 报告](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/grounded-missing-evidence-ablation-budget3-v2-result.md)。A/B 都完成两次 HTTP（discovery + verification），但 B 没有产生有效 missing-evidence obligation 或 context request，故本次没有触发补查，不能作为该机制的收益证据。两组最终均 0 finding；按固定官方 denominator 14，Recall 0、Precision undefined，不晋升、不泛化。逐请求 payload/response、预算 ledger、summary 恢复原因保存在 ignored artifact 目录；不涉及业务源码改动。STATE 中记录了 gold scope 拆分的既有文档差异。
