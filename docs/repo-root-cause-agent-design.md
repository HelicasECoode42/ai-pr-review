# PR 审查中的根因调查 Agent：设计草案

状态：架构草案及阶段 0/1 局部实现记录；v1 Python 符号索引当时未接主链。后续阶段 2 接线应单独追加版本记录；本文不代表端到端效果或真实模型验证。范围仅为现有 PR Review 的跨文件归因；全仓 audit 作为独立后续入口。本方案参考腾讯云文章描述的问题治理模式，不声称复用其代码或未公开实现。

## 目标与边界

让 PR 审查能从变更出发，用有界、可追溯的跨文件证据回答“这次改动是否引入了此问题”，并区分已证实、待补证和被反证的候选。每条结论绑定冻结的 base/head、证据范围与判据版本。

首版入口是现有 CLI、GitHub Actions 与 Web Review；VS Code 扩展保持冻结，不纳入入口或客户端改造。继续使用单机 Python 服务，不增数据库、消息队列、图数据库、向量库、常驻语言服务或外部后台。PR 模式复用单个调查协调器；不预建多 Agent/子 Agent 框架。全仓 audit 有不同的输入规模、成本和报告契约，不能默认为 PR 模式多扫一遍。

静态关系图是“去哪找证据”的导航索引，不代表运行时一定可达，也不证明缺陷成立。Python 首个端到端范围限于解析成功的函数/类、静态 import 绑定与别名、显式属性访问/调用；动态导入、反射、运行时 monkey patch、复杂 descriptor 等明确列为未解析。TypeScript 可沿用现有解析结果作为候选；Go 等尚不支持的语言只保留 lexical 线索和 coverage 缺口，不宣称调用图完整或跨语言效果。

## 来源与借鉴边界

腾讯文章公开描述了共享拓扑/业务知识/代码底座，主 Agent 规划、子 Agent 单点取证、先广度缩圈再定向多跳、证据支撑根因结论，以及经验经核验后回写。文章没有给出其代码、解析器、存储 schema、模型协议或可直接复用接口，本方案不推断这些实现。我们借鉴的是“关系导航 + 分阶段取证 + 证据收敛 + 人工门控知识改进”的结构。

本仓库已有可复用能力：`GitHubSnapshot`/`GitSnapshot` 冻结读取与搜索、`ReviewPlan`/`ReviewUnit`/`ChangeSlice` 切分和 coverage、Python 与 TypeScript 的有限静态解析、grounded hypothesis 与绑定的 `read`/`search` 请求、预算/省略 trace、criteria/evidence validation、候选 decision ledger、unit checkpoint/cancel。现有 `Relationship(strength=strong|weak)` 尚不能充分表达绑定来源、置信级别和歧义；当前文本 `search(symbol)` 也不是可靠调用图。首轮需做契约适配与增量，不复制一套 snapshot、budget、StageInput、decision ledger 或已有协议。

## 运行时数据流

```text
冻结 PR 输入(base/head SHA)
  → 变化切片与语义 review units（现有 plan）
  → 按需构建/读取只读符号索引（每个 side 独立）
  → 选择高价值变更单元并建立调查任务
  → 协调器提出假设、按需申请证据
  → host 工具校验、执行、计量、返回证据或明确失败/省略
  → 对每个候选核验 head 触发条件及 base 对照
  → confirmed / pending / dismissed + 可审计报告与 trace
```

协调器顺序执行并共享结构化任务状态，不共享长篇隐式对话状态。并行仅保留现有可控 unit 执行；同一候选的调查先不并行化。静态图可找到声明、绑定和调用点，最终因果需要 head/base 行为、契约、有效反例或机械 witness；单独存在一条边不能提升 finding 为 confirmed。报告必须区分代码变更导致的回归、被改动放大的既存问题、未改变的旧问题和有意行为变化。

### 关系解析与按需范围

