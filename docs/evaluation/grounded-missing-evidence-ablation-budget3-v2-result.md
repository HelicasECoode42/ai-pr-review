# Grounded missing-evidence A/B：预算内真实配对结果

日期：2026-10-19。固定 dev case `brexhq_CrabTrap_15-5ef0884c`，base `b29930e93765700608313c0f0d32c4818734e158`，head `5ef0884c59b3ee1d5a986dd02686a8232570c80f`；模型 `deepseek-flash`，thinking disabled，temperature 0.1，单 HTTP `max_tokens=3072`。A=`grounded_missing_evidence_followup=false`，B=`true`；其余协议与输入 SHA 相同。运行源码身份重新计算为 commit `ba4eee9f1023f907ef9040771a183f89e69bb613`、dirty=true、Python identity `5a46b76300ae3e1e463889b6ef733ebf5e5815aa2776156ee481751d87894185`。共同预算 hash 和完整身份见同目录 `protocol.json`、`summary.json`。

## 执行与预算

两臂均完整成功，均执行 discovery + verification，各 2 次 HTTP 200、无 fallback、无外层重试；所有请求 `finish_reason=stop`。A 时延 14.10 秒，B 15.49 秒。A 输入/输出 token 为 25,287/2,591；B 为 26,254/2,932；合计 57,064 token。provider 未返回独立 reasoning token 明细；thinking 明确关闭。以下成本按保守 cache-miss 输入 ¥2/M、输出 ¥8/M 估算，不计 cache 命中折扣：A ¥0.071302，B ¥0.075964，合计 ¥0.147266。此为 usage 推导峰价估算，不是账单对账。免费预检余额 ¥9.34，`deepseek-flash` 可用；本次 ¥3 新授权预算尚余 ¥2.852734，未重跑。

| 臂 | HTTP 序号 | 请求体 UTF-8 bytes | prompt / completion | cache hit / miss | 估算费用 |
|---|---:|---:|---:|---:|---:|
| A | 1 discovery | 41,907 | 12,063 / 1,408 | 11,904 / 159 | ¥0.035390 |
| A | 2 verification | 45,227 | 13,224 / 1,183 | 0 / 13,224 | ¥0.035912 |
| B | 1 discovery | 41,907 | 12,063 / 1,541 | 11,904 / 159 | ¥0.036454 |
| B | 2 verification | 49,345 | 14,191 / 1,391 | 384 / 13,807 | ¥0.039510 |

请求体累计 A 87,134、B 91,252 bytes；协议预算累计分别 87,880、91,998 bytes（额外 746 bytes 为每臂固定 reservation envelope），均低于每臂 400,000 bytes 上限。每臂最坏 token 上界 424,576，配对 849,152；全局上限 1,500,000 tokens、1200 秒。费用按 usage 结算，pair 占用 ¥0.147266。模型调用已全部完成；之后本地汇总代码因 `KeyError: common_budget_sha256` 退出，使用两份已封存 result 离线重建 `summary.json` 和 ledger，未重复模型请求。恢复过程在 `summary.json.local_aggregation_recovery` 明确记录。

## 实际证据与 missing-evidence 机制

两组请求均向模型发送了完整 PR 描述/diff，以及受限仓库 source excerpts；没有发送本地评测文档、gold 或 adjudication。逐 HTTP 原始 JSON body、SHA、usage、系统/用户消息、原始响应、解析响应、reserve 前后状态和逻辑调用记录已持久化在 `reports/evaluation/grounded-missing-evidence-ablation-2026-10-19-budget3-v2/model-artifacts/{A,B}/`。

两臂首轮均见 `pg_audit_reader.go` head 60–121；第二轮见 head 275–360，后者在 360 结束。完整 diff 中还看到了 helper 对应 hunk 的 head 388–393（SQL join/order/limit 等）。因此不能说模型完全没看到 helper：**两组都看到部分 diff 388–393，但都没看到 head 371 的 `context.Background()`，也没看到 395–397 的 `pool.Query` 与 nil-on-error 分支；source excerpt 也未覆盖 361–397。** 这些缺失内容分别关联 Gold #1 与 #3 的关键因果。API handler head 264–342、AuditTrail head 94–126 在第二轮 source excerpt 中可见。完整逐请求输入应以保存的 HTTP artifact 为准。

B 开关虽启用，但没有有效的 missing-evidence obligation：discovery 的 intent hypotheses 未声明 `missing_evidence`、补查任务或 `context_requests`；verification 也没有发出 context request。没有第三个 HTTP，也没有 budget refusal。因此本次只能观察同预算下的 A/B 输出差异，**不能解释为补查机制的收益或反证**；机制没有被触发。

