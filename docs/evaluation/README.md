# 评测证据入口

截至当前 HEAD，提交说明提到六个 dev case、三路共 145 条 finding，但工作树中没有可复核的完整协议、逐候选裁决和原始结果；该评测尚未完成质量验收，因此目前没有可用于简历的质量提升指标。功能实现和离线测试结果应按代码与测试本身说明，不能替代真实模型质量验证。

仓库仍保留一份较早的 2026-10-20 raw-model 机制观察。它在两个已暴露的 dev PR 上各跑两个变体，共 4 次请求；人工非盲裁决得到 raw 两变体并集 7/23（30.4%）语义命中，harness 对照为 2/23（8.7%）。候选没有经过核验或发布，样本量只有两个，结果不能解释为 precision、发布召回、泛化或当前 HEAD 的收益。与确定性规则合并的 9/23 是另一条离线通道，不计入模型结果，也不能替代六 case 三路评测的证据。

该实验的汇总与逐候选裁决保留在 [`raw-model-baseline-2026-10-20.json`](raw-model-baseline-2026-10-20.json) 和 [`raw-model-baseline-2026-10-20-adjudication.json`](raw-model-baseline-2026-10-20-adjudication.json)。实验使用 `scripts/evaluation/raw_model_baseline.py`、`AA-Factory_aafactory-prototype_17-a1978cb7` 与 `brexhq_CrabTrap_15-5ef0884c`；输入来自本地 sibling `../eval-data/` 的 prepared、gold 与 mirrors。数据划分及限制见 [`dev-set.md`](dev-set.md)。旧生成报告可从 Git 历史恢复；黄金集、开发划分、测试夹具和评测脚本仍保留在工作树中。

不要把这份实验写成产品质量提升或独立泛化结论。若要给出质量指标，需针对当前代码版本重新运行并完成独立裁决与发布路径核验。
