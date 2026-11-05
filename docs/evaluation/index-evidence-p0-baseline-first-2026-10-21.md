# Baseline-first evidence displacement replay (2026-10-21)

This is a same-case, offline host replay of the frozen P0 in `index-evidence-p0-2026-10-21.md`. It uses the original PR 468 snapshot, mechanically frozen `check_staleness` query, request order, and 6000-byte evidence budget. Only the baseline-first merge behavior changed. The prior P0 Markdown and JSON remain intact. No model prompt or provider was invoked; model/API cost is $0.

| Measure | Index off | Index on, baseline-first |
|---|---:|---:|
| Direct search hits retained in order | 3 | Same 3 in the original order |
| New rendered index evidence | 0 | 0 |
| Rendered evidence refs | 1 | 1 |
| Rendered source ranges | `src/gimmes/cli.py` head 257–338 | Same `cli.py` range |
| UTF-8 evidence byte upper bound | 4347 | 4347 |
| Index files parsed / bytes / scan time | — | 3 / 49,765 B / 42.694 ms |
| Gold source ranges covered | 1/10 | 1/10 |

The new on bundle preserves the only off range. Relative to the previous index-on replay, it removes six test/definition ranges: `tests/unit/test_staleness.py` head 17–32, 36–41, 43–55, 57–70, and 72–84; and `src/gimmes/strategy/staleness.py` base 16–20. The P0 adds no rendered range beyond off, so this is a displacement fix with no demonstrated evidence gain. One selected test caller could not fit after baseline evidence consumed the request budget; three additional indexed caller candidates had no free return slot. The request records both omission reasons.

Gold scoring remains post-hoc only and is unchanged at 1/10 in both arms; it does not establish semantic recall or review quality. The parser still reads 49,765 bytes and spends 42.694 ms on this run, so indexing has an additional local cost even when it adds no rendered evidence. This replay validates only the local single-request baseline-preservation rule under identical inputs and limits. It does not prove candidate usefulness or guarantee against eviction by later unrelated requests.

The machine-readable output is `index-evidence-p0-2026-10-21-baseline-first.json`.
