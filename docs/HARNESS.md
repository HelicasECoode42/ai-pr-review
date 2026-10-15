# HARNESS：本项目的唯一规范

**这份文档是本项目唯一的规范。** 所有 `*-plan.md`、`*-design.md`、`*-review.md`、`*-summary.md` 一律**不作废即归档**，与本文冲突时以本文为准。要改规矩，改这里。

章节：**1 好 harness 的规范** · **2 现状对照** · **3 能力差的根因** · **4 架构规范** · **5 判据规范** · **6 计划** · **7 RSI 规范** · **8 改动量** · **9 非目标** · **10 文档规则**

---

## 1. 好 harness 的规范（12 条硬性条款）

每条都能被检查。**不满足就是 bug**，不是"以后优化"。

| # | 条款 | 为什么 | 怎么检查 |
|---|---|---|---|
| **H1** | **单主线**：只有一条审查链路；其它路径只能是它的降级，不得并行演进 | 两条 prompt 家族 × 两条链路 × 四个入口 = 组合爆炸，改动无法归因 | 代码里只存在一条产生建议的路径；`pytest` 只覆盖它 |
| **H2** | **输入诚实**：模型看到什么**必须可枚举**；任何截断都要显式记账 | 真实 187 文件 PR 默认只送 11% 的 hunk，而模型不知道 | 覆盖率账本：`hunks_complete/total` + `omissions` 可逐条查 |
| **H3** | **来源可证**：任何引文必须来自**已发给模型的文本** | 实测：模型对没看过的文件编造结论并被"验证通过" | 来源校验（已实现）+ 回归测试 |
| **H4** | **能证伪**：核验优先使用**可执行检查**（求值/跑测试），而非模型自述 | "跑一下"是最强证伪手段；我们的模型完全不能执行 | `witness_eval` / `run_tests` 存在且被核验调用 |
| **H5** | **判定成立**：`confirmed` 需要**见证或契约来源**；只有引文不算 | 等价改写（`safe-index`）引文真实但结论可证明为假 | 等价用例在机械上无法 confirmed |
| **H6** | **判据版本化**：每个结果带 `criteria_version`；判据改动可归因 | 判据不版本化 → 改判无法解释 | 报告含 `criteria_version`（已实现） |
| **H7** | **去向可审计**：每条候选的最终去向与**丢弃原因**可查 | "为什么这条没发布"必须能回答 | 过滤器链 + 判定记录 |
| **H8** | **失败不静默**：降级/丢弃/重试/截断都留痕 | 静默降级是本项目的历史病灶 | 全仓无 `except: pass`；每次降级有记录 |
| **H9** | **输出确定**：同一输入两次运行结果**一致** | 不确定的输出无法进 CI 门禁 | 并发下按单元顺序合并（已实现）+ 固定随机性 |
| **H10** | **预算是一等公民**：每次调用的 token 与花费可测、有上限 | 大 PR 单次 = 59 万 tokens；无上限就会烧穿 | usage/cache 指标（已实现）+ 上限配置 |
| **H11** | **门禁机械**：改进的晋升由**程序化评分 + 翻转**决定，禁止自评 | LLM 自评 = "奖励引用、不奖励检验"的老坑 | RC 门禁只用硬信号或独立裁决 |
| **H12** | **可回滚**：知识/判据的每次变更带版本与替代关系，可回退 | 不可回滚的"自我改进"比不改进更危险 | 条目 `version`/`supersedes`；注入记 `experience_ids` |

**补充约束**（来自实测教训，等权）：
- **H13** 时延与成本要有界：单个大 PR 的运行时间必须可预测（并发上限 8、逐单元预算、派发前成本预检）。
- **H14** 发布要幂等：summary 用标记去重；行内按 `(file,line,body)` 哈希去重；**记住被驳回的条目不再重复提**。
- **H15** 安全边界明确：被审仓库是**不可信数据**；执行必须沙箱（超时/无网络/只读/白名单）；对外发数据的边界写进文档。

---

## 2. 现状对照（H1–H15）

