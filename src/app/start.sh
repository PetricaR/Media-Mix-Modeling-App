#!/usr/bin/env bash
# ==============================================================================
# MMM Studio — Local Startup Script
# Porneste automat:
#   1. PostgreSQL local (verifica baza de date `mmm_studio`)
#   2. FastAPI Backend (`http://localhost:8000`)
#   3. Procrastinate Worker (`analysis`, `meridian`, `interactive`)
#   4. React + Vite Frontend (`http://localhost:5173`)
#
# Utilizare:
#   ./src/app/start.sh           # mod nativ (recomandat pe acest Mac)
#   ./src/app/start.sh --docker  # mod Docker Compose (daca Docker Desktop este instalat)
# ==============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${APP_DIR}/../.." && pwd)"
BACKEND_DIR="${APP_DIR}/backend"
FRONTEND_DIR="${APP_DIR}/frontend"

if [[ "${1:-}" == "--docker" ]]; then
  if docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo "==> Pornire MMM Studio prin Docker Compose..."
    exec docker compose -f "${APP_DIR}/docker-compose.yml" up --build
  elif command -v docker-compose >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo "==> Pornire MMM Studio prin docker-compose..."
    exec docker-compose -f "${APP_DIR}/docker-compose.yml" up --build
  else
    echo "=============================================================================="
    echo "ATENTIE: Docker Desktop / 'docker compose' nu este instalat sau pornit pe acest Mac"
    echo "(symlink-urile din ~/.docker/cli-plugins indica spre /Applications/Docker.app care lipseste)."
    echo "==> Comutare automata pe MODUL NATIV (PostgreSQL local + FastAPI + Worker + Vite)..."
    echo "=============================================================================="
  fi
fi

# 1. Incarca .env daca exista
if [[ -f "${APP_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${APP_DIR}/.env"
  set +a
fi

# 2. Detecteaza PostgreSQL local (port 5433 Homebrew PostgreSQL@14 sau 5432 standard)
if [[ -z "${MMM_DATABASE_URL:-}" ]]; then
  if pg_isready -h /tmp -p 5433 >/dev/null 2>&1; then
    PG_PORT=5433
    export MMM_DATABASE_URL="postgresql:///mmm_studio?host=/tmp&port=5433"
  elif pg_isready -h localhost -p 5432 >/dev/null 2>&1 || pg_isready -h /tmp -p 5432 >/dev/null 2>&1; then
    PG_PORT=5432
    export MMM_DATABASE_URL="postgresql://localhost:5432/mmm_studio"
  else
    echo "EROARE: PostgreSQL nu pare pornit pe portul 5433 sau 5432."
    echo "Porneste-l cu: brew services start postgresql@14"
    exit 1
  fi

  # Creeaza baza de date `mmm_studio` daca nu exista deja
  if command -v psql >/dev/null 2>&1; then
    if ! psql -h /tmp -p "${PG_PORT}" -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw mmm_studio; then
      echo "==> Creare baza de date PostgreSQL 'mmm_studio' pe portul ${PG_PORT}..."
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

# Gaseste binarul `uv`: ~/.local/bin/uv inainte de cel din PATH, pentru ca uv-ul vechi din Homebrew rescrie uv.lock
if [[ -x "${HOME}/.local/bin/uv" ]]; then
  UV_BIN="${HOME}/.local/bin/uv"
elif command -v uv >/dev/null 2>&1; then
  UV_BIN="uv"
else
  echo "EROARE: 'uv' nu este instalat. Instaleaza-l cu: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

# 3. Verifica dependentele backend & frontend
# Backend-ul ruleaza in mediul principal al proiectului (${REPO_ROOT}/.venv), unde `mmm-studio` este instalat
# editabil cu extra `worker` (grupul `app` din pyproject.toml-ul radacina)
if ! "${REPO_ROOT}/.venv/bin/python" -c "import mmm_studio, meridian" >/dev/null 2>&1; then
  echo "==> Sincronizare mediu Python principal in ${REPO_ROOT}/.venv..."
  (cd "${REPO_ROOT}" && "${UV_BIN}" sync)
fi

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "==> Instalare pachete npm in ${FRONTEND_DIR}..."
  (cd "${FRONTEND_DIR}" && npm install --cache /tmp/npm-cache)
fi

# 4. Elibereaza eventuale procese vechi ramase pe porturile 8000 / 5173 / 8080
lsof -ti :8000 -ti :5173 -ti :8080 | xargs kill -9 2>/dev/null || true
pkill -f "mmm_studio.cli worker" 2>/dev/null || true

# 5. Oprire curata a tuturor proceselor la Ctrl+C
PIDS=()
cleanup() {
  echo ""
  echo "==> Oprire servicii MMM Studio..."
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  done
  wait >/dev/null 2>&1 || true
  echo "==> Toate serviciile au fost oprite."
}
trap cleanup EXIT INT TERM

echo "=============================================================================="
echo "  MMM Studio — Aplicatie pornita in mod nativ"
echo "  Database : ${MMM_DATABASE_URL}"
echo "  Artifacts: ${MMM_LOCAL_DATA_DIR}"
echo "------------------------------------------------------------------------------"
echo "  Web UI   : http://localhost:5173  (sau http://localhost:8080)"
echo "  Workers  : analysis, meridian, interactive (cate unul pe coada)"
echo "  API Docs : http://localhost:8000/docs"
echo "  Apasa Ctrl+C pentru a opri toate serviciile."
echo "=============================================================================="

# Porneste FastAPI pe portul 8000
(
  cd "${BACKEND_DIR}"
  exec "${UV_BIN}" run --project "${REPO_ROOT}" uvicorn mmm_studio.main:app --host 0.0.0.0 --port 8000 --reload
) &
PIDS+=($!)

# Porneste cate un worker Procrastinate pe fiecare coada, ca scenariile si optimizarile interactive
# sa nu astepte dupa un fit Meridian de cateva minute (fiecare worker proceseaza un job odata)
for QUEUE in analysis meridian interactive; do
  (
    cd "${BACKEND_DIR}"
    exec "${UV_BIN}" run --project "${REPO_ROOT}" python -m mmm_studio.cli worker --queue "${QUEUE}"
  ) &
  PIDS+=($!)
done

# Porneste Vite Dev Server pe portul 5173
(
  cd "${FRONTEND_DIR}"
  exec npm run dev -- --host 0.0.0.0 --port 5173 --strictPort
) &
PIDS+=($!)

# Porneste mirror Vite Dev Server si pe portul 8080 (compatibil cu URL-ul Docker)
(
  cd "${FRONTEND_DIR}"
  exec npm run dev -- --host 0.0.0.0 --port 8080 --strictPort
) &
PIDS+=($!)

wait
