# Offline ReviewBench context coverage: 2026-10-18

## Result

The baseline `5025c559bb67aebf7214ef87c5e987a3f96c2c35` and current `c7f07f8a2da45fd9b1a6692f7eb39555c4a8ad6f` produced byte-identical `build_review_context` text for all 25 cached PRs. The six source files that govern this builder/parser path have identical SHA-256 fingerprints at both revisions. The measured selection change is **zero** for this pair.

Across those cached inputs the builder processed 735 changed-file entries and 1,771 source diff hunks. In its final rendered context, 112 files were included, 74 partial, and 549 excluded; 302 hunks were fully present, 74 were partial, and 1,395 had no corresponding hunk body in the rendered context. These are first-pass context visibility counts, not review findings.

The evaluator loaded 352 raw gold TP records after producing and hashing each context. Each raw record remains in the denominator; no findings were dropped. The informational `(file, start, end, category)` grouping yields 347 groups with five additional co-located records. Independent review confirmed those five records have different descriptions, so this grouping is not deduplication. A stricter content fingerprint found no exact duplicate records. Gold mapping against the rendered new-side diff lines gives:

| Mapping outcome | Findings |
|---|---:|
| All annotated target lines visible in the first-pass context | 170 |
| Some annotated target lines visible | 6 |
| No annotated target lines visible | 127 |
| File/range outside the cached diff | 37 |
| Range overlaps multiple hunks | 3 |
| Unknown: annotated interval not represented by one hunk's parsed new-side lines | 9 |
| **Raw TP records** | **352** |

“Target lines visible” is a mechanical source-to-rendered-context comparison. It does not assert that the target lines are semantically sufficient evidence, that the review would identify the defect, or that a model would report it. Outside-diff, ambiguous, and unknown rows are not treated as misses or false negatives.

## Fixed inputs and procedure

- Dataset scope: the existing cache contains 219 gold files, but only 25 have ready, frozen PR/diff inputs and were included here. The remaining 194 lack paired local frozen PR/diff inputs and were excluded. All nine PRs in the local mirror with both fixed base and head commits are among the 25 included cases. The prepared cache declares benchmark commit `968291d99e82b3e2969d7e0b4acca62585886c13`; its manifest SHA-256 is `f7c66ed67481e3c657c270ca119d48ed6d13d30489079672aac113cf8b84c1b5`. The aggregate gold-file bundle hash is `b0623e08f3390859574d14e51b1268f3760faf66fdd858f01d222dd98db197bc`. Gold text was not passed to the builder.
- Same case IDs, `pr.json` hashes, and `diff.patch` hashes were compared between arms (25/25 identical). Per-case hashes are in the JSON artifact.
- Both revisions used the same settings: `max_patch_tokens=6000`, `include_context_pack=false`, `prioritize_rule_files=false`, `allow_remote_file_fetch=false`. Actual `context.text` was hashed and compared. The contexts contain no model output.
- Builder runs used the repository virtualenv and temporary source trees extracted from the baseline commit/current checkout. No provider, network, GitHub API, or target PR code was invoked. The per-case builder elapsed time was about one second total per arm; no errors occurred.
- The gold evaluator matched exact normalized file paths and the annotated new-side line interval to diff hunk(s). It compared each target line parsed from the source diff with the same line in the final rendered context. If the range did not fit wholly inside one source hunk, the finding was marked unknown. The JSON includes every raw TP record with a stable evaluator-only SHA-256 ID, case/file/range/category, hunk reference, source/visible hunk hashes, per-line hashes and exact-visibility flags, and the context hash. No gold descriptions or source-code bodies are included.
- Source fingerprint: baseline and current share `e7c9cc5cfa5ebd1a984049783d7b55427182c5cd07cbcc711631cb703513d281`. Individual source file hashes are listed in the JSON. Both arms succeeded on 25/25 inputs; error lists are empty.

## Per-case counts

File columns are `total / included / partial / excluded`; hunk columns are `source total / complete in final context / partial in final context / omitted`. Gold columns are `raw TP / all target lines visible / some visible / none visible / outside diff / ambiguous / unknown`.

