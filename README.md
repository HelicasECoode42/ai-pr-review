# AI PR Review Assistant

> AI PR Review Assistant 将 PR diff 自动转化为可读、可信、可追溯的审查结果，并通过 GitHub、Web Console 和 VS Code 插件把问题带回开发者的代码上下文。AI 辅助人工 Review，而非替代。

AI PR Review Assistant 是一个面向 GitHub Pull Request 的 AI 代码审查工具。它围绕真实 Review 流程设计：自动获取 PR 变更，结合本地规则扫描和大模型分析生成结构化报告，再把结果发布到 GitHub、Web Console 和 VS Code IDE 中，帮助开发者更快理解变更、定位风险并回到代码现场处理问题。

项目目标不是用 AI 替代人工 Review，而是把重复性的上下文整理、风险预筛、报告生成和代码定位交给系统，让审查者把注意力放在真正需要判断的设计、业务和安全风险上。

## 产品闭环

```text
GitHub PR
  -> GitHub API 获取元信息、文件变更和 patch
  -> Risk Rules 扫描高风险路径和代码模式
  -> Context Builder 构造受控上下文
  -> LLM Reviewer 生成结构化建议
  -> 本地过滤置信度、重复项和非变更行噪音
  -> Markdown / JSON ReviewReport
  -> GitHub Comment / Web Console / VS Code Problems & Panel
```

当前项目提供三个使用入口：

| 入口 | 目标用户 | 作用 |
|---|---|---|
| GitHub Actions | 团队协作中的 PR 审查者 | PR 创建或更新后自动审查，发布 summary comment 和高置信 inline comments |
| Web Console | 评委、队友、非 IDE 用户 | 粘贴 GitHub PR URL 后直接分析任意 PR，可视化查看建议和完整报告 |
| VS Code 插件 | 正在本地修代码的开发者 | 自动识别当前分支 PR，把 Review 结果同步到 Problems、Review Panel 和 CodeLens |

底层 CLI 和 Review Engine 被多个入口复用，保证同一套分析逻辑可以运行在本地、CI、Web 服务和 IDE 插件中。

## 快速演示

观看 AI PR Review 的完整使用流程：

- **Web Console**：粘贴 PR URL → 一键分析 → 可视化建议与报告
- **GitHub Actions**：PR 创建/更新后自动审查，发布 summary comment 和 inline review
- **VS Code 插件**：Problems 面板跳转本地代码，Review Panel 展示完整报告

> Demo 视频：即将添加 GIF / BiliBili / YouTube 链接。

最短体验路径（无需克隆仓库、不配环境）：打开 Web Console，粘贴任意公开仓库的 PR URL，点击 Analyze。

## 解决的问题

- 大 PR 文件多、diff 长，人工快速建立全局理解成本高。
- 鉴权、异常处理、SQL 注入、命令执行、敏感日志、测试绕过等风险点容易漏看。
- Lint/test 能发现确定性错误，但发现不了很多意图偏差和设计风险。
- 直接让 AI 评论容易噪声太大，需要结构化输出、证据链、本地过滤和置信度控制。
- PR 代码本身可能已经坏掉，审查工具不能因此完全跑不出报告。
- 传统报告和本地代码上下文割裂，开发者需要在 GitHub、报告和 IDE 之间反复切换。

## 当前能力

### 核心分析引擎

- 通过 GitHub REST API 获取 PR 元数据、变更文件和 patch。
- 解析 diff，提取 changed lines 和 patch hunk 上下文。
- 本地规则扫描高风险路径和高风险代码模式。
- 注入轻量 Context Pack，包括项目 Review Guide、函数索引、README 和架构说明。
- 使用 OpenAI-compatible provider 调用模型，支持 OpenAI、DeepSeek、Azure OpenAI 等兼容服务。
- 使用 Pydantic schema 约束模型输出，生成结构化 `ReviewReport`。
- 通过 changed-line、confidence、重复项和每文件数量限制控制噪音。
- AI 失败时自动降级为 rule-only 报告。

### GitHub Actions 自动审查

