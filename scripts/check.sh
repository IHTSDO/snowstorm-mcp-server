#!/usr/bin/env bash
set -euo pipefail

uv run --with '.[dev]' ruff check .
uv run --with '.[dev]' mypy --ignore-missing-imports src
uv run --with '.[dev]' pytest -q tests -m "not integration"
