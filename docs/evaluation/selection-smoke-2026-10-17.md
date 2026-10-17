# 离线选材 smoke（2026-10-17）

本报告只评估 gold 定位是否到达本地构造的 patch context，不评估模型是否理解缺陷，也不代表 PR 级 precision/recall。整个实验没有模型调用，没有网络请求，没有读取目标仓库工作树或执行目标代码。

## 结论

`scripts/selection_recall.py` 并不调用生产的 `build_review_context`：它用 `scan_signals`/`triage` 计算自定义文件顺序，再用 6,000 token 贪心模拟 hunks。它只适合作为选材代理指标，不能代表实际 prompt 覆盖。

因此又用同一组缓存 diff 调用真实 `build_review_context`（`allow_remote_file_fetch=False`），检查首轮 patch/hunk 原文可见性。39 条 TP gold 记录中，35 条能按精确文件路径和行区间映射到 changed hunk；其中 18 条 gold 记录关联的完整 hunk 原文可在 prompt 对应文件片段中找到（18/35 = 51.43%，这是 gold 记录比例，不是 18 个不同 hunk）。另有 7 条映射记录未证明完整 hunk 可见、10 条记录所在文件/hunk 为 partial 或 unknown、4 条记录无法映射；这些都不作为语义漏报或“无缺陷”结论。这个比例是保守的定位覆盖 proxy，不是语义召回率。

对 3 个 PR，文件覆盖账本均完整（无 unaccounted files），但 patch 并非完整：76 hunks 中 42 complete、11 partial、23 omitted；2/3 个 context 状态为 partial。账本完整只说明每个改动文件都被记账，不代表每个 hunk 都进入 prompt 或经过审查。

## 输入与运行身份

- benchmark 本地快照：ReviewBench commit `2e45130f1f6bbdd33747a6963727051009754399`；219 项 manifest 的 SHA-256 为 `e74656b8c1c4bbab5ce85f1fcb742a7aaad550a4d7bd46e1f83a0746807bfa02`。gold 快照位于临时目录 `/private/tmp/reviewbench-probe`，不随仓库版本化。
- 固定子集是旧 3-case/39-TP smoke，不是未见测试集：AA-Factory 在当前 dev split；AY2526S2 在 holdout；AgentParadise 不在当前 split。该子集仅作开发者选材预检，不得用于声称 holdout 泛化或继续调参。
- 代码运行时 HEAD 为 `cfca0b4232ab5bc13236b97ca74967e8ba53f927`，工作区当时已有未提交修改，故不能把结果标为该 HEAD 的干净、已发布结果。复核时以下直接相关文件的 SHA-256 与运行记录一致，说明这些文件内容未变化：`context_builder.py`：`668adb8f4668a4356be18727df2b82a1edac36bd5d20e7d9c85488fa0fa92e36`；`diff_parser.py`：`ddcf062fe05ae2d1074706ad4258684d9588030a84d6f92c671cdc1e3b3e3603`；`tiered_scan.py`：`7930bd25fa246bb03f182c4e79fdd5f70c19e4c5ff8330e56696c43d0e278b62`；用于自定义代理的 dirty `risk_rules.py`：`6573d792a34c2601bb8d5b20ea3a42192e8c0f829f02953c262b62810dc11b8e`。这不能替代完整干净工作树复现。
- Python：仓库 `.venv`，3.12.13。实际 context 参数：patch token budget 6,000；`include_context_pack=False`；`allow_remote_file_fetch=False`；未传 rule findings（与 reviewbench 离线入口一致）；没有模型/provider。knowledge/playbook 不参与这次 patch-context 构造；当前 `knowledge/playbook.md` SHA-256 为 `6e29b97a44690858c9a99846019a08f0485db87f0c58764532e737c82a13b982`，仅作版本记录。
- 三个 diff 与 gold 文件均有本地缓存。各输入版本及散列如下；没有复制完整 gold 到仓库。

| case | base | head | gold SHA-256 | diff SHA-256 |
|---|---|---|---|---|
| `AA-Factory_aafactory-prototype_17-a1978cb7` | `e7f558c7360abac335af2df22f4ed376992b30ab` | `a1978cb75950a879df8805e4a5f373eb6678f6f9` | `ae659d056f8ef9f43f81133bce6cc679f719e98979551e97d46d870c17efa60c` | `536044f32a3720b70e20720ca88ff0b6320e04baf3dd1f0d6f16420c0a3c50ab` |
| `AY2526S2-CS2103T-W14-3_tp_152-e0a9ce03` | `a27b3e40d94f92f229b9301a8a9d5207d9a5b0bb` | `e0a9ce03c8c1cc839eaf409e2677b9abf2017b1f` | `f8c1fcc76bc318b8f110cc46fd3869a2f201d2b6faf91e27f6429b5f540f70ef` | `20e7863ef29465119935b6a6d033c9f40f31cbfc1412f391746acaf5e53c5efd` |
| `AgentParadise_agent-paradise-standards-system_61-fbdecd6b` | `d541a92be2f2f1ceed397d4e0afaf933df8972fd` | `fbdecd6bd0b3148b84d91b6c4eff3c6d7ce54198` | `1135a5cc27a6d4e12c6c964a9443cd726fb326ba025b2eabcea1bf859dfaede6` | `4f1b9e53bfc0ebbb3892e500e1724b8f6072f7985736e15e7ea70622e514153a` |

## 结果

