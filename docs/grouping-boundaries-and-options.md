# 分组边界、chunk 风险与评测方案

本文解释当前图规划里同名符号与分组的含义，列出拆分变更时可能丢失的上下文，并比较可尝试的替代方法。所有替代方案都是待评测选项，不表示已经实现。

## 同名 `save` 是怎么连起来的？

当前实现（[review_plan.py](../src/analyzer/review_plan.py)）不会对每个 `save` 调用实时执行全文 grep，也不会问类型系统“这个接收者到底是哪一个类”。它先在受限的源码 inventory 中解析文件，收集每个 `SourceFile` 的函数、类、方法等定义名，再把每个文件的语法调用名与这些定义名做匹配。结果是文件路径之间的弱关系。

例如：

```python
# storage.py
class PostgresStore:
    def save(self, record): ...

# audit.py
class AuditSink:
    def save(self, event): ...

# handler.py
def handle(store, audit, item):
    store.save(item)
    audit.save(item)
```

Python 解析器只记录属性调用的尾部名称 `save`。它会把 `handler.py` 连到所有索引到的 `save` 定义文件（这里是 `storage.py` 和 `audit.py`），并把边标为 `symbol_reference/weak`。当前不区分 `store` 与 `audit` 的类型，也不保留两次接收者调用各自对应哪个目标。

这不代表两个 `save` 是重载，也不代表发现了 bug。它们通常只是不同类或不同模块恰好采用同一个常见名称。Python 本身允许类方法同名；TypeScript 等语言也有类作用域。只有有类型/绑定信息的解析或运行时信息才能进一步判断接收者目标。当前实现不提供这种保证。

实现细节和边界：

- Python 使用 `ast.Call`：`save(x)` 记为 `save`，`store.save(x)` 也只记为 `save`。导入别名、词法作用域、接收者类型、重载/动态分派均未解析。
- TypeScript 解析器只记录被直接调用的 identifier，例如 `save(x)`；对象成员调用 `store.save(x)` 不满足当前的 `identifier` 分支。它也没有 TypeScript 类型检查或动态导入解析。
- 定义名被收集为“名字 → 文件集合”；调用匹配产出的 Relationship 节点仍是文件路径。单个文件里有多个同名定义时，图不会变成多条可分辨的函数节点。
- 名称目标很多时，当前最多保留按路径排序的前 20 个目标，并记下 `ambiguous_symbol_reference_limit`。这只是限制图大小，不说明保留下来的目标更可能正确。
- 这是语法索引，不是 `GitSnapshot.search()` 搜索结果，也不是“所有引用都找到了”。它受文件数、总字符数、路径过滤和支持语言限制。

因此弱边主要表达“值得作为相关文件提示”，而不是“已证实调用”。现在只有唯一解析的 import 强边驱动合并；弱边进入 `related_paths`，不参与强连通扩展。

## Chunk 边界可能丢掉什么？

当前以 diff hunk 为起点（见 [diff_parser.py](../src/analyzer/diff_parser.py)），而不是把整个函数树切成语义块。一个 hunk 可能覆盖一个函数、两个相邻函数，或者只有几行。生成 hunk 后，程序按 token 预算顺序拼接行；超过预算的单行再按字符边界二分拆分。这样可以保留原文和片段顺序，但片段可能落在表达式、字符串或标识符中间。

例如一个 hunk 可能是：

```diff
@@ -20,2 +20,2 @@
 def calculate_total(items):
-    return sum(item.price for item in items)
+    return sum(item.price for item in items if item.enabled)
```

如果预算把 `if item.enabled` 切到另一个片段，单看第一段可能无法理解筛选条件。更大的问题是，当一个跨文件关系把这些改动分到不同 ReviewUnit 时，逐组审查的模型可能看不到两端的契约变化。

当前每个 `ChangeSlice` 保存 `hunk_id`、`hunk_index` 和 `fragment_index`，可以追溯到原 hunk 和片段顺序；但还没有保存每个 fragment 对应的 hunk 内源行区间，也没有跨组边界核验流程。证据引用可能在 hunk 级生成，因此多个分片会共享同一组较宽的定义证据。不要把这些字段理解成完整的源码映射。

## 可比较的分组方法

