# Review V2：唯一执行计划

更新：2026-10-16。代码基线 HEAD `e48b00d7e7593e6912e4bc357047b7d9bf09e593`；以下本轮 A/B 修复都在未提交工作区，尚未进入 HEAD。目标顺序是：**①让现有工具可信；②做跨文件意图 Agent；③用失败案例改进知识与策略**。本文件是唯一排期与验收计划；[PRD](PRD-review-capability-v2.md)只定义目标/边界，[HANDOFF](../HANDOFF.md)只做当前快照入口。旧 D1–D5、D2 图规划和 RSI 方案只作历史材料，不构成自动执行路线。

本轮只完成阶段1的 A/B 两项：Playbook 真实 PR repo、语言/path/symbol 作用域与 scoped contract 的 fail-closed 边界；最终上下文排序保留 over-budget triage 优先级。生产代码和反例测试的工作区版本 `pytest -q` 为 **387 passed**；CI 当前离线 criteria gate、14 条合成 holdout、默认 src interface audit 均通过。criteria gate 的 safe-division domain 已改为测试 fixture 显式输入，KB-0003 降为 candidate，不再被当作生产 API 契约。没有真实 PR/模型质量验证。**阶段1仍未完成**：C（冻结 Ruff/新增行归因）、启用但未接线路径门禁、完整候选账本与发布幂等仍待做；故 #46 不可标记关闭。CI 规定的最小 Ruff 规则通过；更宽 Ruff 在 `context_builder.py` 有 14 条与 HEAD 基线相同的历史 lint 提示。

## 项目目标与当前成熟度

状态含义：**已接通**=生产调用链存在；**部分**=局部实现或有确定边界；**未接**=代码/测试存在但主路径无调用；**缺证据**=不能由现有实验支持质量/收益主张。

| 目标 | 已有功能与状态 | 当前实际流程 | 目标流程与工作项 | 验收实验/产物；成本 |
|---|---|---|---|---|
| Agent | 部分：`src/agent/policy.py`按大小选 one-shot/two-stage；`src/analyzer/review_plan.py`与工具原型不等于模型可用的跨文件规划闭环 | 固定 diff → 规则/模型策略 → 发现与限定检索；大 PR 仍可能走 legacy two-stage | 输出可修订的意图假设、目标/约束/风险/未知项；据此生成有边来源的跨文件计划；按证据更新或放弃假设；未验证的计划不发布 | 同一组人工标注跨文件 PR，比当前路径测语义根因 D 和发布 P、遗漏/partial；先 fake provider/离线。真实模型须预批预算 |
| 轻便 | 部分：单机 Python + GitHub/CLI/Web 入口，报告和预算指标存在；没有生产持久恢复保证 | provider/上下文/审查阶段有多个独立预算；报告中 usage 仅覆盖实际可见响应 | 只加载相关变更与按需证据；产物引用可复用；文件 checkpoint、原子保存、显式 resume；暂不引入数据库/队列 | 同输入连续执行与进程中断恢复：不混快照、不重复已完成模型阶段、产物可复用；本地故障注入，免费 |
| 好用 | 部分：Markdown/JSON 报告与 Web/CLI 可见；partial/omission 已有一部分 | 发现、核验、发布有状态字段，但用户尚不能从一份完整账本看清所有候选去了哪里 | 每个候选显示发现→取证→核验→发布/扣下状态及原因；取消、超时、预算耗尽或能力未启用时明确报告 partial，而非“无问题” | 用合成故障逐阶段打断，检查 UI/JSON 的状态及下一步动作；免费 |
| 便宜 | 缺证据：源码缓存、provider cache 指标和局部预算存在；“缓存率”不能代替真实省费 | 当前 patch、repository evidence、模型输出/重试等预算口径不完全相同；生产全局 cost cap 与阶段结果恢复不足 | 做相同审查范围的去重证据、稳定前缀、窄 schema、工具端截断与已完成产物复用；预算耗尽则 partial，不静默少审 | 固定 PR、模型/版本、质量目标、配置；记录真实 usage/cost、重复读取/调用、延迟、Recall 与覆盖。只有质量约束相同且 usage 下降才算省钱；先离线测量方案，再审批真实请求 |
| 可靠 | 部分：source provenance、counter-evidence、witness/contract filters、`filter_verdicts` 有接通路径；静态门禁有 CI 配置 | witness 只检查受限表达式；证据匹配不证明真实入口可达；几个完整性/幂等模块未接生产 | 对每次执行校验固定 base/head、实际功能调用、每 hunk 覆盖/遗漏、工具失败状态、取消、重试身份/预算、发布前 head；运行失败或未覆盖就阻止“完整/无问题”结论 | 改 head、强制超时、缺工具、跳过路径、断电/重复 resume 等故障注入；报告必须保留失败状态；免费 |
| 自进化 | 未接：playbook 有 authoritative/candidate 概念；无自动晋升主链；回归 fixtures 不是新 PR 泛化 | 当前 authoritative 条目参与检索，candidate 不能仅凭命中/文字晋升；模型反馈也不是 gold | 失败 trace → 归因 → 带来源/适用范围/反例的候选知识 → 隔离回放 → 人工 accept/reject → 版本发布/回滚；先人工晋升，不自动改生产 | 冻结 KB 与评测版本；在开发集报告覆盖、TP/FP/FN、跨文件召回；知识冻结后用独立新 repo/case 留出集；人工审批，离线/免费门禁优先 |