| # | 条款 | 现状 | 缺什么 |
|---|---|---|---|
| H1 | 单主线 | ❌ 两条 prompt 家族 + 两条链路 | 删 legacy（202 行） |
| H2 | 输入诚实 | ✅ 覆盖率账本 + `omissions` + 单元缺口账目 | — |
| H3 | 来源可证 | ✅ 已实现（本轮修的真漏洞） | — |
| H4 | 能证伪 | ✅ `witness_eval` 对纯表达式实际求值（AST 白名单，无 exec） | `run_tests`（沙箱子进程）未做 |
| H5 | 判定成立 | ✅ 等价改写机械阻断；见证违反声明契约也阻断 | — |
| H6 | 判据版本化 | ✅ `criteria_version` 进报告 | — |
| H7 | 去向可审计 | ✅ 过滤器链：每个过滤器带名字与理由，写进 `filter_verdicts` | 判定记录库（跨运行）未做 |
| H8 | 失败不静默 | ✅ 本轮收口（全仓 1 处有记录的 fallback） | — |
| H9 | 输出确定 | ✅ 单元并发下顺序确定 | — |
| H10 | 预算一等公民 | ✅ token/花费/缓存命中指标 + 上限 | — |
| H11 | 门禁机械 | ⚠️ 离线判据门禁已进 CI（`scripts/criteria_gate.py`）；RSI 的 RC 门禁未做 | P3 之后的 RSI 循环 |
| H12 | 可回滚 | ⚠️ playbook 条目带 `version`/`status`/`supersedes` 且注入记 `experience_ids` | 自动回滚未做 |
| H13 | 时延成本有界 | ⚠️ 有并发与预算，无派发前成本预检 | 成本预估门 |
| H14 | 发布幂等 | ⚠️ summary 去重已做；行内去重与驳回记忆未做 | 哈希去重 + 驳回记忆 |
| H15 | 安全边界 | ⚠️ 不可信数据声明 + 密钥邻近跳过 + IDE/Workflow 文档已写 | 执行沙箱、密钥输出扫描 |

**一句话现状（2026-10-15 执行 P1–P5 后）**：**H2/H3/H4/H5/H6/H7/H8/H9/H10 已立住**；H1（单主线）、H11（RSI 门禁）、H12（自动回滚）、H13（成本预检）、H14（行内去重与驳回记忆）仍未完成；`run_tests` 沙箱与采纳采集未做。

**已知成效（合成 gold 集，同一模型同一协议）**：精确率 **0.50 → 1.00**，假阳 **2 → 0**，召回保持 2/2，成本 18.7k → 17.9k tokens。详见 `docs/evaluation/identification-baseline.md` 第六节。

---

## 3. 能力差的根因（全部有实测证据）

1. **harness 抽掉了模型最强的武器——动手证伪。** 注册的 8 个"工具"是固定流水线阶段，模型只能 `read`/`search`，**不能执行**（H4）。
2. **输入被主动截断。** 187 文件 PR 原始 diff **588,648 tokens**，默认预算只送 **22/193 hunk（11%）**（H2 的账能看见它，但没解决它）。
3. **判据奖励引用、不奖励检验。** confirmed = 字段齐全 + 引文真实；于是真问题被扣（真实运行**发布 0/5**），假结论被放行（合成集**一条可证明为假的误报**），甚至**没展示过的代码也能被引用并确认**（已修来源）。
4. **"agent"是空壳。** `policy.py` 按 diff 规模确定性选阶段；没有模型驱动的循环（H1）。
5. **知识层为空。** 没有契约/不变量/项目规则/误报目录/驳回记忆 → 每次从零推理。
6. **没有采纳回路。** 不知道评论是否被处理 → 无法判断好坏 → 无法学习。
7. **变体爆炸。** 两家族 × 两链路 × 四入口。
8. **文档在代替产品。** 文档曾达 40 篇 / 8,134 行 > 测试 5,352 行，其中 10 份互相冲突的计划。

**历史教训（"之前做错了什么"）**：前几轮我把力气全花在**数据层与提示工程**（账本、覆盖率、prompt 措辞、静默降级收口），这些是**必要的诚实性工作**，但**没有一条提高识别能力**——因为它们没碰 H4/H5。方向感缺失的代价是：账做得很漂亮，能力没变强。

