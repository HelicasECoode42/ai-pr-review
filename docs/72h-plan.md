# 72 小时双人开发计划

## 总体目标

72 小时内完成一个可演示、可讲清楚设计取舍的 AI PR Review MVP。重点不是做完整平台，而是证明核心链路成立：

1. 用户指定 GitHub PR
2. 系统获取变更和必要上下文
3. 规则和 AI 共同分析风险
4. 输出结构化 Review 总结和建议
5. 在作品说明中解释准确性、误报控制、响应速度和扩展方向

## 工作量评估

MVP 工作量约 2 人 x 3 天，强度中高。

可控范围：

- CLI 工具和报告生成：低到中等
- GitHub PR diff 拉取：中等
- 规则扫描：中等
- LLM prompt 和结构化输出：中等
- 演示与文档：中等

高风险范围：

- 自动发布 GitHub review comments：需要处理权限、行号映射、评论重复，建议放到扩展或最后一天可选
- 深度 AST/跨文件调用链分析：72h 内只做接口和少量启发式
- 准确率评估体系：MVP 做人工标注样例和置信度规则，不做大规模 benchmark

## 分工

### 成员 A：GitHub 数据与分析链路

负责范围：

- GitHub API client：PR 元信息、文件变更、patch 获取
- diff parser：解析新增/删除行、定位 new line 行号
- risk rules：实现本地规则预筛
- context builder：构造模型输入，控制 token 和上下文优先级
- 单元测试：diff 解析、规则扫描

交付物：

- `src/github/client.py`
- `src/analyzer/diff_parser.py`
- `src/analyzer/risk_rules.py`
- `src/analyzer/context_builder.py`
- `tests/test_diff_parser.py`
- `tests/test_risk_rules.py`

### 成员 B：AI Review、输出与体验

负责范围：

- LLM provider 抽象和 OpenAI 兼容实现
- prompt 模板和结构化 JSON schema
- Review 结果过滤、排序、合并
- CLI 交互、Markdown/JSON 报告
- 演示文档、作品说明

交付物：

- `src/reviewer/provider.py`
- `src/reviewer/prompt.py`
- `src/reviewer/engine.py`
- `src/output/markdown.py`
- `src/output/json_report.py`
- `src/cli/main.py`
- `docs/model-context-design.md`

## 时间安排

### 0-12 小时：需求收敛和基础闭环

- 明确 MVP：CLI 输入 PR，输出本地报告
- 建立数据模型：PR、文件变更、风险项、AI 建议、最终报告
- GitHub API 跑通
- Markdown 报告跑通

验收：

- `ai-pr-review analyze owner/repo 123 --no-ai` 可以输出基本 PR 信息和规则风险

### 12-36 小时：核心分析能力

- diff parser 支持 line number 映射
- 实现第一批风险规则
- prompt 支持 PR 总结、风险解释、行级建议
- LLM 输出 JSON，并能解析失败重试或降级

验收：

- 对一个真实 PR 能生成 3 类内容：总结、风险、建议
- 每条建议有 `file_path`、`line`、`severity`、`confidence`、`reason`

### 36-56 小时：质量控制与体验

- 对 AI 输出做过滤：低置信度、不在 changed line、无证据建议直接丢弃或降级
- 报告支持风险分组、文件分组、可复制 comment
- 增加示例 fixture 和测试
- 优化命令行参数和错误提示

验收：

- AI 不会对未变更行随意评论
- 没有 key 或 API 失败时仍能产出规则扫描报告

### 56-72 小时：演示、文档和包装

- 准备 demo PR 或 fixture
- 完成架构图和设计说明
- 录制或准备演示流程
- 处理 README、安装说明、限制说明

验收：

- 新机器按 README 可以安装并跑通
- 作品说明覆盖模型选择、上下文获取、未来扩展

## 展示建议

演示时按以下顺序讲：

1. 真实需求：大 PR 理解慢、风险点容易漏、AI 需要被约束
2. 系统链路：GitHub -> diff parser -> rules -> context -> LLM -> filter -> report
3. 现场跑一个 PR
4. 展示报告：总结、风险、行级建议
5. 说明误报控制：只评论 changed lines、规则证据、置信度、人工确认
6. 说明未来扩展：GitHub App、CI 集成、AST/RAG、团队规则库
