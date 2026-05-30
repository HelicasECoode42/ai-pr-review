# 架构设计

本文描述 AI PR Review Assistant 当前的核心架构、数据流、降级策略和 CI 运行模型。

## 设计目标

系统需要在准确性、速度、可解释性和可维护性之间取平衡：

- 使用 GitHub diff 和本地规则先缩小问题空间，再调用模型。
- 模型输入包含 PR 意图、文件级上下文、patch 片段和规则命中证据。
- 模型输出必须结构化，不能把自然语言直接当最终结果。
- 最终建议要经过本地过滤，避免对未变更行、低置信或重复问题发表评论。
- PR 分支代码即使坏掉，也不能阻止 reviewer 产出诊断报告。
- 报告生成和合并门禁分离：坏 PR 可以出报告，但不能被标成可合并。

## 核心流程

```text
CLI / GitHub Action
  |
  v
GitHub Client
  |
  v
PR Metadata + Changed Files + Patch
  |
  v
Diff Parser
  |
  +--> Changed line map
  +--> Added/removed code hunks
  |
  v
Risk Rule Scanner
  |
  v
Context Builder
  |
  v
LLM Review Engine
  |
  v
Result Filter
  |
  v
Markdown / JSON Report
```

## 模块说明

### CLI

位置：`src/cli/main.py`

职责：

- 读取命令行参数和环境配置。
- 通过 GitHub API 获取 PR 信息。
- 调用规则扫描、AI review 和报告渲染。
- 在 GitHub API、import、AI provider 或运行时异常时尽量降级生成诊断报告。
- 在 CI fallback 场景中写入 reviewer 来源、执行状态、降级原因和报告可信度。

### GitHub Client

位置：`src/github/client.py`

职责：

- 调用 GitHub REST API。
- 获取 PR 标题、描述、作者、base/head、head SHA 和 URL。
- 获取 changed files、patch、additions/deletions。
- 将 GitHub API 原始数据转换为内部模型。

### Diff Parser

位置：`src/analyzer/diff_parser.py`

职责：

- 解析 unified diff hunk。
- 建立新增行到 GitHub new line 的映射。
- 提取新增行和删除行，供规则扫描和建议过滤使用。
- 对格式异常的 patch 做容错，避免单个文件解析失败拖垮整个报告。

### Risk Rules

位置：`src/analyzer/risk_rules.py`

职责：

- 在模型调用前进行低成本规则预筛。
- 识别路径风险和代码模式风险。
- 为模型提供证据，减少自由猜测。

当前内置规则覆盖：

- 鉴权、权限、session、jwt 等高风险路径。
- payment、billing、migration 等高风险路径。
- SQL 字符串拼接。
- shell 命令执行。
- eval/exec 动态执行。
- 敏感信息日志。
- 吞异常或隐藏失败。
- 测试跳过。
- 测试断言删除。

### Context Builder

位置：`src/analyzer/context_builder.py`

职责：

- 控制模型上下文预算。
- 汇总 PR 元数据、变更文件、规则命中和 patch。
- 优先保留高风险文件和规则命中文件。
- 跳过 lockfile、生成报告和 demo artifact 等低价值 patch。
- 标记上下文是否被截断，以及哪些文件被跳过。

### Review Engine

位置：`src/reviewer/engine.py`

职责：

- 构造模型 prompt。
- 调用 `ReviewModelProvider`。
- 解析模型 JSON 输出。
- 过滤建议并生成 `ReviewReport`。
- AI 调用失败时降级为 rule-only 报告。
- 保留运行状态字段，供 Markdown / JSON 显示。

### Provider

位置：`src/reviewer/provider.py`

职责：

- 抽象模型调用接口。
- 当前实现 OpenAI-compatible Chat Completions。
- 将 provider 网络、HTTP 和模型输出错误包装为可降级错误。

### Output

位置：`src/output/markdown.py`、`src/output/json_report.py`

职责：

- 渲染 Markdown 报告。
- 输出 JSON 报告。
- 展示运行状态、完整性、建议、规则命中、警告和跳过文件。
- 为自动评论和未来 IDE / Web UI 集成提供结构化数据。

## 数据模型

核心模型位于 `src/models.py`：

- `PullRequest`
- `ChangedFile`
- `ChangedLine`
- `DiffHunk`
- `RiskFinding`
- `ReviewSuggestion`
- `SkippedContextFile`
- `CompletenessItem`
- `ReviewReport`

这些模型让 CLI、GitHub Action、未来 Web UI、GitHub App 和 VS Code 扩展可以复用同一套分析结果。

## GitHub Actions 运行模型

CI 中最重要的设计是“稳定 reviewer”和“PR head 诊断”分离。

```text
Checkout base branch reviewer
  |
  +--> compileall src tests
  |
  v
Checkout PR head into _pr_head/
  |
  +--> compileall _pr_head/src _pr_head/tests
       |
       +--> failure is recorded but does not stop review
  |
  v
Run AI PR Review against the PR diff
  |
  v
Append syntax diagnostics if any
  |
  v
Upload report and post comments
  |
  v
Fail final gate if PR head syntax check failed
```

这么做的原因：

- 如果 PR 修改 reviewer 自己并引入 bug，base 分支 reviewer 仍能运行。
- 如果 PR head 代码语法错误，报告仍能生成并指出错误。
- 如果 PR head 语法错误，workflow 最后仍失败，避免误合并。

语义上：

```text
报告生成成功：reviewer 成功完成诊断
workflow 成功：PR 通过当前门禁
```

二者不能混为一谈。

## 降级策略

工具尽量保证“有用的报告优先”：

- GitHub API 获取失败：输出最小失败报告，CI 可归档 artifact。
- analyzer/reviewer import 失败：输出诊断报告，提示 reviewer 代码可能被 PR 改坏。
- AI provider 失败：降级为 rule-only 报告。
- patch 解析失败：跳过单文件并标记部分分析。
- patch 上下文超预算：截断并记录 warning。
- PR head 语法失败：追加语法诊断，最后让 workflow 失败。

## 本地过滤策略

模型建议进入最终报告前会被过滤：

- 低于置信度阈值的建议会被隐藏。
- 没有 reason 或 recommendation 的建议会被丢弃。
- 指向未变更新增行的行级建议会被丢弃。
- 同文件、同行、同标题的建议会去重。
- 每个文件建议数量有上限。
- 全局建议数量有上限。

## 扩展接口

当前 JSON 报告已经适合作为后续集成边界：

- GitHub Action inline comments。
- Web UI / Dashboard。
- VS Code Problems 面板。
- 本地交互式 review 工具。

未来扩展应优先保持 `ReviewReport` schema 稳定，避免每个前端重复解析 Markdown。