- PR `opened`、`synchronize`、`reopened` 时自动运行。
- 上传 `pr-review.md` 和 `pr-review.json` artifact。
- 发布可更新的 PR summary comment，避免每次运行刷屏。
- 只把高置信、带行号的建议发布为 GitHub inline review comments。
- 记录 ReviewMeta：审查 commit、触发事件、workflow run URL、更新时间和 review mode。
- 使用 base 分支稳定 reviewer 分析 PR diff，避免 PR 把审查器自身改坏后完全无报告。
- 对 PR head 额外执行 Python 语法诊断；坏 PR 仍生成报告，但 workflow 最终失败，避免误合并。

### Web Console

- 提供 FastAPI 后端和静态前端页面。
- 支持粘贴 GitHub PR URL，自动解析 repo 和 PR number。
- 支持中文/英文、AI Review/rule-only 两种模式。
- 展示整体风险、文件数、增删行、AI 使用状态、报告可信度和耗时。
- 提供 Suggestions、Completeness、Report 三个视图。
- 支持按严重程度过滤建议、复制评论、下载 JSON、打开 GitHub PR。

启动方式：

```bash
uv run uvicorn src.service.app:app --reload
```

打开：

```text
http://127.0.0.1:8000
```

### VS Code 插件

插件目录：

```text
vscode-extension/
```

已提供 VSIX 安装包：

```text
vscode-extension/ai-pr-review-0.2.0.vsix
```

安装：

方法1：

```bash
code --install-extension vscode-extension/ai-pr-review-0.2.0.vsix --force
```

方法2：
  1. 打开 VS Code
  2. 按 Ctrl+Shift+X 打开扩展面板（或左侧边栏点那个四个方块图标）
  3. 右上角点 ...（三个点，Views and More Actions）
  4. 选 "Install from VSIX..."（从 VSIX 安装）
  5. 在弹出的文件选择窗口里，找到你项目里的 vscode-extension\ai-pr-review-0.2.0.vsix，选中，确认

安装后在 VS Code 中可使用：

- `AI PR Review: Refresh`：从 GitHub 拉取当前分支对应 PR 的 Review 结果。
- `AI PR Review: Open Review Panel`：打开 IDE 内 Review 面板。
- `AI PR Review: Load Report File`：手动加载本地 `pr-review.json`。
- `AI PR Review: Clear Diagnostics`：清除 Problems 诊断。

IDE 体验：

- 状态栏显示 `AI Review: loading / clean / N issues / no PR`。
- Problems 面板显示 AI Review diagnostics，点击跳转本地文件行。
- Review Panel 左侧展示 inline suggestions，右侧渲染完整 Markdown summary report。
- `Open Code` 和文件路径都可以跳转到本地代码。
- CodeLens 在有建议的代码行上方显示 `AI Review: HIGH 92% - ...`，点击打开 Review Panel。

## 安装与配置

推荐使用 `uv`：

```bash
git clone https://github.com/HelicasECoode42/ai-pr-review.git
cd ai-pr-review
uv sync --extra dev
```

也可以使用 venv + pip：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

创建 `.env` 文件，注意不要提交：

```ini
GITHUB_TOKEN=ghp_xxx

OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
REVIEW_MODEL=gpt-4.1-mini
```

公开仓库可以不配置 `GITHUB_TOKEN`，但匿名请求容易触发 GitHub API rate limit。建议配置 fine-grained token，至少需要：

- Metadata: Read
- Pull requests: Read
- Contents: Read

DeepSeek 示例：

```ini
OPENAI_API_KEY=你的 DeepSeek API Key
OPENAI_BASE_URL=https://api.deepseek.com/v1
REVIEW_MODEL=deepseek-chat
```

### 模型选择依据

项目默认推荐两种模型，兼顾成本、速度和代码理解能力：

| 模型 | 推荐场景 | 优势 |
|------|----------|------|
| `gpt-4.1-mini` | 默认推荐，英文/通用项目 | mini 级模型，代码理解和结构化输出表现稳定；速度快；原生支持 `response_format: json_object`，便于约束输出 schema；成本低，适合高频自动审查 |
| `deepseek-chat` | 中文项目、成本敏感场景 | 中文审查质量好；成本极低（约 ¥1/1M tokens）；OpenAI-compatible API 无缝接入；旗舰模型代码能力强 |

