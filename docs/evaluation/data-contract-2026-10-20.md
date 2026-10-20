# 数据契约与 S 层测量（2026-10-20）

本文记录"稳定数据契约"这一步的具体修复与首次可复现的 S（选材覆盖）测量。所有改动都是离线、免费的：不调用模型、不读 holdout 金标调参。

## 为什么先做这一层

N → S → D → P 漏斗里，S 是"金标行有没有送进模型"。如果 S 缺失，D/P 的模型质量实验无法归因：

- 这轮之前，唯一完成真实配对（CrabTrap）的 prompt **没有保存**，报告只能写 `S = unknown`；事后无法回答"是没送进去，还是送进去了模型没发现"。
- 评估口径靠手写：同一份官方 gold 在 JSON 里是 `introduced 11 + exacerbated 3 + pre-existing 2`，裁决文档写 `12+2+2`。分母都是 14，但契约靠人记，早晚出错。
- 评估跑在 dirty worktree 上（`source_identity.dirty=true`），代码状态无法重建。

## 修复 1：gold scope 由代码派生，不再手写

新增 [`src/evaluation/gold_scope.py`](../../src/evaluation/gold_scope.py)：

- 只接受官方字段 `tp_fp ∈ {tp, fp}`、`scope ∈ {introduced-by-pr, exacerbated-by-pr, pre-existing, unrelated}`；
- 缺字段 / 未知值 / `tp` 配 `unrelated` 一律 `GoldContractError`，fail closed；
- PR-scope 分母 = **tp 且 scope 为 introduced/exacerbated**；pre-existing 单独报；`unrelated` 只允许出现在 fp 上。

配套测试 [`tests/test_gold_contract.py`](../../tests/test_gold_contract.py)。CrabTrap 固定为 `11/3/2`、分母 `14`，由官方 JSON 重算而不是文档抄写。

## 修复 2：S 变成结构化数据，可离线复算

- `ReviewContext` 新增 `rendered_head_ranges`：patch 段里实际渲染到 prompt 的 head 行区间，逐行walk unified diff 得到，**token 截断的尾部不会被误报为可见**（[`context_builder.py`](../../src/analyzer/context_builder.py)）。
- `review_trace.coverage.rendered_head_ranges` 只落区间、不落源码；配合原有 `evidence_refs`，一份保存的 report 就能复算 S。
- 新增 [`src/evaluation/gold_visibility.py`](../../src/evaluation/gold_visibility.py)：按 gold 的 head 行区间计算 **行级覆盖率**，区分 `fully_visible / partially_visible / not_rendered / unlocatable_gold_line`。只做"区间有重叠就算可见"会把 CrabTrap 那种"diff 尾部可见、helper 关键行不可见"的失败掩盖掉。
- 新增离线脚本 [`scripts/evaluation/gold_visibility.py`](../../scripts/evaluation/gold_visibility.py)：两种模式，`--prepared`（prepared 输入 + 本地 mirror，免费跑）和 `--report`（从已保存 trace 复算）。

## 修复 3：证据窗口的确定性缺陷（CrabTrap 漏检的直接原因）

冻结 head 的 `pg_audit_reader.go` 上复现：

| anchor | 旧行为 | 新行为 |
|---|---|---|
| 302（SQL const 内） | `(275, 357, 'IN')` | `(277, 357, None)`，不再伪造 symbol |
| 360（`Query` 方法体） | `(342, 415, 'len')` | `(360, 362, 'Query')` |
| 388（`query` 内 SQL） | `(374, 443, 'len')` | `(370, 413, 'query')`，覆盖 371/395 |

根因三条，全部在 [`repository_context.py`](../../src/analyzer/repository_context.py)：