---

## 4. 架构规范

**分层**（Java 后端的依赖倒置，不用 Java）：

```
Transport(CLI / GitHub Actions / Web / IDE冻存)
   └─► Application(用例编排、provider 与 engine 注入、盖章版本与预算)
         └─► Domain(领域模型：DiffHunk / ReviewUnit / ReviewSuggestion / 账本 / 判据)
               └─► Infrastructure(provider / git / github / 沙箱执行器)
```
规则：domain 不依赖 transport；provider、snapshot、执行器全部注入；**入口只做 I/O 适配**。

**模型能力规范（工具面）**：工具必须声明**能力与成本**，并分三类：

| 类 | 工具 | 现状 |
|---|---|---|
| 读 | `read(path, side)` | ✅ |
| 搜 | `search(symbol, side)` | ✅（有 120KB argv 隐患） |
| **执行** | `witness_eval`（纯表达式求值）、`run_tests`（指定测试） | ❌ **P1 新增** |

**证据类型**：`changed_definition` / `related_code` / `candidate_definition` / `candidate_caller` / **`witness_result`（新）**。所有证据进同一个预算账本（`add` → `excerpts` / `omitted` / `evicted`）。

**知识层规范**：
- **L1 符号图**：定义/导入/调用方，**由冻结 SHA 重建，零 LLM**（永不过期，因为每次重生成）。
- **L2 playbook**：wiki 形态条目（front-matter + 正文），**只准增量 delta，禁止整篇重写**（防 context collapse），条目保细节（防 brevity bias）。
- **L3 检索**：按 scope 过滤后 Top-K 注入；只用关键词/别名/glob，**后加向量**；检索不做抽取。
- 过期由**机械校验**执行：条目引用的符号不存在 → 自动 `expired`。

---

## 5. 判据规范

**闭环**：`冻结输入 → 发现 → 取证 → 核验 → 判定 → 发布 → 事后学习`

**核验过滤器链（顺序固定，每环留痕）**：

```
provenance(引文来自已发送文本) → witness(求值/执行) → static(AST) → re-location(行号回定位) → dedupe → threshold
```

**三类检查**：

| 类 | 判据 | 手段 | 能否自动定对错 |
|---|---|---|---|
| A 机械 | 两表达式在某见证输入下行为不同 | **实际求值** | ✅ |
| B 静态 | 引用/签名/调用点/解包一致 | AST（不调模型） | ✅ |
| C 模型 | 语义/意图/契约 | 模型 + **必须给契约来源** | ❌ 需人工或 gold |

**规则**：A/B 出结论时以机械结论为准，模型只能补充解释。`confirmed` 门槛 = **A/B 见证成立 或 C 类给出可核验契约来源**。

**判定路由**：

| 情形 | 去向 |
|---|---|
| A/B 见证成立 | 发布行内评论 |
| C 类 + 契约来源 | 发布，标注"未经机械复算" |
| 引文合法但无见证 | 只进 summary 待确认区 |
| 来源非法 / 有有效反证 | **丢弃并记事件** |
| 证据不足 | `needs_context`，可再取一轮（上限：3 轮发现 / 2 轮核验） |

**指标定义**（避免混淆）：发现精度 = 候选中的真缺陷比例；精确率 = 报告项中命中 gold 的比例（二分图一对一）；召回 = gold 被找到的比例；**发布召回 = 真缺陷中实际发布的比例**。基线：发现精度 4/5（真实运行，手工判定）、精确 2/4、召回 2/2（合成 gold 集）、**发布召回 0/5**。

---

## 6. 计划：6 个阶段 + 1 个循环

**共 6 个阶段（P0–P5）+ RSI 循环（不是阶段：P1 之后贯穿 P3–P5）。**

```
P0 收口 ─► P1 装手 ─► P2 核验过滤器链 ─► P3 知识层 ─► P4 分片+并发 ─► P5 采纳回路+CI门禁
                └────────── RSI 循环（每轮一个 RC，翻转门禁）──────────┘
```

