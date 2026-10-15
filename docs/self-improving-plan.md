# Self-Improving 计划（含与腾讯方案的对照）

日期：2026-10-15。本文是**计划**，不是已实现能力。所有"已实现"字样都指当前仓库已有代码；所有数字都标注来源。

---

## 0. 先界定我们要做的是哪一层

| 形式 | 含义 | 本计划 |
|---|---|---|
| **RSI 强形式** | Agent 自己修改自己的代码/提示词/流程，无人工闸门 | ❌ **不做**。风险不可控，且没有可信评测就无法回滚 |
| **RSI 弱形式（本文）** | 把每次处置的结构化结果沉淀为**候选经验**，下次任务前按范围召回、按证据使用、离线治理、人工确认后才晋升 | ✅ 做 |
| 记忆（memory） | 只把内容存起来供聊天复用 | ❌ 不做。缺治理的"记忆"会把一次偶发偏差放大成系统偏差 |

腾讯那篇的 Self-Improving 属于**弱形式**——它明确写了"经验不是可以直接执行的'标准答案'，而是需要持续验证和治理的候选决策资产"。这一点我们完全采纳。

---

## 1. 腾讯那篇是怎么做的（来源：用户提供的文章 §3.5，本文只做归纳）

**四项原则**

1. **失败触发学习**：失败/被驳回/证据不足的案例才最值得记，成功案例只记可复用路径。
2. **SOP 约束行为边界**：经验不能覆盖标准流程；经验与 SOP 冲突时**以 SOP 为准**。
3. **持久性筛选控制知识噪声**：不是每次都写库，先筛选、结构化、校验。
4. **可追溯与可回滚控制传播风险**：每条经验有来源、版本、适用边界。

**一条可复用经验必须回答六个问题**
> 在什么范围内、什么现象下、依据哪些证据、采取什么动作、得到什么结果、**何时不适用**。

**运行方式（四个时点）**

- **任务前**：按系统/业务域/节点/异常类型/任务阶段做 Scope 过滤 → 多路召回 → 按相关性、历史有效性、时效性、风险排序 → 只注入 **Top-K**；经验以"**建议与约束**"而非"既定答案"形式出现（原文例："出现库存回写延迟时，先核对消息积压与幂等记录；若消费位点连续推进，则不要优先判断为消费者停滞"）。
- **任务中**：不改变原有 ReAct 流程，只在关键节点给历史提醒，并记录**经验是否被采用、是否缩短路径、是否与当前事实冲突**。
- **任务后**：从完整轨迹提取用户纠正、失败步骤、有效取证、排除路径、最终根因与处置结果 → 规则审核 + 语义审核 + 必要人工审核 → 去上下文化 → 统一结构。
- **离线**：异步计算经验价值，识别重复/冲突/过期，执行合并、降权、淘汰、归档；高频命中且持续有效的经验从"案例"晋升为"规则"，再经更严格审核固化为 SOP。

**关键机制**：结构化经验模型、混合召回与价值排序、冲突与过期治理、受控知识晋升（经验库→知识库→SOP）。

---

## 2. 我们的现实差异（必须先承认，否则会照抄出不成立的东西）

| 腾讯有 | 本仓库有 | 后果 |
|---|---|---|
| 生产日志 / 监控 / Trace / 变更记录 | ❌ 没有 | **"根因判断是否正确"无法在本地验证** |
| 修复后是否复发的线上验证 | ❌ 没有 | 不能把"处置结果"当学习信号 |
| 权威经验库 + 知识库 + SOP 三层 | ❌ 没有 | 需要从零建，且要控制体量（轻量原则） |
| 业务域/节点/异常类型 作为 Scope 维度 | ⚠️ 只有 仓库 / 语言 / 路径 / 文件 / 规则 id / 阶段 | Scope 只能按代码维度定义 |

**因此第一个关键设计决定**：本仓库的学习事件**第一批只建立在"机制事实"上**，不建立在"这个判断对不对"上。

机制事实 = 可被程序独立核查的事实，例如：

- 这条候选请求了哪些证据？请求**是否真的取到了原文**？（`discovery_trace` + `excerpts`）
- 哪些证据被预算**挤出**了？（`evicted`，本次实测 12 条）
- 哪些请求**没产出引用**？（`omitted`，本次实测 34 条）
- 哪些候选最后落到 `needs_context`？缺的是哪一类证据？（本次 5/5）
- 同一条路径是否在多次运行中被重复请求？

