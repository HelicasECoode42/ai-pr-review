# Large PR coverage iteration

Status: offline implementation and fixture checks only. This document does not claim semantic recall, production coverage, or model quality improvement.

## Actual execution path

```mermaid
flowchart TD
  D[ChangedFile patches + frozen base/head Snapshot] --> P[build_review_plan]
  P --> S[ReviewUnit / ChangeSlice]
  S --> C[build_unit_context]
  C --> E[review_with_ai grounded route]
  E --> R[review_grounded]
  R -->|build_user_prompt(context.text)| M[Provider discovery / verification]
  M --> O[GroundedResult]
  O --> K[typed checkpoint artifact + identity]
  O --> H[per-hunk coverage mapping]
  P --> H
  K -->|same run identity + slice hashes| R
  H --> J[ReviewReport metrics + repository_context]
```

`review_with_ai` enters this route only when the existing unit option is enabled, the ordinary patch context is truncated, and the diff has more than two files. The planner creates fragments and bounded units; the runner passes exactly one unit context to `review_grounded`. Discovery and verification prompts use that context's text. The same runner owns the existing per-unit retry, worker cap, cost estimate, checkpoint identity, artifact hash, and cancellation event. The regular one-shot route remains available when unit review is disabled or refused.

## Coverage contract

Plan accounting and model review are separate facts. Each hunk record in `repository_context.unit_details.hunk_coverage` reports:

- `static_scanned`: the planner represented the hunk or recorded an exclusion/unavailable reason.
- `model_exposed`: every planned fragment was present in its exact rendered unit context and a result records at least one discovery call. This is an input visibility claim, not proof of attention.
- `model_review_completed`: every fragment was exposed, the unit returned a non-failed result, and no fragment boundary cut through a diff line.
- `status`: `model_review_completed`, `partial`, `pending_budget`, `excluded`, or `unavailable`. A reason identifies the source, unit cap, cancellation, failure, or fragment boundary.

Every slice retains its exact UTF-8 byte interval within the raw hunk, byte length, SHA-256, source-line span, and `cuts_a_diff_line` flag. Adjacent intervals must join without gaps; their hashes and concatenated text provide an offline reconstruction check. A split line remains `partial` even if every fragment was exposed and the unit requests succeeded. The planner's syntax-level grouping does not prove whole-function or behavioral completeness.

Results from a restored checkpoint count as reviewed only after the run identity (including raw-discovery mode, effective prompt, provider configuration, and applicable policy), unit/slice identity, typed artifact, artifact hash, and schema checks pass. A request that returned a result but failed to persist its checkpoint can be complete for the current report while the overall report remains partial and warns that a later run may repeat the work. Units beyond `unit_review_max_units`, cost-refused work, and cancelled work remain explicitly pending; planning a unit never increments completed review.

### Checkpoint identity hardening (2026-10-27)

Unit checkpoints now bind the declared runtime-file fingerprint as well as raw
discovery mode/scope, the effective system prompts and addendum, provider/critic
configuration, and existing policy and slice identities. Changing the raw or lens
system prompt, or a fingerprinted runtime file, rejects the previous result;
repeating the same configuration still resumes without provider calls. This is
not a claim that the explicit runtime manifest covers every project dependency.

Direct report callers without a supplied engine identity get a fallback trace
that includes raw/index system prompts and the lens/index runtime modules. Its
`prompt_identity_status` is `partial`: the effective addendum cannot be recovered
from the result alone, so the fallback does not claim a complete prompt identity.

## Budget and safe defaults

Unit review stays opt-in through the existing `unit_review_max_units` setting, which defaults to disabled. The existing maximum-unit cap, conservative per-unit cost estimate, attempt limit, worker cap, provider request settings, and checkpoint path remain authoritative. When the total estimated cost exceeds the configured ceiling, the scheduler dispatches only the affordable prefix and marks later units `pending_budget`; when even one estimated unit cannot fit, it refuses before dispatch. The planner's `token_budget` is a per-unit diff-fragment limit; it is not a whole-run HTTP, input, output, time, or invoice cap. The cost estimate cannot guarantee exact provider usage. No setting or publication threshold was widened as part of this iteration.

