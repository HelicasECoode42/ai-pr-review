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

## 上下文获取设计权衡

上下文如何构造是 AI 审查质量的核心影响因素。本节解释设计中选择"patch hunk 而非全量文件"和"显式 budget 控制"的原因。

### 为什么用 patch hunk 而不用全量文件？

PR Review 的定义决定了关注点是「变更了什么」，而非「整个文件是什么」。传入全量文件有两个问题：

1. **Token 浪费**：一个 500 行的文件只改了 3 行，传入全部 500 行会让 99% 的 token 花在模型已经能推理出的旧代码上。
2. **注意力稀释**：模型在大量不变更代码中搜索问题时，更容易产生幻觉或给出与本次变更无关的评论。

Unified diff 的 patch hunk 天然携带上下文行（默认 `@@` 头标记的 3 行上下文），足以让模型理解变更的「前后环境」而不需要整个文件。

### 为什么限制 token 数量？

默认 budget：patch 上下文 24,000 字符 + Context Pack 4,000 字符。

- **审查质量非线性**：更大的上下文并不带来更好的审查结果。实测表明，超过 30K 字符后模型开始遗漏低信号但关键的问题，或产生重复建议。
- **成本可控**：24K 字符约 6K-8K tokens，单次审查的模型成本控制在 ~$0.01 以内（以 gpt-4.1-mini 计），使高频自动审查在经济上可行。
- **响应速度**：较小的 prompt 意味着更低的模型延迟，Web Console 用户等待时间通常在 5-15 秒。

### 哪些文件被跳过？为什么？

| 文件类型 | 跳过原因 |
|----------|----------|
| Lockfile（`uv.lock`、`package-lock.json` 等） | 纯依赖版本号变更，机械性内容，无审查价值；一个 lockfile 可能 10K+ 行 |
| Demo 报告（`docs/demo/`） | 生成 artifact，非源代码 |
| 生成报告（`reports/`） | CI 输出产物，非源代码 |
| 二进制文件（图片、归档、编译产物） | 无可读 patch，解析出乱码会污染 context |
| 编码异常文件（高密度 replacement char 或 NUL 字节） | 与二进制同理，模型无法理解乱码 |

被跳过的文件在报告的 `skipped_context_files` 中列出，变更统计仍然保留。

### 新增/删除文件的 fallback

对于 `status=added` 或 `status=removed` 的文件，patch 可能为空或只有 metadata。此时系统会通过 GitHub API 获取文件全文（从 head_ref 或 base_ref），而非直接放弃审查。这是唯一会向外传「全量文件」的场景——但它仅限于该 PR 新增的文件，而不是整个仓库。

### 上下文截断的透明化

当 patch budget 耗尽时，系统会：
- 在 context 末尾追加 `"Patch budget exhausted. Remaining files omitted."`
- 在报告的 `analysis_warnings` 中记录
- 设置 `context_truncated = true`
- 在 Completeness 面板中展示 `"Patch 上下文: 裁剪 — 超出 token 预算"`

审查者看到这些标记后，可以对被截断文件做补充人工审查。

## 误报控制管线

AI 审查最大的挑战不是「找到问题」，而是「不说废话」。系统通过多层过滤将模型输出收敛为高信号建议。

### 过滤管线（按顺序执行）

```text
模型原始输出（≤20 条建议）
  │
  ├─ 1. Schema 校验 ─────────── Pydantic 强制；不符合 schema 的建议被丢弃
  │    丢弃条件：缺 reason / recommendation，confidence 不在 [0,1]，severity 非法
  │
  ├─ 2. 置信度过滤 ──────────── confidence < min_comment_confidence（默认 0.65）
  │    被过滤数：hidden_suggestions_count
  │
  ├─ 3. Changed-line 过滤 ───── 行级建议必须指向本 PR 的新增行
  │    丢弃条件：line 不在 diff_parser 提取的 added_lines 集合中
  │
  ├─ 4. 去重合并 ────────────── 同文件 + 同行 + 同标题 → 保留第一条
  │
  ├─ 5. 每文件限制 ──────────── 单文件最多 max_suggestions_per_file（默认 5）
  │    超出部分丢弃（保留高严重度优先）
  │
  └─ 6. 全局限制 ────────────── 总计最多 max_suggestions（默认 20）
       超出部分丢弃（保留高严重度优先）
```

### 各阶段的典型过滤效果

| 阶段 | 输入 | 典型输出 | 过滤比例 |
|------|------|----------|----------|
| 模型原始输出 | — | ~12-18 条 | — |
| Schema 校验 | ~15 条 | ~14 条 | ~7%（格式问题） |
| 置信度过滤 | ~14 条 | ~10 条 | ~30%（低置信建议） |
| Changed-line 过滤 | ~10 条 | ~8 条 | ~20%（指向未变更行） |
| 去重 | ~8 条 | ~7 条 | ~12%（同根因重复） |
| 每文件限制 | ~7 条 | ~7 条 | 小 PR 通常不触发 |
| **最终输出** | — | **~6-10 条** | **总体过滤 ~50%** |

> 注：以上为典型 PR（10-20 文件变更）的估算。实际过滤比例因 PR 规模、变更类型和模型输出质量而异。极端情况下（如模型不太确定的 PR），置信度过滤可能丢弃 50%+ 的建议。

### 规则扫描的噪音控制

规则扫描也有自己的过滤：
- `docs/demo/` 和 `reports/` 前缀的文件不做规则扫描（它们不是源代码）
- 语言特定规则（如 `swallowed-exception-python`）只在语言匹配时触发
- `hidden_rule_findings_count` 追踪被过滤的规则命中

### 为什么不让 AI 自己控制噪音？

早期的 prompt-only 方案（让 AI 「只输出高价值建议」）效果不佳——模型在缺少约束时倾向于「多说」而非「说对」。结构化输出 + 本地过滤的组合让控制权回到系统侧：模型可以宽松输出（宁可多一些，不要漏掉），系统根据客观规则做最终筛选。

## 扩展接口

当前 JSON 报告已经适合作为后续集成边界：

- GitHub Action inline comments。
- Web UI / Dashboard。
- VS Code Problems 面板。
- 本地交互式 review 工具。

未来扩展应优先保持 `ReviewReport` schema 稳定，避免每个前端重复解析 Markdown。
