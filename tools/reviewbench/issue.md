The reviewer has deterministic regression tests but no independent semantic benchmark results. Add an isolated ReviewBench mounted-input adapter and controlled experiments without changing existing entrypoint defaults.

Acceptance criteria:
- Read mounted diff and PR metadata without GitHub API calls or golden labels.
- Emit contract-compatible findings and bind experiments to model, diff and source version.
- Distinguish model/critic failures from successful empty findings.
- Score independently adjudicated logical findings with one-to-one maximum matching.
- Reject dataset leakage and unverified negative samples.
- Compare Evidence Gate, Critic and target-context configurations on frozen inputs.
- Keep candidate optimization restricted to configuration, with precision/recall and latency gates.

Current experiment blocker: configured DeepSeek provider returned HTTP 402 (insufficient balance) on 2026-10-13. The recorded runs are failures, not accuracy results. Official judging, broad golden labeling and improvement effectiveness remain pending.

Follow-up scope: frozen validation/test corpus, independently reviewed labels, Langfuse tracing, repeat runs and confidence intervals. Do not claim quality gains from fixtures or the initial synthetic development set.
