# 当前状态与证据边界

更新：2026-10-20。最新清理以 `ba4eee9f1023f907ef9040771a183f89e69bb613` 为父提交，尚未提交；下方历史测量仍保留各自源码版本。阶段计划见 [`review-v2-development.md`](review-v2-development.md)，本页记录当前状态与证据边界。

## P0 补齐与复杂度测量（2026-10-19，工作区未提交）

- **显式缺失证据追踪（实验开关默认关闭）**：`grounded_missing_evidence_followup` / `GROUNDED_MISSING_EVIDENCE_FOLLOWUP` 复用 grounded intent、trace、bundle 与现有 verifier slot。仅处理 hypothesis ID + 已声明 missing ID 绑定的 read/search；至多补检一次，不扩大全局请求、输入或证据预算。trace 区分未请求、失败、预算拒绝、裁剪与实际仍可见的证据 refs/代码范围；同 query 仅复用真实仍可见 refs，空查询复用空结果状态。host 只机械验证声明的请求是否执行及 refs 是否仍渲染，不证明因果证据完整；原 EvidenceGate 不放宽。Unit route 不会因该开关执行普通 context request。RSI 对启用该策略的 source trace fail-closed（`unsupported_source_strategy`），不代表已评估或晋升。
- **离线验收快照**：完整 pytest **536 passed**，1 条既有 Starlette/httpx deprecation warning；最终定向组 **108 passed**，unit-slot 反例在测试夹具 lint 修正后另过 1 项。criteria 4/4、interface audit、holdout 14/14、critical Ruff 与 `git diff --check` 通过。涉及文件完整 Ruff 当前有 97 条全文件诊断；diff-added line 检查为 0。严格 mypy 检查 6 个源码文件有 175 条诊断；由于工作区此前已有大段未提交改动，diff 行映射的 16 条未能严格归因，不能声称零新增类型诊断。完整静态工具不全绿；没有真实 A/B 请求或效果结论。辅助 `src/**/*.py` 树摘要 SHA-256：`56e29f625c2cbbe3a0ac29c8530859de59cf688d3218997e15d37dbf2eb2d808`（80 文件，路径+内容长度+内容稳定排序）；这不是运行时 `source_identity`。

