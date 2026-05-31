# AI PR Review — 项目 Review 约定

本文档定义 AI PR Review 工具在分析 PR 时应遵循的约束和约定。上下文构建时会引用此文档中的相关规则。

## 1. 基本约束

- **PR head 语法错误必须生成报告并最终阻止合并**
  即使 PR 分支代码存在语法或编码错误，仍应生成诊断报告，但工作流最后必须 exit 1 阻止合并。

- **AI 失败必须降级为 rule-only**
  当 AI 模型不可用、超时或返回无效输出时，不得生成假成功报告。系统应自动降级为纯规则扫描报告并标注降级原因。

- **建议优先指向 changed added lines**
  AI 生成的行级建议只能指向新增行（`added_lines`），不允许对未变更行发表评论。

- **lockfile 和生成报告不进入主要 AI patch 上下文**
  `uv.lock`、`package-lock.json`、`yarn.lock`、`Pipfile.lock` 等 lockfile，以及 `docs/demo/`、`reports/` 目录下的生成内容，仅展示变更统计，不消耗 AI token 预算。

## 2. 高风险文件列表

以下路径的变更应触发人工双人复核提示：

| 路径 | 风险原因 | 审查要点 |
|------|----------|----------|
| `.github/workflows/` | CI/CD 流水线 | 降级逻辑、门禁行为、artifact 上传 |
| `src/cli/` | 工具入口 | 参数解析、错误处理、退出码 |
| `src/reviewer/` | 核心审查逻辑 | fallback 行为、prompt、过滤规则 |
| `src/github/` | API 通信 | token 处理、错误处理、分页 |
| `src/analyzer/` | 分析和规则 | diff 解析、上下文构建、规则扫描 |
| `src/models.py` | 共享数据模型 | 字段变更、上下游兼容性 |
| `src/output/` | 报告渲染 | 报告结构、中英文输出、可点击链接 |
| `src/utils/` | 配置和工具函数 | settings 变更、环境变量处理 |

## 3. 报告可信度规则

- `report_confidence = "normal"`：正常评审，报告完整可信
- `report_confidence = "fallback"`：使用 main 分支稳定版 reviewer，结果可信但不含 PR 分支新增能力
- `report_confidence = "partial"`：规则扫描结果可信，但 AI 分析未完成，建议人工复核
- `report_confidence = "failed"`：评审过程存在异常，报告可能不完整

## 4. 评审范围

- 只评审 PR diff 范围内的变更
- 不评审未变更文件
- 不评审 lockfile 的详细内容（仅统计变更行数）
- 不评审 `docs/demo/` 和 `reports/` 下的生成内容

## 5. 建议数量控制

- 全局建议上限：`max_suggestions`（默认 20）
- 每文件建议上限：`max_suggestions_per_file`（默认 5）
- 最低置信度阈值：`min_comment_confidence`（默认 0.65）