## 候选状态与输出

两臂最终均为 0 个发布 finding。A discovery 4 个 hypotheses，verification 3 个候选：candidate 0（summary SQL 占位类型/顺序）dismissed；candidate 1（limit cap 与 `has_more`）dismissed；candidate 2（detail fetch 错误后的 cache/filter 生命周期）`needs_context`。pipeline 计数：raw 3、confirmed 0、pending 1、dismissed 2。

B discovery 4 个 hypotheses，verification 4 个候选：candidate 0（summary SQL 类型/顺序）dismissed；candidate 1（Count 与 Query 条件一致性）`needs_context`；candidate 2（筛选/刷新后的 detail cache）`needs_context`；candidate 3（请求取消/过期响应）`needs_context`。pipeline 计数：raw 4、confirmed 0、pending 3、dismissed 1。B 的 candidate 2 注意到 cache，但没有识别 Gold #23 的实际触发条件（流式响应仍在写入时读到部分详情，随后永久缓存）。以上均没有变成 final finding。

## 候选人工核验与固定金标结果

固定 gold JSON 标为 TP 16 个，排除 pre-existing 2 个，PR-scope denominator 14。两臂都没有 final prediction：Recall 0/14；Precision undefined。当前 JSON scope 拆分为 introduced 11 + exacerbated 3 + pre-existing 2，既有 real-dev-pair adjudication 文档为 12+2+2；总分母相同，本报告保留 JSON 的逐项标签，不擅自改写。没有 judge。

人工逐候选核验（按固定 head 源码；`needs_context` 不是 TP）：

| 臂/候选 | 模型状态 | 人工核验 |
|---|---|---|
| A0 summary SQL placeholder/scan 类型 | dismissed | `auditSummarySelectCols` 的 jsonb/text 占位与 `scanAuditEntry` 的 []byte/string 目标匹配；合理驳回，无 gold match。 |
| A1 capped limit / has_more | dismissed | 使用服务端回传的 effective limit；计数和取页均使用已 cap 的 filter，所述失配场景不成立；无 gold match。 |
| A2 detail error/filter cache | needs_context | 错误后重展开会重取；cache 风险只部分触及 Gold #23，未识别 streaming update 在读取后发生、且 cache 不失效的真实触发，严格不算 TP。 |
| B0 summary SQL placeholder/scan 类型 | dismissed | 同 A0，合理驳回。 |
| B1 Count / Query filter 条件 | needs_context | head 的 Count 与 Query 都调用 `buildAuditQueryConditions`，候选没有识别两次查询非同一快照这一 Gold #2 并发窗口；非 TP。 |
| B2 filter/refresh 后的 detail cache | needs_context | 与 A2 一样，只是 Gold #23 的局部相似，触发条件与路径不匹配。 |
| B3 detail fetch cancellation / stale response | needs_context | 未有证据确认具体可达错误；无 gold match。 |

代表性漏检因果：Gold #1 虽按官方 exacerbated 标签计入，但 base 的旧 `Query` 已使用 `context.Background()`，本次没有看到 helper 371；Gold #3 的关键 `pool.Query` / nil 分支在 395–397，未进入 excerpt/diff；Gold #23 的 proxy streaming `UpdateResponse` 写入路径未提供，B 的泛化 cache 候选未形成严格匹配。Gold #4 的 AuditTrail 103–125 在 source excerpt 中可见但未形成候选；同时 head summary projection 含 `al.id` 且 scanner 赋值 `e.ID`，其 SSE 无 DB id 触发需与 list row 情形区分，官方标签未改写。其余官方 PR-scope 正例亦无严格候选匹配，不据此重新裁决标签。

## 结论边界与复现材料

这是一个已暴露 dev PR 的机制触发观察，不是独立泛化评测。A/B 候选数不同，但 B 没有触发补查，且两臂均零预测；不据此声称 treatment effect、提升/退化或晋升。没有功能开发、源代码/Prompt/Gold/策略改动、holdout 调整或外部写入。

持久化目录 `reports/evaluation/grounded-missing-evidence-ablation-2026-10-19-budget3-v2/` 保存 protocol、preflight、两臂 results、summary/恢复记录和完整模型请求/响应 artifact。费率参考 DeepSeek 官方[API 定价](https://api-docs.deepseek.com/quick_start/pricing)；使用输入峰价，不计 cache 优惠。

运行后重算 `source_identity()` 与运行前 pin 一致（commit/source hash 未漂移），六个冻结核心文件 SHA 也逐一匹配；本任务未改业务源码、Prompt、Gold、策略或 holdout。
