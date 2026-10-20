# Grounded missing-evidence trigger fix

Date: 2026-10-19. This records a prompt-contract repair after the paired run in [the budgeted A/B report](grounded-missing-evidence-ablation-budget3-v2-result.md).

## Finding and change

The A/B report showed that treatment B produced no `intent_hypotheses[].missing_evidence` obligation or context request during discovery. Verification later returned `needs_context`, so the host had no declared obligation it could execute. The host must not infer a path or invent an obligation from that generic verdict.

Discovery already allowed up to four explicit `missing_evidence` obligations, and verification already had a host path for bound `missing_evidence_requests`. The verification prompt's prose described the field, but its final response shape omitted it and did not explicitly connect it to `needs_context`. The prompt now requires the field when an existing obligation remains decision-relevant, includes it in the response shape, and forbids inventing an obligation or path. Duplicate requests for the same `(hypothesis_id, missing_id)` are rejected with a trace task status. A request returned in the final verification slot is recorded as `not_executed_followup_limit`; the corresponding obligation reports `followup_limit_rejected`.

Current prompt hash, calculated as SHA-256 of `DISCOVERY_SYSTEM + VERIFY_SYSTEM`: `836f477cfd8f4520d289b4f089dcebc65a708a337f50a4cba6de5f4e14f48506`. This changes prompt identity for future runs. The prior A/B used the previous prompt and is not a result for this version.

## Offline validation and limits

The focused command `.venv/bin/python -m pytest -q tests/test_grounded_review.py` passed: **65 passed**. `git diff --check` passed. Coverage includes a `needs_context` response with a declared obligation that reads and appears in the next verifier prompt; no declared obligation does not trigger a read; empty, invalid and duplicate requests do not issue the requested read; evidence-budget refusal and request overflow remain traced; and a request in the last verification slot records the single-followup limit. Existing tests also cover valid binding, reuse, and the four-task cap.

These fake-provider tests prove host-side execution and trace behavior only. The host mechanically checks hypothesis and missing-ID binding and safe read/search parameters; it does not verify that an obligation is semantically tied to a particular candidate. The prompt asks the model to request evidence only when an existing obligation keeps a `needs_context` verdict unresolved. The tests do not prove that a real model will declare an obligation, request the right source, or improve review quality. The paired A/B remains a no-trigger observation: both arms had zero findings against denominator 14, and it cannot establish mechanism benefit or harm.

The lowest-cost next real check is one treatment-only run on the same already-exposed development case, with the existing HTTP/token/byte caps and no additional model round. Inspect the saved discovery and verification payloads to see whether the revised contract produces a valid declared obligation and whether the existing verifier slot consumes it. Because the case is exposed and there is no control arm, report it only as a trigger check, not as quality or generalization evidence. No model/API call was made for this fix.