**不做**：把"根因判断"写成经验。因为我们没有 ground truth，写进去的就是模型自述，正是文章警告的"错误结论被反复引用"。

---

## 3. 一个真实实例（本次 0.08 元实验的产物）

我在冻结区间 `89943fe → 13e9a3e`（本仓库自己的 13 个文件）跑了一次真实审查。工具提出 5 条候选，其中 top-1：

- 位置：`src/agent/runner.py:170`，severity=high，自报 confidence=0.85
- 标题：*Critic provider derives reasoning_effort from review_model instead of critic_model*
- 证据：原文行 `reasoning_effort=settings.review_reasoning_effort or ("low" if ... settings.review_model.startswith(...))`
- 因果："同样的表达式在 158 行对 reviewer 是正确的，所以这是复制粘贴回归"
- **人工核对（我做了）：成立。** 该处构造 critic provider，其余参数都用 `critic_model`，唯独 effort 判断用 `review_model`
- 验证器结论：**`needs_context`（0.78）→ 未发布**
- 当次证据用量：`evidence_excerpts 5755/6000`（几乎用尽），`omitted 34`、`evicted 12`

这条实例同时说明三件事：

1. **工具确实能做代码级定位并给出因果**（不是泛泛而谈）；
2. **拦下它不是判断错误，而是证据不足**——发布门槛把一条真问题挡在了用户看不到的地方；
3. **它缺的到底是什么证据，是完全可记录的** → 这正是第一个学习事件该记的东西。

---

## 4. 学习事件模型（P0 的核心产物）

```jsonc
{
  "event_id": "sha256(...)",
  "kind": "evidence_request_miss | evidence_evicted | candidate_needs_context |
           candidate_confirmed | human_feedback | rule_signal_dismissed",
  "scope": {                       // 召回用
    "repo": "HelicasECoode42/ai-pr-review",
    "base": "<40hex>", "head": "<40hex>",
    "paths": ["src/agent/runner.py"],     // 涉及文件
    "languages": ["python"],
    "stage": "discovery | verify | publish"
  },
  "symptom": "证据预算用尽且候选仍缺调用方证据",
  "evidence_refs": [{"path": "...", "side": "head|base", "line": 170, "request_kind": "read"}],
  "action": {"kind": "context_request", "tool": "search", "symbol": "...", "side": "head"},
  "outcome": {"observed": "request_produced_no_citation", "measured": {"pending": 5, "tokens": 74461}},
  "not_applicable_when": "候选已能引用调用方原文时",
  "provenance": {"model": "deepseek-v4-flash", "prompt_version": "…", "run_id": "…",
                 "source_artifact": "reports/real-run/run1.json"},
  "state": "candidate | confirmed | promoted_rule | promoted_sop | expired | rejected",
  "version": 1, "supersedes": null
}
```

**抽取来源全部是已有产物**（本次运行已验证这些字段都有数据）：

| 字段来源 | 本次实例 |
|---|---|
| `candidate_audit` | 5 条（含 status/reason） |
| `behavior_verifications` | 5 条（含 trigger / call_path / expected / actual / introduced_by） |
| `repository_context.requests / excerpts / omitted / evicted / discovery_trace` | requests 若干、excerpts 7、omitted 34、evicted 12 |
| `budget_report` | 运行级账本（本轮已实现） |
| `pending_suggestions` / `evidence_rejections` / `critic_decisions` | 5 / 0 / 0 |
| 用户反馈（`Accepted`/`Rejected`/`Already handled`/`Needs context`） | 需人工或 IDE 回传，**当前未接入** |

**存储**：append-only JSONL（`reports/learning/events.jsonl`），**不引入数据库、不引入向量库**。理由是"轻量优先"且当前量级（每次运行 ≤ 十几条）用文件足够，且 JSONL 可 diff、可回滚。

---

## 5. 分阶段计划

### P0 — 事件与账本（0 元，纯离线，不需要模型）

**做什么**
1. 新增 `src/learning/events.py`：`LearningEvent` 数据模型（`FrozenContract`）+ `extract_events(report) -> list[LearningEvent]`，只从上面的结构化字段抽取，**不调用模型、不写自然语言总结**。
2. 新增 `src/learning/store.py`：append-only JSONL 读写 + `recall(scope, k)`（先做最简单的过滤+计数排序，不引入向量检索）。
3. CLI 增加 `ai-pr-review-learn extract --report reports/...json`（离线、无网络）。

