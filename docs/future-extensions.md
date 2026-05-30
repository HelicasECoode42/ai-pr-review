# 未来扩展（Roadmap & 实施说明）

本文档基于当前代码库（`src/` 下的 `analyzer`、`reviewer`、`github`、`output`、`cli` 等模块）提出可落地的扩展方向、实现要点与注意事项。每项扩展都包含与现有代码的映射位置和优先级建议，便于逐步推进并保持向后兼容。

---

## 1. 服务化：GitHub App / 独立服务（高优先级）

目标：将工具从运行在被评审 PR 分支的 Action 演进为独立的 GitHub App 或托管服务，避免评审器被 PR 改动影响，提升审计与可维护性。

实现要点：
- 构建 webhook 接收器（推荐 FastAPI）负责接收 `pull_request`、`issue_comment` 等事件。
- 将 `src/cli/main.py` 中的触发逻辑抽象为可被 HTTP/队列触发的服务函数。
- 保持 `src/github/client.py` 作为 GitHub REST 调用的单一入口；为 App 模式新增安装/权限校验逻辑。
- 安全实践：为 App 使用最小权限（只读 PR、contents、metadata；写评论按需）、验证 webhook 签名、启用重放与速率保护。
- 部署：提供 Dockerfile、简单的 Kubernetes/Helm 或 GitHub Actions 部署示例。

代码映射：
- 事件触发 & 入口：`src/cli/main.py` -> 提取为 `src/service/webhook.py`
- GitHub 通信：`src/github/client.py`
- 分析任务：复用 `src/analyzer/*`、`src/reviewer/*`、`src/output/*`

收益：稳定性、集中日志/监控、更可控的凭证管理与回归行为。

---

## 2. 离线 Demo 与本地 Provider（中优先级）

目标：提供在无网络或无 API Key 情况下可运行的演示模式，便于本地演示、教育和 CI 测试。

实现要点：
- 在 `src/reviewer/provider.py` 中新增 `LocalProvider`（或 `DemoProvider`）实现，返回预定义的 JSON 结构或调用本地模板生成器。
- 在 CLI 增加 `--offline`/`--demo` 参数，让 `review_with_ai` 在无 Key 时自动降级为本地 provider。
- 将 `docs/demo/` 下的示例报告与测试 fixture 整合为 provider 的数据源。

代码映射：
- Provider 抽象：`src/reviewer/provider.py`
- 调用点：`src/reviewer/engine.py`（`review_with_ai`）
- CLI：`src/cli/main.py`

收益：减少外部依赖、提高用户体验并便于回归测试。

---

## 3. 项目级上下文索引（RAG）：跨文件检索与补充证据（中-高优先级）

目标：在构建 AI 上下文时引入仓库级检索（函数签名、关键模块、历史 Review），提升模型输出精确度并减少误报。

实现要点：
- 建立轻量索引（倒排或向量）：索引 repo 中重要文件和 API（例如 `src/` 下核心模块、README、架构文档）。可以先用 SQLite + simple embeddings，后期可切换到 Milvus/Chroma。
- 在 `src/analyzer/context_builder.py` 的 `build_review_context` 中接入检索步骤：针对当前 diff 中的符号/文件名检索相关定义并将检索结果附加到 context 前缀。
- 提供增量索引更新机制（commit hook / CI job）以控制索引时效性。

注意：索引与 embedding 会增加存储和运维成本，建议作为可选开关。

---

## 4. 规则库可配置化与动态加载（中优先级）

目标：把 `src/analyzer/risk_rules.py` 中的规则迁移为可配置的规则集（JSON/YAML），允许仓库级或组织级覆写。

实现要点：
- 将 `LINE_RULES`、`RISK_PATH_PATTERNS` 等抽象为配置文件，提供 schema 校验（`src/utils/config.py` 可扩展加载器）。
- 在 `scan_risks` 中按优先级合并内置规则与用户规则，并支持规则启用/禁用、置信度调整。
- 提供 CLI 命令用于测试新规则：`ai-pr-review rules validate|test path/to/rules.yml`

代码映射：
- 默认规则：`src/analyzer/risk_rules.py`（重构为加载配置）
- 配置与校验：`src/utils/config.py`

收益：支持团队自定义策略、降低误报并便于合规性管理。

---

## 5. 强化 Model Provider 抽象与多后端支持（高优先级）

目标：增强 `ReviewModelProvider` 的适配能力，支持 Azure OpenAI、DeepSeek、自建 LLM 服务及本地 LLM（如 llama.cpp）等多种后端。