- **候选台账已进入统一入口**：以 run UUID 与 candidate id 关联最终 grounded 已解析候选，包含过滤、无 verdict、预算省略等状态；只在精确身份匹配时关联最终建议。legacy 仅盘点保留结果；未解析内容和更早的 provisional 响应不在完整性范围内。JSON/Markdown 显式记录范围、unknown、持久化状态；默认 `reports/decision-ledger.jsonl`，失败会降级，禁用为 not_persisted。截断 UTF-8 尾行不会破坏此前完整记录。
- **发布边界**：reusable workflow 原先已有指纹、驳回记忆及 head 校验，本轮复用了这些能力，补充台账 artifact、混合成功/未知发布时的 partial，以及固定可信台账路径。台账的 ready_for_publication 不代表 GitHub 已发布；本地记录与远端发布无法原子提交，不保证 exactly-once。本轮无真实 GitHub 写入。
- **受限执行**：本地 Git 响应及 patch 累计限额、GitHub 流式字节/空闲时间/块间时限、异常目录状态已验证。固定 checkpoint 采用本地进程锁；争锁返回未完成且不回退发模型请求；取消阻断重试，provider 清理异常保留完成结果。POSIX 两进程竞争与恢复通过；Windows 分支没有本机验证，本地有界 Git IO 在不支持的平台明确拒绝。响应到达但未持久化时崩溃仍可能重付，不能承诺续跑绝不重付。
- **开关有效性**：grounded + Critic、grounded + 规则复核目前不支持，启用时零模型调用并返回 unsupported/degraded，不再静默忽略。保留 legacy 对照路径。
- **最终离线验收**：最新完整 pytest **523 passed**，1 条既有 Starlette/httpx warning；criteria / interface / holdout / CI 最低 Ruff 与 `git diff --check` 均通过。完整 Ruff 当前列出 252 项，mypy 当前列出 458 项；不同工作树快照的历史 Ruff/mypy 数量不可当作本轮精确差分，不称完整静态检查全绿。
- **测量而非效果宣称**：[复杂度结果及复现方法](evaluation/complexity-2026-10-19.md)。生产入口、扫描、上下文与报告在合成快照上执行，provider 使用脚本响应；16 场景中 13 可执行、3 unsupported，共24次模拟调用。没有计费 token、远端时延或真实 P/R。大输入存在重复读取调用与 hunk 未纳入；未据此盲目增加 Agent 或删除策略。
- 本次 Python 源码快照 SHA-256：`8a98848788f2036ac4c4691b091ec61b2212ed13b19f7dcad27a563dbdf8b0a6`。口径为 `src/tests/tools/scripts` 下 Git tracked + nonignored untracked 的 `.py`，按路径排序拼接 `path + NUL + bytes + NUL`。与此前清理阶段口径不同，不直接比较 hash。复杂度报告另记生产源码/规则/依赖及解释器身份。
- **真实模型小样本收尾**：[试跑记录](evaluation/real-dev-pair-2026-10-19.md)及[人工裁决 JSON](evaluation/real-dev-pair-2026-10-19-adjudication.json)。此前 2048/8192 thinking 配对均因 `finish_reason=length` 失败；用户明确授权后，4096 non-thinking control 成功（0 finding），grounded 首次两次 `stop` 后因 2 HTTP cap 停止；仅 grounded 按 6 HTTP/150000 UTF8 bytes上限重跑，复用 control，成功走完 1 discovery + 1 verification（3 个候选，最终 0 finding、2 pending）。固定 dev gold 有 16 个官方 TP，其中 2 个标为 pre-existing；按官方 PR-scope 14 正例，此 case 两臂 Recall 均 0/14、Precision 不可计算。人工审查确认 1 个候选仅部分重合 Gold #23，不能算 TP；另 2 个候选为合理驳回/虚警。样本已暴露，不是独立泛化证据；无 judge、无晋升。此前两组加本次全部真实 token 的峰价估算累计上界 ¥0.498586，本次 grounded 重跑剩余最坏累计上界 ¥0.91915（低于 ¥1）。调用前余额 ¥9.63；调用后免费余额查询超时，余额变化与真实扣费对账未知。

## 清理与回归验收（2026-10-19）

- 修复 Runner 的 trace 出口：步骤摘要、错误、获取失败 warning、策略原因、sidecar 和 progress 均使用现有递归脱敏；补充裸 `github_pat_` 识别。原异常仍原样重抛，业务报告正文未纳入本次脱敏改动。
- 合并 host-side 意图任务校验，保留 EvidenceBundle 的独立防护；按输入对象复用 hunk 解析，避免同路径不同输入串用结果；合并索引生成器同步/异步分支，删除已证实的死局部、重复断言/测试及 TS 相同返回分支。实验模块、显式 two-stage 和公开契约未退役。
- 两个虚弱测试已改为验证真实长 JSON 解析及多单元 provider prompt 的共享知识前缀。在隔离副本中分别关闭长文本分支、删除知识前缀，对应测试均因断言失败；破坏只发生在临时副本，未带入工作区。
- 四个全新 Luna xhigh 独立复审范围覆盖 trace、grounded、上下文/局部清理及文档。复审发现的裸 `github_pat_` 漏脱敏和函数索引归档分类冲突已由主 Agent 修复并回查闭合；这项审查不等于全仓安全认证。
- 固定父提交的独立本地 clone：**482 passed**；清理后完整 `.venv/bin/python -m pytest -q`：**490 passed**，1 条现存 Starlette/httpx deprecation warning。`criteria_gate.py`、`interface_audit.py`、`holdout_eval.py`、CI 最低 Ruff、TypeScript `tsc --noEmit` 与 `git diff --check` 全部退出 0。Interface audit 仍有 6 个未使用形参提示，不是零告警。
- 完整 Ruff 仍未通过：历史 **313 → 303** 项；严格 mypy 仍未通过：历史 **473 → 470** 项。按文件、规则/错误消息忽略行号偏移比较，本轮无新增诊断；不把最低 CI 规则通过说成完整静态检查全绿。
- 本次已测 Python 快照：对 `src/`、`tests/`、`tools/` 下受版本控制的 `.py` 与新增 `tests/test_generate_functions_index.py`，按路径排序，以 `path + NUL + bytes + NUL` 拼接计算 SHA-256：`6b2035d7ff42b38552ce4537512f21c7fe5295ebaa68124b4f20b03c908a769a`。这是未提交清理树的校验值，不是父提交的源码哈希。
- 本轮未调用真实模型 API、未新增质量评测或策略晋升；上述结果证明离线回归和边界行为，不证明 P/R、token 花费或线上可靠性提高。生成函数索引和模块图已按当前源码刷新，避免旧的 fallback-main 描述进入兼容路径上下文。