**验收（可自动测试）**
- 给定 `reports/real-run/run1.json` 能产出事件，且**每条事件都能追到具体字段**；
- 事件里**不出现**任何模型生成的因果判断（只有 `observed` 事实）；
- `recall` 在 scope 不匹配时返回空（防串仓）。

### P1 — 注入与对照（约 0.08 元/次）

**做什么**
1. `prompt_addendum` 注入 Top-K（复用现有参数，**不改 system prompt、不改 JSON schema**）。
2. 同一冻结区间跑 Run 2，与 Run 1 用同一模型/预算/prompt 版本，只变"是否注入经验"。
3. 报告记录 `experience_ids`（可追溯用了哪几条）。

**验收（机制层，必须如实标注 n=1）**
- 产出对照表：`context_requests` 命中率、`pending` 数、`evidence_excerpts` 用量、tokens、耗时；
- **允许**声称：机制可运行、账目齐全、可回滚；
- **禁止**声称：质量提升、召回提升、普遍有效。

### P2 — 离线治理（0 元）

1. **去重**：同 scope + 同 `symptom` 的近似事件合并，计数加权。
2. **冲突**：同 scope 下动作相反的两条 → 都保持 `candidate` 并标 `conflicting`。
3. **过期**：经验引用的 `path`/`symbol` 在冻结 head 中不存在 → `expired`（这也顺便把"架构变了但经验还在"治理掉）。
4. **价值**：命中次数 + 后续是否被引用/被用户采纳（`human_feedback`）。
5. **晋升**：`candidate --人工确认--> confirmed --多次命中--> promoted_rule --更严审核--> promoted_sop`。
6. **回滚**：`version` + `supersedes`；关闭注入后结果必须可复现（写成测试）。

### P3 — 成本与可靠性的一刀（0 元代码，但需你定方向）

1. **验证阶段的重复重发**：本次实测 3 次调用 = **1 次 discovery + 2 次验证**（`discovery_request_count=1`、`behavior_verification_request_count=2`），prompt 合计 42,454 tokens。每一轮验证都把**完整 diff + 全部证据**重发一次；改为"只发增量 + 变化部分"预计可省约 **1/3**（≈2.5 万 tokens）。discovery 本次只用 1 轮，不是重复重发的主因。
2. **`enable_critic` 默认值**：默认开且用更贵的 `deepseek-v4-pro`；历史对照中 Critic 误删过 2 条成立建议。本次因证据门后无可发布候选（`suggestions=0`）→ Critic 候选为空 → **0 次调用**。也就是说它的成本只在"真有候选"时才发生，但那时也更贵。
3. **发布阈值三处收敛**：workflow 硬编码 `0.80`、config `0.65`、而 `min_confidence` 在 grounded 链路上**被传入却从未使用**。
4. **prefix cache**（你已支持）：稳定前缀（system + 不变量）在前、动态内容（diff/证据/经验）在后；**必须实测 cache 命中再谈节省比例**，不编造。

> 更正记录：本文初稿把 74,461 tokens 的 3 次调用误记为"discovery 3 轮"。核对运行产物的 `metrics.discovery_request_count=1` 与 `behavior_verification_request_count=2` 后更正为"1 次 discovery + 2 次验证"。这也是"账本能自己纠错"的例子——**没有这两条计数，成本结构就只能靠猜**。

---

## 6. 护栏（从既有文档继承，不新造）

- 经验库分**候选区 / 权威区**：自动流程只能写候选区，权威区需人工确认。
- **发现模型不得读 gold 标签**；评测标签不参与调优。
- 不把原始对话/transcript 当经验；答不全"六个问题"的内容只能留在候选区。
- 每次注入记录 `experience_ids` + `prompt_version` + 模型 + 冻结 SHA，可回滚。
- **不用被评测的 agent 自评替代独立 evaluator**。
- 任何"质量提升"的结论必须来自固定协议对照；n=1 只能证明机制跑通。
- 经验与 SOP 冲突时以 SOP 为准（照腾讯那篇的原则）。

## 7. 非目标

- ❌ 自动改写 prompt / 自动修改自身代码（RSI 强形式）
- ❌ 引入数据库、向量库、消息队列
- ❌ 跨仓库共享经验（先把单仓库闭环做通）
- ❌ 用"运行次数"或"经验条数"充当能力指标

## 8. 与其它待决事项的关系

P3 的四项都需要你先定方向（阈值/默认值/是否动 prompt 输入），P0–P2 不需要模型调用即可完成。**建议按 P0 → P1 → P2 → P3 推进**：先把账做对、把回滚做出来，再动成本与门限。
