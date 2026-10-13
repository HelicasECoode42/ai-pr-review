# AI PR Review：研究结论、实施路线与开发规范

日期：2026-10-13。本文记录实施前的研究快照；后续实现和实验进度见 evaluation-driven-review.md。本文是设计建议，不代表所述功能已经开发或经过质量评测。
本轮只整理研究和规范，不继续修改审查代码、调用付费模型、合并 PR 或发布新任务。

## 1. 仓库事实与交付边界

核对远端 main：`d99524db818494ac31afd06f9a40560808de7176`。

| 对象 | 现状 | 尚未完成 |
|---|---|---|
| main | GitHub/CLI/Web/VS Code；规则旁路；one-shot/two-stage；Evidence Gate；可选 Critic；trace、降级与元信息 | 独立语义质量评测；完整目标仓库检索；设计问题审查契约 |
| Draft PR #43 | `4c3ec4781cc78cca85d38561cfe9f3d51586ee5b`；本地 staged/branch/working diff、PR URL、本地目标项目指引 | 尚未合并；不是通用代码检索引擎；指引读取路径及预算仍需验收 |
| Issue #40 | 已存在：20–30 个 PR 的人工 benchmark 与策略比较 | 验收尚未完成；沿用，不再重复创建 |
| Issue #41 | 已存在：Evidence Gate / Critic 的实际误报、漏报验证 | 验收尚未完成；沿用，不再重复创建 |
| Issue #44 / Draft PR #45 | 本轮新增评测适配和原型；`acabdc0fafd67aa7946a451928f8c27e1a571202` | 没有跑完官方25任务，没有接通官方语义 judge，没有可信质量结果，受约束优化仅为函数原型 |

#45 的四个自构造样本用于计分和流程回归，不替代外部评测。真实模型尝试是
4样本×4配置，主请求均返回 DeepSeek HTTP 402 / Insufficient Balance；未得到审查结果。
122项测试和扩展编译通过，只证明工程回归通过。

### 已解决的规则注意力问题

main 的 `review_with_ai` 构建主上下文时不传规则 findings；two-stage 调用也传空
findings。Context Builder 默认不注入规则结论；策略按规模选择，而非规则HIGH数量。
`tests/test_rules_agent_decoupling.py` 有结构测试。这证明输入隔离，不证明召回或精度提升。

### 仍有的实现缺口

1. `engine.py` 在 Evidence Gate/Critic 之后验证和合并 rule signals，新加入的
   `rule_confirmed` 建议不再通过同一验证链。`review_merger.py` 把 verifier.reason
   同时作为原因和 failure_scenario，缺少独立的因果结构。
2. `critic.py` 为候选提供对应文件 patch 的前3000字符，没有按候选位置取窗口，
   没有定义/调用方/契约等支持证据；只处理 medium及以上，不能用来验证所有设计建议。
3. `deep_review.py` 捕获异常返回空列表；`two_stage.py` 将失败空列表与正常无发现混淆。
   triage没有热点会直接返回空发现，注释声称fallback但实际没有，可能漏掉中低风险问题。
4. Evidence Gate只校验新增行引用和非空失败场景；不证明行为、不支持充分的多处证据。
5. Context Pack从工具自己的docs加载指南/函数索引；#43添加目标规则但也没有完全替换
   工具自己的项目知识。远端完整文件获取还存在head缺失时回退main/base的版本混用风险。
6. 旧architecture等文档仍写规则结论进入主模型，和main现状不一致。
7. #45只测单次主审、不覆盖生产two-stage和signal验证路径；严格内部计分不是官方计分；
   context实验没有目标guidance时是no-op，不能用来判断Context Pack是否有用。

上述是本地源码观察，未在本轮调用模型或修复代码。

## 2. 外部研究及可借鉴部分

### GitHub ReviewBench

实际阅读：README、AGENT_CONTRACT、METHODOLOGY、JUDGING、scorer.ts及test corpus。
本地研究版本：`968291d99e82b3e2969d7e0b4acca62585886c13`。

- 官方小集25个PR、352条参考发现；完整集219个PR，小集包含在完整集中，不是额外25个。
- 小集类别：correctness133、reliability58、maintainability44、testing35、security27、
  documentation21、performance12、api-architecture12、accessibility10。
