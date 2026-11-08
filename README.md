# MMM Studio — Marketing Mix Modelling & Budget Forecasting Platform

**MMM Studio** is a full-stack web application and CLI pipeline for Bayesian Marketing Mix Modelling ([Google Meridian](https://github.com/google/meridian) v2.1.0), OLS benchmarking, exploratory data analysis, constrained budget optimisation, and out-of-sample budget-plan scenario forecasting.

## Architecture

- **Self-contained analysis engine (`backend/mmm_studio`)**: Every interactive Plotly chart, diagnostic table, Meridian HTML report, and executive summary is computed directly from an uploaded `.xlsx` or `.csv` dataset.
- **Backend API & Async Workers (`backend/`)**: FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16 + [Procrastinate](https://procrastinate.readthedocs.io/) task queues (`analysis`, `meridian`, and `interactive`), with optional Gemini-powered column mapping, resampling advice, model configuration recommendations, and chart/table explanations.
- **Frontend SPA (`frontend/`)**: React 18 + TypeScript 5.5 + Vite 5 + MUI 5 + TanStack Query 5 + Plotly (`plotly.js-cartesian-dist-min`).
- **Cloud Deployment (`deploy/`)**: Idempotent Google Cloud Run deployment script provisioning Cloud SQL PostgreSQL 16, GCS artifact storage, Secret Manager, a sidecar Cloud Run Service (nginx + FastAPI), and 3 scale-to-zero Cloud Run Jobs for workers.

```text
├── backend/
│   ├── Dockerfile              # Multi-target image (`api` slim and `worker` with Google Meridian)
│   ├── mmm_studio/             # Core MMM engine, FastAPI routes, Procrastinate jobs, Plotly builders
│   ├── pyproject.toml          # Backend package & dependency specification
│   └── tests/                  # Fast unit/API/golden suite + slow Meridian MCMC smoke tests
├── frontend/
│   ├── Dockerfile              # Node 22 build -> nginx 1.27 SPA + API reverse proxy
│   └── src/                    # React + TypeScript pages, upload wizard, settings advisor, charts
├── deploy/
│   ├── deploy.sh               # One-command Google Cloud Run deployment & seeding script
│   └── nginx.conf              # Reverse-proxy and SPA routing configuration
├── docker-compose.yml          # Local 6-service stack (Postgres, API, 3 queue workers, Web)
├── start.sh                    # One-command local startup script (native or `--docker`)
└── pyproject.toml              # Root uv workspace installing `mmm-studio[worker]` into `.venv`
```

---

## 1. Quickstart: Offline CLI Mode (No Server Required)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 (`.python-version`).

You can execute the full 16-stage pipeline directly from the command line against any `.xlsx` or `.csv` workbook and write all JSON sections, Parquet datasets, and HTML reports to a local directory:

```bash
uv sync                  # installs mmm-studio[worker] (FastAPI + Google Meridian) into .venv

# Fast EDA + data preparation + OLS benchmark (~5 seconds)
uv run python -m mmm_studio.cli run \
  --input path/to/your_data.xlsx \
  --out .app_data/runs/cli_demo \
  --profile quick \
  --skip-meridian

# Full pipeline with Google Meridian (~15–25 min on a laptop CPU):
# final fit + forward hold-out, ROI/optimiser/HTML reports, back-test, 42-day forecast, selection grid
uv run python -m mmm_studio.cli run \
  --input path/to/your_data.xlsx \
  --out .app_data/runs/cli_full \
  --profile full
```
*(Both `--input` / `--workbook` and `--profile {full,quick,smoke}` / `--fast` / `--smoke` are supported.)*

---

## 2. Local Full-Stack Development

### Option A: One-Command Startup Script

```bash
./start.sh               # Native mode (local PostgreSQL + FastAPI + 3 workers + Vite)
./start.sh --docker      # Docker Compose mode
```

### Option B: Docker Compose

```bash
docker compose up --build
```

- **Web UI**: `http://localhost:8080`
- **FastAPI Docs (`/docs`)**: `http://localhost:8000/docs`
- **PostgreSQL 16**: `localhost:5432` (`mmm_studio`)

### Option C: Manual Multi-Process Development

1. Ensure local PostgreSQL is running with a `mmm_studio` database (e.g. `createdb mmm_studio`).
2. Copy `.env.example` to `.env` and adjust `MMM_DATABASE_URL` (and optionally `MMM_GEMINI_API_KEY` or `MMM_GEMINI_VERTEX=true`) if needed.
3. Start the API, background workers, and Vite dev server:

```bash
# Terminal 1 — FastAPI Server
cd backend
uv run --project .. uvicorn mmm_studio.main:app --reload --port 8000

# Terminals 2–4 — one Procrastinate worker per queue (so scenarios never wait behind a Meridian fit)
cd backend
uv run --project .. python -m mmm_studio.cli worker --queue analysis
uv run --project .. python -m mmm_studio.cli worker --queue meridian
uv run --project .. python -m mmm_studio.cli worker --queue interactive

# Terminal 5 — React + Vite Frontend
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

---

## 3. Running Tests

```bash
# Backend fast test suite (~14s): workbook profiling, golden metrics, EDA, preparation, OLS, API & worker jobs
cd backend
uv run --project .. pytest tests/ -m "not slow" -v

# Backend slow Meridian MCMC end-to-end smoke test (~55s)
uv run --project .. pytest tests/test_meridian_smoke.py -v

# Frontend unit tests & production build check
cd ../frontend
npm test
npm run build
```

---

## 4. Google Cloud Run Deployment

The idempotent deployment script [`deploy/deploy.sh`](deploy/deploy.sh) provisions and deploys using Cloud Build (no local Docker required):
- **Cloud SQL PostgreSQL 16** (`mmm-studio-pg`, `db-f1-micro`, Enterprise edition), the only resource billed while idle
- **Cloud Storage Bucket** (`gs://<PROJECT_ID>-mmm-studio-artifacts`)
- **Artifact Registry** (`mmm-studio`) & **Secret Manager** (`mmm-studio-database-url`, `mmm-studio-access-code`).
  The studio access code is mandatory in production: `APP_ACCESS_CODE` (≥ 16 characters) if given, else the stored secret, else a generated random code.
- **3 Cloud Run Jobs** (scale-to-zero workers woken on demand by the API):
  - `mmm-studio-worker-analysis` (2 vCPU / 4 GiB, `--burst`)
  - `mmm-studio-worker-meridian` (8 vCPU / 16 GiB, `--burst`, 4 h timeout)
  - `mmm-studio-worker-interactive` (4 vCPU / 8 GiB, `--idle-exit 300` with warm 2-run LRU cache)
- **1 Cloud Run Service** `mmm-studio`: nginx + React SPA, with the FastAPI API as a sidecar on `localhost:8000`.

To deploy (from the repo root, after `gcloud auth login`):

```bash
GCP_PROJECT_ID=your-gcp-project ./deploy/deploy.sh          # provision all resources & seed one Full run
GCP_PROJECT_ID=your-gcp-project ./deploy/deploy.sh update   # rebuild images & roll out jobs + service
GCP_PROJECT_ID=your-gcp-project ./deploy/deploy.sh seed     # upload the sample workbook and start a run
```

---

## License

Released under the [MIT License](LICENSE).
