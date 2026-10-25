#!/usr/bin/env bash
# Quality gate: run before marking any work complete.
set -euo pipefail
cd "$(dirname "$0")/.."
# Tests never read .env.local. Hand them the two ffmpeg paths from it explicitly (a real env var wins).
for key in FFMPEG_PATH FFPROBE_PATH; do
  if [ -z "${!key:-}" ] && [ -f .env.local ]; then
    val=$(grep -E "^${key}=" .env.local | tail -n1 | cut -d= -f2- | sed -E "s/^[[:space:]\"']+//; s/[[:space:]\"']+\$//" || true)
    val="${val/#\~/$HOME}"
    if [ -n "$val" ]; then export "$key=$val"; fi
  fi
done
echo "== ruff lint";       uv run ruff check factory ui tests
echo "== ruff format";     uv run ruff format --check factory ui tests
echo "== typecheck";       uvx --quiet pyright@1.1.400 --pythonpath .venv/bin/python factory ui tests
echo "== tests";           uv run pytest "$@"
echo "== preflight";       uv run factory preflight > /dev/null && echo "ffmpeg ok"
echo "ALL CHECKS PASSED"
