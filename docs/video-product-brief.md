# AI PR Review 视频口播与剪辑文档

## 一句话定位

AI PR Review Assistant 是一个面向 Pull Request 生命周期的智能代码审查助手。它会自动读取 GitHub PR diff，结合本地风险规则和大模型分析生成结构化 Review 报告，并把结果同步到 GitHub、Web Console 和 VS Code IDE，让开发者能从报告直接回到代码现场。

## 视频主旨

视频要表达的不是“我们做了一个会写 Markdown 的 AI 工具”，而是：

> AI PR Review 把代码审查从人工翻 diff，升级为自动整理上下文、发现风险、解释原因、定位代码、辅助修复的工程流程。

最终观众需要记住三件事：

1. 它能自动审查 GitHub PR。
2. 它不只输出总结，还会给出风险等级、代码位置、原因和建议。
3. 它能通过 Web 和 VS Code 插件把结果带回开发者使用场景。

## 推荐视频结构

### 0. 开场：痛点，15-20 秒

画面：

- 一个文件很多、diff 很长的 PR。
- 开发者在 GitHub diff、报告、VS Code 之间来回切换。
- 简单出现风险示例：异常吞掉、敏感日志、测试绕过、workflow 修改。

口播：

> 大型 Pull Request 往往文件多、上下文长，人工审查需要先理解变更，再定位风险，还要在 GitHub 和 IDE 之间来回切换。AI PR Review Assistant 的目标不是替代审查者，而是自动整理上下文、发现高风险变更，并把问题定位回代码现场。

### 1. Web Console：快速审查任意 PR，45-60 秒

画面：

1. 打开 Web Console。
2. 粘贴 GitHub PR URL。
3. 点击 Analyze。
4. 展示顶部指标：Risk、Files、+/-、AI、Confidence、Duration。
5. 切换 Suggestions、Completeness、Report。
6. 展示复制建议、下载 JSON、打开 GitHub PR。

口播：

> 第一种入口是 Web Console。用户只需要粘贴 GitHub PR URL，系统会自动解析仓库和 PR 编号，获取 PR 元信息和变更文件。分析完成后，页面会展示整体风险、文件数量、增删行、AI 是否启用、报告可信度和耗时。
>
> 在 Suggestions 里可以看到按严重程度组织的 Review 建议；Completeness 会说明 PR 数据、规则扫描、AI 分析、上下文是否完整；Report 则保留完整 Markdown 报告。这个入口适合评委、队友或不使用 IDE 插件的用户快速体验任意 PR。

字幕关键词：

```text
Paste PR URL
Risk Overview
Suggestions
Completeness
Markdown Report
JSON Export
```

### 2. GitHub Actions：团队 PR 自动审查，60-75 秒

画面：

1. 打开 GitHub PR。
2. 展示 Actions 中的 `AI PR Review` workflow。
3. 展示 PR conversation 中的 AI Review summary comment。
4. 展示报告中的：
   - 审查元信息
   - 运行状态
   - 分析完整性
   - PR 概览
   - 风险统计
   - 评审建议
5. 如果有 inline comment，切到 Files Changed 展示代码旁边的 AI Review comment。

口播：

> 第二种入口是 GitHub Actions。PR 创建或更新后，workflow 会自动运行。它使用稳定的 base 分支 reviewer 来分析 PR diff，即使 PR 修改了审查器自身，也尽量生成可读报告。
>
> 报告顶部会记录审查目标 commit、触发事件、workflow run URL 和更新时间，保证结果可追溯。运行状态和分析完整性会告诉审查者：这份报告是完整分析、降级分析，还是部分上下文被裁剪。
>
> 对于高置信、带行号的问题，系统会发布 GitHub inline review comments；低置信或需要人工判断的内容保留在 summary report 中，避免刷屏。

字幕关键词：

```text
Auto Review on PR
ReviewMeta
Completeness
High-confidence Inline Comment
Fallback instead of Silent Failure
```

### 3. VS Code 插件：回到本地代码处理问题，60-75 秒

画面：

