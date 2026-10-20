# 当前交接

更新：2026-10-20。冗余清理、trace 脱敏、P0 可靠性补齐及默认关闭的缺失证据补检实验已在 `ba4eee9` 之后的工作区完成，尚未提交；最终完整 pytest 536 passed，定向 108 passed。实验策略尚无真实 A/B 结果，RSI 拒绝将其作为 source trace。具体门禁、静态检查历史欠账及源码快照见 [`docs/STATE.md`](docs/STATE.md)。此前离线 soak 在固定源码归档上完成并退出 0，结果仍对应 [soak 摘要](docs/evaluation/offline-runtime-soak-2026-10-18.json) 所列旧版本，不能归给本轮清理。本轮新增证据窗口修复与数据契约（2026-10-20）：gold scope 改为代码派生，S 层可离线复算（dev 9 case 实测 S = 72.5%），Go 方法的证据窗口与 `follow()` 假 symbol/预算污染已修。详见 [`docs/STATE.md`](docs/STATE.md) 与 [`数据契约与 S 测量`](docs/evaluation/data-contract-2026-10-20.md)。另有完整 pytest 569 passed（工作区，未提交）。本轮把 N→S→D→P 漏斗做成可复算脚本：CrabTrap 当前 S=13/14、D=1、P=0，D 是主损失；dev 9 case 离线 S=72.55%。详见 [`漏斗测量`](docs/evaluation/funnel-2026-10-20.md)。此页只作入口，不要从历史记录推导完成状态。

## 必读

- [唯一执行计划](docs/review-v2-development.md)
- [当前状态与证据边界](docs/STATE.md)
- [数据集与知识库审计](docs/evaluation/dataset-audit-2026-10-16.md)
- [Harness 规范](docs/HARNESS.md)

VS Code 扩展冻结。此前两组 thinking 请求因输出截断失败；最后一组 non-thinking 开发集评测已按下文及[试跑记录](docs/evaluation/real-dev-pair-2026-10-19.md)完成。评测仅为一个已暴露 PR 的回归观察，不代表独立泛化或晋升证据。soak 是单个缓存 PR fixture 的低负载离线稳定性探针。本轮完整测试/gates结果见 STATE；fixture CLI 不能授权晋升旧 holdout 或候选策略。

最后一组真实 non-thinking 开发集评测已完成：one-shot 成功但零 finding；grounded 先在 2 HTTP 上限失败，随后按新预算单独重跑 grounded 并复用 control，成功完成 1 discovery + 1 verification，最终零 finding。grounded 3 个候选中 2 个 pending、1 个 partial root-cause overlap；没有 Critic。金标人工裁决、P/R边界、费用和余额状态见 [最终报告](docs/evaluation/real-dev-pair-2026-10-19.md) 与[人工裁决 JSON](docs/evaluation/real-dev-pair-2026-10-19-adjudication.json)。当前单 case PR-scope Recall 为 0/14、Precision 不可计算；样本已暴露，不支持独立泛化或晋升。

本轮复杂度测试与后续实验顺序见 [复杂度测量记录](docs/evaluation/complexity-2026-10-19.md)。manifest/split hash 已核验相符；不可将失败响应或模拟调用数当成召回/模型能力。

本次默认关闭 missing-evidence follow-up 的 A/B 真实配对已完成，结果详见 [预算内 A/B 报告](docs/evaluation/grounded-missing-evidence-ablation-budget3-v2-result.md)。A/B 都完成两次 HTTP（discovery + verification），但 B 没有产生有效 missing-evidence obligation 或 context request，故本次没有触发补查，不能作为该机制的收益证据。两组最终均 0 finding；按固定官方 denominator 14，Recall 0、Precision undefined，不晋升、不泛化。逐请求 payload/response、预算 ledger、summary 恢复原因保存在 ignored artifact 目录；不涉及业务源码改动。STATE 中记录了 gold scope 拆分的既有文档差异。