| case | gold TP records | changed files | complete / partial / omitted hunks | prompt token count* | gold records with full target hunk visible | partial/unknown gold records | unmappable gold records | mapped gold records, not fully visible | context state |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AA-Factory | 9 | 1 | 1 / 0 / 0 | 632 | 9 | 0 | 0 | 0 | success |
| AY2526S2 | 14 | 19 | 39 / 3 / 7 | 6,980 | 9 | 0 | 3 | 2 | partial |
| AgentParadise | 16 | 56 | 2 / 8 / 16 | 10,716 | 0 | 10 | 1 | 5 | partial |
| **合计** | **39** | **76 hunks** | **42 / 11 / 23** | — | **18** | **10** | **4** | **7** | **2 partial** |

\* `prompt token count` 是 builder 报告的完整 context 估算，可能含非 patch 文本；patch 子预算单独为 6,000。对 19/56 文件 PR，完整 context 计数高于 patch 子预算，不应误读为输入硬上限或全 prompt 预算。

coverage 判据为：gold `file` 与 diff 文件路径精确相等，行区间与新侧 hunk 范围相交，且关联 hunk 的原始 diff 文本完整出现在 prompt 中该文件的 patch 段，才将该 gold 记录计为“完整目标 hunk 原文可见”。不匹配到 hunk 的 4 条单独记为 unmappable；partial 文件中的 10 条记为 partial/unknown；另 7 条映射记录仅表示没证明完整目标 hunk 出现，不将其直接判为语义漏报。多条 gold 可能指向同一个 hunk，故 18 条 gold 记录不等于 18 个不同 hunk，39 条也不是 39 个独立代码位置。

## 自定义 selector 代理

在相同缓存和 6,000 token budget 下，`selection_recall.py` 输出：file-order 21/39（53.85%），evidence-order 23/39（58.97%），两者分别保留 48/114 hunks。该脚本调用规则扫描和 triage 后自定义排序，再自行贪心装箱；它不检查 `build_review_context` 的真实 prompt，因此该 +2 TP 仅是模拟代理结果，不作为生产选材提升结论。该脚本还用路径后缀匹配，可能发生同名路径误配；本报告实际 builder 核对使用精确路径。

原历史产物 `reports/real-run/selection-recall.json` 未覆盖。安全复跑脚本通过 cache-only 替代 fetcher，且把设置注入为空 GitHub token，不读取密钥、不允许 fallback 网络请求；输出位于 `/private/tmp`。系统 Python 缺 `httpx` 的首个导入尝试没有执行评测；后续只用 `.venv/bin/python` 成功。复跑命令：

```sh
.venv/bin/python /private/tmp/ai-pr-review-selection-smoke-2026-10-17.py
```

复跑脚本 SHA-256：`7954c69a8a6a5d918afd3abe4600a8fd858b30c7100fd358a9fa228d5450a8aa`；实际上下文统计输出 SHA-256：`6d5c6f719c51ff055b3cb4b683bed49507b64125201a50082efadaeb6925ff8e`。错误/遗漏：实际 context 缓存输入错误 0；有 4 条 gold 无法映射到 changed hunk，未将它们纳入覆盖率分母。未对目标代码执行 lint、test 或 subprocess。

## 后续真实模型 pilot 预算前置（未调用）

只读当前本地配置得到：provider hostname `api.deepseek.com`，model alias `deepseek-v4-flash`；请求 timeout 45s；repository review enabled；`max_review_input_tokens=None`；repository evidence budget 6,000；unit review 关闭（0）；Critic enabled，且配置了 Critic model。API key 仅检查是否配置，未读取或输出内容；未查询余额。

当前实现并不满足“每 PR 最多 2 请求、每请求输出最多 4,096 tokens”的约束：repository review provider 默认 output cap 为 32,768，输入上限为空；`max_review_input_tokens` 仅参与 budget 诊断，并非发送前硬拒绝；Critic 开启会增加模型路径。现有 `reviewbench_real_eval.py` 还会联网抓 diff，也没有请求数/输出 token cap 参数。因此当前没有可安全直接执行、同时满足这些限制的模型试跑命令；不要把简单 `--limit 1` 当作请求限额。

供 root 评估的单 PR 上界方案：先由 harness 强制每请求总输入 ≤12,000（包括 system prompt、patch、repo evidence）及输出 ≤4,096，并在 provider attempt hook 强制整个 PR 总请求数 ≤2；Critic、LLM judge、外部 retry 关闭；输入超限拒绝发送，超时/partial 仅记为执行结果，不做质量结论。以两次请求均 cache-miss 且都按 peak 计，最多 24,000 input + 8,192 output tokens。DeepSeek 官方价格页当前给 Flash cache-miss peak input $0.30/百万、output $1.20/百万，预算上界约 **$0.0171 USD**；off-peak 公布价格减半。配置中的旧 alias 按官方说明仍被接受，并按 Flash 价格服务于 DeepSeek-V4.1-Flash-1017。价格依据：[DeepSeek API 官方模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)。这是 token 上界估价，不是账户余额/实际扣费确认；实际使用量、是否达到 cache hit、价格调整和服务端请求行为仍需 trace 核对。

实现该上界所需最小依赖：一个可硬拒绝超输入/超输出/第三次请求的本地 provider harness，关闭 Critic/外重试，选择固定 dev PR 与现有冻结 diff，记录每次 request 的输入/输出 usage、状态、partial 与真实延迟。现有命令缺这些硬门禁；本轮未发模型请求。
