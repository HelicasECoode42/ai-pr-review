The current reviewer lacks a reproducible external benchmark entrypoint. This adds a ReviewBench mounted-input adapter, synthetic development fixtures with executable label checks, independently adjudicated finding-level scoring, controlled ablation orchestration and a constrained configuration proposal prototype.

Existing CLI, GitHub Actions, Web and VS Code defaults are preserved. The adapter never supplies golden labels to the reviewer, refuses degraded model/critic results, and avoids loading this project's Context Pack when reviewing another repository.

Validation:
- Full Python regression suite: 122 passed; VS Code extension compiles successfully.
- New evaluation modules pass Ruff checks.
- One real ReviewBench mirror PR parsed successfully (10 changed files), with input hash and benchmark revision recorded.
- Real DeepSeek model experiment attempted: 16/16 calls failed with HTTP 402 due to insufficient balance. No precision, recall or improvement claims.

This draft is deliberately not ready to merge. Semantic evaluation and official ReviewBench judging require usable model credit and independent adjudication. The improvement loop is an offline configuration prototype, not production autonomous code modification. Docker image build is blocked because the local Docker daemon is not running. ReviewBench artifacts and local synthetic cases are explicitly distinguished.

Tracking issue: #44.
