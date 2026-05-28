# 架构设计

## 架构目标

系统需要在准确性、速度和可解释性之间取平衡。设计原则：

- 先用 GitHub diff 和本地规则缩小问题空间，再让模型分析
- 模型输入必须包含 PR 意图、文件级上下文、变更片段和规则命中证据
- 模型输出必须结构化，不能直接把自然语言当作最终结果
- 最终评论要经过本地过滤，避免对未变更行和无证据问题发表评论

## 核心流程

```text
User CLI
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

### GitHub Client

职责：

- 调用 GitHub REST API
- 获取 PR 标题、描述、作者、base/head branch
- 获取 changed files、patch、additions/deletions
- 后续扩展获取 issue comments、review comments、CI 状态

### Diff Parser

职责：

- 解析 unified diff hunk
- 建立新增行到 GitHub new line 的映射
- 提取变更片段，避免将整文件交给模型

### Risk Rules

职责：

- 在模型前进行低成本预筛
- 发现确定性或半确定性风险信号
- 给模型提供证据，减少自由猜测

第一批规则：

- 鉴权/权限相关文件变更
- SQL 字符串拼接
- shell 命令执行
- eval/exec/反序列化
- catch 后吞掉异常
- 删除测试或跳过测试
- 日志中出现 token/password/secret
- 复杂函数新增过多行

### Context Builder

职责：

- 控制 token 预算
- 给每个文件构建紧凑上下文
- 优先包含高风险文件、规则命中片段、PR 标题和描述

上下文优先级：

1. PR 标题和描述
2. changed files 列表和统计
3. 规则命中的 hunk
4. 高风险文件的新增行
5. 普通文件摘要

### Review Engine

职责：

- 调用模型
- 要求结构化 JSON 输出
- 对结果进行 schema 校验
- 在模型失败时降级到规则报告

### Result Filter

职责：

- 丢弃不在变更行上的行级建议
- 合并重复建议
- 按 severity、confidence、文件风险排序
- 控制评论数量，避免刷屏

## 数据流边界

GitHub API 原始数据不会直接进入输出层，而是先转换为内部模型：

- `PullRequest`
- `ChangedFile`
- `DiffHunk`
- `RiskFinding`
- `ReviewSuggestion`
- `ReviewReport`

这样可以让 CLI、Web UI、GitHub App 共用同一套核心分析逻辑。
