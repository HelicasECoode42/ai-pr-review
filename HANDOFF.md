# AI PR Review 交接手册

更新日期：2026-10-15。本文件是新接手者的独立入口：记录已确认目标、仓库事实、实现边界、开发顺序和评测纪律。需求优先级以本文“当前硬约束”为准；PRD 与开发手册描述目标和阶段，旧研究计划、实验草稿与附件只作背景，不可覆盖更新的用户决定。

## 接手时先看什么

1. 先读本文件，再读 [能力 PRD](docs/PRD-review-capability-v2.md) 和 [D1–D5 开发手册](docs/review-v2-development.md)。分组当前实现和限制见 [数据流图](docs/graph-planning-dataflow.md)、[分组边界与候选算法](docs/grouping-boundaries-and-options.md)。
2. 运行 `git status --short`、`git branch --show-current`、`git log --oneline --decorate -8`，不要覆盖工作区的修改，也不要把文档快照里的状态当成当前 Git 状态。
3. 核对 PR #45、PR #43 和远端分支实际 head。本文记录的 SHA 是 2026-10-15 已核对状态；更新本文时应写明新 SHA 和核对日期，不要猜测。
4. 当前继续方向是 D3 设计与实现准备：语义切片、确定性绑定、有限边界证据、跨组核验、契约/caller 读取及可恢复阶段协议。先查失败案例和回归，不从模型数量、分层数量或部署组件数量开始。

## 当前事实快照（2026-10-15）

| 项目 | 核对结果 | 使用时的限制 |
|---|---|---|
| 当前分支 | `codex/evaluation-driven-review` | 开发继续用 `codex/` 前缀，保持 Draft PR |
| 本次交接的代码基线 | `94455e10ae6e2cee36d4c47961cae0dcb47cc5e2` | 2026-10-15 核对；后续文档提交会推进 Git HEAD，但不改变此代码基线 |
| 远端 `main` | `d99524db818494ac31afd06f9a40560808de7176` | 不直接修改或合并 main |
| PR #45 | OPEN / DRAFT，当前分支，https://github.com/HelicasECoode42/ai-pr-review/pull/45 | 不自动合并 |
| PR #43 | OPEN / DRAFT，`agent/issue-42-local-first`，head `4c3ec4781cc78cca85d38561cfe9f3d51586ee5b` | 尚未合并；先读它的实际 diff/commit，不能假设已进入 #45 或 main |
| 主体实现 | `b22702c`：公共应用边界和有界图规划 | D1/D2 核心落在该提交 |
| 兼容及文档主体 | `94455e1`：恢复旧 CLI 兼容并记录图数据流 | 与本文所述历史 CI 分开看 |
| 本地 Python 规模 | 62 个源文件、9,968 行（2026-10-15 只读统计） | 代码行数不等于运行开销，不作为加服务/数据库理由 |
| 历史验证 | 181 项 pytest、最新 CI 三项通过、最新 npm audit 0 项 | 这是先前记录，不是 2026-10-15 重新运行；改动后按范围重新验证 |

在开始编辑前，保留当前工作树所有已存在改动。此交接创建时已有 `docs/PRD-review-capability-v2.md`、`docs/review-v2-development.md` 的本地修改，以及未跟踪的研究文档 `docs/grouping-boundaries-and-options.md`；它们都不能被 reset、checkout 或清理命令丢弃。后续任务若发现状态变化，先判断归属并适配。

## 当前硬约束与需求契约

### 产品定位和运行方式

