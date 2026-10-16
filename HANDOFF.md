# AI PR Review：当前交接

快照日期：2026-10-16。当前本地 HEAD 为 `e48b00d7e7593e6912e4bc357047b7d9bf09e593`，分支 `codex/evaluation-driven-review`；HEAD 后有未提交变动：四份计划文档、用户提供的 `docs/evaluation/dataset-audit-2026-10-16.md`，以及本轮 A/B 源码与测试/criteria fixture 修复，均须保留。线上只读核验：`main` 为 `d99524db818494ac31afd06f9a40560808de7176`；PR #45 仍是 open/draft，head 与本地 e48 对齐，未合并 main。后续执行前重新核对远端状态。

## 先读

- [能力目标与边界](docs/PRD-review-capability-v2.md)：产品目标、用户结果、非目标。
- [唯一执行计划](docs/review-v2-development.md)：三阶段任务、实现流程、Done 条件、实验协议、收敛清单和 issue 草案。
- [数据与知识库评测审计](docs/evaluation/dataset-audit-2026-10-16.md)：数据污染、评测分母、KB 来源和可支持的结论。
- [HARNESS 规范](docs/HARNESS.md)：当前安全/审查规则。旧 D1–D5、D2 图规划和 RSI 文档只作历史参考，不自动排期。

## 当前实现快照：只陈述可验证部分

- 生产入口包括 CLI、GitHub Actions 与 Web；审查上下文、模型发现、证据核验、结果报告已有实现。VS Code 插件已冻结，不是当前受支持入口；源码保留不构成产品承诺。
- A/B 在工作区已修、未提交：Playbook 符号 scope 必须经 changed frozen source 的 AST line mapping 才能匹配；按真实 PR repo/path/language 过滤，`.kt` 映射 Kotlin；scoped domain 不进全 PR prompt/扁平 witness domain，KB-0003 synthetic contract 为 candidate。补充源读取上限为 12 个 changed files、单文件 100k UTF-8 bytes、总量 400k bytes；缺失/`parse_errors`/超限保持 unknown 并告警。无 domain 的 scoped 自然语言 guidance（如 KB-0004）仍可召回。
- `src/analyzer/context_builder.py` 最终送入 context 的排序现将 over-budget triage 优先级置于生产路径/文件体量 tie-break 之前；9-hunk fixture 验证 flagged hunk 和普通上下文都保留。Coverage complete 只表示账目完整。
- C 仍未修：`src/analyzer/deterministic_tools.py` 的 Ruff 仍未绑定冻结 head SHA 或区分 PR 新增行与既存诊断；confidence 1.0 不代表真实 Bug 概率。
- `src/reviewer/decision_ledger.py` 和 `src/reviewer/publish.py` 实现了账本/指纹/驳回记忆组件，但本轮源码检索未找到 `src/` 生产调用。不要声称已具备生产级完整候选账本或发布去重闭环。
- Grounded 发现/证据/核验中的静态边或一跳搜索是有界线索，不是完整调用图或可达性证明。受限 witness 只能处理有限表达式输入域。

## 评测主张边界

- 219 个 ReviewBench PR / 2623 个 TP 是数据规模，不是模型效果；25 PR 已接触过，后切出的 15 PR holdout 不能作未见留出。14 个 `holdout_eval.py` case 是合成表达式判据 fixture。
- `reports/real-run/b7b-diagnostic.json` 可复算 5 PR/43 gold 的 raw file+line match：5/6 published match=83.3% 位置 precision，5/43=11.63% published recall，8/43=18.60% identified recall。它不是人工语义裁决，产物也不足以复原完整模型/配置/重试/费用协议。
- 选材 coverage、fixture 通过、位置匹配与人工判断的真实 Bug 发现必须分别报告，不能拼成同一个漏斗，也不能从这些数字推出“完整可审计”或“瓶颈已移到识别”。
- 费用未知记 unknown；真实模型实验先核对预算和冻结协议。本轮未授权模型调用。
- 本轮验证：`pytest -q` 387 passed；CI 最小 Ruff 规则、`criteria_gate.py`、默认 `interface_audit.py`、14 条 synthetic `holdout_eval.py` 均通过。`criteria_gate.py` 将 safe-division `count:int` 明确作为 fixture 注入，不再依赖 KB-0003 的生产权威状态。无真实 PR/模型验证；C 与 #46 其他接线项未完成。细节见[唯一执行计划](docs/review-v2-development.md)。

## 决策方向

当前认可的三阶段只有：**①现有工具可信；②跨文件意图 Agent；③失败案例驱动知识闭环**。目标是 Agent、轻便、好用、便宜、可靠、自进化；实施次序和验收只看唯一[执行计划](docs/review-v2-development.md)。短期优先关闭已确认的 scope、lint attribution、上下文排序、功能接线状态和账本/发布幂等缺口，再开展带人工语义标签的跨文件实验，最后才做 candidate 隔离回放和人工晋升。

当前不为删行数删除代码/测试/评测工具。大型 PR 仍会走 legacy two-stage；selection 相关实现仍有实验价值；VS Code 冻结但保留源码与安全审计。文档和代码变动前重新检查 HEAD/dirty，并保留所有既有工作区改动。
