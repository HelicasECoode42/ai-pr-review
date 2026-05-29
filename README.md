# AI PR Review Assistant

AI PR Review Assistant 是一个面向 GitHub Pull Request 的代码评审辅助工具。用户输入 `owner/repo` 和 PR 编号后，工具自动获取 PR 元信息、文件变更与 diff，结合规则扫描和大模型分析生成：

- PR 变更总结
- 高风险代码识别
- 行级 Review 建议
- 可提交到 GitHub 的 Review 草稿
- Markdown / JSON 报告

本仓库当前是 72 小时双人开发版本的初始化骨架，目标是先做出可演示的 MVP，再逐步增强上下文理解和误报控制。

## 为什么做这个工具

开发者在 PR Review 中的真实痛点通常不是“看不懂代码”，而是：

- PR 大、文件多，快速建立全局理解成本高
- 容易漏看异常处理、鉴权、并发、性能、兼容性等风险点
- Reviewer 时间碎片化，难以持续保持同等质量
- 自动化 lint/test 能发现格式和确定性错误，但发现不了设计意图偏差
- AI 直接整段评论容易误报，需要控制评论密度、证据链和置信度

因此本项目采用“规则预筛 + 上下文构建 + LLM 结构化分析 + 结果过滤”的组合方式，而不是让模型直接读完整 PR 后自由发挥。

## 技术栈

后端/CLI：

- Python 3.10+
- Typer：命令行入口
- httpx：GitHub REST API 调用
- Pydantic：配置和结构化数据模型
- Rich：终端展示
- GitPython：后续支持本地仓库 checkout 与上下文提取
- tree-sitter：后续支持 AST 级函数/类定位
- pytest / ruff / mypy：测试、格式和类型检查

AI 能力：

- 默认支持 OpenAI 兼容 Chat Completions 接口
- 通过 `ReviewModelProvider` 抽象预留 Anthropic、本地模型、Azure OpenAI
- 输出强制为结构化 JSON，再由本地代码校验、过滤、排序和渲染

前端/体验扩展：

- MVP 先提供 CLI 和 Markdown 报告
- 72h 内可选加一个轻量 Web UI，用于输入 PR、查看风险列表、复制 Review 评论
- GitHub App / GitHub Action 作为后续部署方向

## 快速开始

### 方式一：使用 uv（推荐）

```bash
git clone <repo-url> && cd ai-pr-review
uv sync
uv run ai-pr-review analyze owner/repo 123 --no-ai
```

### 方式二：使用 venv + pip

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 配置环境变量

```bash
export GITHUB_TOKEN="ghp_xxx"
export OPENAI_API_KEY="sk-xxx"
```

或创建 `.env` 文件：

```ini
GITHUB_TOKEN=ghp_xxx
OPENAI_API_KEY=sk-xxx
```

### 基本用法

```bash
# AI + 规则分析
ai-pr-review analyze owner/repo 123 --format markdown --output reports/pr-123.md

# 仅规则扫描（无需 API Key）
ai-pr-review analyze owner/repo 123 --no-ai

# JSON 输出
ai-pr-review analyze owner/repo 123 --format json
```

## 仓库结构

```text
ai-pr-review/
├─ src/
│  ├─ cli/          CLI 入口
│  ├─ github/       GitHub API、PR 数据获取
│  ├─ analyzer/     diff 解析、上下文构建、风险规则扫描
│  ├─ reviewer/     LLM provider、prompt、结构化 Review 生成
│  ├─ output/       Markdown / JSON 渲染
│  └─ utils/        配置、日志、通用工具
├─ tests/           单元测试和集成测试占位
├─ docs/            架构、分工、模型与上下文策略、扩展方向
├─ scripts/         本地演示脚本
└─ reports/         本地生成报告目录，已加入 .gitignore
```

## 72h MVP 验收标准

- 能输入 GitHub PR 并拉取 PR 标题、描述、文件列表、patch
- 能生成 PR 变更总结
- 能识别明显高风险变更：鉴权、SQL、命令执行、反序列化、异常吞掉、测试删除等
- 能输出带文件路径和行号的 Review 建议
- 能以 Markdown 报告展示，支持 JSON 供前端或 GitHub 评论使用
- 在 README/docs 中说明模型选择、上下文获取方式、误报控制、未来扩展

## 文档

- [72 小时双人开发计划](docs/72h-plan.md)
- [协作与提交规范](docs/contribution-guide.md)
- [Agent 辅助开发规范](docs/agent-coding-guide.md)
- [架构设计](docs/architecture.md)
- [模型选择与上下文策略](docs/model-context-design.md)
- [误报与漏报控制](docs/quality-control.md)
- [未来扩展方向](docs/future-extensions.md)