- 目标是快捷、可靠的小工具，默认单机直接运行。保留 Python + TypeScript，不迁移 Java；支持 CLI、GitHub Actions、Web、VS Code，统一底层报告契约并保留现有入口行为。
- 不堆 Agent，不把 Agent/服务/接口数量当成能力。同步运行路径应简单；仅在真实替换边界建抽象。
- 默认可恢复方案是关键阶段文件 checkpoint、原子写入、单任务锁和显式 resume。SQLite 只有在确需事务并发状态或文件方案不足时再评估。Redis、MQ、PostgreSQL、Celery、Temporal 不是默认项，也没有预定必须升级的阶段。
- 轻量文件 checkpoint 可跨本机进程恢复；GitHub Actions runner 磁盘通常随 job 结束。跨 job 恢复必须显式上传/下载 artifact，并验证仓库、base/head、策略/审查器版本，不能依赖 runner 本地文件。
- D3 的交付是审查能力和可恢复阶段边界；D4 是轻量故障措施的验收，不是购买/部署基础设施。只有真实的多机/事务并发需求或实测单机瓶颈，才选择具体扩展。
- SSE/事件日志、JSONL 可用于观察和审计，但只持久化日志不等于任务能恢复。恢复需有阶段状态、输入身份、结果产物、原子提交/校验、锁与显式恢复路径。
- 模型远端调用成功但本地结果未知时，不自动无限重发。费用已经发生后不能事务回滚；结果未知要显式记录并由预算/用户决策处理。MQ 只负责分发/恢复，不能证明审查质量，也不能让单次推理天然更快。

### 发现、核验和判定

- Bug/回归要说明实际或合理可达触发输入、可达入口及路径、预期/实际行为、变更为何引入或加重问题，并用固定版本原文、契约、测试、调用方或配置支撑。无需声称所有路径已穷举，也不能臆造不存在的入口。
- 信息不足进入 `needs_context` / pending；只有明确反证才能 dismissed。没有仓库内 caller 不等于无效：公共 API、外部调用方和支持的接口契约都可能形成有效触发路径。图缺边也不能证明没有 Bug。
- 结构/maintainability 建议独立于运行时 Bug，不要求先 crash。浅封装、接口漂移、实现不完整、职责混杂、高耦合、重复/无效防御等需要指出具体结构事实、维护成本或行为后果，以及可落地的最小改进。
- 正确的外部输入校验、网络/数据库/第三方边界防御不能机械删除。内部重复防御只有在契约可靠且无额外保护价值时，才可作为结构建议。吞异常伪装成功、重试放大副作用等可成为可靠性 Bug。
- 规则信号与主 LLM 审查保持独立。主 prompt 不注入规则结论是已有设计，不得包装成新增收益。规则只作未经验证的线索；任何规则验证都不得压制独立发现。
- 多视角是任务划分，不要求每个视角独立 Agent 或独立调用。不能靠少报来提高 Precision；必须测核验误删、Recall、过度严格造成的损失。
- Evidence Gate / 原文引用 / 失败场景等已有能力不是 D3 新增成果。引用存在只证明引用/位置，不自动证明因果 Bug。

### 入口和用户可见结果

- 保持 GitHub Actions、Web、VS Code、CLI 和 Markdown/JSON ReviewReport 现有契约兼容；不要改变 CLI `ai-pr-review OWNER/REPO PR_NUMBER` 的旧用法。
- `ai-pr-review-plan` 是独立离线规划 CLI，显式传 repo/base/head/token budget/output；它不调用 LLM、不发布评论，不代表主审查已接入计划。
- ReviewReport 应携带审查快照身份。VS Code/Web 不应把旧 commit 或旧策略报告误显示为当前报告：要能核对 head/版本并显示 stale 状态。GitHub 发布前重新检查 PR head，旧 head 报告不得发布成当前报告。
- 开发者反馈可含 `Accepted`、`Rejected`、`Already handled`、`Needs context` 等状态；先去重和审核，再作开发数据。用户反馈是线索，不是 ground truth，不自动修改独立标签或训练集。

## 已实现：D1/D2（不要重做，也不要夸大）

### D1 应用边界

- `src/application/review.py`：公共 ReviewApplication / Provider 生命周期、公共模型配置和冻结仓库 reader 所有权，CLI/Web/Agent 接入。
- `src/application/protocol.py`：有类型 AgentContext、运行/阶段/预算/覆盖边界；拒绝未知字段和非法赋值。
- 主审/critic 资源释放与调用配置已有测试。结构边界有了，不代表所有业务流程已经拆分或 D3 已完成。

### D2 图规划

- `src/analyzer/review_plan.py` 实现文件级 syntax/dependency graph：Python AST，TS/TSX Tree-sitter；有界 greedy 分组、hunk 定位/fragment、lazy evidence refs；`ai-pr-review-plan` 从固定 Git base/head 读取。
- Planning token budget 当前只约束 diff fragments；定义引用是 lazy refs，不计入这个预算；prompt、系统指令、完整证据、输出/实际计费均不在当前预算内。
- 重命名在 plan CLI 当前按删除/新增处理；底层可保存 previous filename。未知/不支持语言必须标注覆盖限制。

