# 本地离线性能快照（2026-10-26）

此快照测量当前 dirty worktree（HEAD `df397d5`，分支 `codex/evaluation-driven-review`），不代表干净提交。运行路径使用 fake provider；没有请求模型或 GitHub。重复采样为 5 次，报告中位数与范围，不从五个样本估计 P95。

## 固定输入与结果身份

| 档位 | 改动文件 | 新增行 | unified diff | 冻结源文件 / bytes | patch SHA-256 | snapshot SHA-256 |
|---|---:|---:|---:|---:|---|---|
| small | 1 | 4 | 335 B | 3 / 318 B | `6ea2b80c360b1c17b7dfdb7147272352dbfdd06cdc0b6502fa821ec73dfb4730` | `8ee198e9f3e2a0251059999c88c8ea0c0d18ccbde42cbcdb8bbe0645c7278d79` |
| medium | 8 | 96 | 4,167 B | 10 / 2,678 B | `834f809b2e4108803cbfc970eeaaf4777a8dcef78480ccd7d240380e6fd938e9` | `b70073d958c8febe3430ce0e7bd9ab031074a533750afe8173c7b208c36e19e2` |
| large | 24 | 1,560 | 41,759 B | 26 / 35,654 B | `34aadfe355e6c56703ddb7baa57789dae2e13d3e352e4fea90dadbc3c5ecd273` | `fd94765362b1caa85fc911d7ee99968d548795967632a53f8318a77c4ff4add4` |

固定合成输入来自现有 `scripts/evaluation/complexity_probe.py`。输入集合 SHA-256：`46e2a65d5a6113f97fed269fede5dc52deadec4a22df86bed27a7b5c01cc1ceb`。当前 `src/`、规则、配置与依赖源码字节的 runtime SHA-256：`17f1cacded80a0aced7392abfc01a26e8d75902cff17e2cfb733dcab0566de1d`；probe 配置 SHA-256：`995a7905cf6cf402beb8fcf1938ef9c2feed31532869267a98d27d01df7a0a6b`。 Python 3.12.7、macOS 27.0.1 arm64、Pydantic 2.13.4、httpx 0.28.1、Ruff 0.15.15。

本机有效设置摘要（只记开关与凭据是否配置，不记值）：repository review 开启、critic 开启、repository index 关闭；API key 和 GitHub token 配置存在。测量覆盖为 fake API key、无 GitHub token、`localhost:9` base URL、fake provider factory；benchmark Settings 使用 `_env_file=None`。读取本机设置摘要时只读取状态布尔值，没有输出凭据值。grounded+critic 组合当前返回 `unsupported` 并回退规则路径，下面将它标为 unsupported；其零模拟调用不是快路径的时延数据。

缺少本地 tokenizer 词表时，正式采样强制 `UTF-8 byte upper bound`。下文所有字节均为字节；它们不是 tokenizer token 或账单 token。没有真实模型调用，因此 prompt 大小只是 fake provider 收到的 UTF-8 字节。

## 启动、扫描与完整假 provider 路径

Python 空进程启动 P50 为 **21.06 ms**（20.39–46.99 ms）；CLI `--help` P50 为 **355.00 ms**（348.88–454.40 ms）。两个数字是各启动独立子进程，操作系统文件缓存未清除。

`rule scan` 覆盖 risk/AST/Ruff 与跨文件扫描；application 和总路径使用现有 production `ReviewApplication`，provider 响应是固定脚本 JSON，**不代表真实模型 E2E**。

| 档位 | rule scan P50 (范围) | grounded 假链路 P50 (范围) | application 阶段 P50 | fake prompt bytes | 报告 JSON bytes / 序列化 P50 |
|---|---:|---:|---:|---:|---:|
| small | 38.3 (36.3–41.6) ms | 45.8 (43.8–52.4) ms | 6.7 ms | 8,484 B | 3,232 B / 0.020 ms |
| medium | 43.1 (40.4–46.8) ms | 55.8 (51.2–97.3) ms | 9.8 ms | 16,192 B | 9,130 B / 0.035 ms |
| large | 87.6 (87.4–89.7) ms | 163.0 (151.4–174.6) ms | 69.5 ms | 23,836 B | 51,471 B / 0.121 ms |

