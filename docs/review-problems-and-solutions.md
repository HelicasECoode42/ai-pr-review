# AI PR Review：问题与解决方案总览

更新：2026-10-20。代码基线：`d7dedc5`（工作区干净）。验证：`577 passed`，CI Ruff、criteria gate、interface audit、holdout 全绿。

本文把当前项目的**目标、漏斗、已证实的问题、已落地的修复、待做方案与权衡**放在一处。带日期的实测细节见 [`STATE.md`](STATE.md) 与 [`evaluation/`](evaluation/)；规范约束以 [`HARNESS.md`](HARNESS.md) 为准。

---

## 0. TL;DR

- 产品目标不是"更多 Agent"，而是：**让 AI 找到 PR 里真实存在的 Bug，有证据、可复现、低误报、成本可控，并证明相对 one-shot 有收益。**
- 我们把它拆成 **N → S → D → P** 四层。当前实测：S 不是主瓶颈（dev 9 case 72.55%），**D 是主瓶颈**，P 是次瓶颈。
- 2026-10-20 的关键发现：**同一模型去掉 harness 后语义 D 从 8.7% 升到 30.4%**（成本还更低）。也就是说，当前的 hypotheses/工具循环/多阶段/schema/verification 组合在压制发现能力，而不是提升它。
- 已落地：数据契约（gold scope 代码派生）、S 可复算（行级暴露账本）、N/S/D/P 漏斗 + 语义裁决、Go 证据窗口修复、raw-model 基线、确定性测试质量规则、付费实验的 clean-tree 前置。
- 下一步：**raw 发现通道（宽进）→ 现有 verification/criteria（严出）**，再补候选精读、条件化证据与模型消融；用 3–5 个 dev case 做门禁评测。

---

## 1. 目标与四层漏斗

### 1.1 真实链路

```
PR metadata + diff
 ├─1. 确定性扫描（规则/AST/Ruff）              只产 signal，不作结论
 ├─2. 选材 S：build_review_context             预算内选 patch、逐 hunk 记账、排除文档/lockfile
 ├─3. 自动证据 S：EvidenceBundle.follow         从 changed 定义读源码窗口 + symbol 搜索
 ├─4. Discovery D：review_grounded 第一段        hypotheses + 有界 read/search + 候选
 ├─5. Verification P：第二段                   confirmed / dismissed / needs_context + counter-evidence
 ├─6. 判据 P：criteria v4                      引文来源、witness、契约域、去重/上限
 └─7. 发布 P：confirmed → inline；pending → 报告；ledger 记去向
```

两个常见误解需要纠正：

1. **不是"全量扫描"**：规则扫描只看 patch；patch 上下文有 token 预算（默认 12000），超预算按 triage/文件优先级/大小/路径截断——这是 S 损失来源。
2. **不是"AI 自动深入询问"**：host 只执行模型在 `intent_hypotheses` 里**显式声明**的 obligation；模型不声明就没有补查（真实 case 里这是常态）。

### 1.2 四层定义与测量口径

| 层 | 含义 | 当前测量方式 | 成本 |
|---|---|---|---|
| **N** | 官方 gold 中 PR-scope true positive 数 | 由官方 `tp_fp`/`scope` 字段**代码派生**（introduced + exacerbated，排除 pre-existing） | 免费 |
| **S** | gold 行是否真的进入 prompt | 行级覆盖率：`rendered_head_ranges`（patch 段）+ `evidence_refs`（源码摘录），区分 fully / partially / not visible | 免费、离线 |
| **D** | 模型是否**识别**了该缺陷（不论最终判定） | 两档：位置匹配（粗、会高估）+ **语义裁决**（人工对照 gold message，准） | 离线；裁决人工 |
| **P** | 最终是否**发布**且正确 | published findings 对照 gold；未裁决时是位置 proxy | 需真实模型调用 |

**铁律**：质量结论只引用 `D_sem`，不用位置匹配。CrabTrap 实测位置 D=3、语义 D=0；AA-Factory 位置 2、语义 2 但命中 id 不同。

---

## 2. 当前质量基线