### 已核实边界

- 当前图是文件路径级关系，不是完整函数调用图。强 import 边只说明可解析 import，不证明运行时调用；弱边是名字匹配候选。
- Python 目前定义名到文件集合并用 `ast.Call` 的名称匹配；属性调用只取尾部 `attr` 名字。TS 目前只识别直接 identifier call。尚未完成 scope 绑定、qualified symbol、alias/re-export、相对导入完整解析、类型和动态派发；实际参数对象的类型不能猜。
- 一处名称对应多个定义通常可能是模块/类命名空间差异，不等于重载、实时搜索结果或 Bug。弱匹配有数量上限（当前最多 20 个目标），不是按相关性排序。
- hunk 依行预算切分，超长行可以按字符切；保障内容次序/字节保留，不保证语义完整。`hunk_index` / `fragment_index` 已有，但完整 fragment → 原源码行范围映射、跨组桥接证据的核验流程尚未完成。
- GitHub snapshot read/search 能力比本地对象库有限。PR `base_sha` 可能不等于实际 review diff 使用的 merge-base。D3 必须从 diff 真实生成方式核对比较基点，base evidence 不匹配时标 `partial/unavailable`，不可宣称对齐。
- 对真实仓库冻结范围 `89943fe → 13e9a3e`，规划得到 22 hunks、14 units，diff 预算 1,000 tokens 时最大单元 986；238 import refs 和 970 weak refs。这只验证覆盖、确定性与预算行为；不是最佳语义分组、质量提高、token/cost 节省或 recall 证据。

## 后续实现顺序：D3 起

### D3：先静态语义绑定与证据闭包，再接模型

1. 固定 source snapshot、真实 diff 比较基点和 hunk 集合。Diff 用于定位变化；head 按需读取定义、调用方、接口契约和异常/副作用，base 按需比对旧行为、删除、重命名。不能默认 diff-only，也不盲读整个仓库/移动工作区 `HEAD`。
2. AST 中区分定义、调用点、import/export 节点。Python 先做确定的普通/相对 import、`as` alias 和局部遮蔽绑定；限定 scope、qualified symbol。TS 再渐进处理 import/export/re-export，并考虑 Compiler API Symbol 或可解释的确定规则。动态调用、无法确定的参数类型或动态分派保留 unresolved/weak，不猜。
3. 将变化映射到语义单元，优先保留完整函数/方法签名、条件/分支范围、异常处理和副作用相关行。预算不足时明确拆 fragment/partial；保留 source line/range mapping。契约变化追到有界 caller/consumer；外部 caller 是 coverage gap，不因此自动否定。
4. 对不可避免的切分生成有限 boundary evidence，跨组汇总核验；不能复制整个仓库上下文到每一组，也不能把图里没有边解释为安全。
5. 可评测一次受预算约束的低成本 LLM 元数据分组建议：只给路径/status/counts/必要签名/导入概要，可选 PR 描述，不传完整源码/完整 diff。宿主核验覆盖、unknown/重复成员、硬预算、强边保留或边界报告；无效/失败回退确定性静态分组。这是候选方案，当前未实现，不是“全流程 all-in LLM”。
6. 第一轮只对照逐文件、静态图、静态图 + 一次元数据建议。加权 greedy + local swap 可当有证据后候选；Leiden、hypergraph 等仅研究方向，不因知名或复杂就先引依赖。Graph cut 等结构指标不能代替 issue recall。

### D4：轻量可恢复运行

优先关键阶段文件 checkpoint + 原子替换 + 单任务锁 + 显式 resume；文件保存阶段输入身份、状态、产物引用、错误/预算。输入需含 repo/base/head、审查器/策略版本、模型配置与阶段身份；身份不匹配不可复用。模型发现/核验是昂贵产物优先持久化对象；Git 读取/AST 索引如可廉价确定性重算，不逐函数 checkpoint。只有文件方案不足以满足真实事务并发时再测 SQLite。没有实测多 worker/多机器/调度瓶颈，不加 MQ/Redis/PostgreSQL。GitHub Actions 跨 job artifact 必须显式保存/恢复并校验 snapshot 和 strategy。

