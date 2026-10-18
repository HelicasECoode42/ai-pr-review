# Constrained review-strategy improvement

This is a deterministic, offline candidate lifecycle. It does not claim autonomous
model reflection. Candidate changes are limited to fixed prompt addenda, including
`cross_file_contract_check_v1` and the host-diagnosed
`bounded_cross_file_evidence_v1` (at most 1,200 characters and an approximate
400-token upper bound). `playbook_recall_k` is fixed at 5 until replay can prove a
retrieval change takes effect. Arbitrary prompt strings, code changes, tool
permissions, and scoring changes are rejected.

The runtime policy loader is `src/application/strategy_policy.py`; it only reads a
store explicitly configured by the application and otherwise uses the baseline.
Accepted immutable versions live under `versions/`, with `active.json` pointing to
one version and its content hash. This detects accidental content changes; it is
not a signature or protection against a user who can rewrite the store. Rollback
can target only `baseline` or a version already present in that store.

## Local lifecycle

Use `scripts/review_improvement.py manifest` to create a no-gold manifest from
persisted `prepared.json` inputs. Use `--split dev` for candidate development and
only ready, fixed metadata/diff files. The command verifies hashes and enforces the
checked-in `datasets/dev/split.json` case assignment, including its SHA, as well as
one case per repository group. The registry pins the trusted prepared file itself;
recomputing only a manifest hash cannot authorize different case groups or SHAs.
Candidate diagnoses are structured failure tags, not free-form prose; each must
bind to a successful run artifact and persisted candidate/audit files. Gold labels,
evaluator conclusions, and held-out answers are never loaded into candidate
generation.

The paired runner's grounded arm can receive that exact manifest with
`--trace-manifest`. The run writer stores a hashed `review_trace.json` sidecar and
links it from the run record. The ordinary mounted-input corpus runner has no
frozen repository snapshot and rejects trace manifests instead of silently
producing an ungrounded trace. The offline `diagnose-traces` and `cycle` commands
consume the grounded paired artifacts. The only automatic diagnosis currently supported is a host-recorded
cross-file task omitted by a known hypothesis because the task or evidence budget
was reached; it does not count as a semantic false negative. Semantic failures
still require independent human adjudication tied to evidence at the frozen head.
`cycle` creates only a bounded prompt candidate and reports `evaluation_required`
until an exact paired replay and independent gate reports are supplied. It never
calls a provider or promotes a policy. If the trace or identity is missing or
unknown, the cycle stays blocked. `cycle-fixture` is test-only.

`candidate` creates a schema-validated candidate record. `replay_candidate` uses
the existing `reviewbench.run_review` engine for paired baseline/candidate calls
on identical inputs and protocol. The Python API takes an explicit provider
factory. The `replay-fixture` command is offline. A separate `replay-live` command
exists only behind the required `--allow-model-requests` flag; it loads configured
provider settings only on that explicit path and reuses the configured
`OpenAICompatibleProvider` and rejects a model mismatch. It requires an explicit
protocol fingerprint, current source fingerprint, one fixed PR, per-arm limit of
two HTTP attempts, an experiment limit of four attempts, a 12,000-byte request
send guard, 4,096 max output tokens, and a bounded timeout. The runner passes the
existing shared `ReviewRequestBudget` to each provider and verifies the provider
attached that exact budget object and output cap. Providers are closed even on
replay failure. No live command was run as part of these changes.

For `holdout`/`test`, provide a named non-exposure and repository-split attestation.
Case and repository scopes are recorded as consumed before the first provider is
created; even a failed replay consumes them, and overlapping subsets are rejected.
This is a single local store boundary: deleting or copying the store can defeat the
guard, so it is not a cross-host lock. The previously exposed 15-PR set is
regression material, not an independent holdout. The checked-in test split is
currently unauthorized, so live replay and production promotion on that split
fail closed until a genuinely fresh test set is authorized.

`gate` requires reports bound to the exact evaluation id, replay file hash, input
manifest hash, and protocol fingerprint. Semantic adjudication must be complete,
independent, and explicitly not based on raw file/line matching. P/R/F1 are
recomputed from supplied TP/FP/FN counts. Coverage must include measured numerator
and denominator, and cost must include provider-measured request/token usage and
USD under the same model/budget/retry policy. Missing values, mismatched identities,
failed/partial replay, lower recall, precision below 0.8, non-improving F1, lower
coverage, or higher/unknown cost reject the candidate. Evidence references and
adjudicator names provide a human audit trail, not identity authentication.
Fixture-provider replays are regression-only and the CLI gate refuses to promote
them; the API exposes an explicit test-only override for lifecycle tests.

Only a passing gate makes a candidate eligible. A human must then run `promote`;
production still loads the version only on its next review. Gate eligibility is not
automatic promotion. No scorer or golden dataset is modified by this lifecycle.