| # | 目标 | 验收（机械可查） | 成本 | 依赖 | 状态 |
|---|---|---|---|---|---|
| **P0** | 收口：单一主路径、文档收敛、版本契约、IDE 冻存、打 tag | `pytest` 只覆盖一条链路；文档只剩本文为规范；报告含版本字段（已做）；`git tag` 非空 | 0 | — | ⚠️ 一半 |
| **P1** | **让 harness 能动手**（最高杠杆） | `safe-index` 类等价改写**机械上无法 confirmed** ✅；越权/超时/联网用例被拒（`run_tests` 未做） | 低 | P0 | ⚠️ `witness_eval` 已完成 |
| **P2** | 核验过滤器链 + 判定记录 | 任取候选能回答"为什么没发布"；关闭任一过滤器可复现差异 | 0 | P1 | ⚠️ provenance 已做 |
| **P3** | 知识层 playbook（agent 自维护） | 注入 Top-K 后 `needs_context` 下降且误报不增；符号消失条目自动 expired ✅ | 0.2 元/轮 | P1,P2 | ⚠️ 存储/召回/过期/契约域已完成；RSI 写入未做 |
| **P4** | 分片 + 并发 | 大 PR 覆盖率 ≫11% **❌ 未达标**（251 文件实测只覆盖 9/146 单元=6%，耗时 352s、558k tokens≈4.5 元）；输出确定性 ✅ | 2–3 元/大 PR | **P1,P2** | ⚠️ 已接引擎；**性价比未达标，结论见 `graph-planning-dataflow.md`** |
| **P5** | 采纳回路 + CI 门禁 | 质量回退能让 CI 变红 ✅（`criteria_gate.py` 进 CI）；采纳率可算 ❌ | 0 | P1,P2 | ⚠️ 门禁已接线；采纳采集未做 |

**依赖铁律**：P1 是 RSI 的前置（没有硬信号，门禁只能自评）；P4 的前置是 P1/P2（核验没修好就分片 = 放大错误，实测 46 次调用只多换来 2 条误报）。

---

## 7. RSI 规范

