# Grounded request identity 回放（2026-10-19）

本次只做本地离线追踪与 scripted replay，没有模型/API 调用。原始 negative run 的封存入口是 [`missing-evidence-trigger-real-2026-10-19.md`](missing-evidence-trigger-real-2026-10-19.md)；协议与 fixture 哈希见该报告对应的 `reports/evaluation/missing-evidence-trigger-real-2026-10-19/protocol.json`。negative 生产 trace SHA-256 为 `d2996ae27489eab16da248d3398df26da4680e09c7228402a068df109603a9f3`。

首个原始 discovery logical response 的 `intent_hypotheses` 明确声明 `h1`、`h2`，同一响应的两个 `context_requests` 分别引用 `h1` 和 `h2`。因此模型有输出 ID。两项 hypothesis 的 `constraints` 却是字符串，不是契约规定的字符串数组；两个 `missing_evidence` obligation 还被放在 hypothesis 数组外，而不是附着于对应 hypothesis。生产 `_intent_hypotheses` 按既有 schema 丢弃两项，留下 `invalid_hypothesis_fields: 2`，当时有效 hypothesis 集合为空。随后的 request validator 将保存原样的 `h1`/`h2` 请求记录为 `unknown_hypothesis_id`，没有执行 read/search。

第二个 discovery response 将 `constraints` 改为数组，host 接受并原样保留 `h1`、`h2`。该响应没有在 hypothesis 内声明 `missing_evidence`，所以本次 run 的有效 obligation 数为零。verification 的原始 `missing_evidence_requests` 为空，不能算缺失证据补读的验证。verification 的普通 `context_requests` 不带 `hypothesis_id`；由于此时计划内已有 `h1`、`h2`，validator 记录的 `unknown_hypothesis_id` 是“请求缺少已知 ID”的状态结果，ID 未被 host 改写。

| 阶段 | 原始 ID / obligation | host 结果 | 结论 |
|---|---|---|---|
| Discovery 1 | hypothesis `h1`,`h2`；requests 引用 `h1`,`h2`；hypothesis 字段不合契约 | 两个 hypothesis 被拒；task trace 保留 `h1`,`h2`，状态 `unknown_hypothesis_id` | 模型确实给出 ID，host 因其所属 hypothesis 不合法而 fail closed；不是有效 ID 经转换丢失 |
| Discovery 2 | hypothesis `h1`,`h2`；字段合法；未声明 missing evidence | `h1`,`h2` 原样进入 plan，`missing_evidence=[]` | ID 保留，obligation 不存在 |
| Verification 1 | 普通 context requests 不含 `hypothesis_id`；`missing_evidence_requests=[]` | 两项 context task 记录空 hypothesis ID 并拒绝；未发起查询 | 不是有效 missing-evidence request |
| Verification 2 | 无 requests | 两个候选均 `needs_context`，pending | 无新增证据，按既有预算终止 |

冗余范围按 side/path/range/symbol 分开核对：negative diff 和候选 definition 已完整提供 HEAD `src/accounts.py` 第 1–5 行。Discovery 请求 read HEAD `src/accounts.py` line 1，verification 后续再请求 read HEAD `src/accounts.py` lines 1–5；两个读取都对应同一份完整五行代码，重复可证。两者均被 ID 校验拒绝，未由请求触发 Snapshot 查询。另两个 search 分别查询 `get` 与 `accounts`：查询词不同，而现有 diff 只有 `accounts.get(...)` 的调用点，没有 `get` 的定义或 missing-ID 契约，所以不把它们算作与 read 相同范围的冗余查询。production snapshot trace 中的 `load_for_actor`、`account_page` 搜索是既有候选/调用者证据收集，不能归到这两个被拒绝的模型请求。

新增的最小脱敏 replay fixture 仅保存首个 response 的两个 hypothesis、两个 bound request，以及第二 response 的可接受 hypothesis 字段。它引用原始 `call-01.json`、`call-02.json` `raw_model_content` SHA-256，见 [`identity_replay.json`](../../tests/fixtures/missing_evidence_trigger/negative/identity_replay.json)。离线测试逐阶段调用现有生产入口，确认 task fingerprint 与封存 trace 一致、无请求落入 Snapshot、修正后 ID 被 host 保留；fixture 不是新的质量样本或真实运行结果。

合法 obligation 的 `needs_context -> 补读 -> 重新判定` 由现有 `test_declared_missing_evidence_uses_one_existing_bounded_followup_slot` 覆盖：显式 hypothesis/missing ID 绑定后读取冻结 helper，下一次 verification 消费已存在的 slot。该路径与本次 negative 的零 obligation 情形严格分开。

验证：

- `./.venv/bin/python -m pytest -q tests/test_grounded_review.py -k 'archived_negative_identity_replay or declared_missing_evidence_uses_one_existing_bounded_followup_slot or intent_plan_executes_frozen_cross_file_task'`：3 passed。
- 未修改生产代码、Prompt、Gold、holdout 或原始 artifacts；未创建新策略结论。

剩余边界：trace 的 `unknown_hypothesis_id` 对“不存在 ID”和“本响应的 hypothesis 未通过 schema 校验”使用同一状态名，根因须结合同轮 `invalid_hypothesis_fields` 与保存的 raw/logical response 判读。本次记录补足了该因果链，但没有改变公共 trace schema 或拒绝行为。
