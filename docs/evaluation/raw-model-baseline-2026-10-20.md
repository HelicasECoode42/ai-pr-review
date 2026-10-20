# Raw-model 基线：去掉 harness 的模型能发现多少（2026-10-20）

## 问题

D 实验 1 证明 prompt 强制三问无效。那问题是不是出在 harness 本身？本实验把输入固定成**同一份冻结 diff / 同一份自动证据**，把 harness 全部去掉：

- 一次模型调用，**没有** hypotheses、context_requests、工具循环、verification、criteria、发布 gate；
- 只要求最小 JSON：`{"findings":[{"file","line","title","reason"}]}`；
- 两个变体：`diff`（只有 PR 元数据 + diff）、`diff+evidence`（再拼上 harness 会渲染的自动 source excerpts）；
- 输出全部当"surfaced 候选"做语义裁决（人工对照官方 gold message），不是发布结果。

## 协议

- 脚本：[`scripts/evaluation/raw_model_baseline.py`](../../scripts/evaluation/raw_model_baseline.py)
- 模型：`deepseek-flash`，thinking disabled，temperature 0.1，`max_tokens=4096`，单次调用、无重试，HTTP cap 6（实际 4）
- case：`AA-Factory_aafactory-prototype_17-a1978cb7`（9 gold，Python 测试质量）+ `brexhq_CrabTrap_15-5ef0884c`（14 gold，Go 跨函数/DB 语义）
- 输入：固定 prepared + mirror；未读 holdout
- 成本：prompt 合计 20,325、completion 2,642 tokens；峰价上界 **¥0.061786**（4 次调用）
- 裁决：非盲人工语义裁决（裁决人已知 gold），只作机制观察

## 结果

| 运行方式 | AA-Factory | CrabTrap | 合计 D 语义 |
|---|---:|---:|---:|
| harness（实验 1 baseline/addendum） | 2/9 | 0/14 | **2/23 = 8.7%** |
| raw，仅 diff | 4/9 | 0/14 | **4/23 = 17.4%** |
| raw，diff+evidence | 3/9 | 3/14 | **6/23 = 26.1%** |
| **raw 两变体并集（能力上限）** | **4/9**（gold 1,5,6,7） | **3/14**（gold 3,23,24） | **7/23 = 30.4%** |

raw 每一行的候选数与语义命中：

| 行 | 候选数 | D 位置 | D 语义 | 命中的 gold |
|---|---:|---:|---:|---|
| AA diff | 5 | 4 | **4** | 1, 5, 6, 7 |
| AA diff+evidence | 5 | 4 | **3** | 1, 5, 6 |
| Crab diff | 5 | 2 | **0** | — |
| Crab diff+evidence | 7 | 6 | **3** | 3, 23, 24 |

raw 找到但 harness 完全没找到的 gold：
- CrabTrap gold 3（`entry.id` 缺失时提前返回、不发起 detail 请求、展开行显示空数据且无错误提示）；
- CrabTrap gold 23/24（新增 limit cap 没有测试覆盖；`has_more` 只测了 false）；
- AA-Factory gold 6（PR 声称两个测试覆盖 elevenlabs/zonos，实际只有一个）；
- AA-Factory gold 7（在 mock 的 side_effect 里改全局 `CHAT_HISTORY` 是间接且脆弱的测法）。

harness 也找到、raw 也找到的：AA gold 1（mock 掉被测函数）、gold 5（全局状态开始清、无 teardown）。

## 解读

1. **D 的主要损失在 harness，不在选材/模型能力。** 同一模型、同一输入，raw 单次调用语义 D 30.4%，harness 8.7%；raw 还便宜约 40%（¥0.062 vs ¥0.103）。这不是 S 的问题（两 case 的 S 分别是 100% / 92.9%）。
2. **证据对跨文件 case 是必需品，对局部 case 是稀释**：CrabTrap 0→3（diff 里看不到 helper/测试覆盖，需要 evidence）；AA-Factory 4→3（同一个 case 内容都在 diff 里，多塞 6k tokens 只带来噪声/注意力稀释）。这与"证据稀释"混淆项一致，但方向取决于缺陷类型。
3. **harness 最可疑的压制点是"多阶段 + schema + verification"**：raw 只要求 5 个字段、一次调用，模型把 5–7 个候选一次说完；harness 要模型同时产 intent_hypotheses/context_requests/结构化候选，再经过 verification 和 criteria，弱假设在阶段间被压缩或扣留。
4. **但 raw 候选没有发布价值**：它们是 surfaced，未经核验。AA 的 raw 5 条里 4 条命中 gold，precision 看起来不错；CrabTrap 7 条里 3 条命中，但另外 4 条是否为真问题未知。**D 和 P 必须分开**：raw 用来提高 D，现有 verification/criteria 用来保 P。