### 2.1 历史绝对指标（proxy）

| 测量 | 样本 | Precision | Recall | 说明 |
|---|---|---:|---:|---|
| 官方 judge，one-shot 基线（2026-10-13） | 25 PR | 91.7% | **3.98%**（14/352 覆盖） | 同模型家族 judge，未人工抽查 |
| 生产路径 raw-match（2026-10-16） | 5 dev PR | 83.3% | **11.6% published / 18.6% identified** | 位置匹配，不是语义裁决 |

### 2.2 本轮漏斗（可复算）

| 测量 | 结果 |
|---|---|
| dev 9 case 的 S | 102 条 PR-scope gold：**74 全可见（72.55%）**、3 部分、25 未可见 |
| CrabTrap 完整漏斗 | N=14、S=13、D_pos=3、**D_sem=0**、P=0 → 损失：未选中 1 + 看见了没识别 13 |
| harness A/B（强制三问 vs baseline，2 case） | 两臂 **D_sem 都是 2/23（8.7%）** |
| **raw-model 并集（2 case）** | **D_sem 7/23（30.4%）**，成本 ¥0.061786 |
| raw ∪ 确定性规则 | **9/23（39.1%）** |
| AA-Factory 确定性规则 | 语义覆盖 **4/9 gold**，候选级 precision 4/5，零模型成本 |

---

## 3. 问题清单（已证实）

> 编号用于和解决方案对应。每条都附证据入口。

### 数据契约

**P1 · gold 口径手写漂移。** 同一份官方 JSON 是 `11 introduced + 3 exacerbated + 2 pre-existing`，裁决文档写 `12+2+2`；分母都是 14 但标签靠人记。
→ 证据：[`evaluation/real-dev-pair-2026-10-19.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/real-dev-pair-2026-10-19.md)、[`evaluation/grounded-missing-evidence-ablation-budget3-v2-result.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/grounded-missing-evidence-ablation-budget3-v2-result.md)

