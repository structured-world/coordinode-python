#!/usr/bin/env bash
# Install coordinode SDK packages from mounted /sdk source.
# Run once inside the container after /sdk is mounted.
set -e
pip install --no-cache-dir -e /sdk/coordinode
pip install --no-cache-dir -e /sdk/llama-index-coordinode
pip install --no-cache-dir -e /sdk/langchain-coordinode