1. 从变更 hunk 定位语义符号；解析失败、跨符号切片或超大定义均保留在 coverage 中，不静默丢弃。
2. 先从冻结树搜索候选文件，再解析候选文件中的定义、import 模块路径与绑定别名；按词法作用域区分同名符号。仅在绑定唯一时生成强边；同名文本、动态属性、模糊导出为弱/ambiguous 线索并显式标注。逆向 caller 查询受候选文件范围、文件数、字节数与超时限制，必须返回 `searched_scope`、`completeness` 和 `omitted`。搜索未发现调用点只能表示“在已搜索范围内未发现”，不能表示不存在调用者。
3. 对 changed symbol 按需取一跳 callers/callees 和定义。仅当证据提出有依据的传播假设，且余下预算允许时，再扩展下一跳。设置每任务深度、节点、返回行数和总字节上限；禁止全仓全文件提示拼接。
4. 关系索引可以先对已变更文件及其依赖邻域按需解析。全量符号盘点若没有可复算收益，不成为首版前置条件。

## 数据契约草案

下列字段是序列化契约提案，不表示已经新增类型。优先扩展 `src/analyzer/review_plan.py` 的近似类型或在其上适配，不平行建立另一套 finding/report schema。

```text
SnapshotIdentity {
  repo_id, base_sha, head_sha
}
RunIdentity {
  run_id, snapshot: SnapshotIdentity, tree_schema_version, parser_set_version,
  policy_version, criteria_version
}
SymbolRef {
  snapshot: SnapshotIdentity, side: base|head, path, language,
  qualified_scope, kind, definition_start, definition_end, content_sha256
  # symbol_id = hash(snapshot + side + path + qualified_scope + definition range)
}
Relationship {
  source: SymbolRef, target: SymbolRef?, kind: imports|binds|calls|contains|reads|writes,
  provenance: parser_rule|import_binding|lexical_search|user_input,
  evidence_level: exact_binding|resolved_binding|ambiguous|lexical, strength: strong|weak,
  source_range, target_range?, limitations[]
}
EvidenceRef {
  snapshot: SnapshotIdentity, path, side, start_line, end_line, sha256, role,
  visibility: returned|partial|not_returned, originating_task_id?
}
Hypothesis {
  hypothesis_id, candidate_id?, statement, trigger, expected_vs_actual,
  source_refs[], requested_unknowns[], status: proposed|supported|refuted|unresolved
}
InvestigationTask {
  task_id, run: RunIdentity, hypothesis_ids[], operation, args,
  budget_reservation, state: queued|running|succeeded|partial|failed|cancelled|budget_skipped,
  attempt, tool_version, evidence_refs[], omission?, error_code?
}
InvestigationResult {
  task_id, state, evidence_refs[], relation_refs[], coverage, budget_used,
  errors[], omissions[]
}
FindingAttribution {
  candidate_id, anchor: EvidenceRef, base_refs[], head_refs[], relation_refs[],
  hypothesis_ids[], verdict: confirmed|pending|dismissed,
  attribution: introduced|exacerbated|preexisting|intentional|unknown,
  criteria_version, reason_codes[], missing_evidence[], trace_id
}
```

`EvidenceRef` 必须绑定原始 side/range/hash；只有 `returned` 可作为模型引用，`partial` 仅能引用实际返回的子范围，`not_returned` 只能说明缺口。`SymbolRef`/边两端必须带 snapshot identity。`evidence_level` 是证据类别，不是概率或置信百分比。base/head 符号对应只能作为可拒绝的匹配；rename/move 或同名歧义不能靠名称硬配。base 与 head 独立构建与缓存，禁止跨 SHA、parser 版本或策略版本复用。文件读失败、parse failure、搜索受限、深度/预算命中、取消都要有显式状态，部分结果不能冒充完整图。

### 缓存和失效

解析缓存 key 为 `(repo_id, commit_sha, path, content_sha256, parser_set_version)`；关系缓存再加 `tree_schema_version`。`RunIdentity` 绑定 base/head、策略和判据版本。快照不可变，因此同 SHA 内容可复用解析结果；PR 的 base/head 绑定、报告与任务不可重绑。head 改变或 PR 更新即创建新 run identity、取消旧任务、拒收迟到结果；base 变化同样使因果对照失效。依赖版本变化只使解析/关系缓存失效，不改变旧 trace。首版缓存可为进程内有界 LRU；checkpoint 的持久化和恢复继续沿用当前身份校验，不能从不匹配身份恢复旧证据。

