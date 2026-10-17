# AI PR Review：当前交接

快照日期：2026-10-18。当前HEAD为 `5025c559bb67aebf7214ef87c5e987a3f96c2c35`，分支 `codex/evaluation-driven-review`；Harness预算/发布、策略生命周期与路由调整仍在共享工作区，存在并行工作，不要覆盖或整体stage。没有真实模型调用；本轮不得推送。

## 先读

- [能力目标与边界](docs/PRD-review-capability-v2.md)：产品目标、用户结果、非目标。
- [唯一执行计划](docs/review-v2-development.md)：三阶段任务、实现流程、Done 条件、实验协议、收敛清单和 issue 草案。
- [数据与知识库评测审计](docs/evaluation/dataset-audit-2026-10-16.md)：数据污染、评测分母、KB 来源和可支持的结论。
- [HARNESS 规范](docs/HARNESS.md)：当前安全/审查规则。旧 D1–D5、D2 图规划和 RSI 文档只作历史参考，不自动排期。

## 当前实现快照：只陈述可验证部分

- 生产入口包括 CLI、GitHub Actions 与 Web；审查上下文、模型发现、证据核验、结果报告已有实现。VS Code 插件已冻结，不是当前受支持入口；源码保留不构成产品承诺。
- A/B 已在 HEAD：Playbook 符号 scope 必须经 changed frozen source 的 AST line mapping 才能匹配；按真实 PR repo/path/language 过滤，`.kt` 映射 Kotlin；scoped domain 不进全 PR prompt/扁平 witness domain，KB-0003 synthetic contract 为 candidate。补充源读取上限为 12 个 changed files、单文件 100k UTF-8 bytes、总量 400k bytes；缺失/`parse_errors`/超限保持 unknown 并告警。无 domain 的 scoped 自然语言 guidance（如 KB-0004）仍可召回。
- `src/analyzer/context_builder.py` 最终送入 context 的排序现将 over-budget triage 优先级置于生产路径/文件体量 tie-break 之前；9-hunk fixture 验证 flagged hunk 和普通上下文都保留。Coverage complete 只表示账目完整。
- C 的核心接线在本轮工作区实现：Ruff 仅在单 PR scan 中通过共用的 base/head snapshot 运行，overview 不启动 Ruff；新增行结果作为未语义确认的 lint candidate，并带工具/快照/失败状态。子进程有输出与超时硬边界；source read 有数量、返回文本和处理预算，但底层 `snapshot.read()` 仍可能先完整取回单次响应再检查大小，不能称硬 I/O 上限。平台管道限制和本地源码门禁已测，Windows 未验证。
- 本轮 Harness 工作区新增共享请求预算：Application 内主审/Critic/unit共用完整输入 UTF-8 字节保守上界、HTTP尝试数和运行总时限；这是发送前保护，不是tokenizer计数或费用结果。`scripts/run_real_review.py` 已走 Application。失败后的有效候选保留为 pending/partial。单个底层请求受 connect/read timeout，但总预算不能取消已收到的远端计算。
- 发布路径已接 reusable workflow：检查当前head、可信bot marker、PR作者针对同marker的驳回；候选逐条写入 publication artifact，API状态不明标unknown，workflow按PR串行。它不提供跨workflow/跨host exactly-once，也没有把 `decision_ledger.py` 接成通用的运行时持久账本。
- `strategy_policy_store_dir` 默认不设置，Application使用baseline；显式提供目录时才读取通过哈希与模板白名单校验的策略。GitHub workflow提供可选路径输入，但runner不跨运行持久化目录，调用方必须自行恢复可信存储。fixture证明真实 `promote_candidate → ReviewApplication prompt → rollback → baseline prompt` 链；不是自动自进化。
- CLI/API/Web默认路由统一为 `auto`；显式 `off` 留作legacy兼容，SSE不会把它偷偷改成Agent；CLI `--two-stage`在Agent路由下生效。auto只是按PR大小的确定性策略，不是LLM语义意图分类器；Agent报告记录requested/effective mode与原因。
- `src/reviewer/decision_ledger.py` 仍未接入生产候选生命周期；不要将发布artifact误称为跨运行的完整决策账本。
- Grounded 发现/证据/核验中的静态边或一跳搜索是有界线索，不是完整调用图或可达性证明。受限 witness 只能处理有限表达式输入域。
- 阶段2窄切片已接入 `grounded_review`：假设与 bound read/search 在同一次 discovery 请求内传递，真实任务经冻结 snapshot 执行、带 evidence refs 进入既有 verification；任务/假设状态不直接改变 finding verdict。未绑定/未知 hypothesis 不执行；同查询复用、无新证据强制停止、任务溢出聚合、evicted refs 不计入 assessment。旧无计划的 verifier 请求只保留 candidate-scoped 兼容状态。70 个 grounded/unit 定向测试通过；真实模型效果、语义 precision/recall 与 token 节省均未测。

## 评测主张边界

- 219 个 ReviewBench PR / 2623 个 TP 是数据规模，不是模型效果；25 PR 已接触过，后切出的 15 PR holdout 不能作未见留出。14 个 `holdout_eval.py` case 是合成表达式判据 fixture。
- `reports/real-run/b7b-diagnostic.json` 可复算 5 PR/43 gold 的 raw file+line match：5/6 published match=83.3% 位置 precision，5/43=11.63% published recall，8/43=18.60% identified recall。它不是人工语义裁决，产物也不足以复原完整模型/配置/重试/费用协议。
- 选材 coverage、fixture 通过、位置匹配与人工判断的真实 Bug 发现必须分别报告，不能拼成同一个漏斗，也不能从这些数字推出“完整可审计”或“瓶颈已移到识别”。
- 费用未知记 unknown；真实模型实验先核对预算和冻结协议。本轮未执行模型调用。
- 2026-10-18 验证：稳定工作区 `.venv/bin/python -m pytest -q` 得 **443 passed**，1 条 Starlette/httpx 弃用提示；关键 Ruff 规则、criteria/holdout/interface 三个离线 gates、14 条合成表达式 `holdout_eval.py` 与 `git diff --check` 通过。23 个改动/新 Python 文件的新增行/新文件 Ruff 扫描为0条新诊断。阶段2/策略晋升仅由 fake-provider 与本地 fixture 验证；无真实 PR 或模型质量、成本收益结论。C 的单次 snapshot 硬 I/O 上限和 unit checkpoint 跨进程/强杀恢复仍待办，详见[唯一执行计划](docs/review-v2-development.md)。

## 决策方向

当前认可的三阶段只有：**①现有工具可信；②跨文件意图 Agent；③失败案例驱动知识闭环**。目标是 Agent、轻便、好用、便宜、可靠、自进化；实施次序和验收只看唯一[执行计划](docs/review-v2-development.md)。短期优先关闭已确认的 scope、lint attribution、上下文排序、功能接线状态和账本/发布幂等缺口，再开展带人工语义标签的跨文件实验，最后才做 candidate 隔离回放和人工晋升。

当前不为删行数删除代码/测试/评测工具。大型 PR 的 auto 策略仍可选 two-stage legacy reviewer；selection 相关实现仍有实验价值；VS Code 冻结但保留源码与安全审计。文档和代码变动前重新检查 HEAD/dirty，并保留所有既有工作区改动。
