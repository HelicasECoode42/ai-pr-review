# AI PR Review Assistant

基于大模型的 GitHub Pull Request 代码评审工具。指定 `owner/repo` 和 PR 编号后，自动获取 diff、执行规则扫描、调用 LLM 生成结构化 Review 报告，并通过本地过滤降低误报。

## 核心思路

**规则预筛 → 上下文构建 → LLM 结构化分析 → 本地过滤 → Markdown / JSON 报告**

不是让模型直接读完整 PR 后自由发挥，而是先用规则扫描缩小问题空间，再构造受控上下文交给模型，最后对模型输出做 schema 校验、changed-line 过滤、置信度过滤和去重。

## 为什么做这个工具

- 大 PR 文件多，快速建立全局理解成本高
- 容易漏看鉴权、异常处理、并发、SQL 注入等风险点
- 自动化 lint/test 能发现格式和确定性错误，发现不了设计意图偏差
- 直接让 AI 评论容易误报，需要控制评论密度、证据链和置信度

## 环境准备

### 依赖

- Python 3.10+
- uv（推荐）或 pip

### 安装

```bash
git clone https://github.com/HelicasECoode42/ai-pr-review.git
cd ai-pr-review
uv sync --extra dev
```

或使用 venv + pip：

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 配置

创建 `.env` 文件（不要提交）：

```ini
# GitHub API token（提高限额，访问私有仓库）
GITHUB_TOKEN=ghp_xxx

# OpenAI 兼容 API
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
REVIEW_MODEL=gpt-4.1-mini
```

公开仓库可以不配置 `GITHUB_TOKEN`，但匿名访问容易触发 GitHub API rate limit。建议配置 token；fine-grained token 至少需要 `Metadata: Read`、`Pull requests: Read`、`Contents: Read`。

**DeepSeek 配置示例**：

```ini
OPENAI_API_KEY=你的 DeepSeek API Key
OPENAI_BASE_URL=https://api.deepseek.com/v1
REVIEW_MODEL=deepseek-chat
```

本项目通过 OpenAI-compatible API 调用模型，可接入 DeepSeek、Azure OpenAI 等兼容服务。使用 DeepSeek 等兼容服务时，需要同时设置 `OPENAI_BASE_URL` 和 `REVIEW_MODEL`，否则会默认请求 OpenAI 官方地址和默认模型。

## 用法

### AI + 规则分析

```bash
uv run python -m src.cli.main owner/repo 123 \
  --language zh \
  --output reports/pr-123.md
```

### 仅规则扫描（无需 API Key）

```bash
uv run python -m src.cli.main owner/repo 123 \
  --no-ai \
  --language zh \
  --output reports/pr-123.md
```

### JSON 输出

```bash
uv run python -m src.cli.main owner/repo 123 \
  --format json \
  --output reports/pr-123.json
```

### 真实 PR 示例

```bash
uv run python -m src.cli.main HelicasECoode42/ai-pr-review 1 \
  --language zh \
  --output reports/pr-1-ai.md
```

### 可用参数

| 参数 | 说明 |
|---|---|
| `--output` / `-o` | 报告输出路径 |
| `--format` | `markdown`（默认）或 `json` |
| `--ai` / `--no-ai` | 是否调用 AI 模型 |
| `--language` | `en`（默认）或 `zh` |

## GitHub Action（一键运行）

创建 PR 时自动触发 AI Review，生成报告 artifact 并发到 PR 评论区。

### 配置 Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 |
|---|---|
| `OPENAI_API_KEY` | 模型 API Key |
| `OPENAI_BASE_URL` | 模型 API 地址（如 `https://api.deepseek.com/v1`） |
| `REVIEW_MODEL` | 模型名称（如 `deepseek-chat`） |

`GITHUB_TOKEN` 由 GitHub Actions 自动提供，无需手动配置。

### 触发方式

- **自动**：PR 创建、推送新 commit、reopen 时自动运行
- **手动**：Actions → AI PR Review → Run workflow，可指定 PR 编号和语言

