#!/usr/bin/env bash
# Install the commit gate. pre-commit is already a dev dependency; this wires it
# into .git/hooks so a failing suite cannot become a commit.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pre_commit install
echo "hooks installed; verify with: .venv/bin/python -m pre_commit run --all-files"
