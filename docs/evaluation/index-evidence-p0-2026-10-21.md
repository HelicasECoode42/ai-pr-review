# P0 Python 索引证据回放（2026-10-21）

结论：这次离线回放没有证明索引改善审查质量。真实 Python dev PR 的 opt-in 结果用六段测试/定义上下文替换了 off 臂唯一的 CLI 上下文；CLI 恰好覆盖一个 Gold 相关范围，on 臂没有覆盖该范围。四条索引边均是同一个测试文件里的弱、词面、歧义调用点，不能说明生产运行时可达。Gold 的完整 source-range 覆盖两臂都是 1/10，且命中不同 finding；语义 precision/recall 与真实模型曝光均为 N/A。

实验严格零模型/API 调用，成本 $0；没有 holdout、prompt/Gold 修改或业务代码修改。每个 arm 新建独立 `GitSnapshot` 和索引 resolver，彼此没有缓存共享；同一 arm 内，正常 `bundle.follow` 后的 snapshot 文本缓存会被索引复用，索引自己的 parse cache 从空状态开始。使用仓库 `.venv`，强制离线 tokenizer 采用 UTF-8 字节上界，报告中的 `tokens` 不是模型 tokenizer token。复现命令：

```sh
.venv/bin/python scripts/evaluation/index_evidence_p0_replay.py > docs/evaluation/index-evidence-p0-2026-10-21.json
```

## 固定对照

样本为 `allan-mobley-jr/gimmes` PR 468，base `08efd0bbc34fd29de2a4077f07d24f3dc02a63b6`，head `e1477f8b3f18c1de6ff87dbca9465a7b106dc193`。没有旧的合法模型请求 trace，因此按 diff 中修改的 Python 函数声明机械冻结唯一查询 `check_staleness`；不是从 Gold 反选。off/on 用同一 snapshot、H1 绑定的正常 host search 请求、同顺序和 6000 字节上界，只切换 resolver。运行 `bundle.follow` 后，off/on 两侧 snapshot 缓存均为 10 个文件、373,950 字节；该搜索请求没有新增 Git snapshot 文件读取（新增字节 0）。初始化和请求共调用 snapshot search 8 次，其中固定的该请求各调用 1 次；on 索引解析 3 个文件、49,765 字节，`scan_ms=43.081`（包括词面 git 搜索和 Python 解析）。

| 项 | off | on |
|---|---:|---:|
| host task 终态 | `executed_with_evidence` | `executed_with_evidence` |
| bundle evidence refs | 1 | 4 |
| 可渲染 source 范围 | `src/gimmes/cli.py` head 257–338 | `tests/unit/test_staleness.py` head 17–32、36–41、43–55、57–70、72–84；`src/gimmes/strategy/staleness.py` base 16–20 |
| UTF-8 字节上界计数 | 4347 | 5028 |
| host index 边 | — | 4 条，均 `weak / lexical_search / ambiguous` |

因此范围差是明确的：on 丢掉 off 的 `cli.py` head 257–338，增加六段上下文。四条边有唯一的 source path/range（测试文件 38、51–53、65–67、80–82 行）并指向同一个 `check_staleness` 定义范围 45–114。手工核对后四处都是测试代码中的实际调用，未发现错误绑定；但它们都是测试调用，不能据此确认运行时入口可达。生产调用文件 `cli.py` 因 100 KB 单文件解析上限被 omission 排除。关系元数据保存在 trace；本回放只验证了可渲染 source bundle，没有构造完整 discovery prompt，也没有声称模型实际看到这些行。

Gold 仅在查询与回放冻结后用于 source-range 计数：off 完整覆盖 10 个 Gold 范围中的 1 个（CLI finding），on 也覆盖 1 个（测试 helper finding）；跨文件 Gold 范围各为 1/7。它们不是同一 finding，不能相减后称质量变化。Gold finding 内容没有进入请求或 bundle。

手工小夹具只标注静态关系行为，不是 Gold：alias 例在 on 识别到 1 条 `strong / import_binding / exact_binding`，但 off/on 的证据范围和 refs 完全相同；no-hit 两臂均无证据，on 额外解析 69 字节、约 23.262 ms；双定义同名例保留 lexical 结果并给出 `ambiguous_symbol_target`，索引不造边；budget=1 两臂均 `executed_no_evidence` 且 `evidence_budget_exhausted`。这些控制说明正确解析一条边也不一定增加可见证据，而无帮助和歧义仍有扫描成本。

## P1 因果判定协议

每个候选分别记录以下三轴，每轴只用 `supported`、`refuted` 或 `unknown`。静态边只能导航取证；文本差异本身不能证明 bug。

| 轴 | supported | refuted | unknown |
|---|---|---|---|
| **Reachability** | 冻结 snapshot 的源证据把候选触发条件从实际 producer/entry 连到目标分支，绑定、dispatch 与覆盖范围足以复核。 | 对已枚举的 producer 和入口，精确 guard/dispatch 证据证明该触发无法到达候选分支。 | 动态调用、歧义绑定、未覆盖文件或不完整 scope 留有可行路径缺口。 |
| **BehaviorDelta** | 同一候选触发输入在 base/head 得到与候选相关的不同实际行为；需要可复核的输入与行为证据。 | 同一触发在相关输入域中行为相同，或精确反例证明所述变化不发生。 | 缺少输入/契约、行为不可执行验证，或只看到了表达式/文本变化。 |
| **PRAttribution** | 候选特有的失败由 PR 改动引入或加重，并由已支持的可达路径和行为差异连接到 changed anchor。 | 同一失败在 base 已以相同触发和路径存在，且改动未增加/加重它；或精确证据反驳所述因果链。 | Reachability 或 BehaviorDelta 未支持、base/head 对照不完整、或只凭调用边、同名关系、文本差异归因。 |

任何一轴为 `unknown` 都保持候选 pending/needs-context；不能以另外两轴的分数补齐。这个协议不改变当前业务判定或发布规则。
