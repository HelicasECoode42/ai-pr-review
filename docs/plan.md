# 唯一的计划

**本文是本项目唯一的计划。** 其它任何 `*-plan.md`、`*-development*.md`、`*-summary.md` 一律作废（哪些保留、哪些归档见 [`docs/README.md`](README.md)）。改动计划请改本文。

---

## 一页总览

**共 6 个阶段（P0–P5）+ 1 个贯穿循环（RSI）。** RSI 不是阶段：它从 P1 完成后开始，贯穿 P3–P5。

```
P0 收口 ──► P1 让 harness 能动手 ──► P2 核验过滤器链 ──► P3 知识层 playbook ──► P4 分片+并发 ──► P5 采纳回路+CI 门禁
                    │                        │                    │                    │                │
                    └──────────── RSI 循环（每轮一个 RC，翻转门禁）────────────────────┘                │
                                                                                        （P5 提供采纳信号回流）
```

**当前进度**：P0 部分完成 · P1 未开始 ←**下一步** · P2 部分完成 · P3 设计完成 · P4 部分完成 · P5 工具就绪未接线。

---

## 阶段表

| # | 目标 | 交付物 | 验收（可机械检查） | 成本 | 依赖 | 状态 |
|---|---|---|---|---|---|---|
| **P0** | 收口：单一主路径、版本契约、文档收敛 | ① 砍 legacy prompt 家族，只留 grounded 一条链路；② 报告带 `schema_version`/`criteria_version`；③ 40 篇文档 → ~10 篇权威 + 归档；④ IDE 冻存；⑤ 打 `0.1.0` tag | `pytest` 只覆盖一条链路且全绿；`docs/README.md` 列出权威/历史；两份报告 JSON 含版本字段；`git tag` 非空 | 0 元 | — | ⚠️ ②④⑤部分完成；①③未做 |
| **P1** | **让 harness 能动手**（最高杠杆） | ① `witness_eval`：纯表达式/纯函数 base-vs-head **实际求值**；② 沙箱执行器（超时、无网络、只读、路径白名单）；③ `run_tests`（跑指定测试作为反证）；④ 二者作为新证据类型接进 `EvidenceBundle` | 合成 gold 集上 `safe-index` **机械上无法 confirmed**；同一见证两次求值结果一致；沙箱越权/超时/联网用例被拒 | 低（本地执行；验证轮 0.2 元） | P0 | ❌ 未开始 |
| **P2** | 核验改成**有名字的过滤器链** + 判定记录 | `provenance → witness → static(AST) → re-location → dedupe → threshold`；每个过滤器记录**丢了什么、为什么**；append-only 判定记录库（含判据版本与见证） | 任取一条候选能回答"它为什么没发布"；**关闭任一过滤器可复现差异**；判定记录可 diff、可回滚 | 0 元 | P1（witness 是链上一环） | ⚠️ `provenance` 已实现；其余未做 |
| **P3** | **知识层**：agent 自维护的 playbook | wiki 形态条目（front-matter + 正文）+ **只准增量 delta**；`expires_when` 由机械校验执行；按 scope 检索 Top-K 注入；候选区→门禁→`authoritative` | 注入 Top-K 后 `needs_context` 数量下降且**误报不增**（固定协议 A/B）；引用符号消失的条目**自动** `expired`；同 scope 冲突条目自动降级 | 0.2 元/轮 | P1、P2 | ⚠️ 设计完成（见 `self-improving-plan.md`），实现未开始 |
| **P4** | 分片 + 并发：大 PR 全量覆盖 | `ReviewUnit` 接进生产链路；按需取证；单元级预算与失败隔离；受限并发 | 大 PR 单元覆盖率显著高于 11%；精确率不低于单次路径；**并发下同一输入两次运行结果一致** | 2–3 元/大 PR | **P1、P2**（否则只是放大错误） | ⚠️ 单元账目 + 并发 + A/B 脚本已实现；**未接引擎** |
| **P5** | 采纳回路 + CI 门禁 | ① 从评论后续 commit 判断是否被处理 + 👍/👎；② gold 集 + 机械复算进 CI；③ CI **影子模式**（只发 summary） | **质量回退能让 CI 变红**；采纳率可计算；影子模式下不产生行内评论 | 0 元（离线）+ 影子运行 | P1、P2 | ⚠️ gold/打分器/复用 workflow 已有；未接线 |

**RSI 循环**（细节见 [`self-improving-plan.md`](self-improving-plan.md)）：`Run → Score → Critic(实现盲) → Diagnose(执行脚本) → RC → Gate(P2F/F2P) → Promote|Discard`。单主线、每轮只出一个 RC、程序化评分优先、held-out 只在最后用一次。**v1 只改 playbook 条目与检索策略**，不改 prompt、不改代码。

---

## 每阶段"什么时候算做完了"

- **P0 完成**：`pytest` 只有一条主链路；文档索引就位；报告自带版本；`git tag` 存在。
- **P1 完成**：`safe-index` 这类等价改写**在机械上无法被 confirmed**；见证可复算。
- **P2 完成**：候选去向可审计；过滤器可单独开关并复现差异。
- **P3 完成**：playbook 能被 agent 增量维护，且过期是机械发现而非人工巡检。
- **P4 完成**：大 PR 覆盖率上去、精确率不掉、输出确定性保持。
- **P5 完成**：质量回退进 CI 门禁；采纳率可用。

---

## 非目标（明确不做）

- ❌ population / evolutionary 搜索（DGM 那部分）；❌ LLM 自评当门禁；❌ 整体重写 prompt/playbook。
- ❌ v1 阶段让 RSI 改 prompt 或改代码。
- ❌ IDE 扩展维护（已冻存）；❌ Web Console 作为对外承诺（降级为演示）。
- ❌ 向量库/消息队列/数据库（先 JSONL + 关键词 + scope 过滤）。
- ❌ 在核验修好之前做分片（P4 的前置是 P1/P2）。

---

## 进度快照（本轮已落地的部分 → 归属阶段）

| 已落地 | 归属 |
|---|---|
| `schema_version` + `criteria_version` 进报告，`metrics` 类型放宽 + 往返测试 | P0 |
| IDE 冻存（CI 非门禁 + README 声明） | P0 |
| 引文**来源校验**（拒绝对未展示代码的引用）+ 回归测试 | P2 |
| 解析失败重试一次（记入 `discovery_trace`） | P2 |
| 单元缺口账目进报告（`units_not_reviewed` 等） | P4 |
| 单元级**并发**（上限 8、顺序确定、失败隔离、预算不合并） | P4 |
| 匹配预算 A/B 脚本 + 已知答案评测脚本 | P4/P5 |
| gold 集 + ReviewBench 适配 + 二分图打分器 | P5 |
| 复用 workflow + 自我审查 workflow 拆分 + README 安装章节 | P5 |
| 决策记录 9 条 ADR、判据设计、RSI 设计、架构评审 | P0 |

**下一步：P1。** 它是精确率的解药，也是 RSI 门禁的地基——没有硬信号，RSI 只能靠 LLM 自评，就回到"奖励引用、不奖励检验"的老坑。
