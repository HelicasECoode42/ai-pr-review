# 复杂度测量与下一轮实验（2026-10-19）

## 口径

本轮使用生产 `ReviewApplication`、默认 engine、规则/AST/Ruff 和跨文件扫描，输入为固定合成 diff 与内存冻结快照，provider 返回脚本结果。不读项目 `.env`、不发网络请求，不计算质量或费用。输入为 1/8/24 文件、4/96/1560 新增行。每组固定相同预算；个别故障场景刻意改小证据预算。

复现：`.venv/bin/python scripts/evaluation/complexity_probe.py`。完整 JSON 输出 `reports/evaluation/complexity-probe-2026-10-19.json`（忽略的本地产物），记录 fixture、源码/规则/依赖 hash、模型参数、每次调用的输入输出字节与阶段、预算和覆盖状态。该脚本不盘点 ReviewBench；不可从其中硬编码样本库存推断当前数据就绪。

## 结果

16 个 AI 组合中 13 个可执行、3 个 grounded+Critic 明确 unsupported 且零调用；共24次模拟调用。另跑3档规则-only，均零 provider 调用。大输入 empty 响应场景：

| 路径 | 模拟调用数 | prompt UTF-8 总字节 | 读取调用/不同side+path/缓存命中 | 证据省略数 |
|---|---:|---:|---|---:|
| one_shot_control | 1 | 25,740 | 20/20/0 | 0 |
| current_grounded | 1 | 39,193 | 417/50/367 | 27 |
| legacy_two_stage | 2 | 49,491 | 20/20/0 | 0 |

- 大输入 grounded 的24个源 hunk：11完整纳入、1部分、12未纳入。账目完整不代表全量内容进入模型。
- 显式取证场景为2次 discovery +1次 verification；1-token证据场景记录预算省略，模拟超时走降级。
- legacy 人造五热点场景是1次分诊+5次深审，prompt 共143,446字节。**6次仅是这个脚本场景的最大值，不是生产最坏上界**。
- 417次读取接口调用里367次为缓存命中，不能说417次磁盘/网络IO，也不能据此宣称检索慢。该合成场景揭示fan-out，应在真实冻结PR上再测。
- 本机扫描/上下文/假响应耗时只作诊断；单次无预热、无重复分布，不当稳定性能基准。`chars/4`仅粗估，UTF-8预算单位也不是账单token；cache tokens和远端模型时延未知。

生产源码/配置身份：`70d4257841ee6c8c15c2ed192cfc07519e7407472cde15f56bc329788ce0993f`；fixture集合身份：`46e2a65d5a6113f97fed269fede5dc52deadec4a22df86bed27a7b5c01cc1ceb`。报告重跑时需重新核对身份；修改源码后旧结果仍属旧身份。

## 后续按结果推进

1. 当前可靠性补齐已通过离线验收，详见 [STATE](../STATE.md)。不引入数据库、队列或额外框架。
2. 已重新核验开发集 prepared manifest 与 split 的固定 hash：两者均为 `f7c66ed67481e3c657c270ca119d48ed6d13d30489079672aac113cf8b84c1b5`，相符；manifest 中旧绝对路径解析到 sibling `../eval-data` 下的固定 prepared 输入。没有重写信任 hash。preflight 为 9 ready / 15 not-ready；本次选中的 dev 输入及 base/head mirror 可用。完整试跑记录见[真实模型开发集试跑](real-dev-pair-2026-10-19.md)。
3. 已对一个先前暴露的开发 PR 各运行一组 2048 与 8192 输出上限的 one-shot/grounded 对照；两组最终都因 `finish_reason=length` 失败，累计 provider token 峰价上界 ¥0.321488，不产生质量分数。thinking 开关的最小修复和 fake transport 回归已通过；预算内计划的最终非推理配对被 auto-review 拒绝，未发送。该样本不作独立评估，不能生成语义 TP/FP/FN、P/R 或晋升结论。
4. 从真实失败中只改一个策略因素，执行诊断→候选→隔离回放→门禁→人工接受/拒绝→回滚演示；旧污染holdout只作回归，独立新样本不能提前用于调参。现在没有真实晋升或自改进增益证据。
5. 相同质量/覆盖与预算下没有收益的路径再退役；保留有实际用途的对照，按入口使用记录核实后删代码，不用行数代替有效性。