## 三阶段交付

### 1. 让现有工具可信

阶段1已修 A/B（仅工作区，等独立 review）；其余待办优先修行为，不扩功能面：

1. **A 已修（工作区）—Playbook scope**：`src/knowledge/playbook.py` 按真实 repo、逐路径语言/path/symbol 匹配；无法解析或来源超预算时 unknown；`src/application/review.py` 只读相关 changed frozen source。scoped domain 不进入全 PR prompt 或扁平 witness domain，KB-0003 synthetic 契约改为 candidate；纯自然语言 KB-0004 guidance 仍可进入 prompt。定向 scope/资源预算/parse-error 反例通过；不证明契约绑定或真实 PR 质量。
2. **C 待做—lint 证据身份**：`src/analyzer/deterministic_tools.py` 对当前工作树 changed-file 路径执行 Ruff 整文件诊断，未绑定审查 head SHA，也未确认诊断行属于 PR 新增/修改行；`confidence=1.0` 代表工具发现本身确定，不是“本 PR 引入缺陷概率”。读取冻结 head，并做新增行归因；锁定/记录 Ruff 版本，缺工具和超时作为 unavailable/partial，不声称通过。
3. **B 已修（工作区）—最终上下文排序**：`src/analyzer/context_builder.py:486-505` 计算 over-budget triage 优先级，`:541-550` 最终构造 patch 时优先级置于 path/size tie-break 之前；曾被 path 排序挤掉的 flagged docs hunk 现在进入最终 context。反例同时保留普通 hunk，并确认 9 个 hunk 都有 coverage 终态；`coverage.complete` 表示账目完整，不表示所有内容完整纳入，也不等于质量提升。
4. **开关必须有行为**：对配置已启用但运行中无调用/无产物的功能 fail-fast 或报告 `unsupported/partial`；将 `scripts/interface_audit.py` 的静态接口检查与关键功能的 runtime fake-provider seam 检查分开，避免把“名称有调用”当完成。
5. **账本与发布只在接线后算完成**：`decision_ledger.py`、`publish.py` 有持久账本/指纹/驳回记忆实现，但当前未发现 `src/` 生产调用。接入每个发现候选（含去重、拒绝、验证失败和未处理候选）的唯一 run/candidate 身份，再接发布幂等；无法保证覆盖时明确标记 incomplete。特别不能把“每个已核验候选有 filter_verdicts”说成“所有模型候选都有持久记录”。

**Done 条件**：以上五类反例均能在离线测试触发对应拒绝/partial/账本事件；CLI/Action/Web 消费同一状态语义；旧路径与新路径差异有可复现报告；`git diff --check`、CI 后续由实施者跑并记录 SHA 与结果。此阶段不以测试数代替真实审查质量。成本：免费 deterministic/fake provider；不调用真实模型。

### 2. 跨文件意图 Agent

链路是：PR 描述和 diff 提供**可疑意图**，而非规范真值 → 生成 `intent hypotheses`（目标、约束、风险、未知、证据与置信依据）→ 选择相关文件/测试/调用方形成 review plan → 有界工具读取原始证据并记录 side/SHA/path/line/边来源 → 按证据修订假设 → 对候选做分类核验 → 发布 confirmed 或明确 partial/needs-context。

