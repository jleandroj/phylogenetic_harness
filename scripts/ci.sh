#!/usr/bin/env bash
# Reproducible CI gate — the same checks the GitHub Actions workflow runs, so they
# can be run locally even without a remote (audit round 4 #2). Exits non-zero on
# the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== ruff =="
ruff check .

echo "== mypy =="
mypy harness

echo "== pytest =="
# Expose the dedicated phylo_extra env if present (explicit, no PATH magic).
EXTRA="$HOME/miniconda3/envs/phylo_extra/bin"
if [ -d "$EXTRA" ]; then export HARNESS_TOOL_PATHS="$EXTRA${HARNESS_TOOL_PATHS:+:$HARNESS_TOOL_PATHS}"; fi
pytest -q

echo "== CI OK =="
