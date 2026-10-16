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

---
id: kb-0003
scope:
  repo: "*"
  languages: [python]
  symbols: ["average", "lookup", "pick"]
kind: contract
statement: "`count`, `index`, `total` and `values` are the numeric/sequence types their signatures imply; the guards are not required to tolerate None, bool or string values."
source: "gold case safe-division: the falsy-guard narrowing only differs for count=None, which the interface excludes"
domain: {count: int, index: int, total: int, values: sequence}
version: 2
status: candidate
---

A witness built from an input outside this example's declared domain shows that
the guard handles a case the fixture caller cannot produce. This entry was
derived from a synthetic benchmark case, not a repository API contract; its
domain must not constrain production candidates by matching function or variable
names. Keep it a candidate until replaced by a contract tied to source evidence
or an explicit repository rule.

---
id: kb-0004
scope:
  repo: "*"
  languages: [python, typescript, javascript, kotlin]
kind: contract
statement: "When a PR description promises a mechanism (a shared executor, a lock, a bounds check) the implementation must actually use it; a description that does not match the code is itself the defect."
source: "ReviewBench gold ardevd_jadx-collaboration_3: description promises one shared executor, code spawns a fresh thread per entry point"
intent_symptoms: [contract_mismatch, concurrency_not_serialised]
version: 1
status: authoritative
---

Compare the stated intent with every entry point that can trigger the path, not
just the one the diff touches. A partial migration leaves the original problem in
place while looking fixed.

---
id: kb-0005
scope:
  repo: "*"
  languages: [python, typescript, javascript, kotlin]
kind: retrieval_hint
statement: "For a concurrency or lifetime change, enumerate every entry point of the touched component and check whether each one shares the new mechanism or still creates its own."
source: "ReviewBench gold ardevd_jadx-collaboration_3 and mheuss_chronicle_14"
intent_symptoms: [needs_context_evidence_exhausted, concurrency_not_serialised]
version: 1
status: authoritative
---

The four entry points are usually a menu item, a key binding, a toolbar action and
a programmatic call. Reading only the changed function misses the other three.

---
id: kb-0006
scope:
  repo: "*"
  languages: [python, typescript, javascript]
kind: false_positive_pattern
statement: "Rejecting oversized patterns or allowlisting regex flags is hardening, not a defect, even though the diff changes validation logic."
source: "ReviewBench gold PierreJanineh_TechDebtMCP_135 (ReDoS guard) - the change is the fix"
intent_symptoms: [hardening_read_as_regression]
version: 1
status: authoritative
---

The PR that introduces a limit while also enforcing it is the remediation for the
class it mentions. Do not report the fix as the bug.