## 本轮离线验收（2026-10-18）

- 本轮增加 grounded intent/trace 与受约束改进 CLI 闭环，已纳入版本控制。显式完整测试 `.venv/bin/python -m pytest -q`：**475 passed**，1 条 Starlette/httpx deprecation warning；随后删除两个确实未使用的 trace 参数并增强 gate 失败断言，相关定向测试 113 passed。正常提交 hook 的全量测试、`criteria_gate.py`、`interface_audit.py`、`holdout_eval.py` 均通过；hook 未打印 pytest 数量。CI 最低 Ruff 规则和 `git diff --check` 通过。Interface audit 仍列出 6 个未使用形参提示，无失效 flag 或调用/定义不匹配，不能称零告警。
- Grounded discovery 的同一响应可携带 versioned intent hypotheses 与有界 read/search plan；host 只执行有效绑定任务、记录冻结证据及预算省略，assessment 不替代原 finding 判据。策略/criteria/runtime/prompt 身份写入 trace/checkpoint；trace sidecar 与 paired evaluation identity 可被诊断 CLI 消费。命令级 fake-provider E2E 到达 `evaluation_required`，provider/replay 未被创建，active policy 仍是 baseline。
- 曾有一次并行 lifecycle gate 返回 reject，但当时未打印 reasons，暂未查明根因。增强断言保留完整 gate/reasons；四个隔离 pytest 进程重新覆盖 paired runner/host omission、holdout consumption、unknown-cost gate 和 promotion/rollback 共 6 项测试，全部通过。该次未复现不等于根因已解释。
- 本轮没有 API 调用，也没有真实策略晋升：fresh test 尚未授权，旧 holdout 仍只作回归且不得用于晋升，active policy 保持 baseline。以上均为离线 fixture/fake-provider 验证，不是真实模型审查效果；真实 P/R、独立泛化、token/cost收益和自动自进化效果未知。只有经过有效独立评估、门禁及显式人工晋升的策略才可能成为 active。

## 当前能力

- 自动路由对大型 PR 保留 grounded 审查；显式 `two_stage` 仍走 legacy 路径。不要将两条路径的能力混称为同一 agent 流程。
- 判据使用 criteria-v4 的有界 witness。超出支持资源/表达式范围的输入保持 unsupported/unknown，不能报告为 confirmed。
- 受约束策略存储只允许固定模板的 prompt addendum；`playbook_recall_k` 固定为 5，当前不能作为可变策略晋升。candidate 需隔离评估并由人工显式晋升；这不是完整自动模型自进化闭环。
- `decision_ledger.py` 已在本轮工作区接入统一入口；范围与不保证事项见上方 P0 记录。发布仍依赖 marker 与 artifact，不提供跨 host exactly-once。
- CLI smoke 的帮助检查和缓存上下文组装不等于完整 PR 审查耗时；不能据此声称 review 提速。

## 离线证据