## Offline evidence

The targeted fixtures cover multiple files, oversized UTF-8 hunks, exact fragment reassembly, unit-cap pending work, cost refusal before runner dispatch, unit failure, cancellation, changed-head checkpoint rejection, and unit checkpoint restore. On the deterministic three-file engine fixture, the planner produced 3 hunks / 27 units; with the existing cap of 8 units, the new accounting recorded 0 fully completed hunks, 1 partial hunk, and 2 pending hunks. The one-shot context at the fixture's 200-token patch cap also had 0 fully included hunks out of 3. This run does **not** show a coverage increase: its total provider input/HTTP/time budget was not matched to the one-shot arm, so it is a state-accounting and failure-path fixture rather than a fair performance comparison.

The separate 2026-10-26 fake-provider snapshot recorded a 24-file / 24-hunk input with 0 fully included hunks, 8 partial hunks, and 16 omitted hunks under one-shot context construction. The new unit accounting has not yet been replayed on that exact fixed input under a matched total HTTP/input/output/time budget. Until that paired replay demonstrates more fully exposed and completed hunks without exceeding the same budget, the coverage gain is unknown. The recorded numbers are structural coverage counts, not true-positive recall.

## Matched offline production-provider replay (2026-10-26)

The fixed `large` fixture was replayed through the production `ReviewApplication` and `OpenAICompatibleProvider.complete_json`; an in-memory `httpx.MockTransport` recorded the exact serialized final messages and returned deterministic empty JSON. This made no model, GitHub, or other network request. Both arms used the same 24-file / 24-hunk patch and frozen snapshot hashes above, the same UTF-8 byte fallback, and the existing shared caps: 16 HTTP attempts, 200,000 UTF-8 input-upper-bound bytes, 60 seconds, 128 output tokens, and 6,000 patch/evidence tokens. Unit review used its current opt-in cap of 8 units, four workers, and the existing 400,000 estimated-token ceiling; that ceiling admitted six units. No provider or review-budget setting was raised.

| Route | Final messages / HTTP attempts | Recorded request-body bytes | Shared input upper bound | Hunks static | Fully exposed | Completed | Remaining |
|---|---:|---:|---:|---:|---:|---:|---:|
| One-shot | 1 | 25,220 B | 25,559 B / 200,000 B | 24 | 0 complete (8 partial) | 0 complete | 24 not fully reviewed |
| Unit opt-in | 6 | 66,006 B | 68,040 B / 200,000 B | 24 | 6 | 6 | 18 pending budget |

The one-shot row uses the exact final production prompt. Its hunk counts were reconstructed with the same `build_review_context` settings used in the production route, and the resulting context text was confirmed as a substring of the captured final user message: zero whole hunks included, eight partial, and sixteen omitted. The one-shot route does not emit the unit route's per-hunk exposure/completion ledger, so “0 complete” means no whole hunk was fully present; partial visibility is reported separately and is not called completed. Unit exposure and completion use the implementation's stricter per-fragment ledger. Both routes stayed under the same caps; unit review consumed five more HTTP attempts and about 42.5 KB more recorded request-body bytes, while completing six hunks. The single local run spent 0.141 s in the one-shot request budget and 0.160 s in the unit route; this is mock-transport process time, not provider latency or a stable performance estimate. This is structural coverage evidence only; the deterministic provider response makes no semantic recall or review-quality claim.

Exact final messages, per-request byte counts and message hashes are preserved in [`docs/evaluation/large-pr-unit-coverage-2026-10-26.json`](evaluation/large-pr-unit-coverage-2026-10-26.json). The targeted offline suite produced 77 passes and one stale assertion in `tests/test_complexity_probe.py::test_unsupported_and_failed_routes_are_reported_as_degraded`: the current critic route now measures as supported and emits one call, while that test still expects the previous unsupported/zero-call behavior.
