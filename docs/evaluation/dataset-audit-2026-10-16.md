# ReviewBench 数据与知识库来源审计

日期：2026-10-16。范围限于数据版本、分组、评测产物及 playbook 来源；未运行模型、裁判或目标仓库代码，也没有把 holdout 标签用于知识条目。

## 数据版本与本地资产

- 当前官方源：[review-bench/ReviewBench](https://github.com/review-bench/ReviewBench)，2026-10-16 只读核对的 `main` HEAD 为 [`2e45130`](https://github.com/review-bench/ReviewBench/tree/2e45130f1f6bbdd33747a6963727051009754399)。实际评测固定版本是 `968291d99e82b3e2969d7e0b4acca62585886c13`；已有的官方仓库检出 HEAD `c17b05be9c90b85a1fc012ce39e455e2d09a7255`。
- 对当前 HEAD 与 `c17b05b` 的 219 个 `golden/*.json` 作了逐文件内容哈希聚合，摘要相同：`bb98ec952cafe2a4a4a9bca29151d8cd621c3f29e0e850155da1c7b1ea5b8451`。两版都可复算为 **219 PR、2623 条 TP**，按类别为 correctness 1068、maintainability 365、reliability 362、documentation 306、testing 223、security 98、api-architecture 81、performance 63、accessibility 57。TP 难度 easy/medium/hard 为 1311/1073/239（50.0%/40.9%/9.1%）；`introduced-by-pr` 为 2391/2623（91.2%）。总和与标签数相符。
- 219 个 case id 无重复，分布在 187 个仓库；其中 18 个仓库含多个 PR。旧 25 PR 基线覆盖 25 个不同仓库，因此它本身没有仓库交叉，但仍然污染这些具体 PR 及其仓库。
- 数据当前位于仓库外的同级 `../eval-data/`：官方检出、219 份 gold、25 份准备输入（均有 `diff.patch` 与 `pr.json`）以及 10 个仓库镜像。只有 6 个准备输入目录含 `project/`，它们也没有 `.git`；不能说 25 个仓库快照都已在本机。`prepared/prepared.json` 固定 benchmark commit 并有 25 条记录；dev/one 子清单分别为 9/1 条，所有 `input_dir` 现存。另有 25 份忽略的输入副本在 `reports/evaluation/reviewbench-official/`。这些资产不受本仓库 Git 版本控制；同级目录 README 记载其由临时目录复制，旧临时目录目前仍存在，但临时目录不构成耐久备份。README 的镜像数量说明已过期（实际目录 10 个，不是文档所说的 1/25）。迁移或清理前应保留固定版本与文件哈希，并更新资产清单。
- `datasets/dev/split.json` 是 2026-10-15 按仓库划分的当前名单：dev 9 PR / 117 TP，holdout 15 PR / 224 TP，另排除 1 PR / 11 TP（上游 head 已改写）。三组 TP 合计 352；dev 与 holdout 无 case 重叠，25 个准备输入均有对应归属或排除记录。

## 分组污染与评测数字

**15 个 holdout 不是未见测试。** 2026-10-13 的 25 PR 基线与 Critic 已经运行并查过评分；它们正是之后切成 dev、holdout、excluded 的同一批 25 个 case。按仓库划分避免了这批内部的同仓交叉，却不能撤销此前对 holdout 的模型暴露和标注分析。它们只能作为已见集回归。14 例 `scripts/holdout_eval.py` 则是合成的表达式判据 fixture（7 个期望 witness、7 个期望等价），测的是 witness 判据，不是 ReviewBench、模型发现能力或真实 PR holdout。

83.3% / 11.6% / 18.6% 可以在忽略目录 `reports/real-run/b7b-diagnostic.json` 中复算，但该产物不是完整实验记录：入口 `scripts/reviewbench_real_eval.py` 过滤当前 dev 并按 gold 数从小到大选取 5 个 case，含 43 条 gold TP。每个 case 用 greedy raw match：同文件且预测行落在 gold 行区间即匹配；这不是人工裁定或官方 LLM judge，也不校验语义、因果、引入性或反证。该规则会把“恰好落在 gold 行”的无关评论算成 TP，也会漏掉真实但定位在相邻行/不同文件的评论。

| 指标 | 可复算结果 | 口径 |
| --- | ---: | --- |
| 发布精确率 | 5/6 = 83.3% | 5 条发布建议 raw-matched，1 条未匹配 |
| 发布召回 | 5/43 = 11.63% | 已发布并匹配的 gold TP / 43 |
| 识别召回 | 8/43 = 18.60% | 发布匹配 5 + 6 条 pending 中另有 3 条定位匹配 / 43 |
| 宏平均精确率 / 召回 | 50.0% / 13.33% | 5 个 PR 的逐案指标平均，显示任务间差异很大 |

产物合计 5 个 PR、6 条发布、6 条 pending、43 个 gold TP、301,797 tokens。可给 `5/6` 一个描述性的 Wilson 95% 二项区间约 43.7%–97.0%，但它不能代表整体性能：分母只有 6 条建议，且样本只含 5 个 PR、按 gold 数择取，发现还在 PR 内聚类。没有足够独立 PR 来给出可信的总体区间或泛化结论。

这份 compact 产物没有记录模型实际响应名、完整运行配置、请求/重试历史、token 分解、judge 费用或金额；因此上述 tokens 不能证明所有尝试的费用均已计入，也无法核验本次模型与重试配置。此脚本使用 raw match，不调用 judge。不能从这份记录单独证明数值为无重试、无漏计成本的模型成绩。作比较，`reports/evaluation/reviewbench-baseline-v2-complete/` 的 25 任务审计记录模型请求名 `deepseek-v4-flash`、25 个成功主审 usage、12 条输出中 10 条 raw-matched，故也得到 10/12=83.3%；但其发布召回是 10/352=2.84%，不是 11.6%。该批配置的 `experience_count` 为 0，未注入 playbook；源码快照标为 dirty，且没有 judge 成本记录。两组结果不可合并。

## Playbook 来源核验

当前 [playbook](../../knowledge/playbook.md) 有 6 条且都标成 `authoritative`。用户给出的新规范要求真实 gold 的 case id、文件和行号、TP 状态、反证边界与类别标签，并先以 candidate 状态评估；现有条目不满足这套可追溯格式。

| 条目 | 当前来源证据 | 审计结论 |
| --- | --- | --- |
| kb-0001 | 合成 `safe-index` 与 witness 实验 | 不是 ReviewBench gold，没有官方 case/file/line/category。可作为合成判据说明，不能作真实 TP 来源。 |
| kb-0002 | 一次真实运行的预算观察 | 不是 gold；“全部五个候选仍需上下文”是运行诊断，不是类别标注。原始运行与口径应单独引用。 |
| kb-0003 | 合成 `safe-division`；附了输入域声明 | 不是 ReviewBench gold，无官方 case/file/line/category。 |
| kb-0004 | 引用 dev case `ardevd_jadx-collaboration_3` | case 含 correctness/reliability TP，但 source 没有定位具体 finding，也没有 file:line；不能据此证明条目措辞对应其中某一 TP。 |
| kb-0005 | 引用 dev cases `ardevd_jadx-collaboration_3`、`mheuss_chronicle_14` | 两个 case 都有 reliability TP，但没有精确到 finding/file:line。其 `intent_symptoms` 是自定义 tag，不是 ReviewBench 类别字段。 |
| kb-0006 | 引用 dev case `PierreJanineh_TechDebtMCP_135` | case 含 security TP，但来源未指明对应 TP 的文件、行号或类别；不能验证 ReDoS 断言与某条 TP 一一对应。 |

上述三个官方来源 case 都属于这 25 个已使用 PR，而且处于 dev；本次没有从 holdout 中挑选或撰写候选。评测器不把 `golden/` 原始标签直接传入审查 prompt，但 [ReviewApplication](../../src/application/review.py) 会把召回的权威 playbook 内容渲染进 prompt。kb-0004/5/6 的案例事实因此可能泄漏回其来源 case 的重跑。b7b compact 结果没有记录 `experience_ids` 或 prompt 指纹，无法确定那次 5-case run 是否注入了这些条目；25-case complete 审计明确记录 `experience_count=0`。新回归应把来源 case 排除出评测，或在运行前冻结并哈希知识库，再用独立 repo/case 评估。

目前没有离线的“知识条目覆盖 gold”门禁。`scripts/criteria_gate.py` 是 4 个合成表达式的判据回归；`scripts/holdout_eval.py` 是 14 个合成表达式判据 fixture；`scripts/selection_recall.py` 测固定预算下的文件/证据选择，不测知识条目命中。关键词或 intent tag 命中不等于召回，更不能作为覆盖提升。`intent_symptoms` 需要明确映射到官方 `category`，最好新增独立 `category` 字段并在冻结开发集上人工审查匹配；heldout 结果须延迟到 KB 冻结后才可读。

## 首批 10 条整理计划

首批只从 9 个 dev case 的 **117 条 TP** 中挑证据；逐条在固定 base/head diff 上核对，记录真实 `case_id + file:line + category`、反证和 candidate 状态。不要用 holdout 15 个 case 或 excluded case 找条目。

| 条目槽位 | 类别 | 适用边界与筛选目标 |
| ---: | --- | --- |
| 1–4 | correctness | 只保留改动造成、可用输入/路径证明的行为变化，例如守卫、边界或错误传播；反证须覆盖等价改写和不可达输入。 |
| 5–6 | reliability | 仅在局部改动加一个调用方/入口能证实资源、异常、生命周期或并发后果时纳入；跨全仓假设留待补证。 |
| 7 | testing | 只有新行为分支有具体遗漏风险且不能由现成覆盖率工具机械判定时纳入；一般“新分支无测试”由工具负责。 |
| 8 | documentation | 只收录会误导调用者并造成实际行为/使用风险的语义偏差；可机械比较的签名/注释不入库。 |
| 9 | api-architecture | 需要一个真实调用方或契约证据，能在 1 跳内证明本次破坏的跨模块行为。 |
| 10 | security | 限定为改动新增的可利用输入/信任边界后果；合法加固、限制或修复不能因文本提及风险而反报。 |

总体 gold 中 easy 与 medium 占约 91%，但这是标签难度分布，不是条目可判断率。首批优先：easy 为 diff 局部即可验证；medium 允许单个调用方/契约跳转且能在约 6k tokens 内引用反证；hard、多入口竞态、全仓约束与多跳数据流先不写。单 hunk + 1 跳/6k 是成本上限，不是完整性保证：2026-10-13 基线 25 个 PR 有 14 个上下文被截断，说明固定预算会丢上下文；这只能证明风险存在，不能证明它单独导致低召回。上线前应在 dev 测量覆盖损失，并保留独立的新 repo/case 作为真实 holdout。

验收必须至少报告冻结前后相同 dev 样本的知识条目覆盖、真实发布 TP/FP/FN 以及 PR 聚类结果；`precision >= 0.8` 与召回需同时测，不能只用覆盖或关键词命中通过。heldout 仅在知识冻结、协议与模型固定后运行一次。当前数据中 15 个已污染 holdout 不可承担该角色。

## 可复查位置与命令

本审计只执行了聚合统计、哈希和源文件检查，没有调用模型、测试套件或目标仓库代码。

- 官方类别/样本量复算：`python` 读取 `../eval-data/reviewbench/golden/*.json`，只聚合 `tp_fp=tp`、`category`、`difficulty`、`scope`；当前 official latest 的 gold 文件与 `c17b05b` 哈希聚合相同。
- 分组复核：读取 `datasets/dev/split.json`，再按 case id 对官方 gold 计 TP；25 PR 旧分数见 `docs/evaluation/reviewbench-2026-10-13/run-summary.json` 与 `REPORT.md`。
- 83.3/11.6/18.6 复算：读取 `reports/real-run/b7b-diagnostic.json` 的 `summary`，对应计分函数为 `scripts/reviewbench_real_eval.py::match`；实际模型、重试与费用不可由该产物复原。
- 当前 25-case 可对照产物：`reports/evaluation/reviewbench-baseline-v2-complete/run.json`、`candidates/` 与 `audits/`；这是本地忽略目录，不是仓库追踪的再现实验包。
- Loader 与状态：`src/knowledge/playbook.py`（仅 `authoritative` 被 `recall` 选择）和 `src/application/review.py`（召回内容渲染后传给审查引擎）。