- 真阳性要求正确、可验证、范围适当、可行动；不局限于运行时Bug。
- golden来自多来源发现、分类与人工复核；包含判定体系，不应把每一条原始参考评论都
  无条件当作“必须发现的真Bug”。保留原生tp_fp等标签，不用简化内部schema覆盖它们。
- 语义匹配按同一根本问题判断，行号不同不自动算错；未匹配建议还会单独分类，避免
  将新发现的真实问题直接记为FP。
- 同时报grounded和augmented precision/recall、macro/micro、类别和严重度。
  grounded recall用于固定目标的可比覆盖率；augmented recall分母会随新发现变化，
  不宜单独用于候选筛选。
- 小集是代表性调试集合，并非独立私有测试集。参与反复研究的样本不能再宣称未见测试。

来源：[仓库](https://github.com/review-bench/ReviewBench)、
[方法](https://github.com/review-bench/ReviewBench/blob/main/docs/METHODOLOGY.md)、
[计分](https://github.com/review-bench/ReviewBench/blob/main/scripts/eval/scorer.ts)、
[Judge](https://github.com/review-bench/ReviewBench/blob/main/docs/JUDGING.md)。

### 开源 PR-Agent

实际阅读repo_context、pr_reviewer、pr_code_suggestions和reflection prompts。
研究版本：`330f0813b5fe0d1f54b794077c2d02aa85a1fb63`。

Reviewer提示词要求具体触发场景、不猜测缺失上下文、不把设计偏好当缺陷。
Suggestions有后置reflection：检查原代码和建议修改是否对应、与diff是否矛盾、位置是否
正确，并对重要性评分。repo_context有配置文件预算与commit相关缓存机制。

借鉴“生成和验证分开、目标仓库指引有预算、代码与修改建议双重核对”。不照搬其
评分阈值或把模型评分当概率，更不能认为同一模型reflection就是真实行为验证。
PR-Agent不同工具的审查范围不同，不把某个prompt当作整个产品的统一保证。

来源：[Reviewer Prompt](https://github.com/The-PR-Agent/pr-agent/blob/main/pr_agent/settings/pr_reviewer_prompts.toml)、
[Reflection Prompt](https://github.com/The-PR-Agent/pr-agent/blob/main/pr_agent/settings/code_suggestions/pr_code_suggestions_reflect_prompts.toml)、
[目标上下文](https://github.com/The-PR-Agent/pr-agent/blob/main/pr_agent/algo/repo_context.py)。

### CodeRabbit

公开文档描述完整仓库隔离分析、静态工具、按需上下文探索以及Review/Verification分工。
ast-grep允许用结构模式触发项目审查指引。其slop detection是PR级提示，不阻断合并，
文档并未证明它能准确识别所有浅封装或耦合问题。闭源实现与真实误报率不能从文档验证。

借鉴“静态工具作为证据源、结构模式触发检查、仓库探索和验证职责分开”；不照搬
50+工具规模，不依据作者是否为AI而判定代码质量。

来源：[架构](https://docs.coderabbit.ai/overview/architecture)、
[AST指引](https://docs.coderabbit.ai/configuration/ast-grep-instructions)、
[Slop](https://docs.coderabbit.ai/pr-reviews/slop-detection)。

### Claude Code Review与Google审查规范

Claude托管Code Review文档描述不同检查方向、仓库上下文、后置行为验证、去重与分级。
开源code-review插件则更强调明显Bug和明确项目规范，排除主观质量建议；二者不同，
不能把公开插件当成托管服务源码。无需为了借鉴验证流程先引入大量agent。

Google指南把设计、功能、复杂度和测试列入审查，反对不必要的通用化，明确个人风格
偏好不应阻断提交。这适合作为设计审查标准，而不是只要求“代码能跑”。

来源：[托管说明](https://support.claude.com/en/articles/14233555-set-up-code-review-for-claude-code)、
[开源插件](https://github.com/anthropics/claude-code/blob/main/plugins/code-review/commands/code-review.md)、
[Google指南](https://google.github.io/eng-practices/review/reviewer/looking-for.html)。

### 候选评论真实性研究

CRJudgeBench研究看似合理但技术上错误的评论，并提出主动收集仓库证据的judge。
它是2026-09预印本，未将其论文指标视为本项目效果或已成熟标准。它补充ReviewBench
的“发现能力”评测：验证器还需要测“能不能识别错误评论”。

来源：[论文](https://arxiv.org/abs/2609.37216)。可作为后续Verifier评测参考，
不是本阶段训练模型的前置工作。

## 3. 提议的应用架构

```text
固定PR snapshot：base/head、diff、目标指引、相关源文件
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
独立Review               规则/AST/类型等扫描
行为视角+设计视角          只产候选信号或可核对事实
不读取扫描风险结论                │
        │                       ▼
        │                独立候选验证
        └───────────┬───────────┘
                    ▼
按根因合并候选，保留origin和证据
                    ▼
统一Location检查+目标上下文补充+行为/契约验证
                    ▼
confirmed / refuted / needs_context / verification_failed
                    ▼
行为缺陷 / 契约与设计问题 / 待复核信息
                    ▼
统一ReviewReport → GitHub / Web / VS Code
```

先在单次Review中明确两个视角，只有实验显示预算竞争或漏报才拆成两个调用。
图中的职责不意味着必须一职责一agent。主审使用独立请求，不复用含规则结论的对话。
检索到的原始代码是上下文；“路径HIGH、疑似SQL注入”是待验证假设，两者区分。

### 对规则误导的处理

- 保留主审零规则结论约束；路径风险不能直接改变最终severity。
- 规则保留位置、匹配片段、检测器版本；正则命中是signal，不是成立事实。
- 编译/类型错误等确定性结果保留具体诊断、配置和版本，不能和启发式一视同仁。
- Signal Verifier允许看到候选，但需从原始代码寻找反证；确认后仍必须进入统一验证。
- 合并按根因，不按同一行简单丢弃；同一行可有两个独立问题。
- 两阶段路由应使用变更规模、接口/符号变化等事实，不能被规则数量和HIGH标签牵引。

### 让“Bug成立”可审计

每个行为候选必须说明：

1. 预期行为及来源：函数契约、调用者、schema、规范或测试。
2. 可达触发条件：输入、状态、环境和实际调用入口。
3. 执行链：哪些代码如何导致结果；检查现有防护是否已经处理。
4. 实际与预期差异：错误值、异常、数据损坏、权限越界或性能代价。
5. PR归因：introduced/exacerbated/pre_existing/unknown，不能把发现旧Bug说成新引入。
6. 支持证据和反证检查；证据不足标needs_context，不能靠高confidence升级。

证据强度分为location_checked、behavior_supported、execution_reproduced。
模型agree只能算model_reasoned；不能写成已经运行复现。不是所有Bug必须执行测试；
明确可达的代码逻辑证明可支持成立。若用base通过/head失败支持回归，必须同一依赖与
配置；base也失败通常是旧问题，不可直接算PR引入。

检索优先：定义→调用方→相关测试→schema/config→项目规范；从少量可追溯片段开始。
初始预算建议每候选最多2轮请求、6个片段、4000个上下文token，都是待测工程预算。
head事实从head SHA获取，base仅用于对比，禁止找不到head时用main/base内容冒充head。
引用允许多个版本绑定的位置；评论anchor和证据位置分开，不能因调用方未修改就删
掉正确的跨文件判断。新增行只是发布锚点，支持证据可来自未变更文件。

## 4. 封装、接口、函数职责与耦合的审查标准

以问题本身判断质量，不推断代码作者。代码短、wrapper多、函数长不是充分证据。

| 类别 | 必须展示的证据 | 可用验证 | 排除的误报 |
|---|---|---|---|
| 浅封装/抽象泄漏 | 多个实际调用者仍重复同一业务判断、解析或异常转换；抽象未承担声明职责 | 调用点比对、既有契约、共享逻辑测试 | 适配层、权限边界、可替换provider、测试注入等有明确作用的薄层 |
| 接口错乱 | producer/consumer对字段、类型、null/空值、异常或版本语义有真实分歧 | schema/类型检查、消费者契约测试、真实路径证据 | 不同领域有意采用不同模型且有显式适配 |
| 函数职责不到位 | 声明后置条件未实现、结果状态丢失、异常被吞、部分执行冒充成功 | 边界测试、调用链和状态转换检查 | 合理委托给下游；只因函数短便判为不完整 |
| 耦合过高 | 新跨层依赖、循环import、直接访问内部状态或可定位的重复同步修改 | 导入图、依赖方向规范、调用者证据 | 仅fan-out高；为明确需求存在的必要依赖 |
| 过度抽象 | 新层仅透传，未提供隔离/策略/契约价值且造成可说明的调用或修改成本 | 使用点、替换点、现有实现比较 | 对明确近期需求或现有多实现进行必要抽象 |
| AI常见表面完成 | stub、未消费配置、假重试、硬编码成功、只断言不崩、失败空列表 | 配置到行为追踪、负路径测试、运行状态检查 | 模拟实现明确隔离为fixture/demo且未冒充production |

报告设置behavior_defect、contract_violation、design_concern三类，分类和严重度独立。
设计建议要给具体影响及小范围改法，不能默认“换架构”。低信心建议进入待复核，不
刷inline评论；纯风格和泛泛“建议拆函数”默认不报告。明确接口契约违例可以是严重
行为缺陷；普通设计建议默认不阻断。不要因为设计建议没有崩溃场景就让Evidence Gate
全部过滤，应使用类别对应的证据契约。

本项目自身可作为案例：深审失败返回[]是职责/状态契约问题；多入口对空结果、模型
降级的解释可能是接口契约问题；跨文件Context只加载工具项目知识是职责边界问题。
这些案例还需按新规范独立标注，不能由开发者自评直接当golden真值。

## 5. 评测与消融计划

### 数据分层

- 4个synthetic样本保留为快速回归测试，永不用于对外语义效果声明。
- 官方25个PR全量作为接入与baseline集合：覆盖9类，不因模型表现或语言挑题。
- 219全量按repo/task group划分dev/validation/test，先去重再冻结；test不得出现在
  诊断、候选生成、人工调prompt输入中。25小集被研究过的任务留在dev。
- 内部20–30个样本沿用#40目标：正常设计负例、接口违例、抽象泄漏、部分失败、
  跨文件权限/异常，以及真实重构。数量是开发目标，不是已收集数量。
- 为验证器增加看似合理的错误候选、已有外层校验、非可达分支等hard negatives。
  可以参考CRJudgeBench，不把生成者/审查者的同源模型标签直接当真值。

### 基线

保存冻结PR和仓库快照、官方数据revision、干净reviewer commit、prompt/config hash、
模型与端点、运行时间、实际request/token usage、judge与版本、失败类型和截断信息。
Reviewer不能读取golden或judge结果。身份元数据只供记录，不应有“正在评测”特权提示。
开始用官方25，不是四题扩容后便宣称benchmark完成。模型402/401等不可恢复错误应
停止批次，保存断点和未执行状态；不得继续16次相同失败，不能记录成“审查无Bug”。

### 实验矩阵

第一组：独立主审；主审+Location Gate；主审+统一Verifier；主审+目标上下文+Verifier。
第二组：在同一配置下开启/关闭旁路Signals，判断额外收益及误报。
第三组：one-shot与two-stage覆盖、失败及成本比较。
第四组：行为视角与行为+设计视角，测设计发现是否挤占Bug召回。

“规则结论注入主审”只作为明确隔离的历史对照；可以加入扰动/错误signal测试注意力
偏移，不作为生产默认。one-shot和two-stage的Gate/Context参数都必须真实生效。
同时做逐个开关与关键组合，避免把交互作用误说成单模块收益。模型随机性需重复运行、
随机/交错安排顺序，按PR给配对结果及不确定性，不把单次小样本均值当稳定收益。

### 指标

正式外部结果用官方judge及原生四类指标；内部严格计分明确命名。主报grounded recall
和augmented precision，同时展示grounded precision、augmented recall；切片category、
severity、language、diff大小与context_required。不能用一项augmented分数隐藏漏报。

额外记录：确切错误评论率、被Gate误删的TP、Critic错误驳回/错误确认、needs_context
数量、设计建议可行动率、每PR评论数、延迟、实际tokens/成本、失败和截断率。
每个变体的失败不能只从分母消失。无结果和指标无定义显示null，失败覆盖率单列。
Judge结果抽样人工复核，重点复核新增TP、模型间分歧、设计问题和拟接受候选。

## 6. 面向后续开发的规范

### 数据和职责契约

- `ReviewCandidate`：来源、类别、主张、anchor、related_locations、contract来源、
  触发条件、执行链、预期/实际、scope、context_requests。
- `EvidenceRef`：repo、commit、file、range、excerpt/hash、用途。范围由本地校验，
  不让模型伪造引用。Location结果不填入behavior_verified。
- `VerificationResult`：confirmed/refuted/needs_context/verification_failed、支持证据、
  反证、方法、未解决假设；confirmed也必须说明强度，不等于形式证明。
- `StageResult`：success/partial/failed/skipped与stage错误；成功空数组是唯一合法“无发现”。
- `ReviewReport`兼容扩展类别、验证状态和coverage；新增字段默认明确。GitHub/Web/
  VS Code使用同一发布策略；rules telemetry不能变回confirmed issues。

路径/结构启发式只输出signal；Context Resolver只取原始证据；Verifier只验证候选；
Merger只做去重及来源聚合；Renderer不能提升可信度或补造结论。每个模块有明确输入、
输出和失败语义，避免在工具函数内部调用全套管线形成隐含耦合。

### 开发红线

1. 主审不读规则结论；独立请求的prompt契约测试检查输入隔离。
2. 所有来源候选经过同一验证策略；不能合并后绕过Gate/Critic。
3. 超时、额度不足、解析失败、深审失败与无发现分开；report不得声称完整clean。
4. 一次候选确认不能靠复制reason填充失败场景；结论必须有可核对代码/契约。
5. 不因圈复杂度、函数长度、wrapper数量或模型confidence单独认定质量缺陷。
6. head/base/工具自身repo来源分开；禁止branch移动和fallback造成证据混用。
7. 测试验证行为及失败路径，不能只镜像实现；接口schema、降级、过期commit和三入口
   发布策略必须有契约测试。正常设计负例与真实失败例成对加入回归。
8. PR单一目标，小步变更；更改公共模型需说明兼容性并更新所有消费者；避免夹带架构
   重写、格式化、接口重命名。文档写“已实现/待验证/计划”而非混写。
9. 每项效果声明链接原始输入、运行与标注；不以unit tests数量替代语义质量。
10. 验证执行使用预配置allowlist与隔离环境，不增加任意shell自动执行；无凭据、限制
    网络、时间、资源。先只做读代码/契约验证，测试执行另行设计。
11. 配置参数必须被实际消费并写入trace；开启/关闭实验必须可验证地改变对应阶段。
12. 成本未采集显示unknown；402停止批次。禁止未经评测自主改生产代码、评分器或标签。

### 顺序与验收

| 阶段 | 修改范围建议 | 验收 |
|---|---|---|
| A 现状整理 | 文档、#40/#41任务对齐、#43/#45边界 | 功能/计划清晰；#45不宣称跑通官方集 |
| B 外部baseline | evaluation adapter、官方schema、版本和usage记录、断点 | 25题均有success/failed记录；judge可复现；失败不算clean |
| C 控制实验 | one-shot/two-stage/signals统一实验开关 | frozen输入；参数生效；按类别和失败切片 |
| D 根据案例修复 | StageResult、统一候选验证、head上下文Resolver、Critic窗口 | 每次修复有真实失败和正常负例；统一入口不回归 |
| E 设计质量 | typed candidates、接口/抽象/依赖检查、各入口分级展示 | 契约证据、设计可行动性标注；不损害Bug召回 |
| F 受约束改进 | Diagnose→Propose→Evaluate→Accept/Rollback及持久化 | 独立validation；test只最终评测；代码/labels/judge不可变；人工批准上线 |

受约束候选只改prompt、上下文预算/策略和阈值；需要真实失败归因、same-dataset验证、
已量化的成本预算和回滚版本。候选生成只读dev；验证器不能由候选改写；迭代次数预设，
保留所有拒绝记录，防止反复validation选择造成泄漏。关键Bug召回和类别指标不下降，
收益需超过重复运行波动；不能以少报提高precision冒充整体进步。
