# Flash 小批量对照（2026-10-14）

用户授权范围：现有余额内小批量。仅运行一个 PR，新旧两组均完成，未启动全量或额外模型评分。

- Reviewer：d9e9b16，干净源码；DeepSeek 请求 deepseek-v4-flash，响应 deepseek-flash，reasoning_effort=low。
- 案例：PierreJanineh/TechDebtMCP #135，base ea0385c7349bbaee04456c57bf0ec3f217d63532，head 16a54f0f85a9091f220418850eac6b4122782be5。
- 旧流程：1 次模型请求，41.87 秒，14,017 tokens，0 条输出。
- 新流程：3 次模型请求，143.68 秒，63,627 tokens，3 个候选；系统确认 1 个，2 个待核实。
- 调用前后余额 API 均返回 CNY 1.30；不能因此声称免费或精确费用为零，可能存在结算延迟或显示粒度。无 HTTP 402。

## 人工复核：仍不能认定审查能力改善

系统确认的候选为 flags:null 在 executePattern 中调用 replace 报错。函数内部局部推理成立，但候选声称 .techdebtrc.json 绕过校验进入执行器，未证明此入口路径。固定 HEAD 的 handleAddCustomRule 先调用 parseAddCustomRuleInput 与 validatePattern；configValidator.validateCustomPattern 也调用 validatePattern。构造器/addRule 可以直接接受数据，不等于实际配置入口会绕过校验。该候选暂列未证实，不计作 TP 或质量提升。

待核实的 ReDoS 候选示例 `(a+)+$` 对纯 a 输入会匹配，不是它所声称的失败输入；需要例如不匹配末尾字符及运行边界才能成立，且要区分修复不完整和新引入回归。另一重复 flags 候选的否定缺少规范的 counter_evidence，被保留待核实。这暴露了因果与反证质量仍需改善。

此处是人工诊断，不是 ReviewBench 官方评分，也没有调用 LLM judge。恢复的标注仓库 HEAD 为 c17b05be9c90b85a1fc012ce39e455e2d09a7255；不可混称此前冻结版本或官方榜单结果。已读标注只能用于开发集分析，不能再称此案例是未见测试。

## 后续针对性修复

核验必须读取实际入口及其校验，不仅验证候选描述里的局部函数；外部输入不能通过类型/运行时校验时转为待核实。变化影响切片需要覆盖输入解析器和配置加载路径。反证应允许核验器精确引用已读取的入口校验，不因输出字段错误而失去真实反证。用对应行为回归用例检验这些改动，再做下一次独立小批量。

原始协议、候选、模型输出和 usage 位于 reports/evaluation/paired-flash-low-small，未修改或回填其系统判断。
