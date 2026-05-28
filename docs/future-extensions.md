# 未来扩展方向

## GitHub App

将 CLI 核心能力包装成 GitHub App：

- PR opened/synchronize 自动触发
- 将高置信度建议发布为 review comments
- 将低置信度建议折叠在 summary comment
- 支持用户用 `/ai-review retry` 重新分析

## GitHub Action

提供轻量 CI 集成：

- 在 PR workflow 中运行
- 上传 Markdown 报告为 artifact
- 可选在 PR 下发布 summary comment
- 支持团队自定义规则配置

## 项目级上下文索引

后续可引入 RAG：

- 索引 repo 中的函数、类、API 路由、数据库 schema
- 根据 diff 检索相关定义和调用方
- 将同领域历史 bug 和 review comment 纳入上下文

## AST 和调用链分析

使用 tree-sitter 做更细粒度理解：

- 函数级 diff
- 新增参数是否影响调用方
- 删除逻辑是否破坏边界条件
- 测试覆盖映射

## 团队规则库

允许团队配置：

- 高风险路径：`auth/**`、`payment/**`、`migration/**`
- 禁止 API：`eval`、`pickle.loads`、`subprocess(..., shell=True)`
- Review 偏好：安全优先、性能优先、测试优先
- 评论语言和模板

## 质量评估

建立评估集：

- 收集真实 PR 和人工标注问题
- 统计 precision、recall、comment acceptance rate
- 按语言、文件类型、风险类别分析表现
- 用历史结果迭代 prompt 和规则
