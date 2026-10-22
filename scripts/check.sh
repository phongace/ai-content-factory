#!/usr/bin/env bash
# Quality gate: run before marking any work complete.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== ruff lint";       uv run ruff check factory ui tests
echo "== ruff format";     uv run ruff format --check factory ui tests
echo "== typecheck";       uvx --quiet pyright@1.1.400 --pythonpath .venv/bin/python factory ui
echo "== tests";           uv run pytest "$@"
echo "== preflight";       uv run factory preflight > /dev/null && echo "ffmpeg ok"
echo "ALL CHECKS PASSED"
