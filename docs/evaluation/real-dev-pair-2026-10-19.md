# 有界真实模型开发集试跑（2026-10-19）

## 输入身份与边界

选用 `brexhq_CrabTrap_15-5ef0884c`，其固定 split 角色是 `dev`，并且是已暴露开发样本，只能作回归观察，不能作独立留出或晋升依据。该 case 未被已有 playbook 条目直接派生；没有读取 gold 标签，也没有调用 judge。

本次重新核对发现 prepared manifest 与受信 split **匹配**：两者 SHA-256 均为 `f7c66ed67481e3c657c270ca119d48ed6d13d30489079672aac113cf8b84c1b5`。manifest 中历史绝对路径指向 `/Users/wangyufan/Desktop/code/ai-pr-review/eval-data/...`，从项目目录解析到实际 sibling `../eval-data/...` prepared 输入，且位于固定 prepared root 下。该 PR 的 metadata SHA-256 为 `af543721b389e0b3dc419969b023cd40ab05df74b3836f102fe1b6d071ab05bc`，diff SHA-256 为 `a41c5980bd62a7e7d9113178c3ce968ad23f4ecdc8658c790331d8a32ddada89`，输入身份 SHA-256 为 `69799bdeb2659339c68f60a7903513116ef96f6cbe6814b482ba9bb45c1253da`。未重写 manifest 或 split hash。

免费本地 preflight 输出为 9 ready、15 not-ready；本次选择的 dev 输入 ready，base/head mirror commit 均可解析。历史 holdout 仍只用于回归。输入 hash 和运行产物保存在 `/tmp/ai-pr-review-reliability/preflight.json`、`/tmp/ai-pr-review-reliability/real-dev-pair/` 与 `/tmp/ai-pr-review-reliability/real-dev-pair-8192/`。

## 已实际发送的请求