- 严格完整测试为 **463 passed**，提交 `ca7b501` 的正常 pre-commit hooks 通过；随后 `6db6173` 仅更新评测文档。该测试结果对应代码树 `ca7b501`（tree `d60702c9a4e4ef98376508cdf666b278c65c75d1`），不是新的 soak 结果。
- 历史轻量 smoke 详见 [`offline-runtime-smoke-2026-10-18.json`](evaluation/offline-runtime-smoke-2026-10-18.json)：其源码 HEAD 为 `805271cfaa1e2628afa4991b1669f829237bedf6`。25 个缓存输入的上下文组装旧计时中位数为 0.3488 秒 / 70.3 MB 峰值 RSS（原报告的 67.1 MiB 是同一量级换算）；当时 hunk denominator 为 1460。后续独立验证修复后 1771/1771 源 hunk 进入终态账目，但没有在修复后重测该耗时，不能把旧计时归给新源码。
- 固定源码 soak 详见 [`offline-runtime-soak-2026-10-18.json`](evaluation/offline-runtime-soak-2026-10-18.json)：约 75 分 34 秒内完成 579 批，11,580 次缓存上下文构建、28,950 次安全 witness、4,632 次 Unicode/预算截断检查和 579 次 fake-provider 生产入口测试，错误数为 0。单输入为 1 个本地缓存 fixture PR（10 个文件）；不含真实模型、真实 PR、Gold 输入或 GitHub 写入，不能证明 P/R、成本或线上网络客户端稳定性。日志记录运行至预设 deadline 附近并正常退出 0，但没有记录独立 stop reason；512 MiB RSS 是采样软停止阈值，不是 OS 硬限制。
- 缓存数据的 1,771 个源 hunk 均进入终态账目；其中 311 个因文件类别排除，分别是文档 125、lockfile 185、敏感路径 1，排除内容不进入 prompt。
- cl100k 选材对照是首轮 patch 目标行可见性实验，不是语义 recall。C 只是实验臂，未改默认：Dev9 对 A1 的 any-visible 为 15 gain/11 loss；security 为 0 gain/2 loss，maintainability 为 0 gain/3 loss。all-visible 为 17 gain/10 loss。不能用净值掩盖分类损失。
- 新样本 roster 20 个；首个公开 GET 返回 HTTP 403 后停止，1 次请求、0 个样本接受，原因未证实。没有新增 PR 泛化验证。
- 本轮没有新增真实模型 P/R、成本或语义裁决结果。历史 raw file+line 匹配只是位置 proxy，不是人工语义精确率/召回率；旧 holdout 15 PR 已被使用，只能作回归。

## 范围与下一步

策略/知识 candidate 的离线生命周期测试与 fake-provider 验证，不证明自主反思或真实模型效果。Soak 用固定源码 `ca7b501`；脚本、网络阻断配置、输入 fixture、日志哈希见摘要文件。VS Code 继续冻结，不作为本轮验收目标。

## Grounded missing-evidence A/B 预算内真实配对（2026-10-19）

- 固定 dev case `brexhq_CrabTrap_15-5ef0884c` 完成 A=false、B=true 配对；模型 `deepseek-flash`、thinking disabled，每臂 discovery+verification 各 2 HTTP 200，无 fallback/retry。B 未产出 missing-evidence obligation/context request，因此没有触发补查；本次不是补查收益证据。A/B 最终 finding 均 0；A raw3、confirmed0、pending1，B raw4、confirmed0、pending3；Recall 0/14、Precision undefined，不能据此晋升或泛化。
- 实际 token A 25,287/2,591，B 26,254/2,932（输入/输出）；按 DeepSeek 峰价、输入不享缓存折扣估算总成本 ¥0.147266。两臂请求体分别 87,134/91,252 UTF-8 bytes，预算计入 envelope 后 87,880/91,998；余额预检 ¥9.34。本地收尾 KeyError 后由已封存结果离线恢复汇总，没有重复调用。逐请求 trace、完整 payload/响应及人工金标核验见 [A/B 结果](evaluation/grounded-missing-evidence-ablation-budget3-v2-result.md) 与 ignored 持久 artifact 目录 `reports/evaluation/grounded-missing-evidence-ablation-2026-10-19-budget3-v2/`。
- Gold 当前 JSON literal scope 为 11 introduced + 3 exacerbated + 2 pre-existing；既有 adjudication 文档写 12+2+2。两者 PR-scope 分母均为 14，本轮按固定 gold 官方逐项 label 保留分母并记录拆分差异。没有策略、Prompt、Gold、holdout 或业务源码更改。

