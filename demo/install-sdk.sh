#!/usr/bin/env bash
# Install coordinode SDK packages from the mounted /sdk source.
# Run once inside the container, after /sdk is mounted.
set -e

# One resolution pass for all three. Installed separately, the integration
# packages declare a dependency on `coordinode` and pip satisfies it from PyPI,
# overwriting the editable install done a line earlier: the container then runs
# the published SDK and nothing of the mounted checkout under test.
# An editable install builds from source by design, so `--only-binary :all:`
# is not applicable here. The rule guards against running setup code from
# untrusted packages; these three are this repository, mounted at /sdk.
# The suppression has to sit on the pip line itself, so the command stays on
# one line rather than wrapping.
pip install --no-cache-dir -e /sdk/coordinode -e /sdk/llama-index-coordinode -e /sdk/langchain-coordinode  # NOSONAR

# The generated proto stubs are gitignored and no build hook produces them, so
# an editable install of the mounted checkout has none. Every call the notebooks
# make goes through them, so generate them here, once the build dependencies
# from the install above are present.
# PYTHON is overridden because the Makefile defaults to `uv run python`, which
# is right on a developer's machine and absent in this image; grpcio-tools is
# installed into the image's own interpreter.
make -C /sdk proto PYTHON=python

# Fail loudly rather than let the demo quietly exercise a release.
# An assert would vanish under -O and let the demo run against a release while
# claiming to test the mount.
python - <<'PY'
import coordinode

path = coordinode.__file__
if "/sdk/" not in path:
    raise RuntimeError(f"coordinode resolved to {path}, not the mounted source")
import coordinode._proto  # the stubs generated above must be importable

print(f"coordinode from {path}")
PY