Large grounded prompt accounting: **24 changed files / 24 hunks; 0 hunks fully included, 8 partially included, 16 omitted**. The report counted 33 evidence omissions (`changed_file_seed_limit` and `evidence_budget_exhausted`), with `context_truncated=true`; Ruff was `partial` at its 10-file cap. The 8 partial hunks do not imply the large PR was fully reviewed. This is a coverage limit, not a favorable latency result.

The grounded fake-chain P50 for the large case was 163.0 ms and includes about 69.5 ms of application work around a fake response. The largest measured stage was scanning at about 92.5 ms (risk/AST/Ruff and cross-file together). Do not read this as provider/network, user-facing E2E, or quality. The fixed fake response returned no suggestions. Follow-up profiling should first check scan-stage cost and large-input evidence coverage; no optimization was made here.

## Allan frozen PR: index off/on

Input is local Allan Mobley Jr. `gimmes` PR 468, base `08efd0bbc34fd29de2a4077f07d24f3dc02a63b6`, head `e1477f8b3f18c1de6ff87dbca9465a7b106dc193`; 4 changed files, diff **12,658 B** (`+129/-30`), with prepared and mirror diff SHA-256 both `6628d97c750caee1aeb01777c77a9d1608867bdc38fb1420575eef91b9ebde0c`. Both arms used the same `check_staleness` search request, 6,000-byte fallback evidence budget, same source snapshot and EvidenceBundle seed; each sample used a fresh GitSnapshot, but OS/Git object caches remained warm.

| Variant | route total P50 (range) | request P50 (range) | snapshot reads / cached source bytes | index parser work | rendered context |
|---|---:|---:|---:|---:|---:|
| off | 642.1 (625.2–690.4) ms | 68.5 (67.5–71.5) ms | 10 files / 373,950 B; request adds 0 files / 0 B | none | 6,085 chars |
| on | 647.8 (617.0–683.6) ms | 79.8 (77.6–99.5) ms | 10 files / 373,950 B; request adds 0 files / 0 B | 3 files / 49,765 B; scan_ms=58.8 | 6,307 chars |

For both arms, GitSnapshot seeded 10 unique source keys (373,950 B); the request added **0 source reads and 0 source bytes**. The indexed arm parsed 3 already cached Python files (49,765 B), produced a partial index and reported `src/gimmes/cli.py` over its byte cap. This 49,765 B is parser input, not extra file I/O. `scan_ms` is the index stage’s internal metric, not an isolated off/on delta.

Both arms rendered the same source excerpt: `src/gimmes/cli.py:257–338`, same evidence reference and SHA-256. The indexed arm added 222 characters of relationship/index metadata (6,085→6,307 chars); it did not add a source range. The recorded `EvidenceBundle.tokens=4,347` is byte-fallback budget accounting, not actual tokens, and equals the earlier baseline-first byte count for this request. The on/off route P50 difference is 5.7 ms and the individual ranges overlap; this single query does not establish an index speedup.

## 内存、冷热与复现

One independent process running a probe round reached **61,554,688 B peak RSS**; the child-process high-water mark after CLI help was **55,001,088 B**. This is a whole benchmark process measurement, **not per-PR memory**. macOS `ru_maxrss` is reported in bytes. Repeated probe and Allan samples ran in one warm Python interpreter with fresh app/snapshot state; the OS and Git object caches were not flushed.

复跑命令：`python /private/tmp/ai_pr_review_local_perf.py`。脚本 SHA-256：`0b7f689d69436d447cd55b918fdd9bd045b0aa64cf13796364bf29ffeddf35eb`。该脚本只复用现有 probe 和 P0 helper，结果写入 `/private/tmp/ai-pr-review-local-perf-results.json`；正式采样通过将 tokenizer fallback 固定为 UTF-8 bytes 禁止词表下载。

透明记录：正式采样前有一次未保护的初始化尝试访问 cl100k 词表；本地代理/沙箱拒绝后立即停止，未下载成功。正式 5 轮采样全都在调用 probe 前强制 byte fallback，因此未再次尝试外联。没有模型或 GitHub 请求、没有性能代码改动、没有 commit/push。
