# 错误识别基线（2026-10-15 实测）

本文只记**测出来的数字**，用于回答一个比成本更根本的问题：这批链路**认不认得错**。

## 一、已知答案测试：合成 gold 集

用仓库自带的 `datasets/golden/synthetic-dev.json`（2 个真缺陷 + 2 个**安全重构负例**），跑真实模型 `deepseek-v4-flash`，用仓库自己的二分图打分器 `src/evaluation/scoring.py::score` 打分。脚本：`scripts/gold_identification.py`。

**结果：召回 2/2 = 100%，精确 2/4 = 50%**（4 个 case、9 次调用、18,738 tokens、余额变化可忽略）。

| case | gold | 预测（置信度 / 来源） | 判定 |
|---|---|---|---|
| `division` | `division-bug` | 0.90 `ai_behavior_verified` "Removed zero-count guard turns a safe return into ZeroDivisionError" | ✅ 真缺陷，机制解释正确 |
| `negative-index` | `negative-index-bug` | 0.97 `ai_behavior_verified` "Negative indices now silently wrap instead of returning None" | ✅ 真缺陷，机制解释正确 |
| `safe-division` | （安全） | 0.85 `ai_behavior_verified` "Narrowed falsy guard turns None/False count into a TypeError" | ⚠️ **行为差异是真的**（base 的 truthiness 守卫确实也吃了 `None`），但契约上不算缺陷；工具没有调用方证据就发布了 |
| `safe-index` | （安全） | 0.78 `ai_behavior_verified` "Negated guard widens the set of inputs that reach values[index]" | ❌ **可证明的误报**：`0 <= index < len(values)` 与 `index < 0 or index >= len(values)` **逻辑等价**（德摩根），不存在行为差异 |

### 结论：问题不在识别，在判定

验证器确认了一条**可证明为假**的结论，因为它检查的是"有没有引用到证据链"，而不是"这条链本身是否成立"。于是它在两种场景下**同时出错**：

- **证据薄弱**（真实仓库、证据预算用尽）→ 把真问题扣成 `needs_context`（见第二节：5 条候选里 4 条是真缺陷，**发布 0 条**）；
- **证据充分但改动很小**（2 行 diff）→ 把**等价改写**确认成缺陷（本次 0.78 + `ai_behavior_verified`）。

### 可执行的修法

把"确认"的判据从"能引用一条链"改为"**能给出一个见证输入，使 base 与 head 行为不同**"，并对纯表达式类结论**实际求值验证**该见证。对 `safe-index` 这类结论，不存在这样的输入，因此机械上无法被确认——这正是该固化成 SOP 的判据。

## 二、真实仓库运行的手工判定（零额外模型调用）

对冻结区间 `89943fe..13e9a3e` 的真实运行（`reports/real-run/run1.json`，5 条候选）逐条对照冻结代码判定：

| # | 位置 | 判定 | 依据 |
|---|---|---|---|
| 0 | `src/agent/runner.py:170` | ✅ 真缺陷 | critic provider 的 `reasoning_effort` 用 `review_model` 判断（复制粘贴回归） |
| 1 | `src/evaluation/paired.py:88` | ✅ 真（低影响，仅评测路径） | 硬编码 `reasoning_effort="low"`，无模型族判断；非 DeepSeek 模型会收到不支持的字段 |
| 2 | `src/evaluation/paired.py:52` | ❌ 误报 | 冻结版本全仓 `git grep` 无任何消费者读旧 `temperature` 键，描述的是假想消费者 |
| 3 | `src/reviewer/grounded_review.py:115` | ✅ 真缺陷 | `raise ValueError("Unsupported model response envelope")` 写在捕获 `ValueError` 的 `try` 内，该检查**永远无法拒绝**，直接落到 repair 分支 |
| 4 | `src/analyzer/repository_context.py:79` | ✅ 真（低严重度，Windows） | 120,000 字节 argv + `_git` 不捕 `OSError` → 32K 限制平台整个 search 中止 |

**发现精度 4/5 = 80%，发布 0/5 = 0%。**

## 三、这两个数字合起来说明什么

| | 证据充分的小 diff | 证据稀薄的真实 diff |
|---|---|---|
| 识别 | 2/2 命中 | 4/5 命中 |
| 判定 | **1 条可证明为假的结论被"验证通过"** | **4 条真缺陷全部被扣下** |

**识别（discovery）在两侧都是好的（80–100%）；崩的是判定（verification）。** 因此"agent 流程（发现 → 取证 → 核验 → 判定）"这个**架构方向是对的**，要改的是**核验的判据**：现在的判据奖励"引用"，不奖励"检验"。

这也解释了为什么 A/B 的成本结论不能用来否定这个流程：成本实验测的是"花多少换来多少覆盖"，它**没有能力**判断"认得对不对"。判断后者只能用本文这类已知答案数据。

## 四、A/B 里发现的更严重问题：引文校验冒充来源校验