## Missing-evidence trigger 契约修复（2026-10-19）

- 后续手读发现 verification prompt 提到 `missing_evidence_requests`，但末尾 JSON response shape 漏列该字段，也未明确要求对 `needs_context` 且已有决策相关 obligation 的情况返回它。discovery 已允许显式声明 obligation；host 不会从泛化 needs_context 猜 obligation 或路径。
- 已修正 Prompt 契约；同一 hypothesis/missing ID 重复请求只记录拒绝，不二次读取；最后 verifier slot 再请求时记录 `not_executed_followup_limit`，obligation 标为 `followup_limit_rejected`。新 prompt SHA-256：`836f477cfd8f4520d289b4f089dcebc65a708a337f50a4cba6de5f4e14f48506`。之前 A/B 是旧 Prompt 版本，不能作为修复后结果。
- 定向 `.venv/bin/python -m pytest -q tests/test_grounded_review.py`：65 passed；`git diff --check` 通过。host 机械核验 hypothesis/missing ID 绑定和安全 read/search 参数，不核验 obligation 与具体 candidate 的语义关联。fake-provider 仅证明 host 对显式 obligation 的执行与留痕，不证明真实模型会主动声明或质量收益。报告及最低成本真实触发检查建议见 [`grounded-missing-evidence-trigger-fix-2026-10-19.md`](evaluation/grounded-missing-evidence-trigger-fix-2026-10-19.md)。本轮未调用真实模型/API。

## Missing-evidence trigger 两例真实合成观察（2026-10-19）

- 依授权对 fixed positive/negative synthetic fixture 顺序调用生产 `reviewbench.run_review`，模型 deepseek-flash、thinking disabled。完整协议、费用、请求与结果说明见 [`missing-evidence-trigger-real-2026-10-19.md`](evaluation/missing-evidence-trigger-real-2026-10-19.md)，逐 HTTP 原始 artifacts 在 ignored 目录 `reports/evaluation/missing-evidence-trigger-real-2026-10-19/`。
- 两例共 6 HTTP、20,773 实际 tokens、¥0.069104、22.87 秒，均未触 cap。Positive discovery 声明并读取 hypothesis-bound helper evidence，且确认未被首轮上下文自动注入；因没有 candidate，verification follow-up 不适用。Negative helper 已在 diff 可见仍发生冗余上下文请求，最终两个候选均 pending/needs_context、confirmed 0；无可绑定的 missing-evidence obligation，不能据此评判 verification 契约。该结果只作合成机制观察，不代表质量或泛化收益。

## Grounded request identity 离线回放（2026-10-19）

- negative 原始 discovery response 明确含 `h1`/`h2`，bound requests 也引用这两个 ID；因 `constraints` 为字符串而非数组，两个 hypothesis 按既有契约被 host 丢弃，原始 ID 留在拒绝 task trace 中。第二次 discovery 修正字段后，host 原样保留 `h1`/`h2`，但未声明任何 hypothesis-bound missing evidence。verification 普通 context requests 缺少 hypothesis ID，missing-evidence requests 为空。完整阶段证据、冗余范围审查及 trace hash 见 [`grounded-request-identity-replay-2026-10-19.md`](evaluation/grounded-request-identity-replay-2026-10-19.md)。
- 保存了引用封存 logical raw-content hashes 的最小脱敏 replay fixture，并新增一条生产入口离线回放测试。与现有有效 ID、合法 obligation follow-up 测试合计定向 3 passed。未改生产代码或 Prompt；fail-closed 拒绝、普通验证规则和预算均未改变。负例 raw 中的 ID 存在但所属 hypothesis 不合法，不能归因于 host 身份转换丢失，也不是合法补读的成功证据。

## 证据窗口与数据契约修复（2026-10-20，工作区未提交）

