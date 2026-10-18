# 离线 Benchmark Preflight 与候选影子回放

本报告由本地 dry-run 生成；没有调用模型、GitHub API 或网络。它不产生新模型质量结论。

## 固定输入与来源

- 基线源码：`b30de7b8340b46a8c8ce8c34c09a9491e7df98b9`，tree `0da1517b3343617fefc062607abc4d7ab951780f`；本地存在：True。
- 当前源码：`f118615a96ae8b9fa76c0b4dc4cb229378d194e2`，tree `3206491f0b7b7d2c400d7f7ba917fde5f1bc6a0e`；工作区干净：False。
- roster：dev 9 个（本地快照 ready 9/9）；regression-only 旧 holdout 15 个（ready 0/15）。总计 ready 9，not_ready 15。
- prepared manifest 与 split hash 一致：True；holdout 状态：`regression_only`，test 状态：`not_authorized`。
- 输入集合 SHA-256：`992e52d61ca08ed57f5f76abbcf64bc477d01920337df9a2a160f513ca1335ed`；脚本 SHA-256：`97b7580f458d9927a470835f35f000c71a8563c1ece552a738f16f89add0df4b`。当前源码树字段是 HEAD tree，脚本另以内容哈希固定。
- metadata 必须与受哈希 manifest 中的记录一致，diff 必须通过 manifest SHA。
  缺少 base/head 对象只标 not_ready；脚本不 fetch、不补取输入。

## 历史数据（不可作为新旧配对结论）

- run记录模型 `deepseek-v4-flash`，audit请求名 `deepseek-v4-flash`；成功 25 / 失败 0，复用 23 条；质量状态 `unadjudicated`，源码dirty=True。
- audit 25 份、usage 25 条：prompt 166,646 + completion 459,135 = 625,781 tokens。
- cost_usd 未记录；失败尝试及 judge 成本/重试账目不完整，不能当作完整总成本。
- 评分：12 预测、352 gold；gold-linked 11（10 TP + 1 FP），另有 1 条 novel TP。
- grounded P=90.9091%；augmented P=91.6667%。unique gold coverage=14/352，grounded R=3.9773%；含 novel TP 后 augmented R=4.2493%。
- matcher `deepseek/deepseek-v4-flash` 与审查模型同家族，不是独立人审；14 是去重 gold 覆盖数，不是预测数。
- 以上分母/precision 定义不同，不能压成一个不带口径的‘精确率’。
  5-case b7b 使用 raw 文件/行位置匹配，是另一套 proxy，不能并入本表。

## 零 API verifier 影子回放

- 当前单行证据子门：输入 12 条，保留 12，拒绝 0，子门不可回放 0。
- 完整 criteria/witness 链：已知候选 12 条，状态 `known`；不可回放已知候选 12 条；候选数未知 case 0 个。
- 旧 audit 缺结构化 base/head 引用、原始 sent bundle、counter-evidence 和未过滤候选；历史建议已受旧流程筛选，不能用它估新 verifier 召回。
- 当前只核验单行证据是否出现在 frozen diff 的 added line；
  它不判断发现是否语义正确。gold 不参与候选回放。

## 固定标签 Critic 过滤消融（历史产物复算）

- 状态：`recomputed_from_artifact_joins`；协议 `fixed_baseline_suggestions_critic_replay`；基线/过滤后候选数 12 / 10。
- 该对比按候选文件精确 join 到基线已有 judge 标签，再只筛掉 Critic 未保留项；不重新 judge。评分：grounded P/R 0.9090909090909091 / 0.03977272727272727 → 0.8888888888888888 / 0.03409090909090909。
- 固定标签计数：TP/FP/novel TP 10/1/1 → 8/1/1；unique gold 14/352 → 12/352。
- 口径展开：baseline gold-linked TP 10/11，含 novel 的 P 11/12；Critic 后 gold-linked TP 8/9，含 novel 的 P 9/10。
- Critic audit usage 9 条，39,353 tokens；成本未记录。
- 这是同家族旧 judge 标签下的过滤器回放，不代表当前 verifier 效果或独立质量裁决；
  fresh Critic score 是另一种重新判分口径。

## 正式比较前仍需准备

固定 PR base/head、reviewer 源码和全部预算/重试协议；baseline/current 配对且每次 attempt 计入；review 与 judge usage/cost 分开。先对逐 finding 盲标 TP/FP/unknown，分歧独立裁决；只有独立语义标签覆盖足够时才报告 micro P/R/F1。未知项报上下界，locator 匹配只单列。Fresh test 仍未授权。

标签 JSONL 字段：`case_id, arm, finding_sha256, blind_reviewer_id, label(tp|fp|unknown), matched_gold_ids[], reason_code, evidence_refs[], rationale`。hash 覆盖 case/arm/文件行/title/reason/evidence；双人盲标，分歧第三人裁决。多 claim 拆成原子 finding；同 case 同根因重复项仅首条计 TP，其余计 FP。行位置重合不自动算 TP。unknown 保留并报上下界；预测完成标注且 gold roster 完整后才出点估计。