D4 验收：原子写入中断、损坏/缺失产物、锁冲突、重复 resume、策略/快照 mismatch、完成阶段复用、取消、旧报告 stale、发布前 head 改变。远端模型结果未知要停在明确状态，禁止无限自动付费重试。只有后端被实际选择后，才增加相应数据库/队列故障测试。

## 评测、费用和证据纪律

- 历史 ReviewBench 公开开发集结果：TP 352 条中约 3.98%（约 14/352）的历史 grounded baseline；Critic 试验约 3.69%（13/352），误删 2 条 TP 并保留 FP。它不是 clean、同预算的最终基准，不能据此宣称模型类别效果或全面提升。精确记录与条件查 [evaluation-driven-review](docs/evaluation-driven-review.md) 及 [小批量 Flash 记录](docs/evaluation/flash-small-2026-10-14.md)。
- 最近单 PR Flash 对照：旧流程 0 项；新流程 1 条系统 confirmed、2 条 pending；人工认为 confirmed 缺入口证据，因此不能计 TP，也不能据此宣称提升。
- 按 repo/PR/提交切分留出集，避免同一个缺陷或相邻提交泄漏到开发集和测试集。发现模型不得读取 gold labels；开发集标签可供开发集诊断，但不得作为发现输入。独立 evaluator/judge 按固定协议读取参考标签；留出测试标签只能用于最终评测，不能参与调优。构造 fixture 只测试规则边界，不能替代真实正负样本；“没证实有 bug”不等于正确负例。
- 报告 micro/macro Precision/Recall、类别、跨文件/新问题 Recall、过度核验/TP 误删、needs_context 中确认成立问题、失败/partial、成本、时延、缓存和覆盖。分别报告 Bug 与结构建议。模型/API 失败不能变成空 finding。
- 同时测启动耗时、依赖安装、内存、服务数/运维步骤；小工具价值也在轻量部署。示例数字 P/R 0.65 等若只是附件演示，必须标“合成示例”，不可写入真实结果。
- 每次真实模型试验固化 source SHA、repo、策略、prompt、请求模型/实际模型、reasoning、输入/缓存/输出/reasoning tokens、调用次数、费用和时延。DeepSeek Flash alias、现余额、价格和可用模型会变动，付费新实验之前用官方/实际配置核实；旧 key 曾查到 1.30 只代表当时该 key，不能推断充值余额或免费。用户当前仅授权小批量实验，不等于授权全 25 条付费运行。
- 用户曾充值 10 元是历史信息，不推断款项去向。遇到 402 停止该次付费路径，报告错误并给可行的无付费工作；余额/充值意图不明确时不消费另一账户。

三种“缓存”需分别计算：应用源码/索引 cache（CPU）；阶段产物 cache（避免模型调用）；provider prefix cache（输入仍是 logical tokens，按供应商实际 cache token/价格核算）。供应商内部 KV 由供应商控制，不能假称放在本地 Redis。稳定 prompt 前缀、动态内容后置、工具输出有界、核验前去重、最终报告引用原文可作为待验证优化；必须记录实际命中/调用量，不能编造节省比例。

自动自优化是 D3/D4/D5 和稳定评测之后的候选实验。GEPA、Reflexion、ADAS 只能走受约束 Diagnose → Propose → Evaluate → Accept/Rollback，初期仅优化 prompt/context/config；禁止触碰 gold labels/scorer、越权生产改写。DGM 若研究，放在独立隔离审查，不能先行进入产品。不要把未完成研究或未验证假设包装成项目业绩。

Langfuse 可作可选观测平台学习项，不默认安装其服务栈；先检查当前日志、usage/span 是否已经解决具体排障需求。若现有本地日志足够，保留轻量实现。

## 仓库结构和阅读路线

按调用链阅读，不从 `src` 目录平铺浏览。建议顺序：

