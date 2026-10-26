#!/usr/bin/env bash
# Run backend checks inside a Linux container instead of the host Python.
#
#   tools/scripts/test-in-docker.sh pytest app/tests/unit -q
#   tools/scripts/test-in-docker.sh ruff check app libs
#   tools/scripts/test-in-docker.sh mypy app/src
#
# The repository is mounted at /repo (edits are seen immediately); dependencies live in the named volume
# `zid-test-venv` and are synced from uv.lock (--frozen) on every run, which is fast once warm. Component
# tests reach the disposable PostgreSQL on the host (port 55432) through host.docker.internal.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST_ROOT="$(cygpath -w "$ROOT" 2>/dev/null || echo "$ROOT")"
# Files mounted from Windows look executable to Linux, which only trips ruff's EXE002; ignore just that here.
if [ "${1:-}" = "ruff" ] && [ "${2:-}" = "check" ]; then set -- "$@" --extend-ignore EXE002; fi
PG_URL="${ZID_TEST_PG_URL_DOCKER:-postgresql+asyncpg://postgres:test@host.docker.internal:55432/postgres}"

MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$HOST_ROOT:/repo" \
  -v zid-test-venv:/venv \
  -v zid-test-uv-cache:/uv-cache \
  -w /repo/backend \
  -e UV_PROJECT_ENVIRONMENT=/venv \
  -e UV_CACHE_DIR=/uv-cache \
  -e UV_LINK_MODE=copy \
  -e UV_PYTHON_DOWNLOADS=never \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e ZID_TEST_PG_URL="$PG_URL" \
  --add-host host.docker.internal:host-gateway \
  zid/testrunner:uv \
  sh -c 'uv sync --frozen --all-packages --quiet && exec uv run --frozen --no-sync "$@"' -- "$@"