1. VS Code 打开项目仓库。
2. 状态栏显示 `AI Review: loading / clean / N issues`。
3. 打开 Problems 面板，展示 AI Review diagnostics。
4. 点击 Problems 中的一条建议，跳到本地文件行。
5. 点击状态栏或命令 `AI PR Review: Open Review Panel`。
6. 展示 Review Panel：
   - 左侧 inline suggestions。
   - `Open Code` 按钮。
   - 文件路径可点击。
   - 右侧完整 Markdown summary report。
7. 如果有建议，展示 CodeLens：`AI Review: HIGH 92% - ...`。

口播：

> 第三种入口是 VS Code 插件。插件会根据当前 git 分支查找对应的 open PR，从 GitHub 拉取 AI Review 结果，并映射为本地 IDE 里的 Problems、Review Panel 和 CodeLens。
>
> 开发者不用在报告和代码之间来回搜索。点击 Problems、Panel 里的 Open Code，或者代码上方的 CodeLens，就能直接跳到本地文件和对应行。这样 GitHub 上产生的 Review 结果，会回到开发者真正修代码的地方。

字幕关键词：

```text
VSIX Extension
Problems Diagnostics
Review Panel
Open Code
CodeLens
Local File Jump
```

### 4. 架构与质量保障，45-60 秒

画面：

展示一张架构图：

```text
GitHub PR
  -> GitHubClient
  -> Diff Parser
  -> Risk Rules
  -> Context Pack
  -> LLM Reviewer
  -> ReviewReport(Pydantic)
  -> Markdown / JSON
  -> GitHub Comment / Web Console / VS Code Plugin
```

可以穿插代码文件：

```text
src/github/client.py
src/analyzer/diff_parser.py
src/analyzer/context_builder.py
src/analyzer/risk_rules.py
src/reviewer/engine.py
src/output/markdown.py
src/service/app.py
vscode-extension/src/extension.ts
```

口播：

> 底层架构上，我们把 GitHub API、diff 解析、规则扫描、上下文构建、模型调用和报告渲染拆成独立模块。模型输出必须符合 Pydantic schema，再经过 changed-line、confidence、重复项和每文件数量限制过滤。
>
> 如果模型不可用，系统会降级为 rule-only 报告；如果 PR head 语法错误，仍会生成诊断报告，但 workflow 最终失败，防止坏代码被误合并。我们还用项目自身进行 dogfooding，根据 AI Review 反馈修复安全、CSP、协议校验和 IDE 交互问题。

字幕关键词：

```text
Structured Output
Confidence Filtering
Changed-line Filtering
Rule-only Fallback
PR Head Syntax Gate
Dogfooding
```

### 5. 收束：未来扩展，20-30 秒

画面：

展示未来路线：

```text
Local Review Mode
Fix Tracking
Configurable Rules
GitHub App / Service
Team Dashboard
```

口播：

> 下一步，我们会把 Review 时机继续前移。Local Review Mode 会在开发者 push 前审查本地未提交 diff；Fix Tracking 会对比新旧 commit，追踪上次建议是否已经修复。长期来看，AI PR Review 可以演进为 GitHub App 或团队级 Review 服务。

收尾句：

> AI PR Review Assistant 让每一次代码变更都自动获得一份可读、可信、可追溯的审查结果，并把问题直接带回代码现场。

## 画面优先级

如果视频时间有限，优先展示：

1. Web Console 输入 PR URL 并生成报告。
2. GitHub PR comment 中的结构化报告。
3. VS Code Problems / Review Panel 跳本地代码。
4. 一张架构图说明底层不是简单 prompt，而是工程化 Review Engine。

可以少展示：

- 长日志。
- 过多命令行安装过程。
- 过长 Markdown 报告全文。
- 复杂分支和 PR 历史。

## 需要避免的误解

视频中建议明确传达：

- 它不是替代人工审查，而是辅助审查。
- 它不会无差别评论所有问题，而是用置信度、变更行和规则过滤控制噪音。
- GitHub Actions 里的 workflow run 链接是审查溯源，不是本地代码跳转；本地跳转由 VS Code 插件完成。
- `clean` 表示没有高置信 inline suggestions，不代表没有 summary report。
- Local Review Mode 是未来扩展，当前主要围绕 GitHub PR。

## 推荐口播全文

下面是一版可以直接录制的口播稿，可根据视频节奏删减。

