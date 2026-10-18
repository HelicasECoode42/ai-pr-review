# 当前状态与证据边界

更新：2026-10-18。文档基线提交 `6db6173`；本次状态补记离线 soak 结果。阶段计划见 [`review-v2-development.md`](review-v2-development.md)，本页是当前状态唯一真相。

## 本轮离线验收（2026-10-18）

- 本轮增加 grounded intent/trace 与受约束改进 CLI 闭环，已纳入版本控制。显式完整测试 `.venv/bin/python -m pytest -q`：**475 passed**，1 条 Starlette/httpx deprecation warning；随后删除两个确实未使用的 trace 参数并增强 gate 失败断言，相关定向测试 113 passed。正常提交 hook 的全量测试、`criteria_gate.py`、`interface_audit.py`、`holdout_eval.py` 均通过；hook 未打印 pytest 数量。CI 最低 Ruff 规则和 `git diff --check` 通过。Interface audit 仍列出 6 个未使用形参提示，无失效 flag 或调用/定义不匹配，不能称零告警。
- Grounded discovery 的同一响应可携带 versioned intent hypotheses 与有界 read/search plan；host 只执行有效绑定任务、记录冻结证据及预算省略，assessment 不替代原 finding 判据。策略/criteria/runtime/prompt 身份写入 trace/checkpoint；trace sidecar 与 paired evaluation identity 可被诊断 CLI 消费。命令级 fake-provider E2E 到达 `evaluation_required`，provider/replay 未被创建，active policy 仍是 baseline。
- 曾有一次并行 lifecycle gate 返回 reject，但当时未打印 reasons，暂未查明根因。增强断言保留完整 gate/reasons；四个隔离 pytest 进程重新覆盖 paired runner/host omission、holdout consumption、unknown-cost gate 和 promotion/rollback 共 6 项测试，全部通过。该次未复现不等于根因已解释。
- 本轮没有 API 调用，也没有真实策略晋升：fresh test 尚未授权，旧 holdout 仍只作回归且不得用于晋升，active policy 保持 baseline。以上均为离线 fixture/fake-provider 验证，不是真实模型审查效果；真实 P/R、独立泛化、token/cost收益和自动自进化效果未知。只有经过有效独立评估、门禁及显式人工晋升的策略才可能成为 active。

## 当前能力

- 自动路由对大型 PR 保留 grounded 审查；显式 `two_stage` 仍走 legacy 路径。不要将两条路径的能力混称为同一 agent 流程。
- 判据使用 criteria-v4 的有界 witness。超出支持资源/表达式范围的输入保持 unsupported/unknown，不能报告为 confirmed。
- 受约束策略存储只允许固定模板的 prompt addendum；`playbook_recall_k` 固定为 5，当前不能作为可变策略晋升。candidate 需隔离评估并由人工显式晋升；这不是完整自动模型自进化闭环。
- `decision_ledger.py` 尚未接入生产候选生命周期。发布依赖 marker 与 artifact，可做有限去重与状态记录，不提供跨 host exactly-once。
- CLI smoke 的帮助检查和缓存上下文组装不等于完整 PR 审查耗时；不能据此声称 review 提速。

## 离线证据

- 严格完整测试为 **463 passed**，提交 `ca7b501` 的正常 pre-commit hooks 通过；随后 `6db6173` 仅更新评测文档。该测试结果对应代码树 `ca7b501`（tree `d60702c9a4e4ef98376508cdf666b278c65c75d1`），不是新的 soak 结果。
- 历史轻量 smoke 详见 [`offline-runtime-smoke-2026-10-18.json`](evaluation/offline-runtime-smoke-2026-10-18.json)：其源码 HEAD 为 `805271cfaa1e2628afa4991b1669f829237bedf6`。25 个缓存输入的上下文组装旧计时中位数为 0.3488 秒 / 70.3 MB 峰值 RSS（原报告的 67.1 MiB 是同一量级换算）；当时 hunk denominator 为 1460。后续独立验证修复后 1771/1771 源 hunk 进入终态账目，但没有在修复后重测该耗时，不能把旧计时归给新源码。
- 固定源码 soak 详见 [`offline-runtime-soak-2026-10-18.json`](evaluation/offline-runtime-soak-2026-10-18.json)：约 75 分 34 秒内完成 579 批，11,580 次缓存上下文构建、28,950 次安全 witness、4,632 次 Unicode/预算截断检查和 579 次 fake-provider 生产入口测试，错误数为 0。单输入为 1 个本地缓存 fixture PR（10 个文件）；不含真实模型、真实 PR、Gold 输入或 GitHub 写入，不能证明 P/R、成本或线上网络客户端稳定性。日志记录运行至预设 deadline 附近并正常退出 0，但没有记录独立 stop reason；512 MiB RSS 是采样软停止阈值，不是 OS 硬限制。
- 缓存数据的 1,771 个源 hunk 均进入终态账目；其中 311 个因文件类别排除，分别是文档 125、lockfile 185、敏感路径 1，排除内容不进入 prompt。
- cl100k 选材对照是首轮 patch 目标行可见性实验，不是语义 recall。C 只是实验臂，未改默认：Dev9 对 A1 的 any-visible 为 15 gain/11 loss；security 为 0 gain/2 loss，maintainability 为 0 gain/3 loss。all-visible 为 17 gain/10 loss。不能用净值掩盖分类损失。
- 新样本 roster 20 个；首个公开 GET 返回 HTTP 403 后停止，1 次请求、0 个样本接受，原因未证实。没有新增 PR 泛化验证。
- 本轮没有新增真实模型 P/R、成本或语义裁决结果。历史 raw file+line 匹配只是位置 proxy，不是人工语义精确率/召回率；旧 holdout 15 PR 已被使用，只能作回归。

## 范围与下一步

策略/知识 candidate 的离线生命周期测试与 fake-provider 验证，不证明自主反思或真实模型效果。Soak 用固定源码 `ca7b501`；脚本、网络阻断配置、输入 fixture、日志哈希见摘要文件。VS Code 继续冻结，不作为本轮验收目标。
