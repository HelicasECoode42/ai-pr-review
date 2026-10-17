# 跨文件开发案例：Gimmes staleness volume 语义

## 候选与证据边界

选择开发集中的 Python 案例 `allan-mobley-jr_gimmes_468-e1477f8b`，PR [#468](https://github.com/allan-mobley-jr/gimmes/pull/468)。固定输入版本为 base `08efd0bbc34fd29de2a4077f07d24f3dc02a63b6`、head `e1477f8b3f18c1de6ff87dbca9465a7b106dc193`，与 2026-10-13 输入准备记录和当前 PR 元数据一致。开发集登记见 `datasets/dev/split.json`；该案例已进入既有评估，不属于未见测试。

本分析只读固定版本源码、PR 描述和 Kalshi API 字段定义；未执行 PR 代码、测试或目标仓库命令，未调用模型，也未下载或克隆仓库。PR 描述只用来理解改动目的，不作为缺陷成立的依据。

**本报告是评估材料，不得整体作为模型输入。**下文的 gold 视图、因果裁决、结论和源码行号都只能供评估者使用。

## 跨文件因果链

- **固定版本代码事实：** head 的 `src/gimmes/strategy/staleness.py:70-85` 先取 `m.volume_24h`，但只要它为零，就回退到 `m.volume`；之后 `has_volume = volume >= volume_floor`，而活跃条件会令 `new_count = 0`。因此只要回退值达到 floor，即使当前 24 小时量为零，也不会累计 stale count。调用端 `src/gimmes/cli.py:312-315` 没有覆盖 `volume_floor`，实际使用默认值 10。
- **依赖外部字段语义：** `src/gimmes/models/market.py:49-51` 将 `volume`、`volume_24h`、`open_interest` 分为不同字段；`src/gimmes/kalshi/markets.py:41-43` 分别映射 API 的同名字段。Kalshi 官方 API schema 将 `volume_24h` 明确定义为过去一天的成交合约数，而 `volume` 是市场成交量计数；把后者解释为累计总量是依据这组字段定义作出的语义判断。参见[官方 API schema](https://gitlab.com/kalshi-api/kalshi-api/-/blob/main/PROMPT-TEXT/ALL-API-TEXT.txt)和[官方市场响应字段示例](https://docs.kalshi.com/api-reference/events/get-multivariate-events)。外部 schema 是当前公开版本，并非已固定在 PR head 日期的历史 schema。
- head 的 `tests/unit/test_staleness.py` 测试 helper 将 `volume_24h` 设为与 `volume` 相同，没有覆盖“过去一天为零、历史累计量仍高”的分支。这个缺口解释了现有测试为什么未暴露该行为；测试缺失本身不是本案例的根因。

## 触发条件、预期与实际

触发条件：市场价格未变、open interest 未变、`volume_24h == 0`，但历史累计 `volume >= 10`。API 将这两种 volume 区分后，代码回退取到的是历史累计量，`has_volume` 为真，计数器被重置为 0。

预期行为是用过去 24 小时交易量判断近期活跃度；零近期成交且价格/OI 不变时，应增加 staleness 计数，并在达到配置阈值后允许过滤该市场。**代码路径的静态推演**是：如果累计历史成交量达到 floor，它会满足 `has_volume` 并重置计数，因此此条件下市场可能持续通过过滤；本报告没有观测到真实市场数据触发或运行结果。因果归属要精确区分：`volume_24h` 为零时回退到 `volume` 的表达式已存在于 base；本 PR 将旧的“相对上轮成交量是否激增”逻辑改为“当前 volume 是否达到 floor”，使同一累计量在每轮都满足 `has_volume` 并重置计数。这是 PR 新决策条件与既有字段回退的交互，不是本 PR 新增了回退语句。

Gold anchor 位于 head 的 `staleness.py:72-73`，锚定的是该既有回退表达式；因果条件 `has_volume = volume >= volume_floor` 位于同一变更 hunk 的 head `staleness.py:79`。评估时应以 hunk/根因语义匹配，不把 gold 的行号锚点误读成该语句由本 PR 新增。

**静态可达性：** head 的配置将 `staleness_cycles` 默认设为 5；CLI 在阈值大于 0 时调用该过滤器，因此正常默认配置会经过这段分支。没有运行 PR 代码或实际扫描市场，尚未观察到具体市场数据触发，也没有测量影响范围。

反证边界：若 `volume_24h` 为正，代码使用近期成交量，此特定问题不触发；若累计 `volume < 10`，回退也不会把市场当作活跃。若上游接口或数据适配器把缺失字段转换为 0，当前逻辑同样回退，但应将“字段缺失/未知”与“确定为零成交”区别处理。以上语义判断依赖 Kalshi 字段定义，不能泛化为其他交易所或其他 Market provider。

## 去 gold 的实验输入与评估视图

可复用的**去 gold 输入视图**仅包括：固定 PR 身份、PR 标题/描述、base→head 变更 diff，以及一个只读源代码工具。重点验证 Agent 是否能从改动自行发现调用方、`Market` 定义、API 字段映射及字段时间窗口。不得附加本报告因果分析、`staleness.py:72` 等行号、gold 文件、gold 标签或下面的评估信息。现有持久输入已包含 PR 元数据和 diff；本轮新增了 `../eval-data/prepared/inputs/allan-mobley-jr_gimmes_468-e1477f8b/model-input-manifest.json`，固定 PR/base/head、模型可见文件 SHA-256、六个静态源码文件在 base/head 的 Git blob 与 SHA-256，并声明 gold 与本报告不进入模型输入。清单还明确 Kalshi API 字段契约尚无固定快照；若实验需要该语义证据，应先保存并校验权威快照，否则只能报告语义证据缺失。单案例 runner 清单为 `../eval-data/prepared/cross-file-gimmes-one.json`，只引用去 gold 输入。输入文件哈希：`pr.json` `254b2d452fb37709696a8d7911768f58a997391213cc4b303c5ae7de69e5f442`；`diff.patch` `6628d97c750caee1aeb01777c77a9d1608867bdc38fb1420575eef91b9ebde0c`；manifest `e2e8e2f8efa42138c71cd0475bfd229be2b3b5ee2f49cbac6775c55a203223ef`。本轮未联网抓取、未读取 gold、未运行目标代码或模型。只读工具可访问以下路径：

- 改动入口：`src/gimmes/strategy/staleness.py`、`src/gimmes/cli.py`、`src/gimmes/config.py`。
- 取证目标：`src/gimmes/models/market.py`、`src/gimmes/kalshi/markets.py` 及必要的 API schema。
- 回归边界：`tests/unit/test_staleness.py`，仅作为当前覆盖情况证据。

Gold 只留在独立评估视图：ReviewBench 快照中的 `golden/allan-mobley-jr_gimmes_468-e1477f8b.json`，SHA-256 `1552af5ae4cb65d2f1edab601dd7a58182b778bad60017b74e56adc27ed39a9d`；对应记录位置是 `src/gimmes/strategy/staleness.py:72-73`。本报告不复制 gold 标签原文；不得把 gold 文件或标签行号放入 Agent prompt、检索索引或证据规划输入。评估时再将模型候选与独立人工根因裁决逐条比对。

该案例可作为跨文件取证的候选开发样例：在**相同 PR diff**下，未读数据字段定义时不能确认 `volume` 的时间窗口；读取 Market 模型、上游映射及字段契约后，才能解释该值如何穿过调用链并可能触发计数器复位。它可用于 fake-provider 调度与 evidence trace 验证；fake-provider 成功只证明工具调用和证据记录流程，不证明真实模型的语义理解或泛化能力。

## 可复查源码

- [变更比较](https://github.com/allan-mobley-jr/gimmes/compare/08efd0bbc34fd29de2a4077f07d24f3dc02a63b6...e1477f8b3f18c1de6ff87dbca9465a7b106dc193)
- [staleness 判定](https://github.com/allan-mobley-jr/gimmes/blob/e1477f8b3f18c1de6ff87dbca9465a7b106dc193/src/gimmes/strategy/staleness.py#L70-L79)；[生产调用](https://github.com/allan-mobley-jr/gimmes/blob/e1477f8b3f18c1de6ff87dbca9465a7b106dc193/src/gimmes/cli.py#L312-L316)
- [staleness 默认配置](https://github.com/allan-mobley-jr/gimmes/blob/e1477f8b3f18c1de6ff87dbca9465a7b106dc193/src/gimmes/config.py#L622-L638)
- [Market 字段](https://github.com/allan-mobley-jr/gimmes/blob/e1477f8b3f18c1de6ff87dbca9465a7b106dc193/src/gimmes/models/market.py#L49-L51)；[Kalshi 字段解析与映射](https://github.com/allan-mobley-jr/gimmes/blob/e1477f8b3f18c1de6ff87dbca9465a7b106dc193/src/gimmes/kalshi/markets.py#L19-L24)及[字段赋值](https://github.com/allan-mobley-jr/gimmes/blob/e1477f8b3f18c1de6ff87dbca9465a7b106dc193/src/gimmes/kalshi/markets.py#L39-L43)
- [测试 helper 与回归用例](https://github.com/allan-mobley-jr/gimmes/blob/e1477f8b3f18c1de6ff87dbca9465a7b106dc193/tests/unit/test_staleness.py#L14-L40)
