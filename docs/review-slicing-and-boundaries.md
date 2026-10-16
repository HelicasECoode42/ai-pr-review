# 变更切片、可靠性与模块边界

## 当前实现与边界

2026-10-14：新仓库审查路径已实现，160 项测试通过。真实新旧对照尚未完成，不能声称召回率改善。历史约 4% 是工程试验基线，不能与不同协议的新试跑直接比较。

当前路径：固定 base/head → diff → 变更定义与旧定义 → 符号引用/潜在调用方 → 模型发现候选并请求只读检索 → 行为核验 → confirmed/dismissed/needs_context → 报告。规则扫描独立保留为未核实线索，不向主发现提示注入规则结论。

Python 定义范围来自 AST；其他语言主要是大括号启发式。符号搜索是文本引用，不是解析后的调用图；GitHub 后端搜索范围比本地 Git 后端有限。初始种子最多 12 个文件、每文件两个 hunk，所有预算与遗漏必须记录。当前没有完整数据流切片、独立多视角发现或生产阶段持久化恢复。

可靠性已经包含：固定源码版本，校验引用的真实行和原文，要求触发条件、可达路径、预期/实际行为及 PR 引入原因；否定候选必须有反证，缺证据进入待核实。源码推理没有执行目标项目测试，引用正确也不等于因果判断必然正确。

## DOVideo-AI 的实际参考

已读取桌面项目 server 的 VideoChunkingService、AgentLoopService、AgentCheckpointRepository。分块保留 rawSegments；编排器通过构造器注入检索、校验、检查点服务；执行草稿可恢复到 Critic；数据库是恢复真源，Redis 是缓存。这些是可借鉴的边界，而不是照搬五分钟分块或整个 Java 技术栈。

代码切片应以变更符号及其契约为中心：向后查输入来源、鉴权和前置条件，向前查返回值消费者、副作用和错误传播。Java 优先沿 Controller → Service → Repository/Mapper，并覆盖异步消费者、事务边界及配置；这条分层链是检索线索，不应假设所有仓库都遵循它。

摘要只用于索引与调度；最终核验始终读取固定版本原始代码。接口和动态调用解析失败时标注不完整，不把同名文本命中伪装成语义调用关系。

## 渐进重构规范

推荐单一应用编排入口，CLI、Web、Action 只适配输入输出，避免三个入口重复构造 provider 与执行策略。暂不引入微服务、MQ 或多层无行为转发封装。

| 阶段 | 输入 → 输出 | 边界 |
| --- | --- | --- |
| SnapshotReader | repo/base/head → 冻结源码 | 不调用模型、不作审查判断 |
| ChangeSlicer | diff/源码索引 → ChangeSlice | 定义、关系、范围、遗漏；不消费模型结论 |
| EvidenceRetriever | slice/读取请求 → EvidenceBundle | 原始证据与来源；不判定 Bug |
| CandidateDiscoverer | 证据/视角 → CandidateBatch | 提出假设；不直接发布评论 |
| BehaviorVerifier | 候选/证据 → VerificationBatch | 因果与反证；不能修改评分标准 |
| DecisionPolicy | 核验/预算 → Decisions | 确认、否定、待核实；不隐式请求模型 |
| Reporter/Publisher | decisions → 展示/评论 | 仅发布已核实问题；保留审计信息 |

阶段产物使用有类型的不可变结构，不能继续依赖共享 dict 的隐含键约定。业务模块显式注入 Snapshot/Provider/Budget；禁止从领域层读取全局配置或创建网络客户端。每次模型调用记录阶段、模型、版本、usage、失败与预算，不把失败当作零发现。

发现阶段先增加可配置视角：逻辑与边界、接口与契约、安全与权限、状态与资源、架构与可维护性。各视角独立产生候选，经统一核验与去重；票数和模型自信度不能代替证据。浅封装、高耦合等结构建议独立呈现，只有明确可观察错误后果才计入 Bug 指标。多视角调用预算必须独立评测，不能预设越多越好。

检查点键至少包含 repo/base/head、审查器版本、策略哈希、模型和阶段；持久化输出后才标完成，恢复只重跑未完成阶段。模型别名不保证权重固定，需要保存实际返回的模型名称。

## 验收顺序

先固定当前实现跑新旧同协议对照，独立裁定候选并报告 Precision、Recall、漏报、误删、请求失败、成本与延迟。随后分别测语义切片、多视角和核验策略；不要把三项同时修改后声称任何单项有效。适配器兼容、阶段失败恢复、旧 base/head 污染与跨文件接口回归必须有行为测试。

## 一手资料

- CodeQL Java/Kotlin 数据流： https://codeql.github.com/docs/codeql-language-guides/analyzing-data-flow-in-java/
- Claude Code Review 的多视角及行为核验： https://support.claude.com/en/articles/14233555-set-up-code-review-for-claude-code
- DeepSeek 余额接口： https://api-docs.deepseek.com/api/get-user-balance/
- DeepSeek Responses 思考强度： https://api-docs.deepseek.com/api/create-response/
