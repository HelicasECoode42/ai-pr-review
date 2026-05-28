# Agent 辅助开发规范

本文档用于统一两个人使用 AI agent 写代码时的基础 prompt 基调、上下文提供方式和验收要求，避免 agent 产出的代码风格割裂。

## 基础基调

给 agent 的共同基调：

```text
你是一个务实、严谨的 Python 工程师。请先阅读现有代码结构，遵循仓库已有风格，不引入不必要的抽象。
当前项目是 72 小时 MVP，优先保证核心链路可运行、接口清晰、测试覆盖关键逻辑。
代码需要类型标注清晰，错误处理明确，输出结构稳定。
不要做无关重构，不要改动未被任务要求的文件。
完成后说明修改了哪些文件、如何验证、还有哪些限制。
```

## 代码风格约定

Python 代码：

- Python 3.10+
- 使用类型标注
- 使用 Pydantic 表达跨模块数据结构
- 函数尽量小而直接，避免过早抽象
- I/O、模型调用、纯分析逻辑分层
- 不在业务代码里直接读取全局环境变量，统一通过 `Settings`
- 错误信息要能帮助定位问题

命名：

- 文件和模块：`snake_case`
- 类：`PascalCase`
- 函数/变量：`snake_case`
- 常量：`UPPER_SNAKE_CASE`

格式：

- 行宽 100
- import 交给 ruff 排序
- 文档说明放在 `docs/`
- 测试文件命名 `test_*.py`

## Agent 任务描述模板

分配给 agent 的任务尽量包含以下内容：

```text
任务：实现/修复/补充什么

背景：
- 这个模块在系统中的职责
- 上游输入是什么
- 下游输出是什么

修改范围：
- 允许修改哪些文件
- 不要修改哪些文件

验收标准：
- 应该支持哪些场景
- 应该新增哪些测试
- 应该运行哪些命令

注意事项：
- 和其他成员正在开发的模块边界
- 不能破坏的接口
```

## 推荐 Prompt 示例

### 实现 GitHub Client

```text
请实现 `src/github/client.py` 的 GitHub PR 数据获取能力。

背景：
- CLI 会传入 `owner/repo` 和 PR 编号。
- client 需要返回 `PullRequest` 和 `ChangedFile` 数据模型。
- 后续 analyzer 会依赖 `ChangedFile.patch` 解析 diff。

修改范围：
- 可以修改 `src/github/client.py`
- 可以补充 `tests/` 里的 client 测试
- 不要修改 reviewer/output 模块

验收标准：
- 支持 PR files 分页
- token 缺失时仍可访问公开仓库
- HTTP 错误需要保留可读报错
- 测试覆盖分页和字段映射
```

### 实现风险规则

```text
请在 `src/analyzer/risk_rules.py` 中补充规则扫描。

背景：
- 输入是 GitHub changed files。
- 输出是 `RiskFinding` 列表。
- findings 会进入模型上下文，也会在无 AI key 时直接进入报告。

修改范围：
- 可以修改 `src/analyzer/risk_rules.py`
- 可以修改或新增 `tests/test_risk_rules.py`
- 不要修改 CLI 和 provider

验收标准：
- 检测 SQL 字符串拼接、shell 执行、secret logging、测试跳过
- 每条 finding 包含 rule_id、severity、evidence、recommendation
- 规则只扫描新增行，除测试删除类规则外
```

### 实现 AI Review

```text
请实现 `src/reviewer/engine.py` 的 AI Review 聚合逻辑。

背景：
- provider 返回 JSON 字符串。
- engine 需要解析、校验、过滤和排序。
- 行级建议只能保留 changed line 上的建议。

修改范围：
- 可以修改 `src/reviewer/engine.py`、`src/reviewer/prompt.py`
- 可以新增 tests
- 不要修改 GitHub client

验收标准：
- 模型 JSON 不合法时抛出清晰错误
- 过滤掉未变更行的建议
- 按 severity 和 confidence 排序
- 限制最大建议数量
```

## Agent 输出要求

每次让 agent 完成任务后，要求它给出：

- 修改文件列表
- 核心实现摘要
- 运行过的测试命令
- 未覆盖或有风险的点

推荐结尾格式：

```text
Changed:
- path/to/file.py

Verified:
- pytest tests/test_x.py

Notes:
- 未真实调用 GitHub API，仅用 mock 覆盖。
```

## 禁止事项

不要让 agent：

- 一次性重写整个项目
- 在没有同步的情况下修改 `src/models.py`
- 把 API key、token 写进代码或文档
- 为了演示伪造“已调用真实模型”的结果
- 引入大型框架替代当前 CLI MVP
- 把 lint-only 改动和功能改动混在同一个 PR

## 人工验收标准

Agent 写完代码后，人需要至少检查：

- 数据模型是否和上下游一致
- 错误处理是否可读
- 测试是否真的覆盖关键路径
- 是否出现无关重构
- 是否破坏两人约定的模块边界

## 双人衔接方式

建议每个模块维护一段“接口承诺”：

- 输入类型
- 输出类型
- 异常行为
- 是否会访问网络
- 是否依赖环境变量

当接口承诺变化时，先同步再改调用方。对于本项目，最敏感的接口是：

- `ChangedFile.patch`
- diff line mapping
- `RiskFinding`
- `ReviewSuggestion`
- `ReviewReport`