- **gold scope 改为代码派生**：[`src/evaluation/gold_scope.py`](../src/evaluation/gold_scope.py) 只接受官方 `tp_fp`/`scope` 字段，PR-scope 分母 = tp 且 introduced/exacerbated；缺字段、未知 scope、tp+unrelated 均 fail closed。CrabTrap 由官方 JSON 重算为 11 introduced + 3 exacerbated + 2 pre-existing，分母 14；此前裁决文档的 12+2+2 是手写抄录，现以代码为准。测试 [`tests/test_gold_contract.py`](../tests/test_gold_contract.py)。
- **S 可离线复算**：`ReviewContext.rendered_head_ranges` 记录 patch 段实际渲染的 head 行区间（截断尾部不误报）；`review_trace.coverage.rendered_head_ranges` 只落区间不落源码；[`src/evaluation/gold_visibility.py`](../src/evaluation/gold_visibility.py) 按 gold 行区间计算 fully/partially/not visible，拒绝"任意重叠即可见"的粗口径。脚本 [`scripts/evaluation/gold_visibility.py`](../scripts/evaluation/gold_visibility.py) 支持 `--prepared`（免费）与 `--report`（从 trace 复算）。
- **证据窗口确定性缺陷修复**：Go 方法（receiver/返回类型/多行签名/interface 字面量）此前无法识别，fallback 把 SQL `IN (`、Go `len(` 当作 symbol，假 symbol 被 `follow()` 搜索后烧掉证据预算，且每文件只 seed 前 2 个 hunk。现 `follow()` 只 seed 已识别定义，按 head/非测试/定义/成本排序，单窗口上限 90 行，每文件最多 4 个 hunk。回归 [`tests/test_evidence_window.py`](../tests/test_evidence_window.py)。
- **首次 S 测量（dev 9 个已暴露 PR，离线免费）**：102 条 PR-scope gold 中 74 条全可见（S = 72.5%），3 条部分可见，25 条未可见（主因文档类排除与超预算截断）；CrabTrap gold #1 从 12/26 行部分可见提升到 26/26，PR-scope S 12/14 → 13/14。详见 [`data-contract-2026-10-20.md`](evaluation/data-contract-2026-10-20.md)。
- **边界**：仍未保存逐请求 serialized prompt 正文；离线 S 对应 `run_review` 默认预算（patch 12000 / evidence 6000），生产大 PR 的 unit 切片路径需另测；工作区仍未提交。S 只证明"行可见"，不证明模型读到或理解。本轮未调用真实模型，不能推出 D/P 变化。

## 漏斗测量与分层诊断（2026-10-20，工作区未提交）

- **N→S→D→P 漏斗已可复算**：新增 [`src/evaluation/funnel.py`](../src/evaluation/funnel.py) 与 [`scripts/evaluation/funnel.py`](../scripts/evaluation/funnel.py)。N 用官方 gold scope（代码派生）；S 用离线选材（免费）；D=published+pending 位置命中，P=published 位置命中，均为 ReviewBench raw-match proxy，不是语义裁决。测试 [`tests/test_funnel.py`](../tests/test_funnel.py)。
- **CrabTrap 完整漏斗（当前 S + 2026-10-19 保存运行）**：N=14、S=13、D=1、P=0；损失分解为未选中 1、看见未识别 12、识别未发布 1。该运行产出于证据窗口修复之前，当时实际 S 低于 13，所以 12 是 D 损失的上界估计，但 D 仍是主损失。
- **dev 9 case S（当前选材）**：102 条 PR-scope gold 中 74 条全可见（S=72.55%）、3 条部分、25 条未可见；未可见主因是文档类排除与超预算截断。
- **文档独立预算（默认关闭）**：`max_documentation_tokens` / `documentation_patch_tokens` 让文档 hunk 用附加预算进入 prompt。实测 1200 tokens 恢复 3 条 gold（72.55%→75.49%），prompt 约 +1.5k tokens；默认 0，开启需付费实验先证明值得。另有测量：把 per-file 预算按文件数摊薄反而使 S 74→70；unit 全量切片在配置注释中已有更差的实测（9/146 units、558k tokens），不作为 S 默认修复。
- **边界**：本轮无真实模型调用，D/P 来自已保存 artifact 的位置匹配；不能据此声称语义 precision/recall。
- **付费 D 实验当前被工作区阻塞**：`src/evaluation/paired.py` 在 `working_tree_dirty` 时直接拒绝运行（"Paired evaluation requires a clean committed reviewer"）。当前工作区 57 个已修改 + 25 个未跟踪文件，必须先拆成独立 commit 才能跑可归因的 D 实验；这也是把"数据契约"落到可复现实验上的前置条件。
- 下一步：提交工作区（按证据修复 / 评测产物 / 历史文档拆分）→ 预批预算的 D 实验：固定 dev case、先算 S、只改一个 D 变量，报 D/N、P/N、cost/case。漏斗链路说明见 [`funnel-2026-10-20.md`](evaluation/funnel-2026-10-20.md)。