```text
大型 Pull Request 往往文件多、上下文长，人工审查需要先理解变更，再定位风险，还要在 GitHub 和 IDE 之间来回切换。AI PR Review Assistant 的目标不是替代审查者，而是自动整理上下文、发现高风险变更，并把问题定位回代码现场。

第一种入口是 Web Console。用户只需要粘贴 GitHub PR URL，系统会自动解析仓库和 PR 编号，获取 PR 元信息和变更文件。分析完成后，页面会展示整体风险、文件数量、增删行、AI 是否启用、报告可信度和耗时。

在 Suggestions 里可以看到按严重程度组织的 Review 建议；Completeness 会说明 PR 数据、规则扫描、AI 分析、上下文是否完整；Report 则保留完整 Markdown 报告。这个入口适合评委、队友或不使用 IDE 插件的用户快速体验任意 PR。

第二种入口是 GitHub Actions。PR 创建或更新后，workflow 会自动运行。它使用稳定的 base 分支 reviewer 来分析 PR diff，即使 PR 修改了审查器自身，也尽量生成可读报告。

报告顶部会记录审查目标 commit、触发事件、workflow run URL 和更新时间，保证结果可追溯。运行状态和分析完整性会告诉审查者：这份报告是完整分析、降级分析，还是部分上下文被裁剪。

对于高置信、带行号的问题，系统会发布 GitHub inline review comments；低置信或需要人工判断的内容保留在 summary report 中，避免刷屏。

第三种入口是 VS Code 插件。插件会根据当前 git 分支查找对应的 open PR，从 GitHub 拉取 AI Review 结果，并映射为本地 IDE 里的 Problems、Review Panel 和 CodeLens。

开发者不用在报告和代码之间来回搜索。点击 Problems、Panel 里的 Open Code，或者代码上方的 CodeLens，就能直接跳到本地文件和对应行。这样 GitHub 上产生的 Review 结果，会回到开发者真正修代码的地方。

底层架构上，我们把 GitHub API、diff 解析、规则扫描、上下文构建、模型调用和报告渲染拆成独立模块。模型输出必须符合 Pydantic schema，再经过 changed-line、confidence、重复项和每文件数量限制过滤。

如果模型不可用，系统会降级为 rule-only 报告；如果 PR head 语法错误，仍会生成诊断报告，但 workflow 最终失败，防止坏代码被误合并。我们还用项目自身进行 dogfooding，根据 AI Review 反馈修复安全、CSP、协议校验和 IDE 交互问题。

下一步，我们会把 Review 时机继续前移。Local Review Mode 会在开发者 push 前审查本地未提交 diff；Fix Tracking 会对比新旧 commit，追踪上次建议是否已经修复。长期来看，AI PR Review 可以演进为 GitHub App 或团队级 Review 服务。

AI PR Review Assistant 让每一次代码变更都自动获得一份可读、可信、可追溯的审查结果，并把问题直接带回代码现场。
```

## 镜头清单

| 顺序 | 画面 | 目的 |
|---|---|---|
| 1 | 大 PR / 长 diff | 建立痛点 |
| 2 | Web Console 粘贴 PR URL | 展示开箱即用 |
| 3 | Web Console Suggestions / Completeness / Report | 展示分析结果 |
| 4 | GitHub Actions run | 展示自动化 |
| 5 | PR summary comment | 展示团队协作入口 |
| 6 | GitHub inline comment | 展示高置信建议贴近 diff |
| 7 | VS Code Problems | 展示 IDE 诊断 |
| 8 | Review Panel + Open Code | 展示本地跳转 |
| 9 | CodeLens | 展示代码 inline 入口 |
| 10 | 架构图 | 展示工程质量 |
| 11 | 未来路线 | 收束扩展方向 |

## 评分点对应表达

### 作品完整度与创新性

表达重点：

```text
不是单一脚本，而是 GitHub Actions、Web Console、VS Code 插件三入口产品。
不是简单文本生成，而是结构化 ReviewReport、置信度控制、代码联动和 IDE 回流。
```

### 开发过程与质量

表达重点：

```text
模块化架构、Pydantic schema、fallback、误报控制、测试、PR 迭代、dogfooding。
```

### 演示与表达

表达重点：

```text
Web 快速体验 -> GitHub 自动协作 -> VS Code 本地处理，形成完整用户旅程。
```
