# 状态一页纸（给新会话/压缩后接手用）

> 只读这一页就能接着干。规范在 [`HARNESS.md`](HARNESS.md)，本页只放**当前事实与下一步**。
> 最后更新：2026-10-16，HEAD `5a01732`，远端已同步，tag `0.1.0`。

## 目标（已按实测重写）

本地优先 + CI 影子模式的 PR 审查工具。**可测目标**：机械可判定类（守卫/边界/空值/错误传播/契约）高召回并**逐类报告**；**精确率 ≥0.8**；lint 能抓的一律不报；单 PR 便宜；**不静默丢**（每个 hunk/单元都有终态与原因）。

**非目标**：大 PR 全量覆盖（物理地板约 8 元/59 万 tokens）、自动改代码、队列/DB/多租户、IDE（已冻存）。

## 真实 PR 基线（ReviewBench 官方 gold，5 个真实 PR，官方 `tp_fp` 标注）

```
发布召回 11.6% | 识别召回 18.6% | 精确率 83.3%（micro）
43 条 gold 去向：发布命中 5 | 识别但被扣 3 | 完全没提出 35（81%）
```
- **81% 是"看不见"（发现/检索），不是判据** → 主战场在"看得更多"
- 参考：阿里 AACR-Bench agent 召回 10.10% / 精确 39.90%；**我们精度高一倍多，召回同量级**
- 脚本：`scripts/reviewbench_real_eval.py --benchmark <ReviewBench clone> --limit N`
  （gold 不入库，需 `git clone --depth 1 https://github.com/review-bench/ReviewBench.git`）

## 合成/离线基线

| 评测 | 数字 | 命令 |
|---|---|---|
| 判据门禁（dev 集） | 等价被拒、真缺陷出见证、契约外见证被阻断 | `scripts/criteria_gate.py` |
| **held-out**（14 case，与 dev 不重叠） | witness 7/7、equivalent 7/7 | `scripts/holdout_eval.py` |
| 接缝审计 | 0 惰性开关、0 调用/定义不匹配 | `scripts/interface_audit.py` |
| 单测 | **360 passed** | `.venv/bin/python -m pytest -q` |
| 真实小 PR 成本 | 74k tokens ≈ 0.08 元 | — |
| 大 PR 逐单元 | 13.8k tokens/单元（旧 62k） | `scripts/run_real_review.py` |
| 缓存命中 | 稳定前缀 96.9%；按单元 50% | `docs/graph-planning-dataflow.md` |

## 已实现（能力 + 门禁）

- **判据链** `chain → provenance → counter_evidence → witness → contract`（`grounded-v3`），每条候选带 `filter_verdicts`
- **见证求值** `witness_eval`（AST 白名单、无 exec）+ 契约域阻断 → 等价改写机械上无法 confirmed
- **账本**：预算/覆盖/单元终态守恒；`checkpoint/resume/retry/cancel`；判定账本 `decision_ledger`
- **两级扫描** `tiered_scan`（**已调准：大 PR 深审 12.7%，小 PR 装得下 100%**；**未接生产**）
- **impact closure** 指标（**已进报告 metrics**）；语义 diff 划分；发布幂等（哈希去重 + 驳回记忆）
- **知识层** playbook（`kb-0001..0006`，含 gold 提炼的契约/并发/ReDoS 假阳性条目）
- **门禁**：CI 四条 + **pre-commit 四条**（已实测能拦住失败提交）
- 文档：规范 `HARNESS.md`、DFD/STD、模块图（生成）、优势与依据 `ADVANTAGES.md`

## 下一步（按优先级，只有一件是主杠杆）

1. **A｜两级扫描接线**（已试过一次，被测试拦回，见下方"A 的失败记录"）：正确做法是**在渲染 patch 的地方过滤**，而不是重建 `ChangedFile.patch`
2. **C｜确定性工具**：ruff/类型 + SARIF 输出（机械类召回，0 模型成本）
3. **E｜分级模型**：便宜模型一级 + 强模型二级（让全量扫便宜到能常开）
4. **F｜重跑真实评测**：`--limit 5`，看识别/发布拆分的变化（2.5 元）
5. **RSI 循环**：门禁 + 判定账本 + held-out 都已就绪，等的就是 A/C 带来的增量信号

## 坑与铁律（都是踩过的）

- **不要**在没有评测验证时声称"已达标"（我错过三次：P4 覆盖率、AST 背锅、扣下是主因）
- **不要**让测试失败进入提交（已 3 次；现由 pre-commit 物理阻断）
- 我的补丁**切坏过文件两次**（正则/块替换）→ 大改就用整文件重写
- 断言**不要**强求 fixture 给不出的形状（closure 0 节点是合法的）
- `.env` 被 gitignore；`PRE_COMMIT_HOME` 需指向工作区（沙箱 `~/.cache` 不可写）

## A 的失败记录（2026-10-16，不要在同一个坑里再摔一次）

在 `build_review_context` 里按 tier-1 结果**重建 `ChangedFile.patch`**（用 `hunk.raw` 拼）后，两个生产路径测试立刻变了：过滤后 `coverage_summary.hunks_total=0`，即**重建的 patch 里没有可解析的 hunk——diff 内容被删掉了**。已回退（默认行为不变，360 通过）。

**教训**：
1. `hunk.raw` 对合法 patch 是有值的，但**不同 fixture/文件形态下不可靠**；
2. 正确接法是**在产出 patch 文本的那个循环里过滤**（那里已有正确的 hunk 文本），而不是在入口处重建文件对象；
3. 补测试时必须断言**"装得下 → 每个 hunk 都在文本里"**和**"装不下 → 只有可疑 hunk + 逐条 omission"**，仅断言 `truncated` 会漏掉内容丢失；
4. **是测试拦住的，不是评审**——所以接 A 之前先写这两条测试。

## 钱

共用 key（别人也在用）。本轮实测花费：3 PR 试点 1.5 元、5 PR 诊断 2.4 元、逐单元验证 1.3 元。**跑评测前先看余额。**
