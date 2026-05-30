# 未来扩展

本文记录 AI PR Review Assistant 的产品和工程路线图。当前项目已经具备 CLI、GitHub Action、规则扫描、AI review、Markdown / JSON 报告和基础失败门禁；下一步重点是提升交互体验、稳定性和组织级可维护性。

## 1. VS Code 扩展：把 Review 带回编辑器

优先级：高

### 目标

当前主要痛点是上下文切换：

```text
写代码 -> git push -> 等 GitHub Action -> 去 PR 页面看评论 -> 回 VS Code 修改
```

报告和 PR comment 是静态结果，不能直接在本地编辑器里定位、筛选和处理。VS Code 扩展的目标是把 JSON 报告投射回 IDE：

- 在侧边栏输入 `owner/repo` 和 PR number，一键运行 review。
- 将 `ReviewSuggestion` 映射为 VS Code Diagnostics，显示在 Problems 面板。
- 点击建议直接跳转到本地文件对应行。
- 在侧边栏显示风险摘要、文件列表、建议详情和复制评论入口。

### MVP 范围

第一版只做只读体验，不碰 merge：

- `AI PR Review: Analyze PR`
- `AI PR Review: Load Existing Report`
- `AI PR Review: Clear Diagnostics`
- 调用现有 CLI 生成 `reports/pr-review.json`
- 读取 JSON 并创建 `DiagnosticCollection`
- Diagnostic severity 映射：
  - `critical` -> Error
  - `high` -> Error
  - `medium` -> Warning
  - `low` -> Information

### 后续能力

- Sidebar Webview：风险统计、文件筛选、建议列表。
- CodeLens / inline hint：在变更行附近显示 AI review 摘要。
- Quick Fix：复制建议、打开报告、跳到 GitHub PR。
- Git 状态提示：当前分支是否落后 main、是否需要 fetch/merge。
- Merge 预览：逐文件或逐 hunk 查看冲突，后续再考虑交互式合并。

### 代码映射

- 后端复用：`src.cli.main`、`src/reviewer/*`、`src/output/json_report.py`
- 新增目录建议：`vscode-extension/`
- 关键边界：稳定 `ReviewReport` JSON schema

## 2. GitHub App / 独立服务化

优先级：高

### 目标

将工具从“跑在仓库 workflow 中”演进为独立 GitHub App 或托管服务，进一步避免 reviewer 被 PR 代码影响，并提供集中日志、权限治理和组织级配置。

### 实现要点

- 构建 webhook 接收器，推荐 FastAPI，处理 `pull_request`、`issue_comment` 等事件。
- 将 `src/cli/main.py` 中的触发逻辑抽成服务函数，供 CLI、Action、Webhook 共用。
- 保持 `src/github/client.py` 作为 GitHub REST 调用单一入口。
- 使用 GitHub App installation token，遵循最小权限原则。
- 验证 webhook 签名，防止重放，记录审计日志。
- 提供 Dockerfile 和部署示例。

### 收益

- reviewer 运行环境完全独立于被审查仓库。
- 组织级统一配置 provider、规则、评论策略。
- 更容易做监控、重试、队列和成本控制。

## 3. 本地 / 离线 Demo Provider

优先级：中高

### 目标

在无网络、无 API Key 或演示场景中仍能跑完整流程，方便教学、调试和 CI 回归。

### 实现要点

- 在 `src/reviewer/provider.py` 增加 `LocalProvider` 或 `DemoProvider`。
- 返回预定义 JSON，或根据 fixture 做简单模板生成。
- CLI 增加 `--demo` / `--offline` 参数。
- 将 `docs/demo/` 和测试 fixture 整合为 provider 数据源。

### 收益

- 降低演示门槛。
- 不依赖外部模型服务即可做端到端测试。
- 方便验证 Markdown / JSON schema 和前端集成。

## 4. 规则库配置化

优先级：中高

### 目标

将 `src/analyzer/risk_rules.py` 中的内置规则迁移为可配置规则集，支持仓库级、团队级或组织级覆盖。

### 实现要点