| 方法 | 通俗解释 | 优点 | 风险或硬约束 | 当前状态 |
|---|---|---|---|---|
| 当前确定性贪心 | 从排序后最早的变更开始，沿唯一解析的 import 边加入能放进预算的变更；公共高连接节点会截断扩展。 | 稳定、快、无需模型；预算按 diff tokens 检查。 | 不给关系打权重；first-fit 受路径顺序影响；局部选择可能错过整体更好的切法；弱语义关系不合组。 | 已实现。 |
| 加权 greedy + 局部交换 | 给直接 import、测试关系、类型引用等不同关系设权重；先贪心成组，再尝试交换组边界附近的文件以减少重要关系跨组。 | 在当前图和小规模改动上容易解释；可把 token 作为节点成本、把跨组关系作为代价。 | 权重和交换次数需要调；每组 token 必须在每次交换后重新硬检查；仍受底层图质量限制。 | 建议优先作为离线对比候选，不代表最佳。 |
| Leiden 社区发现 | 先找图中连接紧密的社区，可生成多层次社区，再按预算拆成审查单元。 | 适合探索文件簇和多尺度结构。 | 社区算法目标通常不等于 token 预算；代码文件级弱边噪声可能误聚类，最终仍需拆分与边界校验。 | 可作实验基线；不能直接解释为函数语义。 |
| 图划分 / 超图划分 | 把文件分到固定数量的组，尽量减少跨组的边或超边；超边能表达“一组文件共同依赖某个接口”。 | 目标函数能显式优化跨组关系；可加入节点大小和近似平衡约束。 | 近似平衡不等于严格 token 上限；需要设定组数/容量并处理无法放入的变更；图目标不保证审查 Recall。 | 研究候选；需在 token 预算和召回上验证。 |
| LLM 元数据分组 | 把文件名、改动统计、签名/导入概要等轻量元数据交给低成本模型，让它提出相关文件组。 | 能利用命名、契约摘要和可选任务语境的语义线索。 | 额外模型成本和不确定性；覆盖校验、大小限制、失败回退必须明确；仅凭元数据可能误分。 | 本项目接受作为最多一次的待评测混合分组建议，不代表已实现或质量/费用已验证。 |

