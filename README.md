# AI PR Review Assistant

一个面向 GitHub Pull Request 的 AI 代码审查工具。它会获取 PR 元数据和 diff，先执行本地规则扫描，再把受控上下文交给 LLM 生成结构化 Review 报告，最后输出 Markdown / JSON，并可在 GitHub Actions 中自动发布 PR 评论。

## 核心思路

```text
GitHub PR diff
  -> 规则预筛
  -> 上下文构建
  -> LLM 结构化分析
  -> 本地过滤
  -> Markdown / JSON 报告
```

工具不会把完整仓库直接丢给模型自由发挥，而是先用确定性规则缩小问题空间，再把 PR 标题、描述、变更文件、patch hunk 和规则证据组织成可控上下文。模型输出必须通过 Pydantic schema 校验，并经过 changed-line、置信度、重复项和每文件数量限制等本地过滤。

## 解决的问题

- 大 PR 文件多，人工快速建立全局理解成本高。
- 鉴权、异常处理、SQL 注入、命令执行、敏感日志等风险点容易漏看。
- lint/test 能发现确定性错误，但发现不了很多意图偏差和设计风险。
- 直接让 AI 评论容易噪声太大，需要结构化输出、证据链和本地过滤。
- PR 代码本身可能已经坏掉，审查工具不能因此完全跑不出报告。

## 当前能力

- 通过 GitHub REST API 获取 PR 元数据、变更文件和 patch。
- 本地规则扫描高风险路径和高风险代码模式。
- OpenAI-compatible provider 调用，支持 OpenAI、DeepSeek、Azure OpenAI 等兼容服务。
- AI 失败时自动降级为 rule-only 报告。
- 输出 Markdown 和 JSON 报告。
- GitHub Actions 自动运行、上传 artifact、发布 summary comment 和高置信 inline comment。
- CI 中使用 base 分支稳定 reviewer 分析 PR diff，避免 PR 把 reviewer 自己改坏后无法出报告。
- 对 PR head 额外执行 Python 语法诊断；诊断失败时仍生成报告，但最后让 workflow 失败，阻止误合并。

## 安装

推荐使用 `uv`：

```bash
git clone https://github.com/HelicasECoode42/ai-pr-review.git
cd ai-pr-review
uv sync --extra dev
```

也可以使用 venv + pip：

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## 配置

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

最后四个参数主要给 CI / fallback 场景使用，普通本地运行一般不需要传。

## GitHub Actions

仓库内置 `.github/workflows/ai-pr-review.yml`。PR 创建、同步或重新打开时会自动运行；也可以手动触发 workflow。

### 运行语义

这个 workflow 故意区分两件事：

```text
报告能否生成 != PR 是否可以合并
```

具体流程：

1. Checkout PR 的 base 分支，使用稳定 reviewer 运行审查工具。
2. 安装依赖并检查 base 分支 reviewer 自身语法。
3. 额外 checkout PR head 到 `_pr_head/`。
4. 对 `_pr_head/src` 和 `_pr_head/tests` 执行 Python 语法诊断。
5. 即使 PR head 语法失败，也继续运行 AI PR Review 并生成报告。
6. 将语法诊断追加到 `reports/pr-review.md`，并上传 artifact。
7. 尝试发布 summary comment 和高置信 inline comment。
8. 如果 PR head 语法失败，最后一步显式 `exit 1`，让 workflow 标红，阻止合并。

也就是说，坏 PR 的预期结果是：

```text
review 报告生成：是
workflow 最终成功：否
PR 可合并：否
```

这能避免“代码坏了导致完全没有报告”，也能避免“有报告所以误以为可以合并”。

### Secrets

在仓库 Settings -> Secrets and variables -> Actions 中配置：

| Secret | 说明 |
|---|---|
| `OPENAI_API_KEY` | 模型 API Key |
| `OPENAI_BASE_URL` | OpenAI-compatible API 地址，例如 `https://api.deepseek.com/v1` |
| `REVIEW_MODEL` | 模型名称，例如 `deepseek-chat` |

`GITHUB_TOKEN` 由 GitHub Actions 自动提供，无需手动配置。

来自 fork 的 PR 可能无法访问仓库 Secrets，此时会自动降级为 rule-only 报告。这是 GitHub 的安全机制，不是工具 bug。

## 报告内容

Markdown 报告包含：

- 运行状态：reviewer 来源、执行状态、降级原因、报告可信度。
- 分析完整性：PR 元数据、变更文件、AI 上下文、AI 分析、规则扫描、patch 上下文是否完整。
- PR 概览：仓库、PR、作者、base/head、变更统计、整体风险。
- 变更总结：AI 或规则生成的摘要。
- 文件变更：文件列表和增删行统计。
- Review 建议：位置、严重程度、置信度、原因和修复建议。
- 规则扫描结果：本地规则命中的风险证据。
- 分析备注：AI 失败、上下文截断、过滤项等说明。
- PR head 语法诊断：如果 PR 提交的 Python 代码无法编译，会追加错误摘要。

JSON 报告用于后续自动评论、IDE 集成和 Web UI。

## 测试

```bash
uv run --extra dev pytest
```

也可以先做语法检查：

```bash
python -m compileall src tests
```

## 仓库结构

```text
src/
  cli/           Typer CLI 入口
  github/        GitHub REST API 客户端
  analyzer/      diff 解析、上下文构建、风险规则扫描
  reviewer/      LLM provider、prompt、结构化 Review 引擎
  output/        Markdown / JSON 渲染
  utils/         配置管理
tests/           单元测试
docs/            架构、质量控制、路线图等文档
reports/         本地报告输出目录，已 gitignore
```

## 未来方向

短期重点：

- 完善本地 / 离线 demo provider。
- 将规则库配置化。
- 提升 CI 诊断和失败报告的可读性。
- 搭建 VS Code 扩展 MVP，把 JSON suggestions 映射成 Problems 面板和本地行级跳转。

中长期方向：

- GitHub App / 独立服务化。
- Web UI / Dashboard。
- 自动发布 Review Comments 的策略治理。
- 项目级上下文索引和 RAG。
- IDE 内的 PR review、merge 预览和交互式修复体验。

更多细节见 [架构设计](docs/architecture.md) 和 [未来扩展](docs/future-extensions.md)。

## 文档导航

- [架构设计](docs/architecture.md)：当前核心流程、模块边界、CI 运行模型和降级策略。
- [当前开发计划与分工](docs/current-development-plan.md)：报告置信度、Web Console、VS Code 插件的开发分工和对应文件。
- [未来扩展](docs/future-extensions.md)：VS Code 扩展、服务化、Context Pack、Web UI 等路线图。
- [协作与提交规范](docs/contribution-guide.md)：分支、commit、PR 和合并前检查规范。
- [阶段性总结](docs/2026-05-29-summary.md)：早期开发进度和历史背景。