1. Go 方法 `func (r *T) name(...) RetType {` 的 receiver/返回类型让 brace 启发式失败，退回 `source_window`；
2. fallback 的 declaration 正则把 SQL `IN (`、Go `len(` 当函数名，假 symbol 又被 `follow()` 拿去 `search`，把证据预算烧在 `.github/`、`.goreleaser` 上；
3. `follow()` 只 seed 每个文件的前 2 个 hunk，`query` 所在的第 3 个 hunk 永远不读。

修复后 `follow()` 改为：只 seed 已识别定义、按 head/非测试/定义优先/成本排序、单定义窗口上限 90 行、每文件最多 4 个 hunk。回归测试见 [`tests/test_evidence_window.py`](../../tests/test_evidence_window.py)。

## 首次实测 S（dev 9 个已暴露 PR，离线、免费）

口径：prepared 输入 + 本地 mirror；`build_review_context(max_patch_tokens=12000)` + `EvidenceBundle.follow()`（`evidence_tokens=6000`），对应 `run_review` 默认路径。这是"模型还没有发出任何 context_request 之前"的自动证据。

| case | PR-scope gold | fully visible | S |
|---|---:|---:|---:|
| PierreJanineh_TechDebtMCP_135 | 9 | 7 | 0.778 |
| AA-Factory_aafactory-prototype_17 | 9 | 9 | 1.000 |
| brexhq_CrabTrap_15 | 14 | 13 | 0.929 |
| ardevd_jadx-collaboration_3 | 9 | 8 | 0.889 |
| superhighfives_pika_200 | 19 | 11 | 0.579 |
| allan-mobley-jr_gimmes_468 | 6 | 5 | 0.833 |
| mheuss_chronicle_14 | 10 | 6 | 0.600 |
| sdsykes_fastimage_165 | 14 | 11 | 0.786 |
| scotthamilton77_agents-config_1 | 12 | 4 | 0.333 |
| **合计** | **102** | **74** | **0.725** |

未可见 25 条，主要原因是文件类别排除（`CHANGELOG.md`、`README.md`、`AGENTS.md`、Skill `*.md`）和超预算截断（pika/chronicle/agents-config 的 Swift/Rust/shell 大文件）。另有 3 条部分可见。

CrabTrap 单例前后：

| 指标 | 修复前（裁决文档记录的 prompt 区间） | 修复后 |
|---|---:|---:|
| gold #1（helper 370–395） | 12/26 行部分可见 | 26/26 全可见 |
| PR-scope S | 12/14（0.857） | 13/14（0.929） |
| 自动 evidence excerpt | `pg_audit_reader.go` 60–121、275–360 | 360–362、370–413（helper 全函数） |

## 结论与边界

- **S 已可测、可复算、免费**；dev 合计约 72.5%（严格全可见口径）。所以不能再说"漏报肯定是因为没送进去"：还有约 27% 的金标行确实没送到，但更大的损失在 **D**——历史生产路径在 5 个 dev PR 上 identified recall 18.6%、published 11.6%，即"送到眼前的金标里约 3/4 没被发现"。
- 因此下一笔付费实验应该先固定 S（本次已可对照），再测 **D 的增量**；否则无法区分"模型不行"与"证据没给"。
- 仍未解决：逐请求 serialized prompt 正文没有保存（结构性区间已够算 S，但不够复核 prompt 措辞）；离线 S 基于 `run_review` 默认预算，生产大 PR 的 unit 切片路径需要单独跑；worktree 仍有未提交改动，评估前应先固定 commit。

## 复现

```sh
# 9 个 dev case 的 S（免费，不调模型）
.venv/bin/python scripts/evaluation/gold_visibility.py \
  --prepared ../eval-data/prepared/dev.json \
  --gold ../eval-data/reviewbench/golden \
  --mirrors ../eval-data/mirrors --out /tmp/gold-visibility.json

# 从一次已保存的 report 复算 S
.venv/bin/python scripts/evaluation/gold_visibility.py \
  --report reports/evaluation/<run>/run.json \
  --gold ../eval-data/reviewbench/golden --out /tmp/gold-visibility-report.json
```