两种模型通过相同的 OpenAI-compatible 接口调用。你可以通过修改 `OPENAI_BASE_URL` 接入任何兼容服务（Azure OpenAI、Ollama 本地模型、其他第三方代理）。

> 为什么不用 GPT-4o 或 Claude？这些大模型在代码审查场景的边际收益有限——对 diff hunk 的局部分析，mini 级别模型已经足够。大模型增加的延迟和成本（5-10x），换来的只是更华丽的措辞，而非更准确的发现。

## CLI 用法

AI + 规则分析：

```bash
uv run python -m src.cli.main owner/repo 123 \
  --language zh \
  --output reports/pr-123.md
```

仅规则扫描，无需模型 API Key：

```bash
uv run python -m src.cli.main owner/repo 123 \
  --no-ai \
  --language zh \
  --output reports/pr-123.md
```

JSON 输出：

```bash
uv run python -m src.cli.main owner/repo 123 \
  --format json \
  --output reports/pr-123.json
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--output` / `-o` | 报告输出路径 |
| `--format` | `markdown`，默认，或 `json` |
| `--ai` / `--no-ai` | 是否调用 AI 模型 |
| `--language` | `en`，默认，或 `zh` |
| `--reviewer-version` | 报告中的 reviewer 来源标记，例如 `pr-branch` / `main-fallback` |
| `--execution-status` | 报告中的执行状态标记，例如 `success` / `degraded` |
| `--degradation-reason` | 降级原因说明 |
| `--report-confidence` | 报告可信度标记，例如 `normal` / `fallback` / `partial` |
| `--reviewed-commit` | 本次审查目标 commit SHA，主要由 CI 传入 |
| `--trigger-event` | 触发事件，例如 `pull_request` / `workflow_dispatch` |
| `--workflow-run-url` | 本次 GitHub Actions run URL |

## GitHub Actions 运行语义

仓库内置 `.github/workflows/ai-pr-review.yml`。这个 workflow 故意区分两件事：

```text
报告能否生成 != PR 是否可以合并
```

流程：

1. Checkout PR 的 base 分支，使用稳定 reviewer 运行审查工具。
2. 安装依赖并检查 base 分支 reviewer 自身语法。
3. 额外 checkout PR head 到 `_pr_head/`。
4. 对 `_pr_head/src` 和 `_pr_head/tests` 执行 Python 语法诊断。
5. 即使 PR head 语法失败，也继续运行 AI PR Review 并生成报告。
6. 将语法诊断追加到 `reports/pr-review.md`，并上传 artifact。
7. 发布 summary comment 和高置信 inline comment。
8. 如果 PR head 语法失败，最后一步显式 `exit 1`，让 workflow 标红，阻止合并。

也就是说，坏 PR 的预期结果是：

```text
review 报告生成：是
workflow 最终成功：否
PR 可合并：否
```

这能避免“代码坏了导致完全没有报告”，也能避免“有报告所以误以为可以合并”。

## 报告内容

Markdown 报告包含：

- 审查元信息：commit、触发事件、workflow run、更新时间、审查模式。
- 修复追踪：为后续判断旧建议是否已修复预留结构。
- 运行状态：reviewer 来源、执行状态、降级原因、报告可信度。
- 分析完整性：PR 元数据、变更文件、AI 上下文、AI 分析、规则扫描、patch 上下文是否完整。
- PR 概览：仓库、PR、作者、base/head、变更统计、整体风险。
- 变更总结：AI 或规则生成的摘要。
- 文件变更：文件列表和增删行统计。
- Review 建议：位置、严重程度、置信度、原因和修复建议。
- 规则扫描结果：本地规则命中的风险证据。
- 分析备注：AI 失败、上下文截断、过滤项等说明。
- PR head 语法诊断：如果 PR 提交的 Python 代码无法编译，会追加错误摘要。

JSON 报告用于自动评论、Web Console、VS Code 插件和未来服务化集成。

## 架构

```text
src/
  cli/           Typer CLI 入口
  github/        GitHub REST API 客户端
  analyzer/      diff 解析、上下文构建、风险规则扫描
  reviewer/      LLM provider、prompt、结构化 Review 引擎
  output/        Markdown / JSON 渲染
  service/       FastAPI Web Console
  utils/         配置管理和 GitHub Actions 辅助工具
tests/           单元测试
docs/            架构、质量控制、路线图和视频说明
vscode-extension/ VS Code 插件源码、编译产物和 VSIX
```