1. `src/models.py`、`src/utils/config.py`：Report、PR/file/suggestion 数据契约和配置。
2. `src/github/client.py`、`src/analyzer/diff_parser.py`：GitHub 元数据、patch 和 changed lines。
3. `src/application/review.py`、`src/application/protocol.py`：统一入口、Provider 资源边界、类型协议。
4. `src/reviewer/engine.py`、`grounded_review.py`、`evidence_validator.py`、`signal_verifier.py`、`critic.py`、`suggestion_filter.py`、`review_merger.py`：发现、引用、独立规则信号、核验/过滤/汇总。
5. `src/analyzer/repository_context.py`、`context_builder.py`、`cross_file.py`、`review_plan.py`：上下文、文件级关系、图规划；plan CLI 独立，别误以为主 review 已消费它。
6. `src/agent/`：策略、Runner、状态与 trace。
7. `src/cli/`、`src/service/`、`vscode-extension/`、`src/output/`、`src/utils/actions.py`：入口、SSE/API、IDE、报告格式、Action 发布。核验 stale/head/checkpoint 的传递。
8. 最后读 `tests/` 对应行为、CI workflow 和 `docs/evaluation/` 数据生成说明。

本地熟悉/教学材料：若当前机器可访问 `/Users/wangyufan/Desktop/DOVideo-AI`，重点参考 `VideoChunkingService`、`AgentLoopService`、`AgentCheckpointRepository` 的职责、原始证据、失败补检索和 checkpoint 思路；不抄 Java，不移植 MQ 全栈。本机已解压 EPUB《深入理解 AI Agent：设计原理与工程实践》位于 `/Users/wangyufan/Desktop/深入理解 AI Agent：设计原理与工程实践.epub`（该路径是本机材料，不保证在其他环境存在）；可从 `EPUB/nav.xhtml` / `ncx` 找缓存、压缩章节，重要结论仍需官方资料复核。

## 学习仓库与技术比较（不是产品承诺）

