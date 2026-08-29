#!/usr/bin/env bash
# Install coordinode SDK packages from the mounted /sdk source.
# Run once inside the container, after /sdk is mounted.
set -e

# One resolution pass for all three. Installed separately, the integration
# packages declare a dependency on `coordinode` and pip satisfies it from PyPI,
# overwriting the editable install done a line earlier: the container then runs
# the published SDK and nothing of the mounted checkout under test.
pip install --no-cache-dir \
    -e /sdk/coordinode \
    -e /sdk/llama-index-coordinode \
    -e /sdk/langchain-coordinode

# Fail loudly rather than let the demo quietly exercise a release.
python - <<'PY'
import coordinode
path = coordinode.__file__
assert "/sdk/" in path, f"coordinode resolved to {path}, not the mounted source"
print(f"coordinode from {path}")
PY
