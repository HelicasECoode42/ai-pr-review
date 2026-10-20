# Missing-evidence trigger 两例真实运行（2026-10-19）

这是一次 treatment-only、合成 fixture 的机制观察，不是 PR 质量、Recall/Precision 或泛化评估。两例按 positive、negative 顺序调用生产 `reviewbench.run_review`；冻结代码身份为 commit `ba4eee9f1023f907ef9040771a183f89e69bb613`、dirty worktree、Python source SHA-256 `92be2edaf8f558afda266c755017a760b4a5927e887d9797ae132e0af857301b`，discovery+verification prompt SHA-256 `836f477cfd8f4520d289b4f089dcebc65a708a337f50a4cba6de5f4e14f48506`。fixture 各文件 SHA、冻结协议及 runner hash 见随附 `protocol.json`、`runner.py`。

| Case | HTTP / input upper bytes | 实际输入/输出 tokens | 峰价费用 | 时间 | 观察 |
|---|---:|---:|---:|---:|---|
| Positive | 2 / 20,054 | 5,066 / 1,117 | ¥0.019068 | 6.10 秒 | 首轮声明两个决策相关 unknown 与 missing-evidence 项，并对 `load_for_actor` 发出 hypothesis-bound search。host trace 记录两个任务 `executed_with_evidence`；head `src/accounts.py` 第 1–5 行不在首轮 user prompt，在第二 discovery prompt 可见。第二轮没有候选，因此 verification 不适用，也没有验证请求触发 follow-up。 |
| Negative | 4 / 43,841 | 11,114 / 3,476 | ¥0.050036 | 16.49 秒 | helper contract 已完整出现在 diff，模型仍请求读取 `src/accounts.py` 并 search `get`，属于冗余请求；trace 将模型任务记为 `unknown_hypothesis_id`，这些请求没有被 host 执行（文件另因 changed diff 而正常进入 changed-source indexing）。模型生成 2 个 raw candidates，最终都为 `needs_context` / pending，confirmed 0、final suggestions 0。verification 的 `missing_evidence_requests` 为空；普通 context requests 的 intent task trace 为 `unknown_hypothesis_id`，host 未按这些请求执行 follow-up。 |
| 合计 | 6 / 63,895 | 16,180 / 4,593（20,773） | **¥0.069104** | **22.87 秒** | 6 个 HTTP 均 200、finish reason `stop`；没有 fallback。usage 全部可得。 |

本次 hard cap 为每例 4 HTTP、200,000 UTF-8 input upper-bound bytes、每 HTTP 3,072 output tokens、300 秒；全局 8 HTTP、400,000 bytes、424,576 token upper bound、600 秒、1,000,000 micro-CNY。按 DeepSeek 官方当时 deepseek-flash 峰价缓存未命中输入 ¥2/M、输出 ¥8/M，完整上界为 ¥0.996608；调用前官方余额预检 ¥9.29，型号 `deepseek-flash` 可用。两个样例均未触 cap。费用是根据 provider usage、并将所有输入按缓存未命中的峰价计算得到的保守估算（整数 micro-CNY）；不等于账户扣款对账。参考[官方价格表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)及[官方余额接口](https://api-docs.deepseek.com/api/get-user-balance/)。

Positive 满足 discovery missing-evidence trigger 与 host read/呈现观察；它没有验证新修订的 verification follow-up 契约，因为 discovery 读取后没有候选。Negative 对 diff 已可见 helper 仍额外索取上下文，未满足负例预期；verification 也没有已有 missing-evidence obligation 可供绑定，不能将空 `missing_evidence_requests` 计作该契约的通过或失败。样例模型输出与 pending 状态不用于质量裁决。

完整逐 HTTP request body、raw response、usage/stop、logical prompt/raw model content、生产 review trace 和 fixture-only Snapshot audit 保存在 `reports/evaluation/missing-evidence-trigger-real-2026-10-19/`：入口为 `summary.json`，每例在 `positive/`、`negative/` 下。首轮 helper 可见性核对依据保存的 HTTP body：positive 首轮未包含 `load_for_actor` 定义，第二 discovery 请求包含对应源代码范围。此次未读取 Gold/holdout，未发送仓库配置、私密文档或项目 guidance，也未修改业务源码、Prompt、fixture 或标签。

各次序列化 HTTP JSON body 的 UTF-8 字节数依次为 positive `[7,255, 12,053]`、negative `[8,842, 10,123, 11,227, 12,157]`，合计 61,657 字节；上表的 63,895 是生产预算 estimator 另加 message envelope 后的输入上界，不是 provider 计量 token。