> 来自 fork 的 PR 可能无法访问仓库 Secrets（`OPENAI_API_KEY` 等），此时会自动降级为 rule-only 报告。这是 GitHub 安全机制，非 bug。

## 测试

```bash
uv run pytest
```

当前状态：25 passed。

## 报告结构

在 `--language zh` 模式下，AI 生成的摘要、建议标题、原因和修复方案会以中文输出。Markdown 报告结构包含：

- **PR 概览** — 仓库、作者、变更统计、风险等级
- **风险统计** — 按严重程度汇总
- **变更总结** — AI 生成的 PR 意图和影响面分析
- **文件变更** — 变更文件清单和增删统计
- **评审建议** — 行级 Review 建议，含位置、原因、修复方案、可复制 GitHub 评论
- **规则扫描结果** — 本地规则命中情况
- **分析备注** — 过滤说明、上下文截断提示、AI 失败回退信息

## 设计说明

### 模型选择

使用支持 JSON 输出的 OpenAI-compatible Chat Completions 模型（如 GPT-4.1、DeepSeek）。采用低 temperature（0.1）和 `response_format: json_object` 约束，降低随机性，保证输出结构稳定。通过 `ReviewModelProvider` 协议抽象，可接入 OpenAI、DeepSeek、Azure OpenAI 等兼容服务。

### 上下文获取

从 GitHub API 获取三层上下文：

1. **PR 级**：标题、描述、作者、base/head、文件列表
2. **Diff 级**：unified diff hunk、新增行行号映射
3. **规则证据**：本地规则扫描命中的高风险片段

上下文构造采用优先级裁剪：高风险文件和规则命中 hunk 优先保留，lockfile（`uv.lock`、`package-lock.json` 等）不进入 AI patch 上下文以节省 token。超预算时裁剪普通大文件并标记 `context_truncated`。

### 误报控制

多层次的本地过滤：

- 只评论 changed added lines，不对未变更行发表建议
- Pydantic schema 校验模型输出
- 置信度阈值过滤（`min_comment_confidence`，默认 0.65）
- 每文件建议数量上限（`max_suggestions_per_file`，默认 5）
- 去重（同文件同标题同行的建议只保留一条）
- 空 reason 或 recommendation 的建议丢弃
- 测试文件中的 secret-logging 规则命中降权到 LOW
- AI 调用失败自动回退到 rule-only 报告，不崩溃

### 响应速度

- 规则扫描本地执行，无网络开销
- patch budget 限制（默认 24,000 字符）
- max_suggestions 限制（默认 20）
- lockfile patch 不消耗 AI 上下文
- 大 PR 上下文裁剪后标记 `context_truncated`

## 仓库结构

```text
├─ src/
│  ├─ cli/           CLI 入口（Typer）
│  ├─ github/        GitHub REST API 客户端
│  ├─ analyzer/      diff 解析、上下文构建、风险规则扫描
│  ├─ reviewer/      LLM provider、prompt、结构化 Review 引擎
│  ├─ output/        Markdown / JSON 渲染
│  └─ utils/         配置管理
├─ tests/            单元测试
├─ docs/             架构与设计文档
└─ reports/          本地报告输出（.gitignore）
```

## 未来扩展

- **GitHub App**：PR 事件触发并通过 Webhook 自动发布 review comments
- **Demo fixture**：无网络无 API Key 可演示的离线模式
- **项目级上下文索引**：RAG 检索同仓库函数/类定义、历史 Review、团队规范
- **团队规则库**：可配置高风险路径、禁止 API、Review 偏好
- **Web UI**：输入 PR、查看风险列表、一键复制评论

## 示例报告

- [Rule-only 基线报告（无 AI）](docs/demo/pr-1-rule.md)
- [AI Review 中文报告](docs/demo/pr-1-ai-stage5.md)

## 文档

- [架构设计](docs/architecture.md)
- [72 小时双人开发计划](docs/72h-plan.md)
- [模型选择与上下文策略](docs/model-context-design.md)
- [误报与漏报控制](docs/quality-control.md)
- [未来扩展方向](docs/future-extensions.md)
