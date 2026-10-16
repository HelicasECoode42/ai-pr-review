Add an independent official ReviewBench evaluation path and improve verification/coverage contracts without replacing existing GitHub Actions, Web or VS Code entrypoints.

Progress:
- [x] Prepare all 25 official test PR snapshots and record benchmark revision/input hashes.
- [x] Connect the original ReviewBench local judging CLI; one real PR smoke scored.
- [x] Preserve binary/rename/empty-file metadata and explicit coverage omissions.
- [x] Apply shared evidence/Critic checks to confirmed rule candidates.
- [x] Distinguish partial/failed hotspot calls from successful empty findings.
- [x] Capture actual model usage and served model identity; bound retries and stop fatal failures.
- [x] Finish all 25-PR generation and original ReviewBench local judging (0 scoring errors).
- [x] Fixed-output Critic ablation: drops two baseline TPs without removing FP; full results versioned.
- [ ] Controlled Gate/Critic/context ablations and independent label audit (continue #40/#41).
- [ ] Freeze held-out data and validate candidate optimization effectiveness.

Model credit has been restored. All 25 real DeepSeek task outputs have succeeded; local judging and Critic replay have completed; timeout/connection failures are preserved and retried without rerunning successful tasks. No precision/recall improvements have been claimed. Draft implementation: #45.

Baseline micro augmented precision 91.67%, grounded recall 3.98%; Critic 90.00% / 3.69%. Fixed-label Critic recall 3.41%. This is a mixed-budget engineering pilot, not a held-out quality claim. Primary next work is bounded repository context and needs_context handling.