实现要点：
- 明确 provider 契约：输入（system/user prompt、max_suggestions、schema）、输出（raw string、tokens 使用统计）与错误行为。
- 增加通用重试/退避、超时与速率限制策略；记录 token/cost 指标。
- 支持分层调用策略：先用轻量模型快速筛选，再对候选问题用大模型精审（在 `src/reviewer/engine.py` 中实现流水线）。

代码映射：
- 抽象与实现：`src/reviewer/provider.py`
- 调用：`src/reviewer/engine.py`

收益：成本控制、后端灵活性与更稳定的可用性。

---

## 6. Web UI / Dashboard（低-中优先级）

目标：为审阅者提供交互式界面，显示报告、接受/忽略建议并导出为 GitHub review comments。

实现要点：
- 前端：React + Vite（或团队既有栈），显示 PR 概览、风险统计和文件级建议。
- 后端：FastAPI，复用 `src/output/markdown.py`、`src/output/json_report.py` 的渲染逻辑，提供 REST 接口用于获取报告、触发分析、发布评论。
- 支持交互：接受/拒绝建议、批量操作、导出草稿作 GitHub Review。

代码映射：
- 渲染：`src/output/*.py`
- 导出评论：将使用 `src/github/client.py` 的评论 API

---

## 7. 自动发布 Review Comments 与交互流程（中优先级）

目标：将 AI 建议半自动或自动发布为 GitHub review comments，同时保留人工审核路径，避免噪音评论。

实现要点：
- `src/github/client.py` 增加创建、更新、删除 review comment 的接口与批量操作支持。
- 定义发布策略：例如只自动发布 HIGH/CRITICAL 且置信度 > 0.8 的建议；低置信度建议作为 summary 或草稿。
- 记录审计日志与用户反馈：当建议被采纳或忽略时，将结果写入持久层以便后续规则/模型迭代。

注意：必须避免在大型 PR 中生成海量评论，建议合并相似建议并提供限速策略。

---

## 8. 性能、可观测性与成本控制（高优先级）

要点：
- 指标监控：记录模型响应时延、token 使用、规则扫描耗时（建议接入 Prometheus/Grafana）。
- 缓存：对相同 patch/context 的模型响应做缓存，避免重复付费。
- 并行与分层分析：规则扫描先行，AI 分析异步或并行执行（受速率与并发限制）。
- 在 `src/analyzer`、`src/reviewer` 中增加轻量 profiler 与日志点，用于定位热点（可使用 Python 的 `cProfile` 或第三方工具）。

---

## 9. 测试策略与质量保障（高优先级）

要点：
- 单元测试覆盖：扩展 `tests/` 覆盖 `src/analyzer`（diff 解析、规则）、`src/reviewer`（payload 解析、过滤逻辑）、`src/github`（使用 HTTP mock）。
- 集成测试：引入录制 fixtures（vcr.py / snapshots）以验证端到端行为与 provider 兼容性。
- 合约测试：为 provider 定义契约测试，确保不同后端返回的结构在 `src/reviewer/engine.py` 中可以通用解析。

---

## 10. 隐私、脱敏与安全注意事项

要点：
- 在发送给模型的上下文中脱敏敏感信息（密钥、密码、用户 PII），`secret-logging` 规则已有检测但应在 `build_review_context` 中增加脱敏流水线。
- 日志策略：避免把原始 API Key、完整 diff（若包含敏感数据）写入持久日志；审计日志需要访问控制。
- 合规性：区分公开仓库与私有仓库的处理策略，以及模型数据使用声明。

---

## 优先级与阶段性计划建议

短期（0–4 周）：
- 强化 provider 抽象与本地 demo provider（`src/reviewer/provider.py`）
- 增加离线模式与示例 fixture（`docs/demo/`）
- 优化 context 构建中的脱敏与错误处理（`src/analyzer/context_builder.py`）

中期（1–3 个月）：
- 服务化（GitHub App）并提供部署示例
- 规则配置化并提供 CLI 管理接口
- 自动发布 review comments 的可控策略

长期（3 个月以上）：
- 项目级 RAG 索引、Web UI、组织级规则管理与权限控制

---

如果你愿意，我可以现在开始实现其中的一项改进（例如：
- 在 `src/reviewer/provider.py` 中增加一个 `LocalProvider` mock 实现，或
- 将 `src/analyzer/risk_rules.py` 的内置规则迁移为 YAML 配置并实现加载器），
并把变更提交到仓库。请指定你希望我优先实现的任务。