NetworkX 提供 Kernighan–Lin 二分等划分算法；可参考其局部交换思想。这类方法提供结构优化工具，不会自动懂代码语义或为本项目选出正确权重。[NetworkX community algorithms](https://networkx.org/documentation/stable/reference/algorithms/community.html)

## 外部实践说明

- [Alibaba Open Code Review 的 `grouping.go`](https://github.com/alibaba/open-code-review/blob/main/internal/agent/grouping.go) 在配置了 LLM 分组步骤时，会向模型提供文件元数据（代码注释说明不带完整 diff）来提议文件组；代码还限制单组最多 10 个文件。它对超预算组回退为逐文件组，对模型没有分配到的文件补逐文件组，LLM 分组失败也回退逐文件。[Architecture 文档](https://github.com/alibaba/open-code-review/blob/main/pages/src/content/docs/en/architecture.md) 描述了这些覆盖和 token 保护。本项目借鉴“相关文件放在一起审查”的目标，没有复刻这套算法或执行策略。
- [Aider repo map](https://aider.chat/docs/repomap.html) 用依赖文件图的 ranking 挑出能放进 token 预算的高相关 repo map 内容。它回答“给模型看哪些地图信息”，不是“把本次改动切成哪些审查单元”。[Aider prompt caching](https://aider.chat/docs/usage/caching.html) 则把系统提示、只读文件、repo map 和已编辑文件组织成可缓存的前缀；缓存命中是另一条节省重复输入成本的路径。
- [LlamaIndex CodeSplitter](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/text/code.py) 递归遍历 AST 子节点来打包 chunk；过大的叶节点（例如长字符串或注释）会硬拆以保留内容。接口里有 `chunk_lines_overlap` 参数，但不能据此推断每个 AST 拆分结果都自动带有重叠上下文。
- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/) 使用 Leiden 对从文本抽取的知识图做层级社区发现。它展示社区层次的用途，不证明把它直接用于代码依赖图会提升代码审查质量。
- [KaHyPar](https://kahypar.org/) 提供多路超图划分，并用 imbalance 参数控制近似平衡。即使把文件估算 tokens 作为节点权重，这类近似平衡也不能单独保证每个组都严格低于模型上下文预算，应用层仍需硬校验和回退。
- [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 讨论 just-in-time 检索：上下文保留路径、查询等轻量引用，需要时再加载内容。当前 `EvidenceRef` 也只保存源码位置和哈希，不计入规划阶段 diff token 预算；真正的按需读取和预算扣减仍需要后续执行阶段完成。

这些项目目标不同。repo map 相关性排序、文本知识图社区、AST chunking、超图割和审查分组不是同一个任务，不能仅凭算法名称比较谁“更省 token”或“更准”。

## 建议的下一轮方案（待离线评测）

可以按风险优先级推进；用户已支持将静态绑定作为 D3 首个交付，并接受低成本元数据建议进入受控对照。无需先加大型聚类依赖：

1. **先加强绑定信息。** AST 明确记录定义、调用点、import/export；绑定以 module + lexical scope + qualified symbol 为 key。Python 先确定性解析普通/相对 import、`as` alias 和局部遮蔽；TypeScript 逐步处理普通 import/export 与 re-export，可采用 compiler symbol 或确定规则。没有足够绑定信息就保留 `unresolved/weak`，不能猜调用目标，也不承诺动态 dispatch。
2. **保留语义单元与行映射。** 将 hunk 映射到包含它的函数、类、接口或配置块；保存 fragment 到文件行区间的映射。超过预算的单个语义单元再拆分，硬拆边界需要显式标为 partial。
3. **对边界作有限补充。** 当强依赖被预算切到不同组时，把对端的签名、接口定义、调用点等短证据作为可选重叠上下文，并单独扣 token 预算；汇总时核验跨组边界关系。重叠要有总量上限，避免复制完整文件。
4. **允许一次受限元数据分组建议。** 静态组先形成；弱关系未决时，可让低成本模型基于 path/status/counts/signature/import-summary 及可选任务描述，提出优化合并。宿主校验全文件/hunk 完整且唯一覆盖、没有未知/重复成员、原有强关系得到保留或产生跨组桥接核验，并对 prompt、响应、推理和请求次数计入总预算。模型不能更改源码证据或 coverage；失败确定性回退。缓存键绑定 snapshot + strategy/prompt + metadata + model。当前没有成本或 Recall 收益数据。
5. **预算不够就明确 partial。** 若完整变更和必要边界证据无法装入组，报告被拆分/未核验的关系与覆盖状态；不要把“所有文件都有某个 unit”误当成“语义上下文完整”。

评测时固定同一组 PR、标注、review model/version、质量目标和每 PR 总预算，比较逐文件、静态图分组、静态+一次元数据建议。逐文件可能重复 prompt 并增加请求次数，不能预先视为成本最优。至少报告问题/严重/跨文件 Recall、Precision、未覆盖 hunk 与跨组边界、partial/fallback、输入/cache-input/output/reasoning tokens、请求数和总调用成本；只在质量达到同一目标后比较省 token 或费用。

衡量结果时不要只看组数、group token 或图切边数。建议固定一批历史 PR 和人工标注的跨文件问题，对每种方法记录：

- 质量：问题级 Precision/Recall、严重问题 Recall、跨文件问题 Recall、误报率；按语言、改动规模和依赖类型分层。
- 覆盖：变更文件/hunk 是否进入计划、跨组依赖数、未定位语义单元数、partial 数和失败回退数。
- 成本与延迟：输入/输出 tokens、缓存命中与未命中 tokens、重复证据 tokens、检索/模型请求数、总费用及 p50/p95 延迟。供应商价格和缓存折扣会变，按实际请求统计，不把 tokenizer 估算当成账单。
- 基线：逐文件审查、当前贪心、加权 greedy + local swap；再视数据质量比较 Leiden、图/超图划分和元数据 LLM 分组。

图 cut ratio 或跨组边数只能说明关系图的结构被切开的程度，是代理指标，不等于缺陷 Recall。只有在质量指标不退化的前提下，再判断 token、费用和延迟是否改善。现有 181 项测试、22 hunks/14 units 的仓库样例证明的是确定性、覆盖与 diff-only 预算边界；没有证明语义分组最佳，也没有测出质量或费用节省。当前没有统一最佳 chunk 大小或通用 token 节省标准。
