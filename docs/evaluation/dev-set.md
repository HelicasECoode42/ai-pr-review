# 开发评测集（dev）与留出集（holdout）

日期：2026-10-15。分组文件：`datasets/dev/split.json`。

## 目的

为当前 grounded 审查流程提供一个小型开发评测集（9 个 PR），用于调试、
对比基线与回归检查。同时登记 15 个 PR 作为 holdout 清单。本文件和分组
文件都不运行模型，也不包含金标签内容。

## 划分规则

按仓库划分（split_rule = "by repository"）：同一仓库的 PR 只能出现在
dev 或 holdout 之一。当前 25 个 ReviewBench PR 来自 25 个不同仓库，因此
划分天然无交叉。

**注意：这 25 个 PR 已在 2026-10-13 的基线与 Critic 实验中使用过
（见 `docs/evaluation/reviewbench-2026-10-13/REPORT.md`），因此 dev 与
holdout 都不是“未见测试”。** holdout 目前只能作为回归检查，不能宣称泛化
成绩。正式泛化需要另行冻结新的 repo 分组测试集。

## 数据来源

- 输入与元数据：`docs/evaluation/reviewbench-2026-10-13/input-preparation.json`
  （repo、PR 号、语言、文件数、新增行数）。
- 基线审查输出与审计：`docs/evaluation/reviewbench-2026-10-13/baseline-audits/<case_id>.json`。
- 金标签（外部、只做引用，不复制）：`/private/tmp/ai-review-reviewbench/golden/<case_id>.json`。
  该路径位于仓库之外的临时目录，可能随系统清理而消失；正式运行前需要把
  ReviewBench 检出到稳定位置（见下文“运行基线”）。

## dev 集（9 个 PR）

“金标签”列为 tp 标签数 / 全部标签数；“跨文件”列为金标签涉及的不同文件数，
括号内为需要相关文件或更大项目上下文的标签数。跨文件判定只基于标签中的
`context_required` 与 `file` 字段，未逐个人工核对调用方。

| case_id | 仓库 | 语言 | 改动文件 | 新增行 | 金标签 tp/全部 | 金标签文件数（需上下文数） | 多文件 | 数据源 |
|---|---|---|---:|---:|---:|---|---|---|
| PierreJanineh_TechDebtMCP_135-16a54f0f | PierreJanineh/TechDebtMCP | TypeScript | 10 | 297 | 9/19 | 4（11） | 是 | input-preparation.json；baseline-audits/ |
| AA-Factory_aafactory-prototype_17-a1978cb7 | AA-Factory/aafactory-prototype | Python | 1 | 45 | 9/17 | 1（7） | 否（单文件小 PR） | 同上 |
| brexhq_CrabTrap_15-5ef0884c | brexhq/CrabTrap | Go | 6 | 198 | 16/25 | 7（14） | 是 | 同上 |
| ardevd_jadx-collaboration_3-853179dc | ardevd/jadx-collaboration | Kotlin | 3 | 65 | 9/14 | 3（7） | 是 | 同上 |
| superhighfives_pika_200-b227aff0 | superhighfives/pika | Swift | 16 | 628 | 19/21 | 9（16） | 是 | 同上 |
| allan-mobley-jr_gimmes_468-e1477f8b | allan-mobley-jr/gimmes | Python | 4 | 129 | 6/10 | 4（7） | 是 | 同上 |
| mheuss_chronicle_14-04ac4c02 | mheuss/chronicle | Rust | 14 | 1124 | 10/18 | 6（13） | 是（大 PR） | 同上 |
| sdsykes_fastimage_165-8fc7cd32 | sdsykes/fastimage | Ruby | 12 | 341 | 14/28 | 8（12） | 是 | 同上 |
| scotthamilton77_agents-config_1-1fc78970 | scotthamilton77/agents-config | Shell | 38 | 495 | 25/37 | 16（12） | 是（最多文件） | 同上 |

覆盖：8 种语言（排除 brianc 后不再包含 JavaScript）；小 PR（1–4 文件）与大 PR（12–38 文件，最大 1124 行）都有；
8 个 PR 的金标签跨越 2 个及以上文件，满足“至少 3 个多文件样本”的要求。