两组请求都通过生产 review 入口使用 `deepseek-flash`，并比较 `control_one_shot` 与 `grounded`；每臂不自动重试，记录了 provider usage 与 finish reason。调用时段按北京时间为峰值时段。当前官方价格页面列出 peak cache-miss input ¥2/百万 token、output ¥8/百万 token；cache-hit 价不用于上界估算：[DeepSeek 官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。费用为实际 provider tokens 按官方峰值、无缓存折扣计算的保守上界，不代表账户最终扣费。

| 配对 | control_one_shot | grounded | 本配对费用上界 | 结果 |
|---|---:|---:|---:|---|
| max output 2048 | 7,203 prompt / 2,048 completion tokens，¥0.030790 | 12,581 / 2,048，¥0.041546 | ¥0.072336 | 两臂均 `finish_reason=length`，按失败记录，不计算召回或空结果 |
| max output 8192 | 7,203 / 8,192，¥0.079942 | 25,837 / 14,692，¥0.169210；2 次 HTTP | ¥0.249152 | 两臂最终均因 `length` 失败；grounded 首次响应 stop 后第二次截断 |

两组累计 token 峰价上界为 ¥0.321488。8192 组 usage 显示 completion 中 reasoning token 消耗了全部 control 输出上限，并占 grounded 输出大部分；此前 provider 只传了 `reasoning_effort=low`，未传 thinking 开关。官方 Thinking Mode 文档说明 `reasoning_effort=low` 仍是 thinking；Chat Completions 使用 `thinking: {"type":"disabled"}` 关闭：[DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)。

## 修复与未完成的最终配对

已实现显式 `REVIEW_THINKING_ENABLED` 配置：默认 `None` 保留原行为；仅对官方 DeepSeek host 和 allowlist 模型 ID 发送 `thinking` 参数，非 DeepSeek endpoint 不会收到该字段。显式关闭时不再同时发送 `reasoning_effort`。配置贯通普通审查、critic 和 unit provider factory；unit checkpoint 身份及 paired protocol 记录该模式，避免以旧模式结果恢复。fake transport 测试断言请求 JSON 的字段和值。

最初计划的 4096-token、两臂同参非推理配对在 auto-review 外发检查处被拒绝，当时没有换路由或重试。用户之后明确授权本次评测；具体实际运行和预算见下方“最终 non-thinking 评测”。

## 代码与本地验收

涉及 `src/utils/config.py`、`src/application/review.py`、`src/reviewer/provider.py`、`src/reviewer/engine.py`、`src/evaluation/paired.py`、`tests/test_provider.py`。当前工作树未提交；在本轮改动后完整 `.venv/bin/python -m pytest -q` 为 **523 passed**，1 条既有 Starlette/httpx 弃用告警。随后一个 provider options 字面量整理也通过了 `tests/test_provider.py`（16 passed）。最低 Ruff gate、criteria gate、interface audit、holdout gate 和 `git diff --check` 均通过；interface audit 仍提示 6 个已存在的 unused-parameter 项。完整 Ruff 当前列出 252 项，完整 mypy 当前列出 458 项；历史基线记录为 Ruff 294 / mypy 459，因工作树和口径不同不把数量差称为精确差分，也不将完整静态检查描述为全绿。新增 thinking 行为有定向 fake transport 测试覆盖。

先前两组 thinking 截断运行仍不形成质量预测，也没有读 gold 做调参。最终 non-thinking 结果与人工语义裁决见下方；样本是已暴露 dev，仅用于回归观察。

## 最终 non-thinking 评测

用户明确授权后，使用官方 `api.deepseek.com` 上的 `deepseek-flash` 完成最后一轮。Control 以 thinking disabled、4096 max tokens 成功返回 0 条 finding（1 HTTP）。Grounded 首次在 2 HTTP 上限内得到两条 `stop` 响应，仍需下一次 provider 调用而被预算拦截；该失败 JSON 保留在 `/tmp/ai-pr-review-reliability/real-dev-pair-nonthinking/`。按更高但仍受限的协议只重跑 grounded（6 HTTP、每臂累计 150000 UTF8 bytes、4096 max tokens），并复用同一输入身份、同参且未触预算的 control。成功的 grounded 重跑用了 2 HTTP，走完 1 discovery + 1 verification，没有触顶或外层重试。

| 成功产物 | HTTP | prompt / completion tokens | cache-hit prompt tokens | 峰价上界 | 输出 |
|---|---:|---:|---:|---:|---|
| Control（复用） | 1 | 7,178 / 1,055 | 0 | ¥0.022796 | 成功，0 findings |
| Grounded 首次尝试（失败，保留） | 2 | 27,617 / 2,878 | 6,272 | ¥0.078258 | `http_attempt_budget_exhausted` |
| Grounded 重跑 | 2 | 26,878 / 2,786 | 12,416 | ¥0.076044 | 3 raw candidates；2 pending，最终 0 findings |

API 未返回 `reasoning_tokens` 分项；completion usage 按 provider 原值记录，thinking 开关为 disabled。前三次运行（control、grounded 失败、grounded 重跑）合计本轮峰价估算上界 ¥0.177098；连早先 thinking 两组 ¥0.321488，累计 ¥0.498586。最后一次 grounded 单臂协议上限 ¥0.496608，加全部先前峰价上界为 ¥0.91915，低于本轮 ¥1 硬上限。以上是按官方峰值单价、不应用缓存折扣的上界，不代表账单实际扣费。调用前余额记录为 ¥9.63；之后只读余额查询 `ConnectTimeout`，调用后余额与账务对账均为 unknown。

人工裁决见[独立 JSON](real-dev-pair-2026-10-19-adjudication.json)。固定 dev gold 有 25 项、官方标签 TP 16 / FP 9；2 个 TP 标为 pre-existing，官方 PR-scope 正例分母为 14。两臂最终都没有建议，因此单 case PR-scope Recall 为 0/14，Precision 为 undefined（无预测）；全 16 个 TP 标签池为 0/16。不能把它当成独立泛化结果。Grounded 的 3 条候选中，列顺序候选有正确的 counter-evidence 并被驳回；分页候选的模型 dismissal 缺 counter-evidence 被 host 保留为 pending，人工核对为虚警；detail-cache 候选与 gold #23 有局部根因重合，但没有识别流式响应更新的触发条件，也没有给出足以匹配的修复建议，严格不计 TP，仍 pending。Grounded 未启用 Critic，不存在 Critic 误删真 bug。

Gold #1 的 `context.Background()` 在 base `internal/admin/api.go:323` → `Query` (`internal/admin/pg_audit_reader.go:347-375`) 已存在；head 改为 `QuerySummaries` 后仍走相同背景 context (`api.go:331`, `pg_audit_reader.go:364-397`)。保留 gold 的 `exacerbated-by-pr` 标签，但人工 base/head 对照不将取消语义说成新引入问题。Gold #3 的 query error 返回 nil 在 base 已存在（`pg_audit_reader.go:373-375`）；head 新增 handler 将 nil 变空数组并与 total/has_more 一起以 200 返回（`api.go:331-340`），是本次响应契约新增的误导性错误表现。

Run artifacts：control、grounded 首次失败均在 `/tmp/ai-pr-review-reliability/real-dev-pair-nonthinking/`；grounded 重跑的 `protocol.json`、`grounded.json`、`summary.json` 在 `/tmp/ai-pr-review-reliability/real-dev-pair-nonthinking-grounded-rerun/`。精确 serialized prompt 未保留，按hunk计算的 S 暴露率为 unknown；可核 trace 显示 grounded 的 `pg_audit_reader.go` excerpt 仅到 head line 360，未覆盖 helper 的 370-397；`api.go` excerpt 是 lines 264-342，handler 片段可见但 helper 的 nil-on-error 逻辑不在该excerpt。模型没有请求补充该 source。缺失原因仅记录为本次 bundle 未提供因果片段，不据此断言搜索算法故障。没有调用 judge 或晋升策略。

## 持久保存的本次产物

成功的 control、grounded、重跑协议和摘要已另存 `reports/evaluation/real-dev-pair-2026-10-19/`（本地忽略目录），不依赖临时目录保留。原始失败试跑仍按上文临时路径记录；金标裁决与核心用量保存在本页及裁决 JSON。