## 工具与模型分工

Host 内部的最小只读工具接口：

```text
read(path, side, line_range, max_bytes) -> EvidenceRef + bounded text + status
search(query, side, scope, limit) -> match refs + completeness/omissions
resolve(path, side, line|name) -> SymbolRef? + ambiguity + coverage
callers(SymbolRef, side, depth=1, limit) -> Relationship[] + omissions
callees(SymbolRef, side, depth=1, limit) -> Relationship[] + omissions
```

`read`/`search` 与既有 grounded 协议对应。`resolve`、`callers`、`callees` 首先由 host 内部从确定性索引提供给协调器，或由协调器通过已有受约束的 `read`/`search` 申请后由 host 解析；不必把五个端点全暴露给模型。单独暴露 `callers` / `callees` 只在同预算离线对照证明能增加语义发现或减少冗余证据后再评估。模型负责提出可证伪假设、选择缺失证据、解释行为链；host 负责快照身份、解析绑定、权限、路径校验、预算、机械校验、排序去重、因果判据和最终状态，模型不能自判成功或扩大预算。

## 预算、失败与恢复

每次运行一个总 token/成本预检，之下分配给语义变更单元、索引构建、调查任务和候选核验的子预算；prompt 内相同证据可去重，但费用不因此去重。每次 HTTP 请求都记录实际输入/输出 token、供应商缓存 token/费用（若返回），不假设供应商 KV 前缀复用；本地索引扫描也记录文件数、字节和时延。调用数、估算/实际 token、缓存命中、读字节、解析文件数、深度、任务终态、截断和遗漏都进现有 budget/trace。不得以“索引是确定性的”名义把扫描成本置零。开始任何 LLM 请求前先核查预留；并发总数、单候选轮数、每轮工具数、单文件 bytes、总 bytes 和最大墙钟时间均有配置上限。超限时跳过并产出 pending/partial，不自动补预算或重试到超限。

失败策略：

- 无效路径、越界/错误 side、未知假设 ID：拒绝请求并记录安全 reason code。
- 解析错误、动态绑定或同名歧义：回传 unsupported/ambiguous 和 coverage gap；不伪装成“没有调用者”。
- IO 超时、限额、provider 错误：保留已完成证据，标记 partial；同一幂等 task 可在预算内有限重试，attempt 与错误可查。
- 取消：停止新任务，活动任务在安全点结束；所有计划任务均有终态。取消后不恢复为新 head 下的运行。
- 进程中断：通过现有 run/unit checkpoint 恢复仅限完全匹配 snapshot/task/policy 身份的已落盘结果；崩溃发生在响应后、落盘前时可能重复调用，不承诺 exactly-once。
- base/head/tree/parser/policy/criteria 任一身份不匹配：缓存或 checkpoint 拒绝复用，重新规划或停止并要求新 run。

## 实施计划（仅方案，待评审）