## D 基线真实运行（2026-10-20，已授权单 case）

- **问题**：证据窗口修复后，Gold #0 的 helper（`pg_audit_reader.go` 370–413，含 371 `context.Background()`、395 `pool.Query`/nil 分支）已真实进入 prompt（trace evidence refs 可核）。模型会不会识别？
- **协议**：`brexhq_CrabTrap_15-5ef0884c`，commit `ff55ecc`、`working_tree_dirty=false`、reviewer source SHA-256 `b231b8b7…`；模型 `deepseek-flash`、thinking disabled、`max_tokens=4096`、HTTP cap 8（实际 2：1 discovery + 1 verification）、无重试、concurrency 1；输入为固定 prepared + mirror，未读 holdout。
- **结果**：0 confirmed / 0 published，3 条 pending；位置匹配 D=3（21.4%），**人工语义裁决 D=0**。三条候选分别谈 ORDER BY 分页稳定性、`has_more`/scan 丢行（失败方向反）、detail 错误展示（只与 Gold #3 部分重合），都不描述 gold 的缺陷。
- **漏斗**：N=14、S=13（92.9%）、D_pos=3、D_sem=0、P=0；损失 = 未选中 1 + 看见了没识别 13。成本 2 HTTP / 15.98s / 25,939 prompt + 2,661 completion tokens，峰价上界 **¥0.073166**（输入按 cache-miss 计）。
- **结论**：S 修复在真实 prompt 里生效，但没有换来 D；CrabTrap 的瓶颈是"看见了没识别"。位置匹配严重高估 D（3 vs 0），后续质量结论只用语义裁决。报告与裁决见 [`d-baseline-crabtrap-2026-10-20.md`](evaluation/d-baseline-crabtrap-2026-10-20.md)、[`adjudication.json`](evaluation/d-baseline-crabtrap-2026-10-20-adjudication.json)、[`funnel JSON`](evaluation/d-baseline-crabtrap-2026-10-20.json)。n=1 已暴露 dev，非盲裁决，只作机制观察，不作泛化或晋升。
- **下一步 D 实验**：prompt 强制三问（零额外调用）→ 候选精读（+1–2 调用）→ host 侧 call-graph 扩展；每轮报 D_sem/N、P/N、cost，一次只改一个变量。

## D 实验 1：prompt 强制三问 A/B（2026-10-20，已授权两个 case）