- 定义 YAML / JSON schema。
- 支持 path pattern、line pattern、severity、confidence、recommendation。
- 内置规则和用户规则按优先级合并。
- 支持启用、禁用、覆盖严重程度和置信度。
- 提供 CLI：

```bash
ai-pr-review rules validate path/to/rules.yml
ai-pr-review rules test path/to/rules.yml path/to/patch.diff
```

### 收益

- 团队可以沉淀自己的 review 策略。
- 降低误报。
- 更适合合规和组织级治理。

## 5. Provider 强化与多后端支持

优先级：中

### 目标

强化 `ReviewModelProvider` 抽象，支持 OpenAI、Azure OpenAI、DeepSeek、自建 LLM、本地 LLM 等多种后端，并记录稳定的错误和成本信息。

### 实现要点

- 明确 provider 契约：输入、输出、错误类型、超时行为。
- 增加 retry、backoff、速率限制和超时配置。
- 记录 token usage、cost、latency。
- 支持分层调用：小模型预筛，大模型精审。
- 增加 provider contract tests。

## 6. 自动发布 Review Comments 策略治理

优先级：中

### 目标

在避免噪声的前提下，将高置信建议发布为 GitHub review comments，并保留人工审核和反馈路径。

### 实现要点

- 在 `src/github/client.py` 增加 review comment 创建、更新、删除接口。
- 定义发布策略：
  - 只发布 HIGH / CRITICAL。
  - 只发布 confidence 大于指定阈值。
  - 每个文件和每个 PR 限制评论数量。
  - 低置信建议只进入 summary。
- 记录哪些建议被发布、隐藏或人工忽略。
- 防止重复评论，支持更新 bot comment。

## 7. Web UI / Dashboard

优先级：中

### 目标

为 reviewer 提供可交互的报告页面，支持筛选、跳转、忽略建议和导出评论草稿。

### 实现要点

- 前端：React + Vite。
- 后端：FastAPI 或复用服务化 API。
- 数据源：`ReviewReport` JSON。
- 页面能力：
  - PR 概览。
  - 风险统计。
  - 文件级建议。
  - 建议筛选和隐藏。
  - 复制 GitHub comment。
  - 链接到 GitHub changed lines。

## 8. 项目级上下文索引 / RAG

优先级：中

### 目标

在构建 AI 上下文时补充仓库级相关信息，例如函数定义、调用方、历史 review、架构文档和团队规则。

### 实现要点

- 建立轻量索引：SQLite、倒排索引或向量索引。
- 从 diff 中提取符号、路径和关键词。
- 检索相关定义和文档，追加到模型上下文。
- 支持增量更新，避免每次全量扫描。
- 对敏感信息做脱敏和访问控制。

## 9. 可观测性与成本控制

优先级：中

### 目标

让运行状态、失败原因、模型成本和性能瓶颈可见。

### 实现要点

- 记录 model latency、token usage、cost、rule scan time、context size。
- 为 provider 调用增加 structured logging。
- 对相同 patch/context 的模型响应做缓存。
- 区分错误类型：GitHub API、provider、schema、context truncation、runtime。
- 在报告中更清楚地展示 partial / fallback / failed 状态。

## 10. 安全、隐私与脱敏

优先级：中

### 目标

降低把敏感信息发送给模型或写入持久日志的风险。

### 实现要点

- 在 `build_review_context` 前增加脱敏流水线。
- 对 token、password、secret、api key、private key 做模式识别和替换。
- 避免将完整 diff 写入非必要持久日志。
- 区分公开仓库和私有仓库的数据策略。
- 文档化模型数据使用说明。

## 阶段计划

### 短期：1-2 周

- 完善 PR head 诊断报告的可读性。
- 增加 demo provider。
- 稳定 `ReviewReport` JSON schema。
- 搭建 VS Code 扩展 MVP，把 suggestions 显示到 Problems 面板。

### 中期：2-6 周

- 规则库配置化。
- GitHub App / 服务化原型。
- 自动评论策略治理。
- Web UI 基础版。

### 长期：6 周以上

- 项目级 RAG。
- 组织级规则管理。
- VS Code 内 merge 预览和交互式修复。
- 成本、监控、审计和权限体系。