| 阶段 | 文件触点（目标文件，实施时重新核对） | 内容 | 离线验收与退出条件 |
|---|---|---|---|
| 0. 契约对齐 | `src/analyzer/review_plan.py`, `src/analyzer/repository_context.py`, `src/reviewer/grounded_review.py`, `docs/HARNESS.md` | 用现存 snapshot、plan、intent task、budget/trace 写一条调用链映射；补清身份、关系 provenance/confidence、coverage | 针对 Python 静态绑定/别名/作用域、同名歧义、parse failure、base/head 独立性的最小 fixture；旧接口行为与 H1-H15 不退化；未满足则不进阶段 1 |
| 1. 单语言索引增量 | `src/analyzer/review_plan.py` 或小型 `src/analyzer/symbol_index.py`；对应 `tests/` | Python 可解析节点的绑定索引与按需 resolve/callers/callees；限深；明确未解析符号 | 对照手工列举 fixture 关系，边 provenance 与 refs 可复核；不存在的/动态调用显式 unresolved；读文件和输出受上限限制 |
| 2. PR 调查适配 | `src/reviewer/grounded_review.py`, `src/application/review.py`, 必要时 `src/reviewer/prompt.py` | 将 index 作为 host 侧只读证据来源接入现有单协调器；仅在现有 hypothesis-bound 流程请求时补证；不改默认策略的发布阈值 | fake-provider/replay 可复算 task→EvidenceRef→candidate；不增加新协议/重复轮次；预算与 omission trace 闭合；base/head 反例阻断错误归因 |
| 3. 评估门槛 | `scripts/evaluation/`、`docs/evaluation/` 新增窄范围离线材料 | 先跑同输入、同预算的离线可见性/证据路径审计，再比较现有默认路径与索引路径；一个变量一次 | 同预算报告 D_sem、P、pending、precision/recall（若有合格盲裁金标）、调用/Token/时延/遗漏；离线不能证明模型效果；任何提升需独立裁决和用户另行授权的真实模型试验 |
| 后续另案 | 独立 PR | TypeScript 绑定精度、Go 等其他语言、全仓 audit、并行子 Agent、知识候选晋升/RSI | 需各自需求、成本基线和评审；不由本设计自动授权或纳入当前实现 |

### 现有入口对接草案

CLI、GHA 与 Web 继续调用现有 review 用例/endpoint，不新增服务入口。初步参数仅供接口讨论：现有 review 请求可选 `investigation=off|static_v1`（默认 `off`），`static_v1` 的有效范围、总预算和上限由服务端策略确定；CLI 可映射同一字段，GHA workflow input 可选暴露显式 opt-in。字段名与挂载层需实施前核对现有 CLI/HTTP/GHA schema，避免各入口另定义策略或绕开默认 Runner。Web 复用现有 review endpoint，不新增 URL。该草案不构成已批准的默认行为或新 API 契约。

### 比较基线与非目标

离线对照固定 base/head 和 gold、provider fixture、策略/criteria 版本、总上下文/调用预算及并发；仅改变索引证据是否可用。分别保存候选、证据引用、遗漏、成本账本和人工裁决，不在 holdout 上调参，不用文件/行位置重合替代语义命中。先验证单元与工具能否无损覆盖，再谈模型 D/P；静态 coverage 提升不能写成缺陷发现提升。若没有当前有效的 dev gold 和完整裁决，报告“数据不足”，不得沿用缺失的 6-dev/145 finding 数据或把早期两例 raw 30.4% 宣称为当前效果。

原设计草案只定义方案，不调用真实模型或付费 API，不改评测/gold、不发布/写 GitHub、不提交。后续获批的 v1 实现仅增加窄范围 Python 符号索引；也不做问题发现 Agent、自动修复 Agent、线上指标/日志拓扑、经验库自动写入、运行时可达性证明、规则/提示自动晋升或全仓扫描。

## 开发期流程与运行期流程

开发期按仓库既定 Task Layer 走：需求/环境确认 → 接口与影响面审查 → 确定性小范围实现（实施时明确模块 owner 与文件范围）→ 独立 agent/评审者审代码和接口 → 离线 fixture、criteria/interface/holdout 回归门禁 → 固定 dev 数据的有界真实模型配对评测 → 人工裁决、人工晋升或回滚。真实评测前冻结协议、同预算基线、变量、上限及停止条件；holdout 不用于调参。实现和评测分支需独立、可归因；原设计草案阶段不建分支、不实现。候选知识/策略仅经独立评估和显式人工批准后晋升；回滚恢复已知策略版本并保留 trace。成功的离线结构验证不得表述为线上根因能力已达标。

运行期仅是 PR Review 中的一个受控调查路径：冻结输入 → 生成有界关系索引 → 协调器提出并更新假设 → host 取证并返回含来源的片段 → 做 base/head 因果检查 → 判定并记录终态 → 输出 findings、pending 和覆盖缺口。未来全仓 audit 单独设计入口、覆盖率与预算，不能借用 PR scope 的保证。