先做小范围关系证据：静态 import、符号、API 参数/返回契约、调用点、测试引用；每条边标 `resolved/heuristic/unresolved`，不追求“全仓完整调用图”。模型只规划下一步读取，宿主执行并限制跳数、文件数、输出量、时限和请求预算；检索结果存成同一 run 的不可变 evidence refs，按需 hydration，重复证据去重。跨文件 bug 的发布必须展示改变了什么行为、谁受影响、为何能从入口到达；仅有同名/弱边不能确认为调用关系。

**Done 条件**：先在冻结 PR 子集建立人工根因标签；比较当前方法与意图计划，在相同候选范围/每 PR 预算下报告 N/S/D/P：N=人工 gold，S=实际进入模型上下文的 gold，D=语义上发现的 gold，P=最终发布且人工判定正确的 gold；记录 FP、未读证据、partial、重复读取、tokens/cost、请求次数、时延和失败。位置命中只作为定位 proxy，需盲化人工语义裁定。先 fake provider 验工具/流程；真实模型实验先提交预算上限和冻结协议，经批准后小批执行。成本减少只有在相同覆盖与质量下实际 usage/重复调用下降才成立。

### 3. 失败案例知识闭环

每个失败案例从运行 trace 归因到可复现错误（检索漏读、边错连、提示误解、核验误删、发布错路由等），提炼 candidate 条目，附真实源 case、固定 SHA/file:line、适用范围、反证/失效条件和变更版本。候选只写隔离目录/数据集，不进入 authoritative 检索；回放后在开发集通过精度红线（建议 precision ≥0.80，同时公布 recall 和样本数）并无严重回归，才人工晋升。用 independent repo/case 做留出，任何标签/提示/KB调参均不得偷看留出；晋升后可按版本回滚。

当前边界：2026-10-16 的 [dataset audit](evaluation/dataset-audit-2026-10-16.md) 记录 219 个 ReviewBench PR / 2623 TP 的数据规模，但 25 个已接触 PR 中的 15 个 holdout 已污染；14 个 holdout_eval fixture 是表达式判据合成样例，不是真实 PR/模型留出。`b7b-diagnostic.json` 的 5 PR/43 gold、位置 raw match 83.3% precision、11.63% published recall、18.60% identified recall 是可复算的定位指标，不是独立语义裁决；紧凑 artifact 缺少完整 model/config/retry/usage 协议。selection-recall 只测选材位置 proxy，不证明模型读到、更不证明识别；这些统计不能拼成同一个漏斗。新独立留出在 KB 冻结后再建，优先真实跨文件 PR。

## 功能实现清单与状态（计划拆解入口）

| 功能点 | 现状 | 实施流程 | 依赖 | 可验收产物/判据 |
|---|---|---|---|---|
| 规则、知识 scope、lint 与冻结快照 | A 在工作区修复；C lint 归因待做 | source SHA 绑定 → 规则执行 → changed-line 归因 → scope 召回 → 报告 | 阶段1 | 冻结快照报告；scope/lint 回归反例；不可用显式状态 |
| 预算、hunk 顺序与覆盖 | B 在工作区修复，fixture 通过 | 输入清单 → triage → 最终选择 → 每 hunk 终态 → omission | 阶段1 | 超预算 fixtures 显示 triage 风险进入最终上下文；所有 hunk 有终态 |
| 候选/判定/发布账本 | 组件存在、生产未接 | candidate id → 每阶段事件 → verdict/过滤 → 发布或扣下原因 | 阶段1 | 一次 run 的完整 candidate ledger；重试/重复运行不重复发评论 |
| 跨文件 Agent 计划与取证 | 部分原型、未知真实增益 | intent hypothesis → relation plan → bounded read → evidence refs → update | 阶段2 | 冻结 PR 人工语义评测；S/D/P 独立；跨文件分层结果 |
| 可取消、限时和可恢复执行 | 部分：资源释放/阶段协议存在；生产持久恢复未实现 | run identity → stage artifact/checkpoint → cancel/timeout → 同身份 resume | 阶段1可信状态后，独立交付 | 故障注入报告；取消不发布；强杀窗口不宣称 exactly-once |
| 知识候选、隔离回放与晋升/回滚 | 未接闭环 | 失败 trace → candidate → isolated replay → human decision → version/rollback | 阶段1 ledger + 阶段2标签集 | 知识版本与来源 case；冻结开发集/独立留出结果；可回滚记录 |

