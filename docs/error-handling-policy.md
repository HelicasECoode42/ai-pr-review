# 异常处理与降级策略

日期：2026-10-15。本文件回答一个具体问题：**什么异常可以吞，什么必须留痕**。它同时是当前实现的清单，便于后续 review 时对照。

背景：项目此前有 74 处宽泛的 `except Exception`，其中一部分是 `pass` / `continue`。它们让"没报错"与"没找到"变得不可区分——最典型的后果是**变更文件集、规则命中或分析结果被静默缩小，而所有下游计数都看起来正常**。

## 1. 判定标准（三类，各有可检查判据）

| 类别 | 允许条件 | 必须留下什么 |
|---|---|---|
| **A. 有意降级** | 上层仍能产出可用结果（例如 provider 失败回退到规则报告） | 一条**可观察**记录：日志、报告 warning、或结构化 `omissions`/`analysis_warnings` |
| **B. 记录后继续** | 单个元素失败不应毁掉整批（一行、一个规则、一个文件） | 记录必须含**对象标识**：文件路径、规则 id、条目名——只写"出错了"没有排查价值 |
| **C. 清理 / 尽力而为** | 只做资源释放或可选副作用 | 允许 `pass`，但 `try` 块内**不得有改变产物内容的业务逻辑** |

**禁止**：在会改变产物内容的路径上用 `except Exception: pass` 或 `except Exception: continue`。

**降级必须可被读者看到**：只写日志不算"用户可见"；如果一次降级会影响用户读到的结论，它必须出现在报告里（`analysis_warnings`、`skipped_context_files`、`coverage`、`budget_report.omissions` 之一）。

## 2. 本轮修复（2026-10-15 第三轮）

按"用户可见风险"而不是数量排序：

1. **上游入口不再能静默缩小变更文件集**（[github/client.py](../src/github/client.py)）。`get_changed_files` / `get_compare` 此前对单个条目 `except Exception: continue`——文件会在**任何覆盖记账看到它之前**消失，于是第一、二轮建立的 `coverage_summary.complete=true` 仍然成立却少审了文件。现在：
   - 字段类型异常由 `_changed_file()` **净化**（错误的 `additions`/`patch` 类型变成 `0`/`None`），文件仍然进入列表；
   - 真正不可表示（非 dict、缺 filename）的条目**抛出 `GitHubApiError` 并带上条目序号**，而不是消失；
   - `list_pull_requests` 是便利索引而非审查输入，仍跳过坏条目，但改为 `logger.warning`。
2. **CLI 的 6 处 `except Exception`**（[cli/main.py](../src/cli/main.py)）：原先 4 处 `pass`。写 step summary 失败现在通过 `_warn_step_summary_failure()` 同时**打印到 console** 并（有报告时）写入 `report.analysis_warnings`——此前 CI 看起来"什么都没报"，而运行自认为成功。失败报告的逐文件列表也保证"每个变更文件一行"，不再因格式化异常少一行。
3. **分析器层的 6 处静默 `continue`**（[ast_rules.py](../src/analyzer/ast_rules.py)、[cross_file.py](../src/analyzer/cross_file.py)、[risk_rules.py](../src/analyzer/risk_rules.py)）：规则抛错、hunk 无法解析、visitor 崩溃、逐行扫描失败，全部改为带对象标识的 `logger.warning`。原先"规则静默不生效"与"规则未命中"无法区分。
4. **证据门不再把工具缺口说成模型错误**（[evidence_validator.py](../src/reviewer/evidence_validator.py)）：文件没有可解析 hunk 时，其 added lines 不会进入证据表，此前任何落在该文件的建议都会被判为 *"Cited location is not an added line in this diff."*——即把"我们读不到"写成"模型编造"。现在这种建议记为 *"Changed file has no parseable diff hunk, so the citation cannot be verified."*（仍然拒绝，因为无法核验，但原因诚实）。
5. **Web 历史列表**（[service/app.py](../src/service/app.py)）：损坏的历史条目仍被跳过（不能因为一条坏文件毁掉整个列表），但改为 `logging.warning`。

## 3. 当前清单（生产路径，`src/` 排除 `src/evaluation/`）

| 类型 | 数量 | 说明 |
|---|---:|---|
| `log`（记录后继续/降级） | 33 | 主要形态，符合类别 B/A |
| `record`（写入警告/结构化遗漏） | 11 | 类别 A |
| `other`（含返回降级值、分派到 fallback 对象） | 9 | 逐个人工确认过 |
| `return`（返回降级摘要，如 `{"count": "?"}`） | 7 | `agent/trace.py` 的摘要器：降级结果本身带可见标记 |
| `reraise` / wrap 成 `GitHubApiError` | 4 | 类别 C/A |
| `pass` | **1** | 见下 |

唯一剩下的 `pass` 是 `ast_rules.py` 的 `_node_source()`：源码片段提取失败时 `pass`，紧接着 `return f"Line {node.lineno}"`——**返回值本身就是降级标记**，属于类别 A，不是静默吞。

全仓（含 `src/evaluation/`）共 71 处宽泛 `except Exception`；`src/evaluation/` 的 6 处属离线实验代码，不在本轮范围。

## 4. 已知缺口（明确不假装已解决）

- **分析器层的 warning 只进日志，未进报告**。`collect_signals()` 只返回 `findings`，要把分析降级上抛成 `analysis_warnings` 需要改它的返回契约（会影响所有调用点）。这是把类别 B 提升为"用户可见"的下一步。
- **`src/evaluation/` 的 6 处未处理**：离线评测 harness，失败会污染实验数据，但优先级低于生产路径。
- 本轮**没有**改动 provider 的重试策略、超时语义或费用相关行为——那些属于"远端调用不可回滚"的既有边界，不在"消除静默降级"的范围内。
