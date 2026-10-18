# Offline cl100k context-selection comparison — 2026-10-18

This zero-model comparison uses 25 cached ReviewBench PRs and 352 raw TP records. All selectors run before gold is opened. The frozen production source is `86cbacac805b0451a2839b3cc9b07cc968097f78`; prepared PR, diff, and gold hashes match the byte-bound study. The cl100k static asset was verified against the installed tiktoken expected SHA-256 (`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`, 1,681,126 bytes). No model/API requests or target-code execution occurred.

| Arm | All target lines visible | Some | None | Any visible | Dev9 all / any |
|---|---:|---:|---:|---:|---:|
| A0 production builder | 170 | 6 | 127 | 176 | 61 / 64 |
| A1 strict 6,000-token rendered prefix | 170 | 6 | 127 | 176 | 61 / 64 |
| B cross-file complete-hunk round-robin | 179 | 0 | 124 | 179 | 64 / 64 |
| C global rule-risk/declaration priority | 191 | 0 | 112 | 191 | 68 / 68 |
| D_full per-file complete-hunk greedy | 138 | 0 | 165 | 138 | 64 / 64 |
| D_prefix actual builder with reordered parser | 166 | 6 | 131 | 172 | 62 / 66 |

Only 303 of 352 raw findings map uniquely to a head-side diff hunk. For the Dev9 split (87 mapped findings), C versus A1 gains 17 and loses 10 in all-visible status, and gains 15 and loses 11 in any-visible status. D_prefix versus A1 gains 4 and loses 3 all-visible, and gains 4 and loses 2 any-visible. The JSON's `dev_comparison_to_A1` field contains machine-derived per-case counts and full finding ID hashes for each change; outside-diff, ambiguous, and unknown records are excluded from these comparisons.

C's fixed global complete-hunk ordering is `(-flagged, -triage_score, -declaration_signature_regex, path, new_start)`. The frozen production scanner and triage score supply the first two terms: `triage_score = 10*rule_hits + 10*risky + 0.02*churn + 0.5*public_interface`; `flagged` means at least one rule hit or score >= 10. The declaration/signature term is a changed-line regular-expression proxy. C removes the production per-file cap, so it combines ranking and per-file allocation changes; this experiment cannot isolate either factor. Its formula was fixed before gold was read. The byte-bound layer had C three fewer dev any-visible findings than A1, while cl100k has four more, so the result is counter-sensitive and does not support changing defaults.

D_full is a separate complete-hunk greedy selector: preserve production file order and per-file cap, prioritize each file's first source hunk, then try remaining hunks by descending raw-hunk cl100k size with original index as stable tie-break; fit checks enforce the per-file and final rendered 6,000-token budget. It selected 421 hunks and rejected 1,039 candidates for per-file or final-budget fit. It is not a prefix-reorder test.

D_prefix is the controlled actual-builder probe. It temporarily replaces only the frozen `context_builder.parse_file_hunks` return order: first source hunk stays first; remaining hunks sort by descending raw-hunk cl100k count with original index tie-break. The injected function is restored in `finally`. The unchanged `build_review_context` then runs with the same PR, settings, and 6,000 budget, and its rendered patch receives the same strict prefix-fit pass as A1. The probe is hashed in the JSON (`d_prefix_injection_sha256`). D_prefix produces 269 complete, 74 partial, and 1,428 absent source hunk bodies; A1 produces 296/74/1,401. Only 8 of 25 D_prefix contexts are byte-for-byte identical to A1. It does not improve dev coverage enough to justify a production change by itself.

| Arm | Complete source hunk bodies | Partial bodies | Absent bodies | Max patch-section tokens | Empty rendered contexts |
|---|---:|---:|---:|---:|---:|
| A0 | 303 | 73 | 1,395 | 6,337 | 0 |
| A1 | 296 | 74 | 1,401 | 5,998 | 0 |
| B | 382 | 0 | 1,389 | 6,000 | 0 |
| C | 331 | 0 | 1,440 | 5,998 | 0 |
| D_full | 421 | 0 | 1,350 | 5,999 | 0 |
| D_prefix | 269 | 74 | 1,428 | 5,999 | 0 |

The source has 1,771 diff hunks total. B, C, and D_full keep complete hunks only; D_prefix and the A arms may expose partial hunk bodies due prefix truncation. All experimental arms were non-empty for all 25 cases. A0 exceeded 6,000 rendered patch-section tokens in 9/25 cases; A1, B, C, D_full, and D_prefix stayed within the strict limit. The 71 pure-deletion source hunks were not filtered by the selector; D_full retained 20. The 15 previously used holdout PRs are regression data, not unseen validation.

These are first-pass patch-location visibility measurements, not semantic evidence sufficiency, defect discovery, precision/recall, or unseen-PR generalization. Historical A0 context SHA-256 values match the prior 25-case artifact for all 25 cases. The machine-readable artifact binds the dataset, split, frozen source files, tokenizer asset, selector and injection hashes, and per-finding hashes/visibility.

Reproduction uses `scripts/evaluation/offline_context_strategy.py --counter cl100k --cache-dir <verified-cache> --data <eval-data> --source-root <frozen-archive> --source-revision 86cbacac805b0451a2839b3cc9b07cc968097f78 --output <result.json>`. The cache must contain the verified official static cl100k asset; the runner refuses tokenizer fallback. `--counter bytebound` is a separate UTF-8 byte upper-bound layer and must not be compared as exact token counts.
