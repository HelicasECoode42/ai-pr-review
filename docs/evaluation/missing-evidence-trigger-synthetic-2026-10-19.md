# Missing-evidence synthetic trigger check

Purpose: observe whether the revised grounded prompts notice when a changed caller's helper contract is decision-relevant and absent from the initial diff, then whether the existing verifier/host path can request and expose it. This is a **synthetic mechanism-trigger observation**. It does not estimate real-PR quality, precision/recall, or generalization.

## Frozen inputs and runner

The two `tests/fixtures/missing_evidence_trigger/` cases use the existing `reviewbench.run_review` inputs (`pr.json` + unified `diff.patch`) and the production grounded review/provider path. The frozen head source files are fixture state for a `Snapshot`-protocol object supplied to `run_review`; they are not copied into the initial prompt unless the model requests them. No gold labels, target line hints, expected symbol/path, or prompt instruction to request evidence are included in model input. Keep the normal discovery/verification rules and `grounded_missing_evidence_followup=True`.

Run both cases, sequentially, treatment only. Save raw request/response artifacts, the report's repository context/intent trace, usage records, and a copy of each exact request body. Freeze the two fixture files, current source identity, and `DISCOVERY_SYSTEM + VERIFY_SYSTEM` prompt hash immediately before the call. Abort if any hash differs during the run. Do not edit PromptGold or the fixture after freezing.

## Expected observations and scoring

Score these four dimensions independently for each case; do not collapse them into a finding-quality score.

1. **Discovery:** the positive case receives credit if discovery either (a) declares a source-grounded, decision-relevant `missing_evidence` obligation and requests matching context, or (b) directly makes a valid hypothesis-bound `context_request`. Merely saying “more context” does not count. The negative case receives credit when the helper contract already visible in the diff is used without an unnecessary missing-evidence request.
2. **Verification:** when verification returns `needs_context` with a still-relevant declared obligation, credit a request only if `missing_evidence_requests` names the existing `hypothesis_id` and `missing_id`, has a concrete purpose, and uses a schema-valid `read` or `search` query. If discovery's evidence already resolves the question, this stage is not applicable, not a failure.
3. **Host read:** mechanically check the trace task status, safe query, evidence refs, and that the corresponding source range appears in the next verifier request. A model request without host execution/visible evidence does not count as success.
4. **Safe termination:** confirm the run ends within the configured call, byte, output-token, and wall-clock caps; repeated/unbound requests do not cause extra reads; evidence remains marked unknown/pending when a cap blocks completion. A cap stop is reported as an incomplete mechanism observation, never as a no-defect result.

## Hard budget

Use one model and one prompt/source version for both cases. No control arm, retry loop, judge, or third case. Give **each case** its own existing `ReviewRequestBudget`: at most **4 HTTP attempts**, **200,000 request-input UTF-8 upper-bound bytes**, and **300 seconds**; set each provider instance to **3,072 maximum output tokens** and disable external retries. These per-case budgets cap the pair at 8 attempts, 400,000 input bytes, 24,576 output tokens, and 600 seconds. The byte guard is the production budget's JSON-encoded prompt byte estimate plus its 512-byte envelope per request; use it as a conservative input-token upper bound, not as reported token usage. At conservative DeepSeek cache-miss prices of ¥2/M input and ¥8/M output, worst-case usage cost is `(400,000×¥2 + 24,576×¥8)/1,000,000 = ¥0.996608` for the pair. This excludes taxes/provider-side accounting differences and must be checked against the current price and available balance before any call. Actual usage and cost must be reported from provider usage; unknown usage is not zero. A single 400 response-format fallback consumes another HTTP attempt under that case's cap.

This is below ¥1 on the stated price schedule, with less than one cent of headroom; any price change or cap increase requires a new calculation. The previous ¥3 authorization covered the earlier A/B and does not authorize this run. Obtain fresh user authorization before any model/API call.

## Mechanical preflight

Offline only: parse both unified diffs through `reviewbench.parse_diff`; verify the changed path/added-line metadata; verify the positive helper is absent from its diff but present in frozen head source; verify the negative helper definition is present in its diff; verify no file contains gold labels or a prompt directive about requesting context; compute and record fixture SHA-256 values. Current reviewer identity at preparation: commit `ba4eee9f1023f907ef9040771a183f89e69bb613`, dirty working tree, Python source SHA-256 `92be2edaf8f558afda266c755017a760b4a5927e887d9797ae132e0af857301b`; prompt SHA-256 `836f477cfd8f4520d289b4f089dcebc65a708a337f50a4cba6de5f4e14f48506`. Both must be rechecked and frozen immediately before the run. Do not run a provider or call an API during this preparation.