## 实验协议与费用边界

- 每次对照冻结 PR base/head、代码/数据/知识库 hash、reviewer commit、提示版本、模型/参数和预算；保存完整预测与运行协议。候选汇总前由人工标根因与语义正误，评审者尽量不知道方案组别。
- 同一 case 记录 N/S/D/P：`N` gold 总数；`S` 实际上下文选择覆盖（仅离线选材时标作 selection proxy）；`D` 盲化人工判定的语义发现；`P` 人工确认且真正发布。另记 FP/FN、去重重复项、每阶段 unknown/partial。所有比例附分子/分母/样本数。
- 省 token 需 same coverage + same semantic quality threshold：记录实际 provider prompt/completion/cache tokens、费用、请求/重试、重复读取、耗时；缓存命中与逻辑 token 分开。缺 usage 计 `unknown`，不能填 0。避免通过减读 hunk 达到成本目标。
- 顺序为无模型基线/静态工具 → fake provider 接线与失败注入 → 小规模真实审查（仅核预算后）→ 对照/消融 → 独立留出。知识库、提示和模型不可同时变化而不做归因。
- 本计划本轮没有授权真实模型请求。未知模型费用、未知远端响应和已发送请求不可撤销；超过预算或取消时停止后续阶段，报告 partial。

## 收敛与暂缓

| 处理 | 候选范围 | 依据与替代路径 | 现在安全删除？ |
|---|---|---|---|
| 保留并先做接线 | `legacy two_stage` | `src/agent/policy.py:75-79` 大 PR 自动选用；须先有新方案真实对照及回滚路径 | 否 |
| 保留为实验工具 | `src/analyzer/selection.py`、`tiered_scan.py`、`scripts/selection_recall.py` | 当前选材证据不充分且排序接线有缺口；后续以主链可观测 selection 取代离线 proxy 再评估 | 否 |
| 冻结，不承诺交付 | `vscode-extension/` 与其 workflow | 产品入口后续再完善；保留安全审计，编译不作为合并门禁；无需以删除代码“瘦身” | 否 |
| 暂不删除 | `claim_check` 等独立核验原型及评测脚本 | 先核生产调用、脚本/CI/export、失败案例用途；可合并为离线工具，未完成依赖搜索前不删 | 否 |

**当前没有足够证据列出可立即安全删除的文件。** 本轮不删源码、脚本、测试、CI 或历史报告；代码量大通过停止重复路线文档和冻结非目标来收敛。`HANDOFF.md` 是短入口；本文件是唯一执行计划；PRD 不再平行排期。

## 建议 issue 草案（不代表已创建）

1. **Make current review path trustworthy before adding capabilities** — 修 scope contract、冻结 Ruff/changed-line 归因、上下文顺序、enabled-but-unwired fail-fast、candidate ledger 与发布幂等；每项附失败反例和可复现报告。验收以真实调用链/partial状态和离线故障测试为准，不以文件或测试数量完成。
2. **Build a bounded cross-file intent review experiment** — 基于冻结 PR，输出非权威 intent hypotheses、关系计划、证据 refs 与修订轨迹；工具执行限额明确；以人工语义 N/S/D/P、跨文件分层 Recall、FP 和 same-quality tokens/cost/calls/latency 对照当前流程。真实模型调用须先核准预算。
3. **Close the failure-to-knowledge loop with isolated replay** — 失败 trace 归因形成带固定源证据/反证的 candidate；禁止运行时读取 candidate；冻结开发集回放、独立留出与人工晋升/回滚。可并入 #44 的实验总计划，不重复创建评测 issue。

## 历史资料

旧阶段草案与外部参考保留于 Git 历史/已有设计材料，可作背景，不作为本计划验收证据：[历史图规划说明](graph-planning-dataflow.md)、[历史分组边界研究](grouping-boundaries-and-options.md)、[腾讯参考实验记录](evaluation/flash-small-2026-10-14.md)、[数据与 KB 评测审计](evaluation/dataset-audit-2026-10-16.md)。旧路线中的队列、SQLite、RSI 自动晋升、完整调用图、VS Code 支持均不是当前交付承诺。
