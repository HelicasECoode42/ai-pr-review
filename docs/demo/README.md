# Demo Reports

This directory contains generated sample reports for the demo workflow. They are
kept as documentation artifacts, not source code, and the reviewer intentionally
skips `docs/demo/` when building AI context or scanning source risks.

Current samples:

- `pr-42-ai-demo.md`: AI review mode, showing ReviewMeta, run status,
  completeness, skipped-context files, model suggestions, and rule findings.
- `pr-42-rule-demo.md`: rule-only mode, showing the same report structure when
  the model is disabled or unavailable.

Generated runtime reports should still be written to `reports/`, which is
ignored by git.
