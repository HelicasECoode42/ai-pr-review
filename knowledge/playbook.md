---
id: kb-0001
scope:
  repo: "*"
  languages: [python]
  symbols: ["*"]
kind: false_positive_pattern
statement: "Rewriting `0 <= i < n` into `i < 0 or i >= n` with swapped branches is an equivalence, not a defect. A witness evaluation finds no input that distinguishes them."
source: "gold case safe-index; witness_eval over 32 assignments"
intent_symptoms: [equivalent_rewrite_confirmed]
version: 1
status: authoritative
---

The two guards reject exactly the same inputs (De Morgan), and swapping which
branch returns the value does not change the result. Claims of the form "the
guard change widens the set of inputs that reach the subscript" must show a
concrete input; none exists.
---
id: kb-0002
scope:
  repo: "*"
  languages: [python]
kind: retrieval_hint
statement: "When a candidate rests on how a changed symbol is used, request the caller or the interface contract instead of re-reading the definition already supplied."
source: "measured: the real run's evidence budget was 96% spent while all five candidates still needed context"
intent_symptoms: [needs_context_evidence_exhausted]
version: 1
status: authoritative
---

In the measured run the discovery round requested nothing at all, so every
candidate reached verification without caller evidence.
