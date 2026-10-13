The reviewer had no complete external evaluation input path, and confirmed rule candidates bypassed the evidence/Critic checks applied to independent findings. Two-stage review could also present failed hotspot calls as empty successful results.

This draft integrates all 25 official ReviewBench task inputs and its original local judging CLI, then fixes those verification and coverage contracts. Rule conclusions stay isolated from primary review. Confirmed signal candidates now require their own evidence/failure scenario and pass the shared gates. Triage sees actual diff context, empty triage falls back to general review, and deep review returns explicit partial/failed stage records. Critic context centres on the candidate hunk.

Evaluation handling preserves renames, mode/empty-file changes and binary metadata, captures actual usage and served model names, separates audit JSON from candidate JSON, rejects stale output directories, and stops on fatal provider errors. Streaming retries preserve earlier successful outputs and impose explicit time/token budgets.

Validation:
- All 25 official task mirrors fetched and diff inputs parsed; source benchmark revision and per-input hashes recorded.
- 135 Python tests passed; new evaluation modules pass Ruff; VS Code extension compiles.
- Real DeepSeek requests resumed after credit restoration; all 25 official PRs now have successful candidate output, with original failures and changed retry budgets retained. Full local judging and fixed-suggestion Critic ablation are complete.
- Original ReviewBench matcher/classifier/scorer completed all 25 PRs, with 0 scoring errors. Baseline: 12 suggestions, micro augmented precision 91.67%, grounded recall 3.98%. Critic: 10 suggestions, precision 90.00%, recall 3.69%. Critic removed two baseline TPs and retained the FP. Fixed baseline labels give Critic recall 3.41%; fresh judging changed one retained match. This is diagnostic evidence, not a general quality gain.
- Versioned artifacts and limitations: docs/evaluation/reviewbench-2026-10-13/REPORT.md.

Scope and limitations:
- Still a draft, not ready to merge. Further Gate/context/two-stage ablations, independent label audit and broader repository context remain required.
- Successful outputs from earlier transports are retained with their original dirty-tree source hashes and audit provenance; this is an engineering baseline, not a clean-commit effectiveness comparison.
- Judge currently uses the same DeepSeek model family as the reviewer; local LLM judging does not substitute for independent human review or official leaderboard scoring.
- Design/contract review schema expansion and autonomous improvement remain deferred. The earlier constrained configuration prototype is not enabled in production.
- Docker build remains unverified because the local daemon is not running.

Tracking: #44; existing semantic-quality work: #40 and #41. Research and development standard: docs/review-research-and-development-standard.md.

CI dependency maintenance: update vsce to 4.0.0, build with Node 22, and refresh the lockfile. Local npm audit reports 0 vulnerabilities; compile and actual VSIX packaging pass.
