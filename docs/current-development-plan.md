# 当前开发计划与分工

本文档整合当前阶段的开发目标、两人分工、对应修改文件和验收方式。它替代早期的 72 小时计划、模型上下文草案和误报控制草案；历史进度记录仍保留在 `docs/2026-05-29-summary.md`。

## 产品定位

本项目的目标是做一个面向真实 Pull Request 流程的 AI Review 助手：

```text
GitHub Action 负责云端分析、报告生成和 merge 门禁
Web Console 负责评委/用户开箱即用的可视化分析入口
VS Code 插件负责开发者在 IDE 内查看、定位和修复 Review 建议
```

核心原则：

- 不能要求使用者本地配置 `.env`、安装 `uv` 或准备模型 API Key。
- 主分支必须始终保持可运行，评委在任意时间查看都能复现演示效果。
- AI 不能直接替代人工 Review；系统要明确展示置信度、上下文完整性和降级状态。
- 坏 PR 也要能生成诊断报告，但不能被误判为可以合并。

## 当前优先级

```text
P0: 报告置信度和门禁可靠性
P0: Web Console 最小可用演示入口
P1: 轻量项目记忆 Context Pack
P1: VS Code 插件 MVP
P2: 更完整的 GitHub App / 服务化 / 多仓库支持
```

## 工作线 1：提升报告置信度

负责人：A 主导，你协助验收。

目标：让报告更可信、更少误报、更能说明“哪些结果可靠，哪些结果只是部分分析”。

### 已有基础

- PR metadata / changed files / patch 获取。
- 规则扫描。
- LLM 结构化输出。
- Pydantic schema 校验。
- changed-line 过滤。
- confidence 过滤。
- 重复建议过滤。
- 每文件建议数量限制。
- AI 失败 fallback。
- base 分支 reviewer 隔离运行。
- PR head 语法诊断：坏 PR 仍生成报告，最后 workflow 失败阻止合并。

### 下一步任务

1. 增强运行完整性说明。

   修改文件：

   - `src/models.py`
   - `src/reviewer/engine.py`
   - `src/output/markdown.py`
   - `tests/test_reviewer_engine.py`

   验收：

   - 报告清楚展示 PR 元数据、变更文件、AI 上下文、AI 分析、规则扫描、PR head 语法诊断是否成功。
   - AI 失败、上下文截断、PR head 语法失败时，报告可信度文案不混乱。

2. 增强规则扫描。

   修改文件：

   - `src/analyzer/risk_rules.py`
   - `tests/test_risk_rules.py`

   重点规则：

   - 修改 `.github/workflows/`、`src/cli/`、`src/reviewer/`、`src/github/` 视为高风险区域。
   - 删除测试文件或大量删除断言。
   - 新增 `except: pass`、`print(token)`、`subprocess`、`eval/exec` 等高风险模式。
   - 修改 reviewer fallback / artifact / workflow gate 相关逻辑时提示人工重点审查。

3. 优化误报控制。

   修改文件：

   - `src/reviewer/engine.py`
   - `src/analyzer/diff_parser.py`
   - `tests/test_reviewer_engine.py`
   - `tests/test_diff_parser.py`

   验收：

   - 建议尽量落在 changed added lines。
   - 同根因重复建议被合并或过滤。
   - 低置信建议不会刷屏。
   - lockfile、demo report、生成报告不会消耗过多模型上下文。

## 工作线 2：轻量项目记忆 Context Pack

负责人：A 主导，你负责项目规则和文档内容。

目标：不用重型 RAG，也让模型理解项目约定、核心函数和高风险模块。

### 设计方案

第一阶段不用向量库，不引入复杂 RAG。采用轻量 Context Pack：

```text
docs/review-guide.md        人工维护的项目 Review 约定
docs/functions-index.md     自动生成的函数索引
README.md                   项目概览
docs/architecture.md        架构说明
```

### 任务

1. 新增项目 Review Guide。

   新增文件：

   - `docs/review-guide.md`

   内容包括：

   - PR head 语法错误必须生成报告并最终阻止合并。
   - AI 失败必须降级为 rule-only。
   - 建议优先指向 changed added lines。
   - lockfile 和生成报告不进入主要 AI patch 上下文。
   - 高风险文件列表和人工审查偏好。

2. 自动生成函数索引。

   新增文件：

   - `tools/generate_functions_index.py`
   - `docs/functions-index.md`

   第一版建议使用 Python 标准库 `ast`，而不是立即启用 tree-sitter。当前仓库虽然依赖 `tree-sitter`，但没有安装 Python grammar 包；对于纯 Python 函数索引，`ast` 更轻、更可靠。

   索引内容：

   - 文件路径。
   - class / function 名称。
   - 参数列表。
   - 返回类型。
   - 起始行号。
   - docstring 或函数上方注释。

3. 在上下文构建中注入相关 Context Pack。

   修改文件：

   - `src/analyzer/context_builder.py`
   - `tests/test_context_builder.py`

   验收：

   - 只注入当前 PR 变更文件相关的函数索引小节。
   - 注入内容有字符预算，不能撑爆 prompt。
   - 文件不存在时静默跳过，不影响核心 review。

## 工作线 3：Web Console

负责人：A 做后端 API，你做前端页面与体验。

目标：提供一个评委和普通用户可以直接访问的最小可用 Web 入口。它不是完整 SaaS，而是一个轻量 PR Review Console。