## 边界

- n=2 case、每个变体单次运行；同一 commit 上 CrabTrap 两次 baseline 候选数 3 vs 4，单次运行有 ±1 噪声。
- 非盲裁决；raw findings 未经过核验，"命中 gold"不等于"可发布"。
- 不能据此宣称 raw 模型的 precision，也不能据此晋升任何策略。
- 已暴露 dev case，结果只作机制观察。

## 对 D 方案的含义（修订版）

| 方案 | 本实验后的判断 |
|---|---|
| raw 发现通道（推荐） | **最强证据支持**：单次调用把 D 从 8.7% 拉到 30.4%，成本更低。把它作为 candidate generator，后接现有 verification/criteria 保 precision |
| 跨文件检索/调用链 | 对 CrabTrap 有效（0→3 来自 evidence），但属于"把证据送到"，量级小于 raw 通道；pika/chronicle 的 S 缺口另算 |
| 确定性测试规则 | **已实现并测过**：`src/analyzer/test_quality_rules.py` 在 AA-Factory head 文件上出 5 条，语义覆盖 gold 0（缺 pytest-mock/pytest-asyncio）、1（调用被 mock 的函数）、2（patch 打不到本地 import 绑定）、5（全局状态无 teardown）= **4/9**，零模型成本 |
| 候选精读（P 层） | raw 会产更多 surfaced 候选，P 层压力更大；精读/verification 扩容是必需的配套 |
| thinking/更强模型 | 可做单变量消融，但 raw 已在 thinking disabled 下拿到 30.4%，优先级低于"通道" |
| Alibaba 式分组 | 仍可做，但本实验说明先修"单次调用被多阶段压低"的问题，再谈分组 |

## 确定性通道（已实现）

[`src/analyzer/test_quality_rules.py`](../../src/analyzer/test_quality_rules.py) 是纯 AST 规则，不调用模型：

| 规则 | 覆盖的 AA gold | 说明 |
|---|---|---|
| TQ001 本地 import 绑定 vs patch | gold 2 | `from X import f` 后 patch `X.f` 打不到本地名字 |
| TQ002 调用被 mock 的函数 | gold 1 | 断言的是 mock 行为，不是被测逻辑 |
| TQ003 全局状态无 teardown | gold 5 | `CHAT_HISTORY.clear()` 后没有 yield fixture |
| TQ004 测试依赖未声明 | gold 0 | 用 `mocker`/`asyncio` 但 pyproject 没有 pytest-mock/pytest-asyncio |

在 AA-Factory head 文件上：5 条 finding，语义覆盖 4/9 gold，候选级 precision 4/5（按 gold message 人工裁决；规则本身不发布）。

**raw + 确定性的并集**：AA `{0,1,2,5,6,7}` = 6/9，Crab `{3,23,24}` = 3/14 → **9/23 = 39.1%**，而 harness 是 2/23 = 8.7%。剩余未覆盖的 AA gold 3/4/8 分别是 mock 调用形状、mock 返回类型、magic-number 下标，需要跨文件签名或更强的语义规则。

## 下一步（按证据排序）

1. **raw 发现通道接入生产**：新增一个默认关闭的 discovery 通道（单次最小 prompt + 自动证据），产出的候选走现有 verification/criteria，只发 confirmed；用本实验的 2 个 case 做门禁评测，先证明"D 升、P 不掉"。
2. **确定性测试质量规则**：覆盖 AA 的 gold 0/2/3/4 类，零模型成本，直接补 raw/harness 都没拿到的 gold。
3. **P 层候选精读**：raw 通道会让 surfaced 候选变多，需要一次 focused re-read 才能把 gold 3/23/24 这类变成 confirmed。
4. thinking/模型消融、Alibaba 式分组作为后续单变量实验。

## 复现

```sh
REVIEW_THINKING_ENABLED=false .venv/bin/python scripts/evaluation/raw_model_baseline.py \
  --prepared ../eval-data/prepared/dev.json \
  --gold ../eval-data/reviewbench/golden --mirrors ../eval-data/mirrors \
  --case brexhq_CrabTrap_15-5ef0884c \
  --case AA-Factory_aafactory-prototype_17-a1978cb7 \
  --modes diff diff+evidence --max-output-tokens 4096 --max-http-attempts 6 \
  --out /tmp/raw-baseline.json
```
