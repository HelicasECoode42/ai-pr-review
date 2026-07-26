# Evidence-first AI Review 改造方案

## 目标

让 AI 直接审查 PR，并用代码证据、失败场景和可执行检查约束其输出。规则不再参与缺陷判定、主 Prompt 排序或风险等级计算，只作为 telemetry；少数高精度机械信号可选择性进入旁路验证。

## 目标链路

```text
PR diff + 项目规范 + 相关函数索引
              │
              ▼
       AI Reviewer（独立判断）
              │  每条建议必须给出
              │  - changed line
              │  - exact evidence excerpt
              │  - failure scenario
              ▼
   Evidence Validator（本地确定性校验）
              │  证据是否来自该新增行？
              ▼
      suggestion filter + risk calculation
              │
              ├── 可选：高精度 mechanical signal 的批量 Verifier
              └── telemetry：所有规则命中、驳回、漏斗指标
```

这里的确定性校验只验证 **模型结论是否被 diff 支撑**，不使用正则判断“代码是不是 bug”。

## 本次实现范围

1. `ReviewSuggestion` 新增 `evidence` 与 `failure_scenario`。
2. Prompt 强制模型引用新增行中的原始代码片段，并描述可复现的失败路径。
3. 新增 `evidence_validator`：丢弃无证据、证据不在对应变更行、或没有失败场景的 AI 建议，并将拒绝原因写入 JSON。
4. 总体 `risk_level` 只根据最终通过 evidence gate 的建议计算。
5. 默认 `VERIFY_RULE_SIGNALS=false`：规则只计数，不增加模型调用；启用时也只批量验证 `secret-logging`、`shell-execution`、`dynamic-execution`、`sql-string-concat` 等高精度机械 signal。

## 不在本次范围

- 自动运行任意项目测试。下一阶段只允许从 CI 声明的安全命令中读取结果，不能让模型生成 shell 命令执行。
- 自动构建完整调用图。当前复用已有 Function Index；下一阶段再加入基于 AST 的调用方检索。
- 用单个 PR 宣称误报率改善。

## 验收与数据

| 检查 | 验收条件 | 留档字段 |
|---|---|---|
| 主 AI 独立性 | `main_review_prompt_signal_count = 0` | `metrics` |
| 证据归属 | 每条可见 AI 建议的 evidence 出现在 `file_path:line` 新增行 | `evidence_rejections`、`metrics` |
| 失败路径 | 每条可见 AI 建议有 `failure_scenario` | `evidence_rejections` |
| 风险一致性 | `risk_level` 等于最终可见建议最大严重度 | 单测 + report |
| 规则成本 | 默认 `signal_verification_request_count = 0` | `metrics` |
| 规则旁路 | 开启时最多 1 次批量请求 | `metrics` |

## 实验设计

对至少 10 个历史 PR 运行同一模型与相同上下文预算；人工标注每条可见建议：

```json
{
  "pr": "owner/repo#123",
  "source": "ai | rule_confirmed",
  "human_label": "true_positive | false_positive | unclear",
  "evidence_valid": true,
  "reviewer_note": "判断依据"
}
```

分别计算 AI 建议精确率、规则旁路精确率、evidence 拒绝率、平均耗时与单 PR 模型请求数。没有人工标签，不宣称质量提升。
