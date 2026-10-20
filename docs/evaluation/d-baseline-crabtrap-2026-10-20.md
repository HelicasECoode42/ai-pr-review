# D 基线：修完 S 之后，模型能不能认出眼前的 gold（2026-10-20）

## 问题

2026-10-19 的 CrabTrap 真实运行里，Gold #1（`pg_audit_reader.go` 的 `query` helper 丢掉 caller context）没有被发现，当时 prompt 只看到 helper 的 275–360，关键行 371/395 不在其中。证据窗口修复后，本轮要回答一个具体问题：

> **把 gold 的完整 helper（370–413，含 371 的 `context.Background()` 和 395 的 `pool.Query`/nil 分支）真正放进 prompt 之后，模型会不会识别出这个 bug？**

如果会，S 修复就换来了 D；如果不会，瓶颈确定在 D，而不是选材。

## 协议

- case：`brexhq_CrabTrap_15-5ef0884c`（已暴露 dev，只作机制观察，不作泛化/晋升）
- 代码身份：commit `ff55ecc`，`working_tree_dirty=false`，reviewer source SHA-256 `b231b8b7…`
- 输入：prepared dev 固定输入 + sibling `../eval-data/mirrors`；prepared manifest hash `f7c66ed6…`；未读 holdout
- 模型：`deepseek-flash`，thinking disabled，temperature 0.1，单 HTTP `max_tokens=4096`
- 预算：运行级 HTTP cap 8（实际 **2**：1 discovery + 1 verification），`max_attempts=1`（无重试），concurrency 1
- 复用既有 `src/evaluation/paired.py` 生产入口；`follow_changes=True`，repository evidence budget 6000
- 人工语义裁决：对照官方 gold message 与固定 head 源码；裁决人已知 gold（非盲），因此只作机制观察

## 证据确实进了 prompt

运行 trace 的 evidence refs 包含：

```
internal/admin/pg_audit_reader.go 370-413
internal/admin/pg_audit_reader.go 360-362
internal/admin/api.go 267-342
...
```

`370–413` 覆盖 Gold #0 的 371（`ctx := context.Background()`）与 395–397（`r.pool.Query` + `err != nil` 分支）。**S 修复在真实调用里生效了，不是只在离线账本里成立。**

## 结果

2 次 HTTP、25,939 prompt / 2,661 completion tokens、15.98 秒；按 DeepSeek 峰价、输入不享缓存折扣的保守上界 **¥0.073166**。

| 层 | 数量 | 说明 |
|---|---:|---|
| N PR-scope gold | 14 | 11 introduced + 3 exacerbated |
| S 全可见 | 13 | 离线选材账本 |
| D 位置匹配（published+pending） | **3** | 3 条候选都落在 gold 行区间内 |
| **D 语义裁决** | **0** | 没有一条候选描述 gold 的缺陷 |
| P published | 0 | 0 confirmed |

候选与裁决（完整 JSON 见 [`d-baseline-crabtrap-2026-10-20-adjudication.json`](d-baseline-crabtrap-2026-10-20-adjudication.json)）：

| 候选 | 位置匹配 gold | 语义裁决 | 原因 |
|---|---|---|---|
| ORDER BY `al.id DESC` 破坏分页稳定性 | gold 0 | **非 gold** | gold 0 是 helper 丢 caller context / 连接池耗尽；候选讨论 UUID tiebreaker，因果链无关 |
| `has_more = offset+len < total`，scan 丢行导致提前 false | gold 1/2/20/24 | **非 gold** | gold 1 是非事务性 Count/Query race，gold 2 是 nil-on-error 返回 200，gold 20/24 是 limit+1 与测试覆盖；候选的失败方向还是反的（丢行让 len 更小 → has_more 更易为 true） |
| detail 错误存了但可能没展示 | gold 3 | **部分重合，不算 TP** | gold 3 的触发是 `entry.id` guard 在 `setExpandedId` 之后导致没发起请求；候选没识别这个 trigger |

## 结论

1. **位置匹配严重高估 D**：同一文件同一行区间内可以塞一条描述完全不同缺陷的候选。本轮位置 D=3（21.4%），语义 D=0。之前 10-19 运行的位置 D=1 经人工裁决同样是 0。
2. **S 修复没有换来 D**：gold 的 helper 完整进入 prompt 后，模型仍然没发现「丢 caller context」和「nil 返回 200」；它发现的是同一文件/同一区域的另外三个问题。CrabTrap 的损失因此是 **13 条"看见了但没识别"**，不是选材。
3. **成本对照**：2 HTTP / 16 秒 / ¥0.073 的上界，对一个 PR 是可接受的；这轮没有新增基础设施，钱花在了"验证假设"上。

## 边界

- n=1、已暴露 dev case、非盲裁决；不是独立泛化，不是 judge，不能用于晋升或宣称 precision/recall 能力。
- 位置匹配仍是预筛；只有 `d_semantic` 可以写进质量结论。
- 本轮只跑 grounded 一臂，没有配对 control；不能与 one-shot 做质量差对比。
- 原始 HTTP 请求/响应 artifact 在 `/tmp/crabtrap-d-2026-10-20/`（临时目录，可能被系统清理）；本文件与裁决 JSON 已入库。

## 下一步（D 实验）

按代价排序，一次只改一个变量：

1. **prompt 强制三问**（零额外调用）：每个候选必须回答调用方/错误分支/生命周期；尤其对"数据缺失/错误被吞"类缺陷要求给出展示路径。
2. **候选精读**（+1–2 调用）：对 pending 候选带完整函数 + 调用方做一次 focused re-read，再判 confirmed/needs_context。
3. **host 侧 call-graph 扩展**（低成本）：从 changed hunk 自动读被调符号定义，减少"模型没想到要读"。
4. 提高 discovery 输出预算/更强模型只做 discovery（先解决截断）。

度量方式已就绪：`scripts/evaluation/funnel.py`（S 免费 + D_semantic + P）+ 人工/盲裁决 JSON；每轮报 D_sem/N、P/N、cost。
