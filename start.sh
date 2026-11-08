#!/usr/bin/env bash
# ==============================================================================
# MMM Studio — Local Startup Script
# Starts automatically:
#   1. Local PostgreSQL (verifies the `mmm_studio` database)
#   2. FastAPI Backend (`http://localhost:8000`)
#   3. Procrastinate Workers (`analysis`, `meridian`, `interactive`)
#   4. React + Vite Frontend (`http://localhost:5173`)
#
# Usage:
#   ./start.sh           # native mode (PostgreSQL + FastAPI + Workers + Vite)
#   ./start.sh --docker  # Docker Compose mode
# ==============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
FRONTEND_DIR="${REPO_ROOT}/frontend"

if [[ "${1:-}" == "--docker" ]]; then
  if docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo "==> Starting MMM Studio via Docker Compose..."
    exec docker compose -f "${REPO_ROOT}/docker-compose.yml" up --build
  elif command -v docker-compose >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo "==> Starting MMM Studio via docker-compose..."
    exec docker-compose -f "${REPO_ROOT}/docker-compose.yml" up --build
  else
    echo "=============================================================================="
    echo "WARNING: Docker Desktop / 'docker compose' is not installed or running."
    echo "==> Falling back automatically to NATIVE MODE (local PostgreSQL + FastAPI + Workers + Vite)..."
    echo "=============================================================================="
  fi
fi

# 1. Load .env if present
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

# 2. Detect local PostgreSQL (port 5433 Homebrew PostgreSQL@14 or standard port 5432)
if [[ -z "${MMM_DATABASE_URL:-}" ]]; then
  if pg_isready -h /tmp -p 5433 >/dev/null 2>&1; then
    PG_PORT=5433
    export MMM_DATABASE_URL="postgresql:///mmm_studio?host=/tmp&port=5433"
  elif pg_isready -h localhost -p 5432 >/dev/null 2>&1 || pg_isready -h /tmp -p 5432 >/dev/null 2>&1; then
    PG_PORT=5432
    export MMM_DATABASE_URL="postgresql://localhost:5432/mmm_studio"
  else
    echo "ERROR: PostgreSQL does not appear to be running on port 5433 or 5432."
    echo "Start it with: brew services start postgresql@14"
    exit 1
  fi

  # Create the `mmm_studio` database if it does not exist yet
  if command -v psql >/dev/null 2>&1; then
    if ! psql -h /tmp -p "${PG_PORT}" -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw mmm_studio; then
      echo "==> Creating PostgreSQL database 'mmm_studio' on port ${PG_PORT}..."
      createdb -h /tmp -p "${PG_PORT}" mmm_studio || true
    fi
  fi
fi

export MMM_STORAGE_BACKEND="${MMM_STORAGE_BACKEND:-local}"
export MMM_LOCAL_DATA_DIR="${MMM_LOCAL_DATA_DIR:-${REPO_ROOT}/.app_data}"
export MMM_WAKE_WORKERS="${MMM_WAKE_WORKERS:-false}"
export MMM_APP_ACCESS_CODE="${MMM_APP_ACCESS_CODE:-}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
unset VIRTUAL_ENV

mkdir -p "${MMM_LOCAL_DATA_DIR}"

# Prefer ~/.local/bin/uv before PATH
if [[ -x "${HOME}/.local/bin/uv" ]]; then
  UV_BIN="${HOME}/.local/bin/uv"
elif command -v uv >/dev/null 2>&1; then
  UV_BIN="uv"
else
  echo "ERROR: 'uv' is not installed. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

# 3. Verify backend & frontend dependencies
if ! "${REPO_ROOT}/.venv/bin/python" -c "import importlib.metadata as m; m.version('mmm-studio'); m.version('google-meridian')" >/dev/null 2>&1; then
  echo "==> Syncing Python environment in ${REPO_ROOT}/.venv..."
  (cd "${REPO_ROOT}" && "${UV_BIN}" sync)
fi

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "==> Installing npm packages in ${FRONTEND_DIR}..."
  (cd "${FRONTEND_DIR}" && npm install --cache /tmp/npm-cache)
fi

# 4. Free any stale processes bound to ports 8000 / 5173 / 8080
lsof -ti :8000 -ti :5173 -ti :8080 | xargs kill -9 2>/dev/null || true
pkill -f "mmm_studio.cli worker" 2>/dev/null || true

# 5. Clean shutdown of all child processes on Ctrl+C
PIDS=()
cleanup() {
  trap - EXIT INT TERM
  echo ""
  echo "==> Stopping MMM Studio services..."
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  done
  pkill -f "mmm_studio.cli worker" 2>/dev/null || true
  wait >/dev/null 2>&1 || true
  echo "==> All services stopped."
}
trap cleanup EXIT INT TERM

echo "=============================================================================="
echo "  MMM Studio — Running in Native Mode"
echo "  Database : ${MMM_DATABASE_URL}"
echo "  Artifacts: ${MMM_LOCAL_DATA_DIR}"
echo "------------------------------------------------------------------------------"
echo "  Web UI   : http://localhost:5173  (or http://localhost:8080)"
echo "  Workers  : analysis, meridian, interactive (one per queue)"
echo "  API Docs : http://localhost:8000/docs"
echo "  Press Ctrl+C to stop all services."
echo "=============================================================================="

# Start FastAPI on port 8000
(
  cd "${BACKEND_DIR}"
  exec "${UV_BIN}" run --no-sync --project "${REPO_ROOT}" uvicorn mmm_studio.main:app --host 0.0.0.0 --port 8000 --reload
) &
PIDS+=($!)

# Start one Procrastinate worker per queue so interactive scenarios never wait behind a multi-minute Meridian fit
for QUEUE in analysis meridian interactive; do
  (
    cd "${BACKEND_DIR}"
    exec "${UV_BIN}" run --no-sync --project "${REPO_ROOT}" python -m mmm_studio.cli worker --queue "${QUEUE}"
  ) &
  PIDS+=($!)
done

# Start Vite Dev Server on port 5173
(
  cd "${FRONTEND_DIR}"
  exec npm run dev -- --host 0.0.0.0 --port 5173 --strictPort
) &
PIDS+=($!)

# Start mirror Vite Dev Server on port 8080 (matching Docker URL)
(
  cd "${FRONTEND_DIR}"
  exec npm run dev -- --host 0.0.0.0 --port 8080 --strictPort
) &
PIDS+=($!)

wait
