# D 实验 1：prompt 强制三问（2026-10-20）

## 问题

D 层实验第一刀：**在不增加模型调用、不改证据的前提下，用 prompt 强制每个候选回答"调用方/错误分支/生命周期"三问，能不能提高 D？**

这是最便宜的一刀（0 额外调用）。如果它没用，就说明 D 的缺口不是"模型没被要求做交叉检查"，而是模型能力/上下文整合问题，后面的钱应该花在检索、模型或候选精读上。

## 协议

- 代码身份：commit `062dcfd`，`working_tree_dirty=false`（本轮两个 arm 同一 commit）
- case：`brexhq_CrabTrap_15-5ef0884c`（14 PR-scope gold，Go，跨函数/DB 语义）+ `AA-Factory_aafactory-prototype_17-a1978cb7`（9 gold，Python 测试质量）
- 输入：固定 prepared + sibling `../eval-data/mirrors`，未读 holdout
- 模型：`deepseek-flash`，thinking disabled，temperature 0.1，单 HTTP `max_tokens=4096`
- 预算：每 arm HTTP cap 8（实际每 case 2，全部 4），`max_attempts=1`，concurrency 1
- 变量只有一个：`--prompt-addendum-file`。addendum 内容见 [`prompts/d-forced-checks-2026-10-20.txt`](prompts/d-forced-checks-2026-10-20.txt)，SHA-256 记录在 protocol
- 语义裁决：对照官方 gold message 与固定 head 源码，逐候选标 `gold_index | null`；裁决人已知 gold（非盲），只作机制观察

## 结果

| arm | case | HTTP | prompt/completion tokens | 候选 | D 位置 | **D 语义** | P | 语义命中的 gold |
|---|---|---:|---:|---:|---:|---:|---:|---|
| baseline | CrabTrap | 2 | 25,720 / 2,915 | 4 pending | 3 | **0** | 0 | — |
| baseline | AA-Factory | 2 | 5,445 / 2,123 | 2 pending | 2 | **2** | 0 | gold 1, gold 5 |
| addendum | CrabTrap | 2 | 26,508 / 3,205 | 3 pending | 3 | **0** | 0 | — |
| addendum | AA-Factory | 2 | 6,342 / 2,734 | 2 pending | 2 | **2** | 0 | gold 1, gold 5 |

合计（两 case）：

| arm | N | S | D 位置 | **D 语义** | P | 成本上界（峰价、输入不享折扣） |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 23 | 22 | 5 | **2（8.7%）** | 0 | ¥0.102634 |
| addendum | 23 | 22 | 5 | **2（8.7%）** | 0 | ¥0.113212 |

- 强制三问**没有改变语义 D**：还是两条 gold（AA-Factory gold 1 / gold 5），CrabTrap 还是 0；命中的 gold 完全相同。
- 它改变的是**候选的表述结构**（reason 里出现 CALLER/CONTRACT、ERROR BRANCH、LIFECYCLE）和锚点位置，不是发现能力。
- 成本 +¥0.0106（+10.3%），延迟 26.1s → 28.3s。
- 位置匹配仍然高估：baseline 位置 D=5、语义 2；CrabTrap 位置 3、语义 0（三条候选落在 gold 行区间内但描述的是别的缺陷）。

## 两个额外发现

1. **D 不是均匀为 0**：AA-Factory 的 9 条 gold 是测试反模式（mock 掉被测函数、全局状态不清、缺依赖），模型在两臂都稳定找到 2 条（22%）。CrabTrap 的 gold 是"helper 丢掉 caller context / nil-on-error 返回 200"这类跨函数、需要 DB 语义的缺陷，模型即使拿到完整 helper 也找不到。**D 的缺口是 case 类型相关的：局部可模式化的能发现，跨函数/运行时语义的发现了不了。**
2. **P 层在丢已识别的 TP**：AA-Factory 的两条语义命中都是 pending、0 published。也就是说这两条 gold 是"发现了但被核验扣下"。这与历史 b7b 的 `matched_but_withheld` 现象一致。

## 解读：为什么 prompt 没用

- 模型在 addendum arm 里确实按三问格式写了理由，说明它**读懂了要求**，但没有因此发现新缺陷。强制三问只能让模型把已有假设说得更完整，不能补上它没建立起来的因果链。
- CrabTrap 的 gold 需要：把 diff 里的调用变化（QuerySummaries → query）与 helper 里的 `context.Background()`、`pool.Query` 错误分支联系起来，并判断"请求取消/连接池耗尽"这个运行时后果。这是**跨函数语义整合**，不是 checklist 能逼出来的。
- AA-Factory 的 gold 是**同文件内可模式化**的测试反模式，模型本来就能找到——这类 gold 更适合交给确定性规则（见下）。

## 边界与噪声

- 每个 case 每 arm 只跑 1 次。同一 commit 上 CrabTrap 两次 baseline（`ff55ecc` vs `062dcfd`）候选数分别是 3 和 4，说明**单次运行就有 ±1 候选的噪声**；本实验只能排除"大幅提升"，不能精确估计小效应。
- 非盲裁决（裁决人已知 gold）；不是 judge；n=2 case；已暴露 dev；不晋升、不泛化。
- 原始 artifact 在 `/tmp/d-addendum-2026-10-20/`、`/tmp/d-forced-baseline-crab/`、`/tmp/d-addendum-baseline-aa/`；裁决 JSON 与 funnel JSON 已入库。

## 下一步（按证据排序）

1. **raw-model 基线**：同一份冻结 diff + 证据，最小 prompt、无 JSON schema/工具循环，测"去掉 harness 的模型本身"能做多少。这是回答"D 是不是被 harness 拖累"的唯一干净办法；成本约 ¥0.04–0.08/case。
2. **确定性测试质量规则**：AA-Factory 这类 gold 用 AST/规则可高精度覆盖（mock 掉被测函数、teardown 缺失、patch 目标与 import 不一致、依赖缺失）。零模型成本、高 precision，能直接把这类 recall 拿回来。
3. **跨文件检索/调用链扩展**（行业里最大的 recall 杠杆）：对 CrabTrap 这类 gold，需要"改动的调用 → 被调 helper → 错误分支/context 使用"的链式取证，而不是把整段 helper 塞进 prompt 等模型自己连。
4. **候选精读**（P 层）：AA-Factory 两条已识别 TP 被扣下，带完整测试文件 + 依赖做一次 focused re-read，可能把 pending 变 confirmed。