`scripts/agent_flow_identification.py` 用合成大 PR（40 文件 / 13,108 diff tokens / 预算 6000，4 个已知文件散布其中）对比"文件顺序截断"与"逐单元"，gold 由构造保证。

| | arm A 截断（4 次调用） | arm B 逐单元（46 次调用） |
|---|---:|---:|
| patch 上下文可见文件 | **19 / 40**（`mod_00..mod_20`） | 40 / 40（每单元一个 prompt） |
| tokens | 53,653 | 83,649 |
| 发布 | 2 | 4 |
| 命中 gold | 2 / 2 | 2 / 2 |
| 精度 | 1.00 | 0.50 |

**关键发现（比成本对比重要得多）**：arm A 报出的第二条是 `mod_23.py:2`，逐字引用了该文件的 base/head 原文，并以 **0.95 + `ai_behavior_verified`** 发布。但 `mod_23.py` 在它的 prompt 里**只有一行元数据**（`- mod_23.py [modified] +1/-1`），**代码一个字都没有**（已用文本检索确认：`values[index] if index < len(values)`、`def lookup` 都不在 `context.text` 里，证据 bundle 也把它标为 `omitted`）。

根因在 `src/reviewer/grounded_review.py::_valid_citation` 的第二个分支：引用只要落在**被改动文件的行范围内**就算 `seen`，**即使那一行从未进入发给模型的 prompt**，随后它从快照读出真行、比对引文。于是：

- 模型可以对**没看过的代码**编造结论（并从元数据的路径 + 符号名 + 同类可见缺陷推断出"标准写法"）；
- 只要它的猜测与真实代码一致，引文就"验证通过"；
- 在真实仓库里，这意味着**关于未取到的代码的臆造结论会以 `ai_behavior_verified` 发布**。

**已修**（本轮的代码提交）：校验改为**先查来源**——引文必须出现在**已渲染给模型的证据摘录**里，或出现在**实际发送的 patch 文本**中；"只在 diff 行范围内"不再算数。新增 3 个回归测试（未发送的 hunk 引文必须被拒、已渲染摘录的引文必须通过、与快照不符的引文仍必须被拒）。

**残留问题**：引文正确 ≠ 结论正确（`safe-index` 的等价改写仍会被"验证通过"，因为它的引文是真的）。这需要下一节的见证修复。

## 五、因此，什么是真正该改的

| 层级 | 现状 | 证据 |
|---|---|---|
| 发现（discovery） | **好**：合成集 2/2、真实仓库 4/5 命中 | 本文一、二节 |
| 取证（retrieval） | 弱：证据预算用尽、真实仓库 5 条候选全因缺证据被扣 | 第二节 |
| **核验（verification）** | **坏**：① 可证明为假的结论被确认（`safe-index`）；② **未被展示的代码也能被引用并确认**（`mod_23`） | 本文一、四节 |

结论：**"发现 → 取证 → 核验 → 判定"这个 agent 架构方向是对的**；错的是**核验判据**——它奖励"引用"，不奖励"检验"，也不检查来源。这两条（来源校验已修、见证校验待做）才是提高错误识别质量的杠杆，而不是"截断还是分片"。

## 六、改进后复测（2026-10-15，P1+P2+P3 落地后）

同一套 gold 集、同一模型、同一协议，改走**生产判据**（含 playbook 召回与声明契约域）：

| | 改进前 | 改进后 |
|---|---:|---:|
| 发布数 | 4 | **2** |
| 真阳 tp | 2 | 2 |
| **假阳 fp** | **2** | **0** |
| **精确率** | **0.50** | **1.00** |
| 召回 | 2/2 = 1.00 | 2/2 = 1.00 |
| 成本 | 18,738 tokens | 17,947 tokens |

两个误报消失的机制各不相同，且都是机械的：

| case | 改进前 | 改进后 | 机制 |
|---|---|---|---|
| `safe-index` | 0.78 发布（可证明为假的误报） | **0 发布 / 1 待确认** | `witness_eval` 在 32 个赋值上找不到见证 → 阻断确认 |
| `safe-division` | 0.85 发布 | **0 发布 / 1 待确认** | 见证为 `count=None`，**违反声明的契约域** `count: int` → 不算见证 |
| `division` | 0.90 发布 | 0.85 发布（带见证 `count=0`） | 真缺陷，见证成立 |
| `negative-index` | 0.97 发布 | 0.97 发布（带见证 `index=-4`） | 真缺陷，见证成立 |

判据版本从 `grounded-v1` 变为 `grounded-v3: chain+provenance+witness+contract`；报告里每条候选都带 `filter_verdicts`（各过滤器的通过与理由）。

**离线门禁**：`scripts/criteria_gate.py`（无模型、无网络、无 key）断言四个 case 各自路由正确，已作为命名步骤进 CI。

**边界（必须如实说）**：n=4 的合成集不是精确率估计；这里真正被证明的是**两条具体误报被机械阻断**，以及真缺陷仍带见证发布。真实仓库上的精确率仍待评测集验证。