## 版本记录

### v0 → v1：阶段 0 契约对齐与阶段 1 Python 索引

**阶段 0 已核对：** 继续复用 `Snapshot`/`GitSnapshot` 冻结读取、`ReviewPlan`/`ReviewUnit`/`ChangeSlice`/`EvidenceRef`、`Coverage`/`BudgetReport`/`Omission`。`ReviewPlan.base/head` 与冻结快照身份已有 SHA；`SourceIndexCache` 以内容和 side 缓存解析结果。旧 `Relationship` 仅有路径端点和 `strong|weak`，旧 Python 调用匹配依赖平铺名称，无法表示符号绑定、词法作用域或未解析引用。新关系仍沿用 plan 的切片、coverage、预算与路径分组，不复制 snapshot、plan 或账本。

**阶段 1 已实现：** 新增 `src/analyzer/symbol_index.py`，将 Python 声明的符号身份绑定到 base/head SHA、side、路径、限定作用域、种类、行范围和内容 hash；调用边记录 provenance、evidence level、两端 symbol ID 与调用/定义范围。静态 import alias、同文件词法绑定以及参数/赋值遮蔽进入关系结果；无法静态解析的属性/动态调用、歧义和解析失败保留为 unresolved/limitation。没有实现类继承分派；默认 `import pkg.sub` 的根名不当作 `pkg.sub` 别名，保持 unresolved，显式 `import pkg.sub as ps` 才允许按完整模块别名解析。`PythonSymbolIndex.from_plan(plan, side=...)` 会带入 `ReviewPlan.index_omissions` 与 `unresolved_references`；`query` 提供受深度与结果数上限约束的 callers/callees 查询，并返回实际 `searched_scope`、`completeness` 和 omissions。Callers 查询只要已搜索文件里存在无法绑定的调用就保守标为 partial。`complete` 只表示在 `searched_scope` 内没有已知缺口，不能据此断言仓库其他位置不存在调用者；Base 查询还标记 `base_inventory_not_enumerated`，因为 plan 只读取明确涉及的 base 文件。跨文件路径关系仅供导航，不证明运行时可达。

```mermaid
flowchart LR
    S[现有 Snapshot / GitSnapshot<br/>base/head SHA, read(side)] -->|只读冻结源码| P[现有 build_review_plan<br/>扫描文件数/字符限额]
    P -->|path, text, side, SHA| C[现有 SourceIndexCache<br/>SHA/side 纳入缓存身份]
    C -->|Python AST| X[新增 symbol_index.py<br/>SymbolRef + 绑定/未解析引用]
    X -->|有 provenance 的关系边| P
    P -->|units / relationships / coverage / budget| R[现有 ReviewPlan]
    R -->|已有消费| U[ReviewUnit 切分与 CutReport]
    R -.->|可供阶段2按需调用，尚未接线| Q[新增 PythonSymbolIndex.query<br/>callers / callees, 有界 scope]
    Q -.->|结果尚未作为证据请求接入| G[现有 grounded_review 协调器]
```

**v1 时点尚未接线：** `PythonSymbolIndex.query` 在 v1 记录时没有从 `grounded_review`、应用入口或工具协议被调用；索引结果当时不会增加模型可见证据，也未做根因 finding 的端到端因果验收。当前扫描受 `build_review_plan` 的文件数/字符上限限制，base 查询只覆盖 plan 实际读取的 base 源文件；任何未覆盖均须作为 partial/omission 处理。尚无独立 parser-set/tree-schema 版本字段，因此缓存与符号 ID 由当前 base/head SHA、side、路径和内容 hash 隔离，不能宣称跨解析器版本缓存安全。后续接线单独追加版本记录。

本版本仅证明给定 fixture 上的静态绑定/范围处理、边 provenance、查询上限和身份隔离。没有语义召回、缺陷发现、成本、线上表现或全仓完整性提升数据。阶段 2 接线另行评估如何在现有 hypothesis-bound 证据闭环内按需消费查询结果。
