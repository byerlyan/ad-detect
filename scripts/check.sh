#!/bin/sh
set -eu

PROJECT_UV_CACHE="${PROJECT_UV_CACHE:-var/uv-cache}"
export UV_CACHE_DIR="$PROJECT_UV_CACHE"

.venv/bin/uv lock --check
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/python -m pytest