**采用**：`AgentDevel 的发布工程循环`（[arXiv 2601.04620](https://ar5iv.labs.arxiv.org/html/2601.04620)）× `ACE 的 playbook 增量维护`（[arXiv 2510.04618](https://arxiv.org/abs/2510.04618)）× `DGM 的沙箱与人工监督`（[ICLR 2026](https://proceedings.iclr.cc/paper_files/paper/2026/hash/aa5f5e6eb6f613ec412f1d948dfa21a5-Abstract-Conference.html)）。

**循环**：`Run → Score → Critic(实现盲) → Diagnose(执行脚本) → RC → Gate(P2F/F2P) → Promote|Discard`

**硬规则**
1. **单主线，每轮只出一个 RC**；拒绝则保留当前版并记录原因。
2. **程序化评分优先**：有机械检查就用机械检查，没有才用批评者症状标签。
3. **批评者实现盲**：只看 rubric + trace + 评分，**不看 prompt/代码**，不归因、不提修法。
4. **翻转门禁**：`P2F = 0（或≤阈值）且 F2P > 0 且修复集中在声明的症状` 才晋升。
5. **playbook 只准增量**（防 context collapse）；条目保细节（防 brevity bias）。
6. **held-out 只在最后用一次**；迭代期间模型/预算/工具**冻结**。
7. **v1 只改两类对象**：playbook 条目 + 检索策略。**不改 prompt、不改代码。**
8. **停止条件提前定义**：F2P 连续两轮过小 / P2F 上升 / RC 连续被拒 / 收益集中在缩小子集。
9. **沙箱 + 人工闸门**：`candidate → authoritative` 需人工确认；发现路径不得读 gold。

**第一次迭代**：目标症状 = 等价改写被 confirmed + 真候选被扣；RC = 新增 `false_positive_pattern: equivalent_guard_rewrite` 条目 + 检索提示；期望 `F2P ≥ 1`（safe-index 翻转）、**`P2F = 0`**；成本 ≈ **0.2 元**（gold 4 例实测 18,738 tokens）。

---

## 8. 改动量（回答"要改的多吗"）

**不大：P0–P2 合计约 600 行 / 12,161 行（≈5%），不引入新依赖。**

| 阶段 | 改动 | 规模 |
|---|---|---|
| P0 | 删 legacy 路径（`two_stage.py` 89 行 + `prompt.py` 113 行）+ 调整约 15 处引用 + 文档归档 | **删 ~200 行**，改 ~15 处 |
| P1 | 新增 `src/reviewer/witness.py`（求值 + 沙箱）+ 作为证据类型接入 `EvidenceBundle` | **新增 ~250 行**，接入 ~50 行 |
| P2 | 把 `grounded_review.py`（513 行）中的核验拆成过滤器链 + 判定记录 | **重组 ~150 行** |
| P3 | playbook 存储 + `recall` + 过期机械校验 | 新增 ~200 行 |
| P4 | `ReviewUnit` 接入引擎（并发与账目已实现） | 新增 ~150 行 |
| P5 | 采纳采集 + CI 门禁接线（gold/打分器已有） | 新增 ~150 行 |

**技术栈**：现有依赖够用且克制；**只需补一个受控执行器**。**不引入**向量库/消息队列/数据库/K8s。

---

## 9. 非目标

- ❌ population/evolutionary 搜索；❌ LLM 自评当门禁；❌ 整体重写 prompt/playbook。
- ❌ v1 让 RSI 改 prompt 或改代码。
- ❌ IDE 扩展维护（已冻存，产物保留）；❌ Web Console 作为对外承诺（降级为演示）。
- ❌ 在核验修好前做分片。

---

## 10. 文档规则

**规范只允许一份——本文。** 其它文档只能是两类：

**优势文档**：`ADVANTAGES.md`（我们强在哪、为什么；主张必须带实测或可核对来源）。

**证据文档**（数据为准，不得写规范；与本文冲突时以本文为准）：
`evaluation/identification-baseline.md`（识别基线实测）、`graph-planning-dataflow.md`（预算/覆盖实测）、`evaluation/` 下的结果与协议、`code-review-practices-and-prompt-design.md`（外部实践调研）、`error-handling-policy.md`、`review-guide.md`、`architecture.md`、`functions-index.md`、`contribution-guide.md`。

**归档**（`docs/archive/`，历史记录，不再维护）：本次已归档 13 份。**待归档**：`agent-refactor-execution-plan.md`、`agent-refactor-review-and-refined-plan.md`、`rules-agent-synergy-plan.md`、`current-development-plan.md`、`review-capability-improvement-plan.md`、`web-console-demo-first-plan.md`、`sse-review-stream.md`、`video-product-brief.md`、`2026-05-29-summary.md`、`evaluation-driven-review.md`、`review-v2-development.md`、`PRD-review-capability-v2.md`、`review-research-and-development-standard.md`、`grouping-boundaries-and-options.md`、`review-slicing-and-boundaries.md`。

归档标准：① 是否已被本文取代；② 是否仍被引用（引用者先改指向）；③ 是否含唯一实验数据（有则先并入证据文档）。

**仓库瘦身（2026-10-16）**：删除零引用的 `src/evaluation/retry.py`、一次性迁移脚本 `tools/convert_to_utf8.py`、未被引用的 `demo-animation/`、以及误入库的构建产物 `vscode-extension/out/`（18 个文件，`npm run compile` 可重建）。保留：`.vsix`（冻存产品的存档件）、`tools/reviewbench/`（评测脚手架）、历史评测 JSON（被 `evaluation/dev-set.md` 引用为证据）。`src/evaluation/` 中仅被测试引用的 6 个模块（`adjudicate`/`critic_replay`/`experiment`/`improvement`/`paired`/`parallel_retry`）**保留**：它们是 RSI 翻转门禁与成对实验的现成基础设施，删除会拆掉 P3/P5 的地基；它们不在主路径上，不影响运行。

**规矩**：新增规范类内容**只加进本文**；写新文档前先问"这是规范还是证据"——是规范就改本文。
