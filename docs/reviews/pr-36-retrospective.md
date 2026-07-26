# PR #36 复盘：为什么从规则结论转向 Evidence-first AI Review

## 事实

- PR：`HelicasECoode42/ai-pr-review#36`，12 个变更文件。
- 首次 Provider 配置使用已失效的 `deepseek-chat`，返回 HTTP 400；更换为 `deepseek-v4-flash` 后真实调用成功。
- 真实运行得到 4 个规则 signal、1 条独立 AI 建议、1 次批量 signal verification，耗时 59.9 秒。

## 结果

| 项目 | 数量 | 结论 |
|---|---:|---|
| 跨文件规则误报 | 2 | Verifier 驳回：`__ast_dummy__` 与“5 functions”没有 diff 证据 |
| 路径类 signal | 2 | 仅表示关键路径变化，不等于缺陷；不会默认影响主 AI 或风险等级 |
| 独立 AI 建议 | 1 | 初始人工复核为误报：UTF-8 byte upper bound 是明确的离线保守策略 |
| 最终可确认 bug | 0 | 此 PR 不能作为“发现 bug”的样本，但可作为误报治理样本 |

## 暴露的问题

1. 旧路径/跨文件规则把“值得关注”包装成了“缺陷建议”。
2. 独立 AI 虽然没有被规则锚定，仍可能误解设计意图。
3. 旧报告保留模型草稿的 `critical` 风险，即使相关建议被过滤，造成风险与可见结果不一致。
4. 首次真实运行未保存逐条 confirmed signal，降低审计完整性。

## 已采取措施

- 主 Prompt 不再包含规则结论，风险由最终可见建议计算。
- 新增 `confirmed_signals` 审计字段。
- DeepSeek 示例更新为 `deepseek-v4-flash`。
- 本轮加入 evidence 与 failure scenario 的本地验证门。

## 下一轮复盘问题

1. evidence gate 拒绝了多少模型建议？拒绝原因是什么？
2. 被保留建议中，人工认为真问题的比例是多少？
3. 高精度 mechanical signal 是否值得额外一次模型调用？
4. 哪些失败场景需要由 CI 测试结果而非模型推理确认？