### 后端任务

负责人：A。

新增或修改文件：

- `src/service/review_runner.py`
- `src/service/app.py`
- `src/service/__init__.py`
- `src/utils/config.py`
- `pyproject.toml`
- `tests/integration/`

功能：

- 抽出核心函数 `analyze_pr(...) -> ReviewReport`，供 CLI、Action、Web API 共用。
- 提供 `GET /api/health`。
- 提供 `POST /api/analyze`。
- 支持 repo 白名单：`ALLOWED_REPOS`。
- 所有模型密钥只在服务端环境变量中配置，不暴露到前端。

建议 API：

```http
POST /api/analyze
```

请求：

```json
{
  "repo": "HelicasECoode42/ai-pr-review",
  "pr_number": 11,
  "language": "zh",
  "use_ai": true
}
```

响应：

```json
{
  "report": {},
  "markdown": "...",
  "duration_seconds": 12.3
}
```

### 前端任务

负责人：你。

新增文件：

- `src/service/static/index.html`
- `src/service/static/app.js`
- `src/service/static/style.css`

页面结构：

- 输入区：repo、PR number、language、AI/rule-only。
- 状态区：fetching、scanning、reviewing、done、failed。
- 概览区：risk level、files changed、additions/deletions、AI used。
- 建议列表：severity、file:line、title、reason、recommendation。
- 报告区：Markdown 预览、复制报告、下载 JSON。
- 快捷链接：打开 GitHub PR。

验收：

- 评委打开页面后，不需要配置本地环境即可使用。
- 默认填好 demo repo 和 demo PR。
- 错误提示友好，例如 repo 不在白名单、GitHub API 失败、模型不可用。

## 工作线 4：VS Code 插件

负责人：你主导，A 保证 JSON schema 稳定。

目标：把 AI Review 结果带回开发者正在写代码的 IDE，减少“GitHub 页面看报告 -> VS Code 修代码”的上下文切换。

### MVP 范围

第一版只做读取已有报告，不触发 GitHub Action：

```text
AI PR Review: Load Report
AI PR Review: Clear Diagnostics
```

新增目录：

- `vscode-extension/package.json`
- `vscode-extension/tsconfig.json`
- `vscode-extension/src/extension.ts`
- `vscode-extension/src/diagnostics.ts`
- `vscode-extension/src/report.ts`

能力：

- 读取 `reports/pr-review.json`。
- 将 `suggestions` 转成 `DiagnosticCollection`。
- severity 映射：
  - `critical` -> Error
  - `high` -> Error
  - `medium` -> Warning
  - `low` -> Information
- 点击 Problems 面板跳转本地文件对应行。

### 后续能力

- 自动识别当前分支对应 PR。
- 从 GitHub Actions artifact 拉取最新 `pr-review.json`。
- 触发 workflow_dispatch 并轮询结果。
- Sidebar 展示风险摘要和建议列表。
- Git 状态提示：是否落后 main，是否需要 merge。

## 工作线 5：GitHub Action 和 merge 门禁

负责人：你主导，A review。

目标：保证 PR 审查在云端稳定运行，并且坏 PR 不会因为报告生成成功而被允许合并。

修改文件：

- `.github/workflows/ai-pr-review.yml`
- `src/cli/main.py`
- `src/reviewer/engine.py`
- `tests/test_reviewer_engine.py`

当前语义：

```text
1. checkout base 分支 reviewer
2. 检查 base reviewer 语法
3. checkout PR head 到 _pr_head/
4. 对 PR head 做语法诊断
5. 即使 PR head 语法失败，也继续生成 review 报告
6. 上传 artifact 和发布评论
7. 最后如果 PR head 语法失败，workflow exit 1
```

验收：

- PR 修改 reviewer 自己并引入语法错误时，仍能生成诊断报告。
- PR head 语法错误时，报告生成，但 workflow 最后失败。
- 正常 PR workflow 成功。
- `reports/pr-review.md`、`reports/pr-review.json`、`reports/pr-syntax-check.txt` 能按预期上传。

## 演示路径

最终建议准备三条演示路径：

1. GitHub PR 页面。

   - 展示 Action 自动运行。
   - 展示 bot summary comment。
   - 展示 artifact。
   - 展示坏 PR 有报告但不能 merge。

2. Web Console。

   - 输入或选择示例 PR。
   - 点击 Analyze。
   - 展示风险摘要、建议列表、Markdown 报告。

3. VS Code 插件。

   - 加载 `reports/pr-review.json`。
   - Problems 面板出现 AI Review 建议。
   - 点击跳转本地文件行。

## 文档维护

保留：

- `README.md`：面向用户和评委的入口。
- `docs/architecture.md`：当前架构。
- `docs/current-development-plan.md`：当前开发计划和分工。
- `docs/future-extensions.md`：后续路线。
- `docs/2026-05-29-summary.md`：历史进度记录。
- `docs/contribution-guide.md`：协作和提交规范。
- `docs/demo/`：示例报告。
- `docs/video-product-brief.md`：视频/展示素材，可在演示阶段继续使用。

删除或不再维护：

- `docs/72h-plan.md`
- `docs/agent-coding-guide.md`
- `docs/model-context-design.md`
- `docs/quality-control.md`

这些内容已被 `architecture.md`、`future-extensions.md` 和本文档覆盖。
