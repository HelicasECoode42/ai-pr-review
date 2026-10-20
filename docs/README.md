# docs

**当前文档分工**：[`HARNESS.md`](HARNESS.md) 是规范约束的唯一来源；[`review-v2-development.md`](review-v2-development.md) 是当前执行顺序与验收计划；[`STATE.md`](STATE.md) 记录带日期和版本边界的当前状态及实测结果；[`PRD-review-capability-v2.md`](PRD-review-capability-v2.md) 定义 V2 目标与非目标。

其余文档按用途分类：

- **优势与依据**：[`ADVANTAGES.md`](ADVANTAGES.md)——我们强在哪、为什么，带实测与外部对照
- **实现与证据文档**（用于说明代码、数据或接口，不另立规范）：`architecture.md`、`DFD-STD.md`（非规范架构图）、`sse-review-stream.md`（仍维护的 Web SSE 接口）、`evaluation/identification-baseline.md`、`graph-planning-dataflow.md`、`evaluation/`、`code-review-practices-and-prompt-design.md`、`error-handling-policy.md`、`review-guide.md`、`functions-index.md`、`contribution-guide.md`
- **归档**（历史，不再维护）：`archive/`

写新文档前先问：这是**规范约束**、**当前执行/验收**、**状态证据**还是**接口说明**？规范约束改 `HARNESS.md`；执行顺序与验收改 `review-v2-development.md`；当前测量写入 `STATE.md`；接口变化更新对应接口文档。`DFD-STD.md` 是实现快照，不能据此将图外功能判为禁止。