| 项目/资料 | 值得看 | 不可推断 |
|---|---|---|
| [Alibaba OpenCodeReview](https://github.com/alibaba/open-code-review) [`grouping.go`](https://github.com/alibaba/open-code-review/blob/main/internal/agent/grouping.go)、[architecture](https://github.com/alibaba/open-code-review/blob/main/pages/src/content/docs/en/architecture.md) | metadata LLM 分组及宿主覆盖/预算/fallback guards；有界 goroutine；本地 append-only JSONL，viewer 读文件 | 不是纯确定性，也不是全流程 LLM；JSONL 不自动等于可恢复 checkpoint；不是所有产品都应照搬 |
| [AACR-Bench](https://github.com/alibaba/aacr-bench) | 外部仓库级留出样本候选，核对数据/标签协议 | 不能把不同 judge/类别/分母拼成单一 Recall |
| [PR-Agent](https://github.com/The-PR-Agent/pr-agent) | Python GitHub 集成、Actions/webhook 部署 | Gunicorn worker 不证明 Celery；公开自托管不代表 Qodo 商业后端 |
| [Claude Code Review plugin](https://github.com/anthropics/claude-code/blob/main/plugins/code-review/commands/code-review.md) | 宿主内多视角、验证流程 | 插件实现不是 Anthropic 托管审查服务架构证明 |
| [Aider repomap](https://github.com/Aider-AI/aider/blob/main/aider/repomap.py) | PageRank 相关性选择预算上下文 | 不是依赖聚类或 review correctness 保证 |
| [LangChain DeepAgents](https://github.com/langchain-ai/deepagents) | filesystem/summarization 工具、大内容外置和文件工具边界 | 工具结果有引用不代表摘要无损 |
| [pi](https://github.com/earendil-works/pi) | 历史压缩工程实现 | 压缩有损，需看如何保留最近轮次/边界 |
| [LlamaIndex code parser](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/text/code.py) | AST 子节点递归 chunk，超大叶子可能硬切 | 参数存在不等于所有 AST chunk 都自动重叠 |
| [GraphRAG](https://microsoft.github.io/graphrag/)、[NetworkX community](https://networkx.org/documentation/stable/reference/algorithms/community.html)、[KaHyPar](https://kahypar.org/) | Leiden、Kernighan–Lin、容量约束图/超图算法候选 | 知识图社区不保证代码语义；结构 cut 不等于 issue Recall；当前算法没有实现这些方法 |
| [ReviewBench](https://github.com/review-bench/ReviewBench)、[Martian benchmark](https://github.com/withmartian/code-review-benchmark) | 区分离线标签与真实在线后续修复分析 | 在线“被修复”是价值 proxy，不是完美真值 |
| [Kodus self-hosted](https://kodus.io/self-hosted-ai-code-review/)、[installer compose](https://github.com/kodustech/kodus-installer/blob/main/docker-compose.yml) | 公开团队平台确实可使用 RabbitMQ/Postgres 等 | 这不是个人 CLI 的默认部署建议，也不能推断未公开 SaaS |

过去对 RSI / 自我改进的阅读可从 [evaluation-driven-review](docs/evaluation-driven-review.md) 和现有研究文档追踪；必须先落实防止标签污染、审查器过严、无效重试和不可复现实验的护栏。供应商价格、余额、当前 API 能力和“当前最优”建议都要在需要时重新核实。

## 熟悉后的开发检查单

开始一个任务前：

- [ ] 先找对应行为失败/真实案例，读当前代码及调用链；确认用户授权的模型花费范围。
- [ ] 核对 `git status`、分支和上游状态；不 reset 用户/其他 agent 文件，不从工作区 HEAD 伪装固定 source snapshot。
- [ ] 明确行为契约和影响入口（CLI、Action、Web、VS Code、report），保留已有参数/字段兼容。
- [ ] 以真实已有边界实现最小改变；先补行为回归，再讨论抽象、队列、模型多视角或自动优化。
- [ ] source 内容只读，不执行目标仓库代码或恶意测试命令；Git 证据以固定 commit/object 为准。

本地常用验证：

```bash
uv sync --frozen --extra dev
uv run pytest -q
uv run ruff check --select E9,F63,F7,F82 src tests
```

Python CI 最低规则当前为 `ruff check --select E9,F63,F7,F82 src tests`，另运行 `pytest -q`。`uv run ruff check src tests` 是更完整的全仓诊断；仓库有历史 lint 问题，不能把它与 CI 最低 gate 混为一谈，也不能把已有问题算成新改动。修改 Python 文件时，对修改文件运行完整 Ruff 并区分基线与新增问题。VS Code 变更再核对 `vscode-extension` workflow 的 `npm ci`、`npm audit --audit-level=high`、`npm run compile`。根据修改范围选择验证，不能把历史通过写成当前结果。完成后检查 `git diff --check`，记录命令、SHA、结果和没验证的边界。

验证与质量的交付不等价：181 个单测通过不证明 Review 质量提升；图分组数量/结构度量不证明召回；一个 PR 不证明普遍效果。模型失败需单独标记，不折算为 0 findings。

## 实验、提交和发布规则

- 使用固定 source commit、reviewer commit、模型请求与策略版本；对照组总预算/质量目标清楚，保存原始候选、判定和费用/usage。先内部回归和开发集，再按 repo/PR/提交留出评测。
- 问题修复源于可重现失败或需求契约。不要先写 RSI 自改代码；发现模型不得看到 gold labels，而独立 evaluator/judge 可按固定协议读取标签。留出集标签不得用于调优；自动建议需要独立 evaluator、固定样本、人工审核、可回滚。
- 已有 Conventional Commits：`feat(review)`、`fix(review)`、`docs(review)`。个人完整可验收功能可作完整 feature commit，包含必要代码/测试/文档，不为每个文件制造微型提交；已发布历史不重写。
- 保持 `codex/` 分支和 Draft PR。未经用户明确请求，不合并 main、不强推/重写他人分支、不自动发布新行为。
- 代码作者偏好：用户要求 Luna high 写代码，主 Agent 负责设计、审查和验收；后续交接应遵从该偏好，不自行换另一个模型实施。文档变更也不得覆盖其他人未提交研究。

## 变更记录方式

本文是当前需求与已确认事实入口；每个阶段完成后更新“当前快照”“实现状态”“验证记录”和“已知限制”，保留日期/commit hash。设计候选只能放候选段，做成实现须附代码位置和验证。新的用户决定优先于旧文档；更新 PRD 和开发手册时同步同一口径，避免一个说 SQLite 默认、另一个说文件默认。旧实验、FAQ、简报可以保留为历史，不应删除或冒充最新约束。