## 质量保障

- 核心模型使用 Pydantic schema，避免模型自由文本难以解析。
- 规则扫描和 AI 输出分层，规则结果可在 AI 失败时独立生成报告。
- 建议经过 changed-line、confidence、去重、每文件数量限制过滤，减少刷屏。
- 大 diff 会触发上下文截断并在报告中明确标注。
- CI 使用 base branch reviewer fallback，避免 PR 修改审查工具后完全无报告。
- Workflow 会单独检查 PR head 语法，报告生成和合并门禁解耦。
- 项目使用自身 AI Review 工具 dogfooding，多轮修复安全、CSP、协议校验和 IDE 交互问题。

测试：

```bash
uv run --extra dev pytest
```

语法检查：

```bash
python -m compileall src tests
```

当前单元测试覆盖：

- context builder
- diff parser
- reviewer engine
- risk rules

## 安全性

### Token 管理

- GitHub Token 和 API Key 通过 `.env` 文件配置（已加入 `.gitignore`，不会提交到仓库）。
- CI 环境中使用 GitHub Actions Secrets（`GITHUB_TOKEN`、`OPENAI_API_KEY`），运行时注入，不落盘。
- 支持 GitHub fine-grained token，最小权限原则：仅需 Metadata Read、Pull Requests Read、Contents Read。

### 数据隐私

- **上下文最小化**：传给 AI 模型的 context 仅包含 PR diff（patch hunk + 上下文行），不传全量源码。模型看到的只是变更片段和周围几行代码，不是整个仓库。
- **不外传敏感内容**：lockfile、生成报告、demo artifact 等低价值文件只展示变更统计，不消耗 AI token，也不会被发送到外部 API。
- **不落盘**：除 GitHub Actions artifact（`pr-review.md`、`pr-review.json`）外，系统不将 PR 内容持久化到数据库或日志文件。artifact 受 GitHub 访问权限保护。
- **全链路 HTTPS**：所有与 GitHub API 和 AI provider 的通信均通过 HTTPS 加密传输。

### Provider 隔离

OpenAI-compatible 接口允许用户指向自托管模型（如通过 Ollama 或 vLLM 部署的本地模型）。设置 `OPENAI_BASE_URL=http://localhost:11434/v1` 即可确保代码数据不出企业网络。

## 开发迭代

项目按 PR 逐步演进：

```text
阶段 1：CLI MVP 与 GitHub PR 数据获取
阶段 2：GitHub Actions 自动审查与报告发布
阶段 3：报告可信度、运行完整性和降级策略
阶段 4：规则扫描增强、误报控制和 Context Pack
阶段 5：Web Console 可视化审查入口
阶段 6：ReviewMeta / FixTracking 生命周期管理
阶段 7：VS Code 插件、Review Panel、Problems、CodeLens 和 VSIX
阶段 8：使用本工具审查自身 PR，持续修复安全与交互问题
```

## 未来方向

短期：

- Local Review Mode：审查本地未提交 diff，在 push 或开 PR 前发现问题。
- Fix Tracking：对比上次 Review 建议和最新 commit，追踪问题是否已修复。
- 规则库配置化：让团队按语言、框架和风险偏好配置规则。
- Web Console 增加鉴权、repo 白名单和更稳定的服务端运行模式。

中长期：

- GitHub App / 独立服务化，集中管理 webhook、模型配置、日志和权限。
- 多仓库 Dashboard，展示 Review 历史、风险趋势和修复状态。
- 更细粒度的项目上下文索引和 RAG。
- IDE 内交互式修复建议、重新审查和本地 diff review。

## 文档导航

- [架构设计](docs/architecture.md)：核心流程、模块边界、CI 运行模型和降级策略。
- [当前开发计划与分工](docs/current-development-plan.md)：开发目标、分工和验收方式。
- [未来扩展](docs/future-extensions.md)：VS Code 扩展、服务化、Context Pack、Web UI 等路线图。
- [协作与提交规范](docs/contribution-guide.md)：分支、commit、PR 和合并前检查规范。
- [阶段性总结](docs/2026-05-29-summary.md)：早期开发进度和历史背景。
