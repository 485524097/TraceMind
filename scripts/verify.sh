#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "${script_dir}/.." && pwd)"
run_integration=false
if [[ "${1:-}" == "--integration" ]]; then
  run_integration=true
fi

echo "[1/3] Verifying backend"
cd "${repo_dir}/backend"
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -m "not integration"
if [[ "${run_integration}" == "true" ]]; then
  : "${TEST_DATABASE_URL:?TEST_DATABASE_URL is required with --integration}"
  DATABASE_URL="${TEST_DATABASE_URL}" uv run alembic upgrade head
  uv run pytest -m integration
fi

echo "[2/3] Verifying frontend"
cd "${repo_dir}/frontend"
npm ci
npm run lint
npm run test:unit -- --run
npm run build

echo "[3/3] Verifying Docker Compose"
docker compose --env-file "${repo_dir}/.env.example" -f "${repo_dir}/compose.yaml" config --quiet
echo "All checks passed."