| Case | Files total / included / partial / excluded | Hunks total / complete / partial / omitted | Raw TP | Gold all / some / none / outside / ambiguous / unknown |
|---|---:|---:|---:|---:|
| `PierreJanineh_TechDebtMCP_135-16a54f0f` | 10 / 4 / 3 / 3 | 19 / 12 / 3 / 4 | 9 | 5 / 1 / 1 / 2 / 0 / 0 |
| `simnaut_emdash_2-41d75006` | 408 / 11 / 4 / 393 | 1013 / 22 / 4 / 987 | 6 | 0 / 0 / 6 / 0 / 0 / 0 |
| `konturio_disaster-ninja-fe_1230-d169e9da` | 2 / 2 / 0 / 0 | 3 / 3 / 0 / 0 | 13 | 13 / 0 / 0 / 0 / 0 / 0 |
| `cmik_apilix_12-0c34439a` | 22 / 0 / 8 / 14 | 40 / 4 / 8 / 28 | 36 | 8 / 2 / 25 / 0 / 0 / 1 |
| `AA-Factory_aafactory-prototype_17-a1978cb7` | 1 / 1 / 0 / 0 | 1 / 1 / 0 / 0 | 9 | 9 / 0 / 0 / 0 / 0 / 0 |
| `DisciplinedSoftware_Codebase-Conversion_1-4b46d68c` | 23 / 0 / 8 / 15 | 23 / 0 / 8 / 15 | 31 | 2 / 0 / 28 / 1 / 0 / 0 |
| `brianc_node-postgres_3650-b922f0dd` | 6 / 4 / 2 / 0 | 11 / 9 / 2 / 0 | 11 | 10 / 0 / 0 / 1 / 0 / 0 |
| `PaulStSmith_figlet-comment-generator_32-1d0b6642` | 21 / 15 / 2 / 4 | 47 / 37 / 2 / 8 | 9 | 6 / 0 / 2 / 0 / 1 / 0 |
| `brexhq_CrabTrap_15-5ef0884c` | 6 / 4 / 2 / 0 | 26 / 20 / 2 / 4 | 16 | 12 / 0 / 0 / 3 / 1 / 0 |
| `k1LoW_gh-copilot-review_9-f9828193` | 2 / 2 / 0 / 0 | 3 / 3 / 0 / 0 | 7 | 7 / 0 / 0 / 0 / 0 / 0 |
| `scotthamilton77_agents-config_1-1fc78970` | 38 / 6 / 2 / 30 | 18 / 7 / 2 / 9 | 25 | 3 / 1 / 5 / 15 / 0 / 1 |
| `ardevd_jadx-collaboration_3-853179dc` | 3 / 3 / 0 / 0 | 10 / 10 / 0 / 0 | 9 | 7 / 0 / 0 / 2 / 0 / 0 |
| `AY2526S2-CS2103T-W14-3_tp_152-e0a9ce03` | 19 / 12 / 3 / 4 | 51 / 39 / 3 / 9 | 14 | 9 / 0 / 2 / 3 / 0 / 0 |
| `vanyastaff_nebula_224-d5ba86dd` | 69 / 1 / 8 / 60 | 188 / 2 / 8 / 178 | 9 | 0 / 0 / 5 / 2 / 0 / 2 |
| `NCATSTranslator_translator-ingests_336-efb31e9c` | 2 / 1 / 1 / 0 | 22 / 10 / 1 / 11 | 17 | 8 / 0 / 7 / 1 / 0 / 1 |
| `mheuss_chronicle_14-04ac4c02` | 14 / 5 / 7 / 2 | 57 / 23 / 7 / 27 | 10 | 4 / 0 / 5 / 0 / 1 / 0 |
| `superhighfives_pika_200-b227aff0` | 16 / 8 / 5 / 3 | 46 / 32 / 5 / 9 | 19 | 8 / 0 / 9 / 1 / 0 / 1 |
| `gagneurlab_PROTRIDER_10-b462b109` | 22 / 6 / 6 / 10 | 108 / 15 / 6 / 87 | 21 | 7 / 1 / 12 / 1 / 0 / 0 |
| `allan-mobley-jr_gimmes_468-e1477f8b` | 4 / 3 / 1 / 0 | 12 / 9 / 1 / 2 | 6 | 4 / 0 / 0 / 2 / 0 / 0 |
| `superyyrrzz_ForgeMap_106-2f3977ed` | 2 / 1 / 1 / 0 | 2 / 1 / 1 / 0 | 15 | 14 / 0 / 1 / 0 / 0 / 0 |
| `renedierking_LANdalf_88-bc1698f4` | 2 / 2 / 0 / 0 | 4 / 4 / 0 / 0 | 6 | 4 / 0 / 0 / 1 / 0 / 1 |
| `scottlz0310_Mcp-Docker_29-e7705d26` | 8 / 4 / 3 / 1 | 7 / 4 / 3 / 0 | 22 | 11 / 0 / 11 / 0 / 0 / 0 |
| `wirechat_wirechat_183-6e3156e7` | 20 / 8 / 5 / 7 | 36 / 20 / 5 / 11 | 12 | 5 / 0 / 5 / 1 / 0 / 1 |
| `sdsykes_fastimage_165-8fc7cd32` | 12 / 6 / 3 / 3 | 19 / 10 / 3 / 6 | 14 | 9 / 1 / 3 / 1 / 0 / 0 |
| `Marvell-Consulting_statswales-frontend_626-30ba27a5` | 3 / 3 / 0 / 0 | 5 / 5 / 0 / 0 | 6 | 5 / 0 / 0 / 0 / 0 / 1 |

## Scope and limitations

This measures only the first-pass patch context returned by `build_review_context`. It is not the complete system/user prompt, does not include repository evidence packs or subsequent Agent retrieval, and cannot measure routing, semantic confirmation, publication, precision, or recall. The 25 PRs are previously used cached cases, not an unseen holdout. This is an offline context visibility diagnostic and a zero-delta code comparison, not evidence of quality improvement or generalization.

Machine-readable per-case and per-finding evidence: [offline-context-eval-2026-10-18.json](offline-context-eval-2026-10-18.json).
