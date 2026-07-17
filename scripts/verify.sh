#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "${script_dir}/.." && pwd)"

echo "[1/3] Verifying backend"
cd "${repo_dir}/backend"
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest

echo "[2/3] Verifying frontend"
cd "${repo_dir}/frontend"
npm ci
npm run lint
npm run test:unit -- --run
npm run build

echo "[3/3] Verifying Docker Compose"
docker compose --env-file "${repo_dir}/.env.example" -f "${repo_dir}/compose.yaml" config --quiet
echo "All checks passed."
