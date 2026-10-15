# RSI 设计：把自进化当发布工程（2026-10-15 重写）

本文**取代**旧版计划。旧版把知识写成"人写、模型只提候选"——那个判断是错的：**格式用 wiki 是对的，作者应该是 agent 自己**。安全不来自"人不让机器写"，而来自**发布门禁**。

三个一手来源（都已抓取）：

| 来源 | 它给的东西 | 我们采纳什么 |
|---|---|---|
| [ACE：Agentic Context Engineering (ICLR 2026, arXiv 2510.04618)](https://arxiv.org/abs/2510.04618) | context 是**演进的 playbook**；机制 = 生成 → 反思 → 策展；警告 **brevity bias** 与 **context collapse**；解法 = **结构化增量更新**；可**无标注**、用自然执行反馈学习 | **wiki 形态 + 增量 delta（禁止整体重写）+ 用执行反馈而非人工标注** |
| [AgentDevel：自进化即发布工程 (arXiv 2601.04620)](https://ar5iv.labs.arxiv.org/html/2601.04620) | 单条主线；每轮**只出一个 RC**；`Run → Score → Critic → Diagnose(脚本) → RC → Gate(P→F/F→P) → Promote`；程序化评分优先；**实现盲**批评者；可执行的诊断脚本；**翻转为中心**的门禁 | **整个循环骨架 + 门禁规则** |
| [Darwin Gödel Machine (ICLR 2026)](https://proceedings.iclr.cc/paper_files/paper/2026/hash/aa5f5e6eb6f613ec412f1d948dfa21a5-Abstract-Conference.html) | 自改代码 + **基准实证验证** + archive；**沙箱 + 人工监督** | **验证必须实证、执行必须沙箱、必须留人工闸门**；**不采纳**其 population 搜索（v1 用不到，风险高） |

---

## 1. RSI 采用什么（明确回答）

**采用：`AgentDevel 的发布工程循环` × `ACE 的 playbook 增量维护` × `DGM 的沙箱与监督`。**

明确**不用**：
- ❌ population / evolutionary 搜索（DGM 的多分支探索）——我们量级太小，且单主线更容易审计与回滚。
- ❌ 让模型"反思一下自己变好"（Self-Refine / Reflexion 那一类）——**同一上下文自评**在 AgentDevel 的对照下正是它要避免的"改进藏在 agent 内部"。
- ❌ 整体重写 prompt / playbook（ACE 明说是 **context collapse** 的成因）。
- ❌ 用 LLM 自评当门禁。门禁必须是**程序化评分优先**，LLM 只补没有客观检查的部分。

## 2. 步骤（每轮一次迭代，映射到本仓库）

```
Run → Score → Critic → Diagnose(脚本) → RC → Gate(P2F/F2P) → Promote|Discard
```

| 步 | AgentDevel 的要求 | 我们的具体做法 | 现状 |
|---|---|---|---|
| **Run** | 在固定 dev 集上跑当前版本，保存**结构化 trace** | 冻结 SHA + 固定模型 + 固定预算跑 gold 集/评测集；trace 落 JSONL（已有 `discovery_trace`、`candidate_audit`、`budget_report`） | ✅ 大部分已有 |
| **Score** | **程序化、确定性**评分为主 | ① `witness_eval` 机械求值（二值）；② gold 集 + 仓库自带二分图打分器（P/R/F1）；③ 引文来源校验（已实现） | ⚠️ ①未做，②已做但没进循环 |
| **Critic** | **实现盲**：只看 rubric + trace + 评分，不看 blueprint；**不归因、不提修法**，只给症状标签 | 新增"症状批评者"：输入=用例、trace、得分；输出=`symptom_label` + 一句现象描述。**不给它看 prompt/代码** | ❌ 未做（现有 critic 看得到上下文，违反实现盲） |
| **Diagnose** | 生成并**执行脚本**，聚合症状、触发条件、代表例、影响面；脚本每轮重生成、可参考上轮 | `scripts/diagnose_failures.py`：读 JSONL → 按症状聚合 → 输出**工程规格**（哪些症状占主导、哪些样例翻转、影响面多大） | ❌ 未做 |
| **RC** | **每轮只出一个**发布候选，带"变更意图"（针对哪些症状） | 一个 RC = 一处变更包：playbook 条目 delta / 检索策略 / 判据阈值。带 `intent.symptoms[]` | ❌ 未做 |
| **Gate** | **翻转为中心**：P→F（回归）当事故，F→P（修复）当证据，且修复要集中在声明的症状上 | 在同一固定用例集上跑旧版与 RC，算 `P2F`/`F2P` 集合与比率：**接受条件 = `P2F = 0`（或≤阈值）且 `F2P > 0` 且修复集中在声明的症状** | ❌ 未做 |
| **Promote** | 接受→成为下一版；拒绝→保留当前版 | RC 进 `authoritative`；拒绝则丢弃并**记录原因**（这也是学习事件） | ❌ 未做 |
| **Stop** | 提前定义的停止条件 | ① 连续两轮 `F2P` 很小；② `P2F` 持续上升；③ RC 连续被拒；④ 收益集中在缩小的子集（过拟合） | ❌ 未做 |

**两条硬规则（来自三个来源的共同点）**
1. **held-out 只在最后用一次**：门禁与停止只用 dev 集。我们 `datasets/dev/split.json` 有 dev/holdout 划分，但**历史 25 个 PR 已被接触过**——必须另立干净 holdout，否则一切数字都是自欺。
2. **迭代期间引擎固定**：模型、预算、工具版本全部冻结，只允许 RC 改变，否则无法归因。

## 3. RSI 改什么（blueprint 范围，按风险从低到高）

| 顺序 | 对象 | 为什么先它 | 风险 |
|---|---|---|---|
| 1️⃣ | **知识 playbook 条目**（契约/规则/**误报目录**） | 纯增量、可回滚、不动代码；直接攻击当前最大缺口（知识层为空） | 低 |
| 2️⃣ | **检索策略**（请求什么证据、1 跳还是 2 跳） | 直接治"证据不足→needs_context" | 低 |
| 3️⃣ | **判据阈值与路由** | 影响发布面 | 中 |
| 4️⃣ | **prompt 文本** | 影响面大、难归因 | 中高 |
| 5️⃣ | **代码**（含工具实现） | DGM 才做的事，需要更强沙箱与监督 | 高，**v1 不做** |

**v1 只做 1️⃣ 和 2️⃣**：改写 playbook 条目 + 检索策略，prompt 与代码不动。这样最坏情况是"知识写歪了"，回滚一条 delta 即可。

## 4. playbook 形态：wiki，但**只准增量**

按 ACE 的警告，playbook 必须是**条目化 + delta 更新**，不能整体重写：

```markdown
---
id: kb-0007
scope: {repo: "*", languages: [python], symbols: ["*guard*"]}
kind: false_positive_pattern        # contract | project_rule | false_positive_pattern | retrieval_hint
statement: "把 `0 <= i < n` 改写成 `i < 0 or i >= n` 的等价守卫翻转，不是缺陷；求值验证可证明等价。"
source: "gold case safe-index; witness_eval 全输入等价"
intent_symptoms: [equivalent_rewrite_confirmed]
version: 3
last_verified: 2026-10-15
expires_when: "symbol_missing OR 两次复算失败"
status: authoritative             # candidate | authoritative | expired | rejected
supersedes: null
---

（正文：为什么、反例、适用边界）
```

更新规则：
- **只追加/修改单个条目**，禁止整篇重写（ACE：防 context collapse）。
- 条目变更必须带 `intent_symptoms`，供门禁检查"修复是否集中在声明症状"。
- `expires_when` 由**机械校验**执行：引用符号不存在 → `expired`（不需要 LLM）。
- 冲突条目（同 scope 相反 statement）→ 双方降级为 `candidate` 并标记。

## 5. 门禁规则（可直接编码）

```python
def accept(prev: Run, rc: Run, intent: dict, *, max_p2f: int = 0) -> tuple[bool, str]:
    p2f = {c.id for c in prev.cases if c.passed} - {c.id for c in rc.cases if c.passed}
    f2p = {c.id for c in rc.cases if c.passed} - {c.id for c in prev.cases if c.passed}
    if len(p2f) > max_p2f:
        return False, f"regression: {sorted(p2f)}"            # P→F 是事故
    if not f2p:
        return False, "no fix evidence"                        # 没有 F→P 不值得发版
    off_target = {i for i in f2p if intent_symptom(i) not in intent["symptoms"]}
    if off_target:
        return False, f"fixes outside declared intent: {sorted(off_target)}"
    return True, f"promote: fixes {sorted(f2p)}"
```

**"pass"的定义要和 AgentDevel 一致**：**有程序化评分就用程序化评分**，没有才用批评者判断。我们的映射：
- 机械类用例（等价改写、越界检查、守卫删除）→ `witness_eval` 求值 = 硬信号；
- gold 集用例 → 二分图打分器的 TP/FP/FN = 硬信号；
- 其余 → 批评者症状标签 = 软信号。

## 6. 第一次迭代（现在就够跑）

| 项 | 内容 |
|---|---|
| 目标症状 | ① 等价改写被 confirmed（`safe-index`）；② 真候选被扣成 `needs_context`（真实运行 5/5） |
| RC 内容（v1 范围 1️⃣2️⃣） | 新增 playbook 条目 `false_positive_pattern: equivalent_guard_rewrite` + 检索提示"优先请求调用方/契约" |
| 硬信号 | `datasets/golden/synthetic-dev.json` 4 例（2 真 2 安全）+ 机械复算 |
| 期望的 F→P | `safe-index` 从"误报"变为"被正确拒绝" |
| 期望的 P→F | **0**（两个真缺陷必须仍然被确认） |
| 成本 | 单轮约 **1.9 万 tokens ≈ 0.2 元**（实测：gold 4 例 = 18,738 tokens） |
| 停止 | 若 `F2P = 0` 连续两轮，或 RC 连续两次被拒 → 停 |

**注意**：这轮 RC 若要真正生效，需要 `witness_eval` 或"等价守卫"这条判据落地——即 Phase 1。所以 **Phase 1 是 RSI 的前置**，不是并列项。

## 7. 安全与护栏（继承 ADR + DGM 的监督要求）

- **沙箱**：任何执行（求值、跑测试）必须在受控进程内：超时、无网络、只读挂载、路径白名单。
- **人工闸门**：`candidate → authoritative` 的晋升必须有人确认（DGM 的 human oversight）；自动流程只能写 `candidate`。
- **不读 gold 于发现路径**：发现模型不得读评测标签；gold 只参与评分与门禁。
- **可回滚**：每条 delta 有 `version`/`supersedes`；注入时记 `experience_ids`；报告带 `criteria_version`（已实现）。
- **判据版本化**：改判据必须能解释"哪些条目因此改判"（ADR-006）。
- **禁止自评当门禁**：门禁只用程序化评分或独立裁决。
- **停止条件前置**：不允许"再试几轮看看"。

## 8. 与其它阶段的关系

```
Phase 1（witness_eval + 过滤器链）  ← RSI 的前置：提供硬信号
        ↓
RSI v1（playbook delta + 检索策略，单主线，翻转门禁）
        ↓
Phase 3（知识层扩容：playbook 条目规模上来后再考虑混合检索）
```

**没有硬信号的 RSI 是自欺**：门禁只能靠 LLM 自评，就会回到我们现在这个坑——判据奖励引用、不奖励检验。
