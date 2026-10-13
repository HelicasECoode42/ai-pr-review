# Evaluation-driven AI PR Review

## Status and scope

Implementation starts from main `d99524db818494ac31afd06f9a40560808de7176` on
`codex/evaluation-driven-review`. Draft PR #43 adds the daily local workflow;
this change does not copy or depend on its CLI implementation. The existing
reviewer and all entrypoint defaults remain compatible.

This is evaluation infrastructure and an offline configuration-search prototype.
It does not establish improved semantic quality or complete official evaluation.
The 4 synthetic development cases are bootstrap fixtures, not external evidence.
Their negative labels have executable equivalence checks for stated input domains,
not a proof that arbitrary real PRs are defect-free.

## ReviewBench adapter

Source contract: https://github.com/review-bench/ReviewBench/blob/main/AGENT_CONTRACT.md

```sh
# Set RB_NWO, RB_PR_NUMBER, RB_BASE, RB_HEAD, RB_AGENT,
# RB_DIFF, RB_PR_JSON, RB_OUT using the official mounted-input contract.
# OPENAI_API_KEY is read from environment; never place it in the image.
python -m src.evaluation.reviewbench

docker build --platform linux/amd64 \
  --build-arg REVIEWER_COMMIT=$(git rev-parse HEAD) \
  -f tools/reviewbench/Dockerfile -t ai-pr-review-bench .
```

The image is a starting point, not yet a validated leaderboard submission.
Build from a committed checkout; base image digest pinning and official
onboarding are required for published reproducibility. The adapter reads
RB_CONFIG_MODEL, RB_CONFIG_EVIDENCE, RB_CONFIG_CRITIC, RB_CONFIG_CONTEXT_PACK
and RB_MODEL_BASE_URL. Configuration booleans must be true or false.
It removes stale output before running and writes findings only after successful
AI review. Critic failure also fails the experiment. Binary/header-only patches
fail explicitly rather than silently excluding changes. Deleted files cannot
produce head-line findings. Remote context fetch is disabled.

Target Context Pack reads only AGENTS.md and docs/review-guide.md inside the
mounted target root, at most 500 tokens per file; symlinks escaping the root are
excluded. It is intentionally narrower than production Context Pack, so this
ablation measures target guidance, not arbitrary repository retrieval.

## Dataset and scoring

`GoldenCase` records provenance, task group, split, input and logical defects.
`validate_splits` rejects duplicate cases and task groups crossing splits.
Freeze a repository-grouped dev/validation/test manifest before optimization;
all bootstrap fixtures currently belong to dev. No held-out score exists yet.

`score(prediction_count, golden_ids, edges)` accepts independently adjudicated
semantic edges, not line-distance matches. Maximum bipartite matching counts
one prediction per golden defect and leaves duplicate predictions as false
positives. Empty denominators produce null rather than fabricated perfect scores.
This strict internal score is not the official ReviewBench score: the official
judge also classifies unmatched findings, which may identify valid new issues.
Use https://github.com/review-bench/ReviewBench/blob/main/docs/judging.md for
local official judging; local scores cannot be published as leaderboard results.

## Controlled experiments

```sh
python -m src.evaluation.experiment datasets/golden/synthetic-dev.json \
  --output reports/evaluation/synthetic-model-run.json
```

The variants are baseline, no_evidence, with_critic and no_context. Main model,
inputs, threshold and suggestion budgets stay fixed. The changed-line filter
continues to apply even when Evidence Gate is disabled. Context Pack differences
require a target root with guidance; an absent guide is a no-op, not evidence that
context has no effect. This smoke experiment does not provide such a guide.
Provider stochasticity remains; repeat and randomize variant order before quality
claims. Latency is wall time; token usage and dollar cost are null until provider
usage instrumentation is added. Never confuse context token estimates with billed
model token counts.

Recorded real attempt: `docs/evaluation/synthetic-model-run.json`, model
`deepseek-v4-flash`, 16 failed runs, provider HTTP 402 / insufficient balance.
No semantic metrics were computed. `reviewbench-input-smoke.json` records one
real mirror task's parsed diff and benchmark revision without model calls.
The manifest's change-count fields differ from the actual mirror diff; the
adapter uses the frozen base...head diff as the review input.

## Constrained improvement

`improve` receives only dev failure traces, asks a model for a bounded
ReviewStrategy JSON and invokes a caller-owned validation evaluator for current
and candidate configurations. Unknown fields are forbidden. The selection gate
requires no precision/recall regression, strictly higher F1, no failed cases and
latency at most 1.5x baseline. Missing/nonfinite metrics reject the candidate.
It returns an accept/rollback record and selected configuration, never overwrites
production files. A final test evaluation and human review are still required.

The caller must keep the validation evaluator, labels, judge and input dataset
immutable; do not accept arbitrary model-supplied metrics. There is no end-to-end
semantic optimizer result yet. Configuration cannot alter evaluator code, splits,
golden labels or deployment. Add actual provider cost budgets before any claimed
cost-constrained improvement; the current gate controls latency only.

## Research decisions

GEPA (https://arxiv.org/abs/2507.19457) motivates proposing prompt candidates from
failure feedback; this prototype is not an implementation of GEPA's full search.
Reflexion (https://arxiv.org/abs/2303.11366) motivates retained failure narratives;
ADAS (https://github.com/ShengranHu/ADAS) motivates evaluating design candidates.
Unrestricted DGM-style source rewriting is outside this stage. No gains reported
by these papers are treated as expected code-review gains.

## Delivery and resume boundary

After restoring model credit: run frozen external development tasks, independently
adjudicate findings, establish baseline, repeat ablations, freeze validation and
final test sets, then exercise the candidate loop. Add Langfuse afterward.
Keep the draft unmerged until these acceptance conditions are met.

Truthful interim resume wording: “为 AI 代码审查系统实现 ReviewBench 输入输出适配、
缺陷级独立标注计分与可复现消融实验框架；通过失败状态隔离和数据划分校验防止
将模型降级结果或测试集泄漏误报为质量提升。” Do not add numeric accuracy gains.

Independent annotations can be scored with:

```sh
python -m src.evaluation.adjudicate DATASET.json EXPERIMENT.json ANNOTATIONS.json \
  --output reports/evaluation/metrics.json
```

Annotations are an array of `{case_id, variant, reviewer, exhaustive: true,
matches: [[prediction_index, golden_id], ...]}`. An exhaustive empty match list
means all predictions were adjudicated as unmatched. Failed or unadjudicated runs
make a variant's quality metrics null. Do not use partial aggregates to accept a
candidate. The initial model attempt used an uncommitted working tree; its commit
field identifies the base only, which is explicitly recorded in its artifact.
