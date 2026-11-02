# MMM Studio — Standalone Marketing Mix Modelling Web Application

**MMM Studio** (`src/app/`) is a self-contained, full-stack web application and CLI pipeline for Bayesian Marketing Mix Modelling (Google Meridian v1.5.4), OLS benchmarking, exploratory data analysis, constrained budget optimisation, and out-of-sample budget-plan forecasting.

## Architectural Isolation

`src/app/` is completely self-contained:
- **Zero dependency on `tchibo_mmm`, `notebooks/`, `outputs/`, `data/processed/`, or `reports/`**: Every chart (all 55 deduplicated interactive Plotly figures covering all 61 notebook visualizations), table (~30 structured tables), Meridian HTML report (3 self-contained reports), and management narrative is generated from scratch by `mmm_studio.core` and `mmm_studio.pipeline` starting solely from an uploaded `.xlsx` workbook.
- **Independent Python environment**: Managed via `src/app/backend/pyproject.toml` and `src/app/backend/uv.lock`.
- **Independent Frontend SPA**: React 18 + TypeScript 5.5 + Vite 5 + MUI 5 + TanStack Query 5 + Plotly (`plotly.js-cartesian-dist-min`) in `src/app/frontend/`.

---

## 1. Quickstart: Offline CLI Mode (No Server Required)

You can execute the full 15-stage pipeline directly from the command line against `data/DataScienceMarketingUseCase.xlsx` and write all JSON sections, Parquet datasets, and HTML reports to a local directory:

```bash
cd src/app/backend
uv sync

# Fast EDA + Feature Preparation + OLS Benchmarks (~5 seconds)
uv run python -m mmm_studio.cli run \
  --workbook ../../../data/DataScienceMarketingUseCase.xlsx \
  --out ../../.app_data/runs/cli_demo \
  --profile quick \
  --skip-meridian

# Full Pipeline including Google Meridian Bayesian MCMC + 3 HTML Reports + 42d Forecast
uv run python -m mmm_studio.cli run \
  --workbook ../../../data/DataScienceMarketingUseCase.xlsx \
  --out ../../.app_data/runs/cli_full \
  --profile quick
```

---

## 2. Local Full-Stack Development

### Option A: Docker Compose (One Command)

From the repository root:

```bash
docker compose -f src/app/docker-compose.yml up --build
```

- **Web UI**: `http://localhost:8080`
- **FastAPI Docs (`/docs`)**: `http://localhost:8000/docs`
- **PostgreSQL 16**: `localhost:5432` (`mmm_studio`)

### Option B: Native 3-Process Development

1. Ensure local PostgreSQL is running with a `mmm_studio` database (e.g. `createdb mmm_studio`).
2. Copy `src/app/.env.example` to `src/app/.env` and adjust `MMM_DATABASE_URL` if needed.
3. Start the API, background worker, and Vite dev server:

```bash
# Terminal 1 — FastAPI Server
cd src/app/backend
uv run uvicorn mmm_studio.main:app --reload --port 8000

# Terminal 2 — Procrastinate Worker (analysis, meridian, interactive queues)
cd src/app/backend
uv run python -m mmm_studio.cli worker --queue all

# Terminal 3 — React + Vite Frontend
cd src/app/frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

---

## 3. Running Tests

```bash
cd src/app/backend

# Fast test suite (~14s): workbook profiling, golden metrics, EDA, preparation, OLS, API & worker jobs
uv run pytest tests/ -m "not slow" -v

# Slow Meridian MCMC end-to-end smoke test (~55s)
uv run pytest tests/test_meridian_smoke.py -v
```

---

## 4. Google Cloud Run Deployment (`formare-dev` / `europe-west3`)

The idempotent deployment script [`src/app/deploy/deploy.sh`](deploy/deploy.sh) provisions and deploys:
- **Cloud SQL PostgreSQL 16** (`mmm-studio-pg`, `db-f1-micro`)
- **Cloud Storage Bucket** (`gs://formare-dev-mmm-studio-artifacts`)
- **Artifact Registry** (`mmm-studio`) & **Secret Manager** (`mmm-studio-db-url`, `mmm-studio-access-code`)
- **3 Cloud Run Jobs** (scale-to-zero workers woken on demand via Cloud Run Admin API v2):
  - `mmm-studio-worker-analysis` (2 vCPU / 4 GiB, `--burst`)
  - `mmm-studio-worker-meridian` (4 vCPU / 16 GiB, `--burst`)
  - `mmm-studio-worker-interactive` (2 vCPU / 8 GiB, `--idle-exit 300` with warm 2-run LRU cache)
- **2 Cloud Run Services**:
  - `mmm-studio-api` (FastAPI)
  - `mmm-studio-web` (Nginx + React SPA)

To deploy:

```bash
PROJECT_ID=formare-dev REGION=europe-west3 bash src/app/deploy/deploy.sh
```
