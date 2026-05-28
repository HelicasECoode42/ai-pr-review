# 协作与提交规范

本文档用于统一双人 72 小时开发期间的分支、提交、PR 和沟通方式。目标是让两个人可以并行推进，又能在合并时快速理解彼此做了什么。

## 开发节奏

建议每天至少同步 3 次：

- 开工同步：确认今天各自负责的模块和接口边界
- 中段同步：对齐阻塞点、数据模型变化、接口变化
- 收工同步：确认已完成内容、未完成风险、第二天优先级

每次同步只关注三件事：

- 我完成了什么
- 我接下来做什么
- 我需要对方配合什么

## 分支策略

主分支：

- `main`：始终保持可运行，不直接在上面开发

功能分支命名：

- `feat/github-client`
- `feat/risk-rules`
- `feat/llm-review`
- `feat/report-output`
- `docs/demo-script`
- `fix/diff-line-map`

命名规则：

- 小写英文
- 用 `/` 区分类型
- 用 `-` 连接单词
- 分支名能看出主要改动范围

## 提交方式

采用简化版 Conventional Commits：

```text
<type>(<scope>): <summary>
```

常用 type：

- `feat`：新增功能
- `fix`：修复 bug
- `docs`：文档
- `test`：测试
- `refactor`：不改变行为的重构
- `chore`：配置、依赖、脚本
- `style`：格式调整，不改变逻辑

scope 建议使用模块名：

- `github`
- `analyzer`
- `reviewer`
- `output`
- `cli`
- `docs`
- `tests`

示例：

```text
feat(github): fetch pull request files with pagination
feat(analyzer): add risky shell execution rule
fix(diff): map added line numbers correctly
docs(plan): add 72h collaboration schedule
test(rules): cover secret logging detection
chore(project): configure hatch build package
```

## 单次提交原则

一次提交应该表达一个完整意图：

- 可以是一个小功能
- 可以是一个 bug fix
- 可以是一组对应测试
- 不要把无关文档、格式化、功能改动混在一起

不推荐：

```text
update files
fix stuff
wip
final version
```

可以临时提交 `wip`，但合并前需要整理成可读提交。

## PR 规范

每个 PR 尽量控制在 300 行核心代码以内。72h 比赛期间可以放宽，但仍要保证 reviewer 能快速理解。

PR 标题格式：

```text
[模块] 简短描述
```

示例：

```text
[Analyzer] Add rule-based risk scanner
[Reviewer] Add OpenAI-compatible review provider
```

PR 描述模板：

```markdown
## What

- 做了什么

## Why

- 为什么需要这个改动

## How

- 核心实现方式

## Test

- 跑了哪些测试
- 哪些还没覆盖

## Risk

- 可能影响哪里
- 需要对方重点 review 什么
```

## Review 重点

Review 时优先看：

- 逻辑正确性
- GitHub diff 行号是否准确
- AI 输出是否被本地校验
- 错误处理和降级行为
- 是否有最小测试覆盖
- 文档是否同步更新

低优先级：

- 命名微调
- 个人偏好的写法
- 不影响演示的抽象拆分

## 合并前检查

合并前至少完成：

```powershell
pytest
ruff check .
```

如果安装了 mypy，则加上：

```powershell
mypy src
```

无法运行检查时，需要在 PR 描述中说明原因。

## 冲突处理

如果两个人都要改同一个模块，先同步接口再动手。优先按照以下边界拆分：

- 成员 A 改 `src/github/`、`src/analyzer/`、相关测试
- 成员 B 改 `src/reviewer/`、`src/output/`、`src/cli/`、相关测试
- 共享数据模型 `src/models.py` 修改前先口头同步

## 每日交付清单

每晚收工前更新：

- 当前能跑通的命令
- 已完成模块
- 明天第一优先级
- 阻塞点
- demo 风险