**P2 · S 不可重建。** 历史真实配对没有保存 serialized prompt，report 里也没有行级暴露范围，只能写 `S = unknown`；事后无法区分"没送进去"和"送进去了没发现"。
→ 证据：[`evaluation/real-dev-pair-2026-10-19.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/real-dev-pair-2026-10-19.md) 的 prompt exposure 段

**P3 · D 位置匹配太粗。** 同文件同区间可以放一条描述完全不同缺陷的候选。CrabTrap 位置 D=3、语义 D=0；AA-Factory 位置与语义命中 id 不同。
→ 证据：[`evaluation/raw-model-baseline-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/raw-model-baseline-2026-10-20.md)、[`evaluation/d-forced-checks-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/d-forced-checks-2026-10-20.md)

**P4 · 付费实验被脏工作区阻塞。** `src/evaluation/paired.py` 在 `git status` 非空时直接拒绝运行（"Paired evaluation requires a clean committed reviewer"）；工作区曾有 86 处未提交改动，导致可归因实验无法开始。
→ 证据：`paired.py` 的 clean-tree gate

### S 层

**P5 · Go 证据窗口确定性错误。** `func (r *T) name(...) RetType {` 的 receiver/返回类型让 brace 启发式失效；fallback 把 SQL `IN (`、Go `len(` 当函数名；假 symbol 被 `follow()` 拿去 search，烧掉证据预算；每文件只 seed 前 2 个 hunk，导致 helper 所在第 3 个 hunk 永不读取。CrabTrap gold #1 的 371/395 因此进不了 prompt。
→ 证据：[`evaluation/data-contract-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/data-contract-2026-10-20.md)、anchor 复现表

**P6 · S 的剩余缺口是预算性的。** pika 57.9%、chronicle 60%、agents-config 33.3%；未可见主因是文档类排除与超预算截断。已测过的两条路都更差：per-file 预算摊薄（S 74→70）、unit 全量切片（9/146 units、558k tokens）。
→ 证据：[`evaluation/funnel-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/funnel-2026-10-20.md)、`src/utils/config.py` 的 unit 注释

### D 层

**P7 · harness 在压制 D。** 同一模型、同一输入：raw 单次最小调用 D_sem 30.4%，harness 8.7%；raw 成本低约 40%。harness 的 hypotheses/工具循环/多阶段/schema/verification 组合把弱候选压掉了。
→ 证据：[`evaluation/raw-model-baseline-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/raw-model-baseline-2026-10-20.md)

**P8 · prompt 纪律无效。** 强制"调用方/错误分支/生命周期"三问后，两臂 D_sem 完全相同（2/23），只多了约 10% 成本；模型照格式写了理由，但没有新发现。
→ 证据：[`evaluation/d-forced-checks-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/d-forced-checks-2026-10-20.md)

**P9 · 证据的作用是分类型的。** 跨文件 case 需要证据（CrabTrap raw diff 0→3），局部 case 被证据稀释（AA 4→3）。不能全局"多给证据"。
→ 证据：同上 raw 基线表

**P10 · D 的缺口是 case 类型相关的。** 同文件、可模式化的测试反模式能稳定发现（AA 2–4/9）；跨函数/运行时语义（丢 caller context、nil-on-error 返回 200）即使 helper 全可见也发现不了（Crab 0/14）。
→ 证据：D 基线与 raw 基线

### P 层

**P11 · 已识别的 TP 被核验扣下。** AA-Factory 两条语义命中都是 pending、0 published；历史 b7b 也有 `matched_but_withheld`（5 dev 里 3 条，identified 18.6% → published 11.6%）。
→ 证据：[`evaluation/d-forced-checks-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/d-forced-checks-2026-10-20.md)、`reports/real-run/b7b-diagnostic.json`

### 评测与工程

**P12 · 评测只有终点、没有漏斗。** 之前只有 published 的 P/R，没有 N/S/D/P 分层，导致每次改动无法归因；可离线测的可靠性工作获得正反馈，真正影响 recall 的 S/D 被推迟。
→ 证据：[`evaluation/funnel-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/funnel-2026-10-20.md) 的动机段

**P13 · 测试数增长与真实损失路径错位。** 577 个测试全绿的同时，Go 证据窗口 bug 长期存在；CI 最低 Ruff 还被 `tests/fixtures` 的故意不完整源码打红。
→ 证据：[`tests/test_evidence_window.py`](../tests/test_evidence_window.py)、`pyproject.toml` 的 per-file-ignores

**P14 · 单次运行噪声不可忽略。** 同 commit 上 CrabTrap 两次 baseline 候选数 3 vs 4；n=1/arm 只能排除大效应，不能估计小效应。
→ 证据：D 基线与实验 1

**P15 · 确定性可检测的缺陷没有走确定性通道。** AA gold 0（缺测试依赖）、2（patch 打不到本地 import 绑定）、3（mock 调用形状）、5（全局状态无 teardown）本可由 AST 规则零成本命中，却都指望模型。
→ 证据：`src/analyzer/test_quality_rules.py` 的探针结果

---

## 4. 解决方案

### 4.1 已落地（代码 + 测试 + 文档）

| 方案 | 对应问题 | 做了什么 | 证据 |
|---|---|---|---|
| **S1 gold scope 代码派生** | P1 | `src/evaluation/gold_scope.py`：只认官方 `tp_fp`/`scope`；缺字段/未知值/tp+unrelated fail closed；分母由代码算 | `tests/test_gold_contract.py`；CrabTrap 重算 11+3+2=14 |
| **S2 S 可复算** | P2 | `ReviewContext.rendered_head_ranges` + `review_trace.coverage.rendered_head_ranges`（只存区间）；`gold_visibility.py` 行级覆盖率；离线脚本 | `tests/test_gold_visibility.py`；dev S=72.55% |
| **S3 D 语义裁决** | P3 | `funnel.py` 同时报位置/语义 D；证据包 `--candidates-out`；`--adjudication` 逐候选 gold/null/list；fail-closed 校验 | `tests/test_funnel.py`；CrabTrap 3 vs 0 |
| **S4 证据窗口修复** | P5 | Go receiver/返回类型/多行签名/interface；fallback 只认行首关键字；`follow()` 最多 4 hunk、按 head/非测试/定义/成本排序、单窗口 90 行 | `tests/test_evidence_window.py`；CrabTrap gold #1 12/26→26/26 |
| **S5 文档独立预算（默认关）** | P6 | `max_documentation_tokens`：文档 hunk 走附加预算，不占 code patch 预算 | 实测 1200 tokens 恢复 3 条 gold（S 72.55→75.49），默认 0 |
| **S6 clean-tree 前置** | P4 | 工作区拆成 7 个 commit 提交；付费实验必须 clean tree；本轮之后 `git status` 始终干净 | `30de268`、`ea59f93`、`d7dedc5` 等 |
| **S7 funnel 工具** | P12 | `scripts/evaluation/funnel.py`：N（gold）+ S（离线）+ D/P（artifact）一行命令出账 | 报告与 JSON 已入库 |
| **S8 raw-model 基线** | P7/P9 | `scripts/evaluation/raw_model_baseline.py`：同 diff/证据、单次最小调用、预算 cap；diff 与 diff+evidence 两变体 | raw 并集 D_sem 7/23，成本 ¥0.0618 |
| **S9 确定性测试规则** | P15 | `src/analyzer/test_quality_rules.py`：TQ001 import 绑定 vs patch、TQ002 调用被 mock 函数、TQ003 全局状态无 teardown、TQ004 未声明测试依赖 | 测试 5 条；AA 语义覆盖 4/9 |
| **S10 实验变量可复现** | P8 | `paired.py --prompt-addendum-file`：addendum 的 SHA-256 进 protocol，实验可复现 | `tests` + protocol 字段 |
| **S11 CI gate 修复** | P13 | fixtures 的故意不完整源码加 per-file-ignores；CI 最低 Ruff 恢复全绿 | `pyproject.toml` |

### 4.2 待做（按 ROI 排序）

| 方案 | 对应问题 | 做法 | 验收 | 成本/风险 |
|---|---|---|---|---|
| **S12 raw 发现通道（首选）** | P7/P8/P10 | 默认关闭的 discovery 通道：单次最小 prompt + 自动证据生成候选，**后接现有 verification/criteria**，只发 confirmed | 3–5 dev case：D_sem ≥ 30% 且 P 不降；published precision ≥ 0.8 | 一次真实评测 ≤¥0.2；风险是 P 层压力变大 |
| **S13 候选精读（P）** | P11 | 对 pending 候选带完整函数 + 调用方做一次 focused re-read，再判 confirmed/needs_context | AA gold 1/5 变 confirmed，且不新增 FP（人工裁决） | +1–2 调用/case，约 ¥0.05 |
| **S14 条件化证据** | P9 | 仅当 changed symbol 的定义不在 diff 内/存在跨文件调用时注入 source evidence；局部 case 不塞 | AA D 不降、Crab D 保持；prompt tokens 下降 | 免费离线；需要 call-graph 信号 |
| **S15 剩余确定性规则** | P15 | TQ005 mock 调用形状 vs 真实签名、TQ006 mock 返回类型、TQ007 magic-number 下标 | AA gold 3/4/8 至少覆盖 2 条 | 免费；需要冻结 snapshot 里的签名 |
| **S16 thinking/模型消融** | P7 | 同 case 开 thinking / 更大 max_tokens / 更强模型只做 discovery | D_sem 变化 + 成本 + 截断率 | 约 ¥0.1/case；先解决截断 |
| **S17 双通道指标** | P11/P12 | 报告/Web 明确 `confirmed(auto-publish)` 与 `surfaced(review queue)` 两套指标 | surfaced recall、published precision 分开报告 | 产品/UI 文案；模型零成本 |
| **S18 重复运行与噪声控制** | P14 | 任何 D 结论至少 3 次重复，报范围/中位数；预注册协议 | 方差可估计 | 3× 成本 |
| **S19 Alibaba 式分组** | P10（稀释） | 按文件元数据（路径+churn）做语义分组，分组内 Plan→Main→filter→re-location；并发 | 分组后 D 与成本对照 | 1+3–5×groups 调用；当前 unit 路径已测更差，需重做 |
| **S20 保存 serialized prompt** | P2 | 评测时落完整 system/user prompt artifact（脱敏），身份 hash 进 protocol | 可复核 prompt 措辞 | 存储与隐私成本 |

---

## 5. 实验记录（2026-10-13 ~ 10-20）

| 日期 | 实验 | 协议要点 | 结果 | 成本 |
|---|---|---|---|---|
| 10-13 | 25 PR 官方 judge | one-shot 控制基线 | P 91.7%、R 3.98% | 历史 |
| 10-13 | +Critic A/B | 固定建议再过滤 | Critic 净删 2 TP、0 FP | 历史 |
| 10-16 | b7b 5 dev PR | 生产路径 raw-match | identified 18.6%、published 11.6%、P 83.3% | 历史 |
| 10-19 | CrabTrap 配对 | 真实 control/grounded | 两臂 0 发布；grounded 3 候选 | ¥0.177 |
| 10-20 | 证据修复后 D 基线 | 单 case、2 HTTP、thinking off | N14/S13/D_pos3/D_sem0/P0 | ¥0.0732 |
| 10-20 | 强制三问 A/B | 2 case、同 commit、单变量 | 两臂 D_sem 2/23；+10.3% 成本 | ¥0.1026 / ¥0.1132 |
| 10-20 | raw-model 基线 | 2 case × 2 变体、单次调用 | 并集 D_sem 7/23（30.4%） | ¥0.0618 |
| 10-20 | 确定性规则探针 | AA head 文件 AST | 语义覆盖 4/9；raw∪确定性 9/23 | 免费 |

---

## 6. 路线图与验收门槛

### Phase 0（已完成，2026-10-20）

数据契约 + S 可复算 + N/S/D/P 漏斗 + 证据窗口修复 + raw 基线 + 确定性规则。验收：漏斗可一行命令复现；CrabTrap D_sem 可测；577 tests 与全部门禁绿。

### Phase 1（下一步）：宽进严出

raw 发现通道（默认关）+ 候选精读 + 条件化证据 + 双通道指标。验收（3–5 dev case，预注册协议）：

- `D_sem / N` ≥ 30%，且相对 harness 基线有可重复提升；
- `published precision`（人工语义裁决）≥ 0.80；
- `published recall` ≥ 0.15（当前 11.6%）；
- 单 case 成本 ≤ ¥0.15，HTTP 有硬上限；
- 任何 D 结论 ≥ 3 次重复或明确标注单次。

### Phase 2：跨文件与模型

条件化证据 + call-graph 扩展 + thinking/更强模型消融；在 Phase 1 的同一协议上比较，一次一个变量。

### Phase 3：独立泛化

新建未接触 repo 分组（不污染 holdout），冻结 KB/提示后只跑一次；不再用 dev 调参。目标不是"超过某个榜单"，而是证明相对 one-shot 的**同质量成本/同成本质量**收益。

### "全和正确"的定义

必须分通道，不能用一个指标同时追：

- **Auto-publish**：只发 confirmed，目标是 precision ≥ 0.8；
- **Review queue**：展示所有有证据的 surfaced 候选（含 pending/needs_context），目标是 recall；
- 报告同时给 `surfaced recall` 与 `published precision`，不让"少发"伪装成"准确"。

---

## 7. 边界与风险

- **n 很小**：已授权真实运行都是 1–2 个已暴露 dev case，非盲裁决；不能据此晋升、泛化或宣称线上能力。
- **D/P 都是 proxy**：语义裁决仍是人工非盲；位置匹配只能当预筛。raw 候选未核验，"命中 gold" ≠ 可发布。
- **成本口径**：所有费用是 provider usage × 官方峰价、输入不享缓存折扣的**上界**，不是账单对账；余额变化未对账。
- **证据修正不保证质量**：S 提升只证明"行可见"，不证明模型读到或理解。
- **holdout 已污染**：15 个历史 holdout 不能用于晋升；新独立留出必须新建。
- **不要在未测的情况下换默认值**：文档预算、raw 通道、条件化证据都应默认关闭，先做单变量评测。

---

## 8. 复现命令

```sh
# 1) N/S/D/P 漏斗（S 免费；D/P 需要一次已保存 run）
.venv/bin/python scripts/evaluation/funnel.py \
  --prepared ../eval-data/prepared/dev.json \
  --gold ../eval-data/reviewbench/golden \
  --mirrors ../eval-data/mirrors --out /tmp/funnel-dev.json

# 2) 单 case 完整漏斗 + 语义裁决
.venv/bin/python scripts/evaluation/funnel.py \
  --prepared ../eval-data/prepared/dev.json \
  --gold ../eval-data/reviewbench/golden --mirrors ../eval-data/mirrors \
  --report /tmp/crabtrap-d-2026-10-20/grounded/audits/brexhq_CrabTrap_15-5ef0884c.json \
  --case brexhq_CrabTrap_15-5ef0884c \
  --adjudication docs/evaluation/d-baseline-crabtrap-2026-10-20-adjudication.json \
  --out /tmp/funnel-crab.json

# 3) 付费实验前必须 clean tree；跑一臂（硬上限）
REVIEW_THINKING_ENABLED=false .venv/bin/python -m src.evaluation.paired /tmp/case-one.json \
  --mirrors ../eval-data/mirrors --output /tmp/run-dir --arms grounded \
  --concurrency 1 --max-attempts 1 --max-http-attempts 8 --max-output-tokens 4096

# 4) raw-model 基线（单次最小调用）
REVIEW_THINKING_ENABLED=false .venv/bin/python scripts/evaluation/raw_model_baseline.py \
  --prepared ../eval-data/prepared/dev.json \
  --gold ../eval-data/reviewbench/golden --mirrors ../eval-data/mirrors \
  --case brexhq_CrabTrap_15-5ef0884c --modes diff diff+evidence \
  --max-http-attempts 6 --out /tmp/raw-baseline.json
```

---

## 9. 相关文件索引

**代码**

- 数据契约：[`../src/evaluation/gold_scope.py`](../src/evaluation/gold_scope.py)
- S 暴露账本：[`../src/evaluation/gold_visibility.py`](../src/evaluation/gold_visibility.py)、[`../src/analyzer/context_builder.py`](../src/analyzer/context_builder.py)、[`../src/analyzer/repository_context.py`](../src/analyzer/repository_context.py)
- 漏斗：[`../src/evaluation/funnel.py`](../src/evaluation/funnel.py)、[`../scripts/evaluation/funnel.py`](../scripts/evaluation/funnel.py)
- raw 基线：[`../scripts/evaluation/raw_model_baseline.py`](../scripts/evaluation/raw_model_baseline.py)
- 确定性规则：[`../src/analyzer/test_quality_rules.py`](../src/analyzer/test_quality_rules.py)
- 付费实验入口：[`../src/evaluation/paired.py`](../src/evaluation/paired.py)

**证据与报告**

- [`evaluation/data-contract-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/data-contract-2026-10-20.md)
- [`evaluation/funnel-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/funnel-2026-10-20.md)
- [`evaluation/d-baseline-crabtrap-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/d-baseline-crabtrap-2026-10-20.md)
- [`evaluation/d-forced-checks-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/d-forced-checks-2026-10-20.md)
- [`evaluation/raw-model-baseline-2026-10-20.md`](https://github.com/HelicasECoode42/ai-pr-review/blob/df397d5/docs/evaluation/raw-model-baseline-2026-10-20.md)
- [`STATE.md`](STATE.md)（带日期的当前状态与证据边界）

**测试**

- [`../tests/test_gold_contract.py`](../tests/test_gold_contract.py)、[`../tests/test_gold_visibility.py`](../tests/test_gold_visibility.py)、[`../tests/test_funnel.py`](../tests/test_funnel.py)、[`../tests/test_evidence_window.py`](../tests/test_evidence_window.py)、[`../tests/test_test_quality_rules.py`](../tests/test_test_quality_rules.py)
