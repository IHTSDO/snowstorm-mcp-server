#!/usr/bin/env bash
set -euo pipefail

# --locked: run against exactly the versions in uv.lock, the ones the Docker
# image ships, and fail if the lock is stale.
uv run --locked --extra dev ruff check .
uv run --locked --extra dev mypy --ignore-missing-imports src
uv run --locked --extra dev pytest -q tests -m "not integration"