- **问题**：不增加调用、不改证据，用 prompt 强制候选回答"调用方/错误分支/生命周期"三问，能否提高 D？
- **协议**：commit `062dcfd`（两 arm 同 commit、clean tree）；case=`brexhq_CrabTrap_15-5ef0884c`（14 gold，跨函数/DB 语义）+ `AA-Factory_aafactory-prototype_17-a1978cb7`（9 gold，测试质量）；`deepseek-flash`、thinking disabled、4096 max tokens、HTTP cap 8（实际每 arm 4）、无重试；唯一变量是 `--prompt-addendum-file`，addendum SHA-256 记录在 protocol。
- **结果**：两臂 **D 语义完全相同（2/23 = 8.7%）**：AA-Factory 稳定命中 gold 1/5（mock 掉被测函数、全局状态无 teardown），CrabTrap 两臂都是 0；位置 D 都是 5（CrabTrap 位置 3、语义 0）。P 两臂都是 0。成本基线 ¥0.102634、addendum ¥0.113212（+10.3%），延迟 26.1s→28.3s。
- **结论**：强制三问只改变了候选的表述结构与锚点，没有改变发现能力；D 缺口不是"没被要求交叉检查"，而是跨函数/运行时语义整合能力。D 是 case 类型相关的：同文件可模式化的测试反模式能稳定发现（AA 2/9），需要 DB/context 语义的发现不了（Crab 0/14）。
- **额外发现**：AA-Factory 两条语义命中都是 pending、0 published —— P 层在丢已识别的 TP；CrabTrap 按位置匹配会高估 D（3 vs 0），质量结论只能用语义口径。
- **噪声**：同一 commit 上 CrabTrap 两次 baseline 候选数 3 vs 4，单次运行有 ±1 候选噪声；n=2 只排除大幅提升，不能估计小效应。非盲裁决，已暴露 dev，不晋升/不泛化。
- **产物**：报告 [`d-forced-checks-2026-10-20.md`](evaluation/d-forced-checks-2026-10-20.md)、裁决 JSON、A/B funnel JSON；addendum 文件 [`prompts/d-forced-checks-2026-10-20.txt`](evaluation/prompts/d-forced-checks-2026-10-20.txt)。
- **下一步**：① raw-model 基线（同 diff+证据、最小 prompt、无 schema/工具）回答"harness 是否拖累能力"；② 确定性测试质量规则（AA 类 gold 零模型成本）；③ 跨文件检索/调用链扩展（Crab 类 gold）；④ 候选精读（P 层把已识别 TP 变 confirmed）。

## Raw-model 基线：harness 正在压制 D（2026-10-20，已授权 ≤¥0.2）

- **问题**：同一模型、同一份冻结 diff/自动证据，去掉 harness（无 hypotheses/工具循环/verification/criteria）后能发现多少？
- **协议**：`scripts/evaluation/raw_model_baseline.py` 单次调用、最小 JSON（file/line/title/reason），`deepseek-flash` thinking disabled、4096；两 case × 两变体（diff / diff+evidence）共 4 HTTP；成本 **¥0.061786**（prompt 20,325 + completion 2,642 tokens）。
- **结果**：raw 并集语义 D = **7/23（30.4%）**；harness 同 case 两臂 = 2/23（8.7%）。按变体：raw diff 4/23（17.4%）、raw diff+evidence 6/23（26.1%）。AA-Factory raw 命中 gold 1/5/6/7，CrabTrap raw 命中 gold 3/23/24（harness 完全没找到这些）。evidence 对跨文件 case 是必需（Crab 0→3），对局部 case 是稀释（AA 4→3）。
- **确定性通道**：新增 `src/analyzer/test_quality_rules.py`（TQ001 import 绑定 vs patch、TQ002 调用被 mock 的函数、TQ003 全局状态无 teardown、TQ004 测试依赖未声明），零模型成本；AA-Factory head 上 5 条 finding 语义覆盖 gold 0/1/2/5 = 4/9，候选级 precision 4/5。raw ∪ 确定性 = **9/23 = 39.1%**。
- **解读**：D 的主要损失在 harness 而不是选材/模型；最可疑的压制点是多阶段 + schema + verification。但 raw 候选是 surfaced、未核验，D 与 P 必须分开；raw 通道需后接现有 verification/criteria 才能发布。
- **边界**：n=2、每变体单次运行、非盲裁决；raw findings 未核验，不能宣称 precision/晋升；已暴露 dev。
- **产物**：报告 [`raw-model-baseline-2026-10-20.md`](evaluation/raw-model-baseline-2026-10-20.md)、[`raw-model-baseline-2026-10-20.json`](evaluation/raw-model-baseline-2026-10-20.json)（含逐候选 gold 对照）、[`adjudication.json`](evaluation/raw-model-baseline-2026-10-20-adjudication.json)。**下一步**：把 raw 发现通道接进生产（默认关闭）→ 现有 verification/criteria 严出；补剩余确定性规则（mock 形状/返回类型/magic index）；P 层候选精读。
