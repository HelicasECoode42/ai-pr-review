# Offline first-pass context and rule-signal audit — 2026-10-18

## Findings

This zero-model run covered all 25 locally prepared cases and 352 raw TP records. It used the production `build_review_context` and `scan_signals` entry points from revision `86cbacac805b0451a2839b3cc9b07cc968097f78`. Gold was opened only after all four selector arms had generated and hashed their contexts. No model, network, GitHub API, or target-repository code was used.

Gold target-line visibility is a mechanical path-and-line comparison, not semantic evidence sufficiency, defect discovery, precision, or recall. Of 352 raw records, 303 map to one head-side diff hunk; 37 are outside the cached diff, 3 overlap multiple hunks, and 9 have unknown line mapping. These 49 are kept separate from visible and missed findings.

| Arm | All mapped target lines visible | Some visible | None visible | Any visible | Patch-section byte upper bound |
|---|---:|---:|---:|---:|---:|
| A0 production builder | 77 | 6 | 220 | 83 | 11/25 cases exceed 6,000; max 6,725 |
| A1 original rendered prefix, hard fit | 75 | 7 | 221 | 82 | 0 exceed; max 5,994 |
| B round-robin complete hunks | 86 | 0 | 217 | 86 | 0 exceed; max 5,955 |
| C global risk/declaration priority | 85 | 0 | 218 | 85 | 0 exceed; max 5,995 |

The counter is an explicit UTF-8 byte upper bound, not model tokens. The prior cl100k-based visibility result (170 fully visible) is not comparable because this run uses the byte-bound fallback. A0 remains a production-path reference; its rendered patch section can exceed the stated budget because the builder accounts for raw file text, while the final rendering also contains per-file headings and code fences. A1/B/C are the same-budget comparison.

| Split from `datasets/dev/split.json` | Cases / raw TP | A0 all/some/none | A1 all/some/none | B all/some/none | C all/some/none |
|---|---:|---:|---:|---:|---:|
| Dev | 9 / 117 | 28 / 3 / 56 | 27 / 4 / 56 | 30 / 0 / 57 | 28 / 0 / 59 |
| Previously used holdout, regression only | 15 / 224 | 45 / 1 / 160 | 45 / 1 / 160 | 49 / 0 / 157 | 50 / 0 / 156 |
| Excluded node-postgres case | 1 / 11 | 4 / 2 / 4 | 3 / 2 / 5 | 7 / 0 / 3 | 7 / 0 / 3 |

On the 9 development cases, B has 30 findings with all target lines visible but 30 with any target line visible, versus 31 for A1. C has 28 any-visible. Thus, complete-hunk visibility rises while some partly visible locations disappear; neither strategy improves the development set's any-visible count. The 15 prior holdout cases are contaminated regression data, not unseen evidence. These results do not justify changing production selection defaults.

## Rule-signal overlap with A0 visibility

The production offline rule/AST scan received 735 changed-file entries, all with cached patches; no configured path skips or scanner warnings occurred. Forty Python files were eligible for the AST scanner. Five gold locations refer to a path absent from the cached diff, so those target files were not scanner inputs. A scanner receiving a file does not mean a rule matched it. The scan intentionally disabled cross-file analysis and linters; it reports only the offline path, added-line, and AST rules.

Among the 303 findings mapped to a single head-side hunk:

| Signal class at the gold location | Findings | All lines visible | Some visible | No lines visible |
|---|---:|---:|---:|---:|
| No matching signal | 268 | 70 | 6 | 192 |
| File-only signal | 30 | 7 | 0 | 23 |
| Line signal only | 2 | 0 | 0 | 2 |
| Line and file signal | 3 | 0 | 0 | 3 |

Therefore 28 of the 220 mapped findings with no A0 target lines visible had a same-file or same-range rule signal: 23 file-only and 5 line-anchored, with three line-anchored findings also having a file signal. These are location hints only. They are not confirmed defect matches and do not show that rules discovered or would report the gold issue. One additional line-anchored signal is on an ambiguous mapping, and one file signal is on a gold range outside the changed hunk. The JSON records signal IDs, source line anchors, and input eligibility without copying rule evidence text.

A0's 220 mapped no-visible findings have builder ledger reasons: 184 `patch_clipped`, 28 `patch_budget_exhausted`, 7 `documentation_only_change`, and 1 `lockfile`. The reason labels describe builder coverage state; they do not establish semantic cause for a missed review.

## Frozen protocol and artifacts

- Arms were fixed before evaluating gold: A0 is the actual builder with 6,000 byte-bound budget, no context pack or remote fetch; A1 keeps the original rendered patch prefix and fits it at line boundaries; B cycles original file/hunk order and keeps complete hunks that fit; C globally orders complete hunks using existing `scan_signals`/`triage` risk, then a static declaration/signature tie-break, and removes the per-file cap. C is a combined treatment, not a single-factor test.
- All four arms used the same cached `pr.json` and `diff.patch` for each case. The script verifies input hashes, records per-case context hashes, runs all selectors before opening gold, then evaluates raw TP records without same-position deduplication.
- The split manifest is read and hashed from `datasets/dev/split.json`; its 9/15/1 membership must be disjoint and exactly cover the 25 inputs. Per-arm visibility totals are asserted to equal raw gold denominators.
- B and C verify exactly one `## Patches` marker, at least one selected file and hunk in every case, and a rendered patch section at or below 6,000 byte-bound units. A1/B/C selected 212/169/139 rendered hunk bodies respectively; B/C skipped 1,291/1,321 complete-hunk candidates that did not fit, then continued considering later candidates.
- One frozen-file fallback diagnostic reported unavailable contents for `services/copilot-review-mcp/go.sum`; remote fetching was disabled. Scanner warnings were zero. The remaining per-case status, hashes, coverage, input eligibility, and location-signal anchors are in the machine-readable artifact.
- The production modules were read from a local `git archive` of revision `86cbacac805b0451a2839b3cc9b07cc968097f78`, so concurrent worktree edits did not enter this run. Reproduction command: `./.venv/bin/python scripts/evaluation/offline_context_strategy.py --source-root <archive-of-86cbaca> --source-revision 86cbacac805b0451a2839b3cc9b07cc968097f78 --data ../eval-data --output docs/evaluation/offline-context-strategy-2026-10-18.json`.
- The exact runtime source-file SHA-256 values, evaluation-script SHA-256, prepared-input manifest hash, split hash, and per-case PR/diff/gold/context hashes are recorded in the JSON.

This evaluates only first-pass patch text. It does not measure the complete prompt, subsequent Agent retrieval, routing, semantic confirmation, publication, or model behavior. The cached holdout has already been used and cannot support a generalization claim.
