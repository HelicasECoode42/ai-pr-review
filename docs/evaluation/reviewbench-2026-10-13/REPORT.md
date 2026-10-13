# ReviewBench 首轮真实基线与 Critic 对照

日期：2026-10-13。工程试运行，不是排行榜成绩、独立人工验收或未见测试结果。

## 结果

官方25个PR全部完成真实模型审查与原版ReviewBench本地计分，计分没有执行错误。
配置名为deepseek-v4-flash；25个成功响应均报告实际模型deepseek-flash。
judge也使用DeepSeek同一模型家族，因此仍需人工抽查。

| 指标（微平均） | 单次主审基线 | 固定建议 + Critic |
|---|---:|---:|
| 任务数 | 25 | 25 |
| 候选建议 | 12 | 10 |
| 匹配TP建议 | 10 | 8 |
| 匹配FP建议 | 1 | 1 |
| 新发现TP建议 | 1 | 1 |
| Augmented Precision | 91.67% | 90.00% |
| Grounded Recall | 3.98% | 3.69% |
| 覆盖参考TP | 14 / 352 | 13 / 352 |

不要把“11/12建议判为有价值”写成“91.7%准确率”：它衡量已报告建议，无法解释大量
未发现问题。当前主要瓶颈是覆盖，而非如何再减少评论数量。

官方匹配允许一条建议覆盖多个参考问题，所以建议TP数与覆盖TP数不同。
两次独立judge对同一条保留建议的匹配范围存在差异：k1LoW任务的覆盖数从1变2。
为了归因，又使用固定的基线标签及匹配关系计算Critic效果：保留9条TP、1条FP，
覆盖12/352，Grounded Recall为3.41%。这更直接显示删掉2条基线TP的影响。
本次不是重复运行统计，不能据此宣称一般性的稳定增益或损失。

## Critic 失败案例

Critic没有过滤掉基线误报；它把两条此前被judge匹配到参考TP的建议标为uncertain，
生产apply_critic策略随即删除：

1. DisciplinedSoftware/Codebase-Conversion：Rust函数结果先输出，Fortran结果在数组
   后输出，位置比较产生错误结果。Critic看到Rust片段，但没有Fortran生成器与比较器
   代码，因此不能确认整条执行链。
2. superyyrrzz/ForgeMap：Pages并发分组按ref切分导致部署协调问题。Critic只有并发
   配置片段，没有workflow触发器，无法确认多ref场景。

这不意味着两个结论已经经独立人工证明。它说明当前验证器因上下文不足而丢弃此前
判为有效的建议，尚不能把“uncertain”解释为“已反驳”。完整消息与Critic理由保存在
critic-removed-findings.json。

下一步应让needs_context触发有界补充检索或进入待复核展示，先获得调用方/契约/配置
证据，再决定是否发布。不要简单去掉Critic或放宽所有阈值。

## 类别和上下文

基线对maintainability、api-architecture、testing、documentation、performance、
accessibility的参考覆盖为0。它目前偏向行为缺陷，不能声称已实现设计审查。
14/25个任务标记上下文截断，值得作为后续漏报诊断维度，但本实验没有证明截断是
所有漏报的原因。目标仓库指引只读AGENTS.md和docs/review-guide.md，没有完整定义/
调用者检索。

这是显式one-shot控制基线：Evidence Gate开启，Critic关闭，signals验证关闭，目标
指引开启；不是生产auto/two-stage总体效果。rule-only和two-stage、Evidence Gate移除、
新上下文策略、设计视角仍须分别做控制实验。

## 执行与复现边界

原版ReviewBench代码：968291d99e82b3e2969d7e0b4acca62585886c13。黄金集和judge prompt
fingerprint在baseline-score.json的eval_config中；官方计分实现未被改写。

初轮曾出现402；用户恢复额度后出现45s超时和中途连接关闭。后续使用流式连接，
保留成功结果，只补失败。成功样本中有不设输出上限、32768、65536三类预算，读取
超时有45s和180s差异；source hash与dirty状态逐任务留档。它们是一轮工程接入试验，
不能当作统一预算、干净commit的最终效能基线。后续正式before/after必须固定预算、
干净版本、judge和输入，重复测量。

Critic对照使用已固定的基线建议和完全相同消息，不重新生成主审内容，因此可分析
此组建议的过滤效果。官方本地judge结果不等于ReviewBench官方托管评测。
25公开小集已用于研究，不能再宣称未见测试；最终泛化需另外冻结repo-grouped测试集。

run-summary.json的token统计只覆盖最终成功主审响应，不包含被取消、失败、重试、
Critic和judge全部调用。不能把它当实际总花费；没有编造美元成本。

## 文件说明

- input-preparation.json：全部25个任务的官方身份、diff hash与准备状态。
- baseline-candidates/：原始统一输出，25个任务，无失败空结果替代。
- baseline-audits/：审查报告、实际usage、source hash、预算和覆盖。patch正文以hash
  替代，按冻结base/head从镜像恢复，避免重复提交大体积代码。
- baseline-score.json / baseline-score.details.json：原版计分与逐条标签。
- critic-score.json / critic-score.details.json：Critic后重新计分。
- critic-audits/：每个任务的Critic判定与usage。
- critic-removed-findings.json：两条被删除建议及先前标签。
- run-summary.json：指标、逐任务运行信息和统计边界。
- attempt-history.json：各轮成功、失败、复用及中断状态；中断的在途请求未必返回用量。

工程验证：135项Python测试通过；扩展审计0漏洞、编译及VSIX打包通过；远端Python、
VS Code build、review三项检查通过。Draft PR #45保持未合并，自改进未启用。

## PR #45 自审暴露的问题与修复

自审运行 38388354570 成功，并非 HTTP 402。报告却同时声明“全部文件进入上下文”与“上下文被裁剪”，把评测 JSON 引用代码的 7 个匹配呈现为高风险规则命中。该工作流实际 checkout 基准分支，不能用其成功结果证明 PR 新版 reviewer 的效果。

新增修复：评测 JSON 排除代码规则及 patch 上下文（Python 源码仍扫描）；源码优先且单文件预算受限；预算遗漏逐文件记录；截断报告标为 partial；规则明确为未验证线索，不再自动发成 GitHub 行内评论；Critic uncertain/unverified 候选保存在 pending_suggestions 并展示，排除确认问题统计及发布。帮助文本检测不再使用会提前关闭管道的 grep -q，固定输出宽度避免旗标折行。

同一 a652820 冻结 diff 的确定性检查见 self-review-reliability-check.json：评测 JSON 命中 7→0；新版上下文包含 9 个补丁段，仍有 29 个文件因预算遗漏。此检查无需模型、不证明 Precision/Recall 提升，也没有把未覆盖文件称为审查成功。真实模型效果需另行运行与独立标注。
