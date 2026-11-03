# MMM Studio — Standalone Marketing Mix Modelling Web Application

**MMM Studio** (`src/app/`), branded with Tchibo's design tokens and labelled as an independent case-study prototype, is a self-contained, full-stack web application and CLI pipeline for Bayesian Marketing Mix Modelling (Google Meridian v2.1.0), OLS benchmarking, exploratory data analysis, constrained budget optimisation, and out-of-sample budget-plan forecasting.

## Architectural Isolation

`src/app/` is completely self-contained:
- **Zero dependency on `tchibo_mmm`, `notebooks/`, `outputs/`, `data/processed/`, or `reports/`**: Every interactive Plotly chart, table, Meridian HTML report and management narrative is generated from scratch by `mmm_studio.core` and `mmm_studio.pipeline` starting solely from an uploaded `.xlsx` workbook.
- **Independent Python environment**: Managed via `src/app/backend/pyproject.toml` and `src/app/backend/uv.lock`.
- **Independent Frontend SPA**: React 18 + TypeScript 5.5 + Vite 5 + MUI 5 + TanStack Query 5 + Plotly (`plotly.js-cartesian-dist-min`) in `src/app/frontend/`.

---

## 1. Quickstart: Offline CLI Mode (No Server Required)

You can execute the full 16-stage pipeline directly from the command line against `data/DataScienceMarketingUseCase.xlsx` and write all JSON sections, Parquet datasets, and HTML reports to a local directory:

```bash
cd src/app/backend
uv sync --extra worker

# Fast EDA + data preparation + OLS benchmark (~5 seconds)
uv run python -m mmm_studio.cli run \
  --input ../../../data/DataScienceMarketingUseCase.xlsx \
  --out ../../.app_data/runs/cli_demo \
  --profile quick \
  --skip-meridian

# Full pipeline with Google Meridian, matching notebooks 03–05 (~15–25 min on a laptop CPU):
# final fit + forward hold-out, ROI/optimiser/HTML reports, back-test, 42-day forecast, selection grid
uv run python -m mmm_studio.cli run \
  --input ../../../data/DataScienceMarketingUseCase.xlsx \
  --out ../../.app_data/runs/cli_full \
  --profile full
```
*(Both `--input` / `--workbook` and `--profile {full,quick,smoke}` / `--fast` / `--smoke` are supported.)*

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

# Terminals 2–4 — one Procrastinate worker per queue (so scenarios never wait behind a Meridian fit)
cd src/app/backend
uv run python -m mmm_studio.cli worker --queue analysis
uv run python -m mmm_studio.cli worker --queue meridian
uv run python -m mmm_studio.cli worker --queue interactive

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

The idempotent deployment script [`src/app/deploy/deploy.sh`](deploy/deploy.sh) provisions and deploys, using Cloud
Build (no local Docker):
- **Cloud SQL PostgreSQL 16** (`mmm-studio-pg`, `db-f1-micro`, Enterprise edition), the only resource billed while idle
- **Cloud Storage Bucket** (`gs://formare-dev-mmm-studio-artifacts`)
- **Artifact Registry** (`mmm-studio`) & **Secret Manager** (`mmm-studio-database-url`, `mmm-studio-access-code`).
  The studio access code is mandatory: `APP_ACCESS_CODE` (≥ 16 characters) if given, else the stored secret, else a
  generated random code. Every visitor signs in with it; the API refuses to start in production without it.
- **3 Cloud Run Jobs** (scale-to-zero workers; the API starts an execution when it defers work on their queue and none
  is running):
  - `mmm-studio-worker-analysis` (2 vCPU / 4 GiB, `--burst`)
  - `mmm-studio-worker-meridian` (8 vCPU / 16 GiB, `--burst`, 4 h timeout)
  - `mmm-studio-worker-interactive` (4 vCPU / 8 GiB, `--idle-exit 300` with warm 2-run LRU cache)
- **1 Cloud Run Service** `mmm-studio`: nginx + React SPA, with the FastAPI API as a sidecar on `localhost:8000`
  (reachable through `invoker-iam-disabled`, which works under domain-restricted sharing; the access code protects
  every page and download). For named users instead of one shared code, put Identity-Aware Proxy in front.

To deploy (from the repo root, after `gcloud auth login`):

```bash
GCP_PROJECT_ID=formare-dev ./src/app/deploy/deploy.sh          # everything, then seeds one Full run if none exists
GCP_PROJECT_ID=formare-dev ./src/app/deploy/deploy.sh update   # code changes: rebuild images, roll out jobs + service
GCP_PROJECT_ID=formare-dev ./src/app/deploy/deploy.sh seed     # upload the case-study workbook and start a run
```