**排除**：`brianc_node-postgres_3650-b922f0dd` 已从 dev 移出（记录于 `datasets/dev/split.json` 的 `excluded` 字段）。原因：head commit `b922f0dd` 在远端不可达，PR ref 已被改写；2026-10-15 检查确认。因此 dev 集现为 9 个 PR。

## holdout 清单（15 个 PR，仅登记，调参期间不得读取其金标签）

simnaut_emdash_2-41d75006；konturio_disaster-ninja-fe_1230-d169e9da；
cmik_apilix_12-0c34439a；DisciplinedSoftware_Codebase-Conversion_1-4b46d68c；
PaulStSmith_figlet-comment-generator_32-1d0b6642；k1LoW_gh-copilot-review_9-f9828193；
AY2526S2-CS2103T-W14-3_tp_152-e0a9ce03；vanyastaff_nebula_224-d5ba86dd；
NCATSTranslator_translator-ingests_336-efb31e9c；gagneurlab_PROTRIDER_10-b462b109；
superyyrrzz_ForgeMap_106-2f3977ed；renedierking_LANdalf_88-bc1698f4；
scottlz0310_Mcp-Docker_29-e7705d26；wirechat_wirechat_183-6e3156e7；
Marvell-Consulting_statswales-frontend_626-30ba27a5

## 如何运行基线（尚未执行，会调用模型）

当前流程的入口与模型调用点：

1. 准备输入（不调用模型，但需要 ReviewBench 检出与仓库缓存）：
   ```sh
   uv run python -m src.evaluation.corpus prepare \
     --benchmark <ReviewBench 检出目录> --cache <仓库镜像缓存> --output <输出目录>
   ```
2. 运行 grounded 臂（**调用模型**；默认 `--arms` 同时运行 control 与 grounded，
   成本翻倍，因此只跑 grounded 时需显式指定）：
   ```sh
   OPENAI_API_KEY=*** uv run python -m src.evaluation.paired <prepared.json> \
     --mirrors <仓库镜像目录> --output <结果.json> --arms grounded --concurrency 2
   ```
   说明：`paired.py` 目前没有按 dev 的 id 过滤的参数。需要先用一个新的小脚本
   从 prepared.json 中筛出 `datasets/dev/split.json` 的 `dev` 列表，属于待办项。
3. 评分（**需要 LLM 裁判**）：
   ```sh
   uv run python -m src.evaluation.corpus judge --benchmark <ReviewBench 检出目录> \
     --candidates <候选.json> --provider <provider> --model <judge 模型> \
     --output <评分.json> --repo-cache <仓库镜像缓存>
   ```
   官方本地 ReviewBench 裁判（`npm run judge`）用 LLM 做分类与匹配。
   `adjudicate.py` 仅做确定性计分，但输入必须是独立、穷尽的人工或裁判标注。

预计调用与成本驱动：每个 PR 的 grounded 审查若干次主模型调用（仓库证据
读取会增加调用次数，输出预算可达 32768 tokens）；每个候选的裁判调用；重复 3 次
会按倍数放大。运行前必须固定模型、预算、judge 与输入指纹。

## 需要报告的指标

- 精确率（precision）、召回率（recall）：沿用官方本地计分的 grounded 指标。
- 多文件召回率：只统计 dev 中金标签跨文件的条目。
- needs_context 后续确认率：`needs_context` 候选经补充上下文后被确认的比例。
- 运行间稳定性：同一配置重复 3 次，报告各指标的范围或标准差。
- 成本：按 usage 记录的 prompt/completion tokens 与可用时的美元金额，
  分别报告主审与裁判的开销；未记录的部分标为空，不估算。
- 延迟：单 PR 墙钟时间与总耗时（中位数与最大值）。

## 规则

- holdout 的金标签、裁判输出与其分数**绝不用于调参**。
- 调参只能使用 dev 集；任何在 holdout 上的结果只作为回归检查报告。
- 不把 `reports/` 或 gold 文件复制进模型可读的输入中。
- 当前 25 个 PR 已经被研究使用，不得宣称未见测试的结果。

## 已知问题与阻塞

- ReviewBench 检出位于 `/private/tmp`，需迁移到稳定路径，否则金标签引用会失效。
- 缺少按 id 过滤 prepared 输入的命令，dev 运行前需要新增小工具。
- 2026-10-13 的 token 统计只覆盖最终成功的主审响应，不包含重试、Critic 与裁判调用。
- 多文件判定基于标签字段，未人工核对调用方。
