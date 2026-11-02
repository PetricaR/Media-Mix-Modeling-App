# MMM Studio: web app for the Tchibo Meridian case study (plan v3)

**Goal:** a standalone web app where you upload **only the Excel workbook**. The app re-implements, in its own code,
everything notebooks 01–05 do: the data audit, EDA, correlations, catalog deep dive, data preparation, Meridian MMM,
OLS benchmark, optimiser, forecasts and scenarios. Long work runs on a task queue with its own workers. The app shows
every chart, table and insight interactively, runs on one `docker compose up` locally, and runs on Google Cloud.

**Status:** plan v3, after two review rounds (Appendix A and A2). Nothing has been built or deployed yet. Section 13
lists the open decisions.

**What changed from v2:** the app no longer imports `src/tchibo_mmm` or depends on the notebooks; all notebook code is
ported into the app (section 6). Background work moves from "subprocess locally, Cloud Run Job in the cloud" to a
real task queue with the same workers everywhere (section 3). Scenarios and the optimiser leave the API process.

---

## 1. What the app must cover

### 1.1 Everything in the repository, re-implemented and generated again from the upload

The notebooks and outputs below are the **reference**: the app reproduces their results with its own code and never
reads these files at runtime.

| Reference in this repo | What it produces | Where it goes in the app |
| --- | --- | --- |
| `notebooks/01_data_understanding.ipynb` | Quality checks, channel shares, target, costs, correlations, catalog delay and halo, efficiency KPIs, naive baselines, answers Q1–Q3 | Data quality, Explore, Answers |
| `notebooks/01b_eda_mmm_deep_dive.ipynb` | Executive summary, data-quality audit, 34 EDA figures, MMM readiness, answers, implications | Overview, Data quality, Explore (7 tabs) |
| `notebooks/02_data_preparation.ipynb` | Wide table, cost considerations (booking vs effect, adstock shift, saturation), features, channel importance (OLS with HAC errors), Meridian input roles | Preparation |
| `notebooks/03_meridian_mmm.ipynb` | Model selection, prior sensitivity, final fit, R-hat and health card, accuracy, residuals, Q3 robustness, ROI, contribution, adstock, response curves, optimiser, catalog what-if | Model, Drivers & ROI, Optimiser |
| `notebooks/04_benchmark_ols.ipynb` | Naive / OLS-1 / OLS-2 / Meridian on two hold-outs, walk-forward cross-validation | Benchmark |
| `notebooks/05_forecast.ipynb` + `tchibo-forecast` CLI | Leak-free back-test, example budget plan, daily forecast with intervals, scenarios | Forecast & scenarios |
| `outputs/*.html` | Meridian summary, optimisation report, health card | Reports (sandboxed embed) |
| `outputs/*.csv`, `*.json`, `*.binpb`; `data/processed/*.csv` | Metrics, hold-out forecast, plan forecast, saved model, wide table, Meridian input table | Downloads, per run |
| `PLAN.md` §5 Phase 4, §8 | Google Cloud MLOps mapping, risks, limitations | Answers to the brief › Cloud & limitations |

In total that is **61 notebook figures** (27 in `reports/figures/`, 34 in `reports/figures/eda/`), about 30 tables,
3 Meridian HTML reports and every written answer. Appendix B maps each figure to its page.

Notebooks 01 and 01b overlap on 6 figures (for example `01_corr_matrix` and `eda/corr_matrix_costs`). The app shows
each insight once, using the richer 01b version, with a Pearson/Spearman toggle where 01 had both.

### 1.2 Features from Cassandra and Meridian's Scenario Planner

[Cassandra's platform](https://cassandra.app/platform) and Google's Meridian Scenario Planner (no-code, built on
Looker Studio, open beta since February 2026) set the bar for an MMM product. What the app takes from them:

| Feature | In MMM Studio | Scope |
| --- | --- | --- |
| Incremental sales per channel, with uncertainty | Contribution, ROI and marginal ROI with 90 % credible intervals | ✅ |
| "Uncertainty per channel: which channels need calibration" | A **calibration needed** badge when the ROI interval is wider than its mean | ✅ |
| Overinvestment and growth signals | A badge per channel: marginal ROI < 1 means *over-invested*; marginal ROI well above 1 means *room to grow* | ✅ |
| Budget allocator | Optimiser with fixed or flexible total budget, per-channel spend bounds and a planning window | ✅ |
| Scenario planner / "compare returns" | Up to 4 saved scenarios side by side, Δ sales vs Δ spend, with 90 % intervals | ✅ |
| Plan and forecast | Daily forecast for an uploaded or generated budget plan, prediction and credible intervals | ✅ |
| Spend-response and marginal-ROI curves | Response curves per channel, current spend marked | ✅ |
| Accuracy assessment and forecast validation | Hold-out, second forward hold-out, leak-free back-test, interval coverage | ✅ |
| Calibration with experiment results | Advanced run setting: per-channel ROI prior (μ, σ) from a lift test, which feeds `MMMConfig.roi_prior` | ✅ |
| Model refresh and decision log | Every upload is a versioned run (config, confirmed data roles, Meridian and app versions); one run is marked **active** | ✅ |
| Result check (observed vs saved forecast) | When a newer workbook covers a forecast window, compare actuals with the stored forecast | Optional (M7) |
| AI narrative | Gemini summary of a run's results (the catalog app already uses `google-genai`) | Optional (M7) |
| Ad-platform connectors, agents, writing to ad accounts, creative analysis | Not in scope: the input is the workbook only | ❌ |

### 1.3 Independence rules

1. The app lives entirely in `src/app/`. Its backend is its **own uv project** (`src/app/backend/pyproject.toml` and
   `uv.lock`), and every Docker build uses `src/app/` as its context.
2. It never imports `tchibo_mmm` and never reads `notebooks/`, `outputs/`, `data/processed/` or `reports/`.
3. The notebooks and `src/tchibo_mmm` stay **frozen** as the case-study deliverable. They are not refactored.
4. Only two things are copied in, both as test data: the workbook (`tests/fixtures/case_study.xlsx`) and the
   reference numbers (`tests/golden/case_study.json`, section 6.3).
5. Trade-off, accepted: the modelling logic exists twice, frozen in the notebooks and maintained in the app. If you
   later want a single source, the notebooks can import `mmm_studio.core` (optional M8).

---

## 2. Technical stack: taken from the data science catalog

"The data science catalog" is `~/PFA/Projects/ds-data-catalog` (`catalog_app/`). The app reuses its stack and its
Cloud Run deployment pattern, and adds a task queue and the analytics stack.

| Layer | ds-data-catalog | MMM Studio | Change |
| --- | --- | --- | --- |
| API | FastAPI 0.162, uvicorn, pydantic 2, pydantic-settings, Python 3.12 | Same | = |
| Database | SQLAlchemy 2.0 + PostgreSQL 16 (Cloud SQL), psycopg2 | Same, but **psycopg 3** so SQLAlchemy and the queue share one driver. Postgres locally too (no SQLite) | ~ driver |
| Task queue | Planned in its roadmap: jobs table + worker, *not* FastAPI BackgroundTasks | **Procrastinate** (Postgres-backed task queue): queues, locks, retries, abort, burst workers (section 3) | + queue |
| Workers | – | Same backend codebase, `mmm-studio worker --queue …`. Long-running processes locally; **Cloud Run Jobs in burst mode** in the cloud | + workers |
| Object storage | – | Google Cloud Storage (local folder in dev) | + GCS |
| Analytics | – | The app's own `mmm_studio.core`: Google Meridian 2.1.0 (TensorFlow 2.21, JAX), statsmodels, scipy, pandas, plotly. No matplotlib, seaborn, Jupyter or LightGBM | + analytics |
| Frontend | React 18.3 + TypeScript 5.5 + Vite 5 + MUI 5.16 (emotion) + TanStack Query 5 + axios + react-router 6 | Same versions | = |
| Charts | – | `react-plotly.js` with `plotly.js-cartesian-dist-min`, lazy-loaded | + Plotly |
| Theme | `theme.ts`: design tokens → `createTheme` | Same structure, tokens from the case-study palette (one palette for app and charts) | = |
| Serving | One Cloud Run service: nginx container + backend sidecar on `localhost:8000` | Same; the API image is slim (no TensorFlow) | = |
| Images | One backend image | **Two backend targets from one Dockerfile**: `api` (≈ 300 MB) and `worker` (Meridian, ≈ 3 GB) | + target |
| Build | `gcloud builds submit`, Artifact Registry, build SA with `roles/run.builder` | Same | = |
| Deploy | `deploy/run/deploy.sh` (bash 3.2 safe, idempotent, secrets in Secret Manager, `invoker-iam-disabled`) | Same pattern, plus bucket and three Cloud Run Jobs | + bucket, jobs |
| Auth | Google OAuth + JWT + roles | Access code for actions that cost money; viewing open (decision 4) | Simpler |
| Local dev | `start.sh`: uvicorn `--reload` + Vite dev server | `docker compose up` (Postgres, API, 3 workers, Vite). Without Docker: Homebrew Postgres + `honcho start` on `Procfile.dev` | ~ |
| CI | GitHub Actions: pytest, `npm run build`, `bash -n` | Same, with a Postgres service container | = |

Python dependencies are managed by **uv** in the app's own project. The base dependencies serve the API (fastapi,
uvicorn, sqlalchemy, psycopg, procrastinate, pydantic-settings, python-multipart, pandas, openpyxl,
google-cloud-storage, google-cloud-run). The `worker` extra adds `google-meridian[schema]==2.1.0`, statsmodels, scipy,
holidays and plotly. The images install with `uv sync --frozen` (`--extra worker` for the worker target).

Docker Desktop is installed on this Mac, but its daemon was not running on 2026-11-02; `docker compose` needs it
started. `Procfile.dev` is the fallback without Docker.

Library docs are fetched with `chub` before coding, per the global CLAUDE.md. Entries exist for `fastapi/package`,
`sqlalchemy/package`, `psycopg/package`, `plotly/package`, `google/storage`, `mui/material`, `react/react` and
`celery/package`. Procrastinate, Cloud Run Jobs and react-plotly have no entry, so their official docs are used.

---

## 3. Background processing

### 3.1 Choice: a task queue on the app's own Postgres

The workload: few jobs (a handful per day), long (1–60 min), CPU- and memory-heavy (TensorFlow/JAX), one user at a
time, plus short interactive calls (scenarios, optimiser) that need a fitted model already in memory.

| | Celery + Redis | **Procrastinate (recommended)** | v2: subprocess / Cloud Run Job |
| --- | --- | --- | --- |
| New infrastructure | Redis broker: Memorystore + Direct VPC egress, ≈ €30–40/month idle | None: queue tables live in the existing Postgres | None |
| Same code locally and in the cloud | Yes | Yes | No: two runners |
| Queueing, priorities, locks | Yes (routing, canvas) | Yes: queues, `lock` (one Meridian fit at a time), `queueing_lock` (no duplicates) | No: 409 when busy |
| Run row and job in one database | No: Postgres + Redis, two writes | Yes | Yes |
| Scale to zero on Cloud Run | Workers are always-on consumers: Cloud Run worker pools + CREMA autoscaling on Redis list length. In-flight tasks leave that list, so the autoscaler can remove a worker mid-fit without a custom metric | Burst workers (`wait=False`: drain the queue, then exit) map onto Cloud Run Job executions; nothing scales a worker down mid-fit | Yes |
| Cancel a running job | `revoke(terminate=True)` | Abort request, checked by the task (`should_abort`) | Cancel the execution |
| Gotchas for TF/JAX | Prefork after a TF import deadlocks; Redis `visibility_timeout` must exceed the longest task or it is redelivered | Long-running local workers keep TF memory: solved by running each fit in a spawned child process (both options need this) | – |
| Familiarity | Industry standard, Flower dashboard | Smaller project; our own progress UI replaces a dashboard | – |

**Decision (recommended):** Procrastinate. It gives the app a real job system with no new infrastructure, keeps the
idle cost at the Cloud SQL instance only, and fits Cloud Run's pay-per-execution model. If you prefer Celery for
name recognition, section 3.5 lists what changes; the pipeline code does not.

### 3.2 Queues and tasks

| Task | Queue | Triggered by | Does | Typical time |
| --- | --- | --- | --- | --- |
| `run_analysis(run_id)` | `analysis` | `POST /runs/{id}/start` | Stages 1–4 and the draft overview; then defers `run_modelling` | 10–30 s |
| `run_modelling(run_id)` | `meridian` | `run_analysis` on success | Stages 5–11 | Quick 5–10 min, Full 30–60 min (measured in M5) |
| `fit_forecast(forecast_id)` | `meridian` | A new budget plan | Refits Meridian on history + plan window (Meridian only predicts inside its fitted window) | 1 fit |
| `run_scenario(scenario_id)` | `interactive` | A scenario edit | Expected sales for an alternative plan on a fitted forecast model | Seconds when warm |
| `run_optimisation(optimisation_id)` | `interactive` | The optimiser form | `BudgetOptimizer` on the run's fitted model | Seconds to minutes (measured in M2) |

### 3.3 Reliability rules

1. **Postgres is the system of record.** Tables `runs`, `stages`, `forecasts`, `scenarios`, `optimisations` hold
   status and progress. The API never asks the queue for results.
2. **One fit at a time.** `meridian` jobs share `lock="meridian"`, so they run one after another even if two workers
   start. Each run uses `queueing_lock=f"run-{id}"`, so it cannot be queued twice. At most 3 queued Meridian jobs;
   the next request gets 429 with the queue position.
3. **Fits run in a spawned child process** (`ProcessPoolExecutor(max_workers=1, mp_context="spawn",
   max_tasks_per_child=1)`). TensorFlow and JAX are never forked, their memory is freed after each fit, and a cancel
   can kill the child.
4. **Cancel:** the API requests an abort on the job. The task checks `should_abort()` between stages and while it
   waits on the child process, then kills the child and marks the run `cancelled`. A job that has not started is
   simply cancelled.
5. **Heartbeats:** a running task writes `heartbeat_at` every 30 s. When the API reads a `running` run whose
   heartbeat is older than 5 minutes, it marks it `failed: worker lost`. No periodic process is needed.
6. **Retries:** none for Meridian tasks (cost guard); one retry for `run_analysis`. Every task starts by loading its
   row and exits when the status is not `queued`, so a redelivered job does no work twice.
7. **Progressive results:** each stage writes its section JSON to storage and its status to `stages` as soon as it
   ends. The UI polls run status every 3 s.
8. **Hand-over between tasks:** `run_analysis` stores the prepared tables (`tables/*.parquet`) and the confirmed data
   roles; `run_modelling` starts from them, so it never re-reads the workbook.

Exact Procrastinate API names (`configure(lock=…, queueing_lock=…)`, `cancel_job_by_id(…, abort=True)`,
`should_abort()`, `run_worker(wait=False)`) are checked against the pinned version's docs in M3.

### 3.4 Where workers run

| Worker | Local (`docker compose` or `Procfile.dev`) | Google Cloud |
| --- | --- | --- |
| `analysis` | Long-running process | Cloud Run Job `mmm-worker-analysis`, 2 vCPU / 4 GiB, burst, timeout 30 min |
| `meridian` | Long-running process, one job at a time | Cloud Run Job `mmm-worker-meridian`, 8 vCPU / 16 GiB, burst, timeout 4 h, no retries |
| `interactive` | Long-running process with a warm model cache (LRU, 2 models) | Cloud Run Job `mmm-worker-interactive`, 4 vCPU / 8 GiB, keeps its model cache warm and exits after 15 min idle |

- One command for all: `mmm-studio worker --queue meridian [--burst] [--idle-exit 900]`.
- **Waking a worker in the cloud:** after deferring a job, the API starts an execution of that queue's Cloud Run Job
  unless a worker for the queue has a heartbeat younger than 60 s. Starting a second worker by accident is harmless:
  both drain the queue, and the `meridian` lock keeps fits sequential.
- The executions run without overrides, so the API service account only needs `roles/run.invoker` on the three jobs.
- **First scenario of a session:** the interactive worker has to start and load the model (expected 30–60 s; measured
  in M5). The UI shows "Warming up the model"; later scenarios take seconds while the worker stays up.

### 3.5 If you choose Celery instead

- Broker: Redis 7 in `docker compose`; Memorystore for Redis or Valkey in the cloud, reached through Direct VPC egress
  from the service and the workers. (Celery ≥ 5.5 can also use Pub/Sub as broker; its support for `revoke` would need
  checking.)
- Workers: Cloud Run worker pools (check GA status and pricing first), scaled 0 ↔ 1 by CREMA on a metric that counts
  queued **and running** tasks, or kept at one instance (always-on cost).
- Settings: `task_acks_late=True`, `worker_prefetch_multiplier=1`, `visibility_timeout` above `task_time_limit`,
  prefork with `worker_max_tasks_per_child=1` for the `meridian` queue, solo pool for `interactive`, no TensorFlow
  import in the worker's parent process, `task_ignore_result=True` (Postgres stays the system of record).
- Code that changes: `jobs/app.py`, `jobs/tasks.py` and the `defer` calls in the API. `core/` and `pipeline/` stay
  as they are, because they import no queue framework.

---

## 4. Architecture

```text
Browser ──HTTPS──▶ Cloud Run service "mmm-studio"  (europe-west3, scales to zero)
                   ├─ frontend: nginx :80, React SPA, proxies /api/v1 → localhost:8000
                   └─ api: FastAPI :8000, slim image (no TensorFlow): upload, validation, data roles,
                           runs, sections, downloads; defers jobs and wakes workers
                          │ SQL                                  │ run_job (no overrides)
                          ▼                                      ▼
                   Cloud SQL PG16  ◀── jobs, status, ──  Cloud Run Jobs (worker image: Meridian, TF/JAX)
                   app tables +        heartbeats        ├─ mmm-worker-analysis     2 vCPU / 4 GiB, burst
                   Procrastinate                         ├─ mmm-worker-meridian     8 vCPU / 16 GiB, burst, 1 fit at a time
                   queue tables                          └─ mmm-worker-interactive  4 vCPU / 8 GiB, warm cache, idle exit
                                                                 │ objects
                                                                 ▼
                   GCS bucket: runs/{id}/input.xlsx · tables/*.parquet · sections/*.json · model/*.binpb
                               reports/*.html · exports/*.csv · forecasts/{fid}/…

Secret Manager: DATABASE_URL, APP_ACCESS_CODE   Artifact Registry: frontend, api, worker images (Cloud Build)
```

Locally the same processes run in `docker compose`: `postgres`, `api`, `worker-analysis`, `worker-meridian`,
`worker-interactive` and `web` (Vite), with storage in `./.app_data/`.

**Key design decisions:**

1. **Results are data, not pages.** The pipeline writes one JSON file per section. A section is a list of blocks:
   `markdown`, `kpis`, `chart` (a Plotly figure), `table`, `callout`. The frontend has one generic
   `SectionRenderer`, and a new analysis needs no frontend change. Pages that need input (Import, Optimiser,
   Forecast & scenarios) are custom React pages.
2. **The API never imports Meridian.** Everything that touches a fitted model runs on a worker, so the API image stays
   small, cold-starts in seconds and cannot run out of memory on a scenario.
3. **Framework-free core.** `core/` (analytics) and `pipeline/` (stages → sections) import neither FastAPI nor the
   queue. The offline CLI, the workers and the tests all call them directly.
4. **Progressive results.** Analysis sections appear within seconds of starting, even while another run's Meridian
   fit is in progress, because analysis has its own queue and worker.
5. **Data roles are confirmed, not guessed silently.** Channel roles, the flighted channel, commission channels,
   hold-out windows and regime breaks are inferred from the workbook, shown to you, and frozen into the run
   (section 7).

---

## 5. Repository layout

```text
src/app/
├── PLAN.md                         # this file
├── README.md                       # run locally, deploy, architecture, costs
├── docker-compose.yml              # postgres, api, worker-analysis, worker-meridian, worker-interactive, web
├── Procfile.dev                    # the same processes without Docker (Homebrew Postgres + honcho)
├── .env.example
├── .gcloudignore
├── backend/
│   ├── pyproject.toml              # own uv project; base = API, extra "worker" = Meridian, statsmodels, scipy, plotly
│   ├── uv.lock
│   ├── Dockerfile                  # targets: api (slim) and worker (Meridian); build context is src/app/
│   ├── src/mmm_studio/
│   │   ├── settings.py             # pydantic-settings: STORAGE=local|gcs, WAKE_WORKERS=none|cloudrun, …
│   │   ├── cli.py                  # mmm-studio api | worker | migrate | run --input x.xlsx --out dir (offline)
│   │   ├── core/                   # analytics, ported from notebooks 01–05 and tchibo_mmm (section 6)
│   │   │   ├── workbook.py         # read + validate the .xlsx: sheets, columns, types, row limits
│   │   │   ├── schema.py           # Schema (channels, roles, flighted and commission channels, windows) + inference
│   │   │   ├── tables.py           # long → wide, pivots, missing days, send-out dates
│   │   │   ├── calendar.py         # holidays(country), Black Week, Christmas, Fourier terms, trend
│   │   │   ├── metrics.py          # MAE/mean, RMSE, R², MAPE, bias, naive baselines
│   │   │   ├── eda/                # quality, target, channels, spend, efficiency, correlations, catalog, readiness
│   │   │   ├── preparation.py      # cost handling, shift scan, saturation, features, OLS importance (HAC)
│   │   │   ├── ols.py              # OLS ladder, two hold-outs, walk-forward CV
│   │   │   └── meridian/           # imported only by the worker image
│   │   │       ├── spec.py         # MMMConfig, input data, priors, model
│   │   │       ├── fit.py          # sampling profiles; fit in a spawned child process
│   │   │       ├── analysis.py     # fit table, ROI, contribution, adstock, response curves, R-hat, health card
│   │   │       ├── optimise.py     # BudgetOptimizer, catalog what-if
│   │   │       └── forecast.py     # plan validation, example plan, refit on plan window, scenarios, back-test
│   │   ├── charts/                 # Plotly template (case-study palette) + builders: line_with_band, heatmap, …
│   │   ├── report/                 # Section / Block dataclasses → JSON; insight templates filled with numbers
│   │   ├── pipeline/               # RunContext, stage registry and profiles, s01–s15 (call core, emit sections)
│   │   ├── jobs/                   # Procrastinate app, tasks, worker loop (burst, idle exit), heartbeats,
│   │   │                           # model cache, wake-up (none | Cloud Run run_job)
│   │   ├── api/                    # FastAPI app, routers under /api/v1, schemas, dependencies (access code)
│   │   ├── db/                     # SQLAlchemy models, session, create_all
│   │   └── storage.py              # Storage protocol: LocalStorage | GCSStorage
│   └── tests/
│       ├── fixtures/case_study.xlsx
│       ├── golden/case_study.json  # reference numbers from the notebooks (section 6.3)
│       ├── core/                   # unit + golden parity tests per module, no database
│       ├── test_pipeline_fast.py   # stages 1–4 on the fixture; every section renders, numbers match golden
│       ├── test_api.py             # Postgres; upload → roles → start → sections; cancel; access code; 429
│       ├── test_jobs.py            # locks, worker lost (stale heartbeat), abort, no double run
│       └── test_meridian_smoke.py  # @slow: tiny sampling, model stages end to end
├── frontend/
│   ├── package.json, vite.config.ts, tsconfig*.json, index.html
│   └── src/
│       ├── main.tsx, App.tsx, theme.ts
│       ├── api/                    # client.ts (axios), runs, sections, forecasts, scenarios, optimiser, types
│       ├── components/
│       │   ├── Layout.tsx          # app bar (run switcher, status chip) + left navigation
│       │   ├── RunProgress.tsx     # queue position, stage stepper with timings and errors
│       │   ├── UploadDropzone.tsx
│       │   ├── DataRoles.tsx       # confirm inferred channel roles, windows and breaks
│       │   └── blocks/             # SectionRenderer, KpiRow, ChartBlock (lazy Plotly, renders on scroll),
│       │                           # TableBlock, MarkdownBlock, Callout, ChannelChip
│       └── pages/
│           ├── Import.tsx          # wizard: upload → confirm data roles → profile + start; run history
│           ├── Overview.tsx
│           ├── DataQuality.tsx, Explore.tsx (tabs), Preparation.tsx
│           ├── Model.tsx, Drivers.tsx, Benchmark.tsx
│           ├── Optimizer.tsx, Forecast.tsx (plan fit + scenario planner)
│           ├── Reports.tsx         # Meridian HTML reports + downloads
│           └── Brief.tsx           # answers to the brief, Google Cloud MLOps, limitations, next steps
└── deploy/
    ├── deploy.sh                   # APIs → SAs → bucket → Cloud SQL → images → migrate → jobs → service → seed
    └── nginx.conf                  # /api/v1 proxy, SPA fallback, gzip for JSON, client_max_body_size 12m
```

Changes outside `src/app/`: `.gitignore` (add `.app_data/` and `src/app/frontend/node_modules/`), a link from the root
`README.md`, and `.github/workflows/app-ci.yml`. **No change** to the root `pyproject.toml`, `uv.lock`,
`src/tchibo_mmm/` or the notebooks.

---

## 6. Porting the notebooks into the app

### 6.1 Rules

- **Port, don't import.** Each function takes explicit inputs (`Schema`, `RunConfig`, DataFrames) and returns data
  (DataFrames, dataclasses). It never draws a figure, prints, or reads or writes a file.
- **No module-level state.** Channel lists, colours, paths and windows come from `Schema` and `RunConfig`. Today they
  are import-time globals (`PAID_CHANNELS`, `CHANNEL_COLORS`, `PLAN_COLUMNS`, `PROJECT_ROOT/outputs`,
  `HOLDOUT_Q4_2024`), which a long-running worker serving different workbooks cannot use.
- **Charts are rebuilt in Plotly** in `charts/` from the returned data. Chart titles state the takeaway, as in the
  notebooks.
- **Narrative text** comes from `report/` templates filled with computed numbers, so it stays true for a new workbook.
- **Meridian, TensorFlow and JAX** are imported only under `core/meridian/`.

### 6.2 Inventory

About **2,000 lines of analysis code live only in the notebooks**, against 688 in `tchibo_mmm`. Notebook 01b alone has
1,154 lines and 16 helper functions, and does not import `tchibo_mmm` at all. v2 only planned to generalise the
package, so most of this table was missing.

| Reference | Code | What is ported | Into `core/` |
| --- | ---: | --- | --- |
| `tchibo_mmm/data.py`, `features.py`, `evaluate.py` | 238 | Sorting, pivots, wide table, send-out dates, calendar and seasonality controls, Meridian table, metrics, baselines | `tables`, `calendar`, `metrics`, `meridian/spec` |
| `tchibo_mmm/mmm.py`, `forecast.py` | 365 | Model spec, ROI priors, fit, fit and ROI tables, persistence, plan validation, example plan, refit forecast, scenarios | `meridian/*` |
| `tchibo_mmm/plotting.py` | 84 | Palette and channel colours only; the matplotlib style is dropped | `charts/` |
| Notebook 01 | 219 | STL decomposition, shares, lagged correlations, catalog event study and halo, efficiency KPIs | `eda/*` |
| Notebook 01b | 1,154 | Target profile, stationarity, ACF, same-weekday baseline, extreme days, calendar design, residualised correlations, clustered heatmaps (scipy), VIF, LOWESS spend response, regime changes, catalog decay, natural experiment and circulation, readiness | `eda/*` |
| Notebook 02 | 154 | Adstock illustration, shift scan, saturation, adjusted-R² ladder, OLS importance with HAC errors, Meridian roles | `preparation` |
| Notebook 03 | 279 | Model-selection grid, prior sensitivity, hold-out scores, R-hat, health card (`meridian.analysis.review`), Summarizer HTML, `BudgetOptimizer`, catalog what-if | `meridian/analysis`, `meridian/optimise` |
| Notebook 04 | 95 | OLS-1 / OLS-2 forecasts, weekday-mean baseline, two hold-outs, walk-forward CV | `ols` |
| Notebook 05 | 103 | Leak-free back-test, example plan, forecast and scenario data | `meridian/forecast` |

Appendix B maps every figure to its page; each figure is ported together with the function that computes its data.

### 6.3 Parity with the notebooks: golden tests, not notebook runs

v2 proved parity by running notebooks headless in tests. That ties the app to the notebooks and needs Meridian in CI.
Instead, the reference numbers are recorded **once** (from the notebooks' printed output, `outputs/meridian_metrics.json`
and the root `PLAN.md`) in `tests/golden/case_study.json`, and the app's tests compare against them:

| Check | Reference | Tolerance |
| --- | --- | --- |
| Rows · days · TV missing days | 8,740 · 732 · 38 | exact |
| Mean daily sales | 787 k€ | printed precision |
| Catalog cost vs sales, lag 0 / lag 3 | −0.01 / 0.40 | printed precision |
| Naive baselines (constant · weekday · lag 7 · lag 1) | 26.1 · 24.9 · 26.0 · 19.1 % | printed precision |
| Digital-channel VIF | 1.4–2.4 | printed precision |
| Halo uplift (SEO · Social · Direct · Affiliate · SEA) | +20 · +20 · +17 · +14 · +11 % | printed precision |
| OLS ladder, Q4 hold-out and walk-forward | root `PLAN.md` §2.6 table | printed precision |
| Meridian Q4 hold-out MAE/mean, Full profile | 8.53 % | ± 0.5 pp, same ROI ranking, max R-hat < 1.01 |
| Meridian Q3 forward hold-out MAE/mean | 10.98 % | ± 1 pp |

Fast checks run on every CI build. The Meridian checks run in the `@slow` suite and in M2 acceptance.

---

## 7. Data roles: inferred, confirmed, frozen

On upload the API validates the workbook and infers a `Schema`. The Import wizard shows it in **Confirm data roles**,
where you can change any value before starting. The confirmed schema is stored with the run and drives every stage.

| Role | Inferred from the upload (case-study value is the default) |
| --- | --- |
| Paid and unpaid channels | Paid = channels with any cost > 0; unpaid = the rest. Order: by total cost (colours follow the channel) |
| Flighted channel (catalog logic: in-home shift, binomial decay, send-outs, event study, halo) | Named "catalog", else the paid channel with spend on < 10 % of days. If there is none, catalog sections are skipped with a note and no shift is applied |
| Commission channels (entered as controls) | Named "affiliate" |
| Hold-out window | The last calendar quarter with ≥ 60 days of data, through the last date (gives the case-study window 2024-10-01 → 2025-01-01) |
| Robustness window | The quarter before the hold-out, trained only up to its start |
| Regime breaks (Social 1 Aug 2024, Affiliate commission Feb 2024) | Change-point scan on 28-day rolling spend and commission rate; a break is reported when the level ratio is > 2 |
| Example plan | Starts the day after the history ends, 6 weeks long, send-outs on the usual weekday at the median cadence |
| Holidays | `country="DE"` |

---

## 8. Pipeline, stages and run profiles

| # | Stage | Content | Queue | Meridian fits | Quick | Full |
| --- | --- | --- | --- | --- | :---: | :---: |
| 1 | validate | Sheets, columns, types; confirmed schema loaded | analysis | – | ✅ | ✅ |
| 2 | s01–s08 | Quality, target, channels, spend, efficiency, correlations, catalog, readiness and answers | analysis | – | ✅ | ✅ |
| 3 | s09 | Preparation (wide table, cost handling, features, OLS channel importance, Meridian input) | analysis | – | ✅ | ✅ |
| 4 | s10 | OLS benchmark + walk-forward CV (adds the Meridian row after stage 6) | analysis | – | ✅ | ✅ |
| 5 | model selection + sensitivity | Catalog shift k ∈ {1, 2, 3} × affiliate media/control; 3 prior variants | meridian | 9 fast | – | ✅ |
| 6 | final fit | Selected config, convergence, health card, accuracy, residuals | meridian | 1 | fast | full |
| 7 | robustness | Second forward hold-out | meridian | 1 fast | – | ✅ |
| 8 | s12–s13 | ROI, contribution, adstock, response curves, optimiser, catalog what-if, HTML reports | meridian | – | ✅ | ✅ |
| 9 | back-test | History up to the hold-out start, plan = actual hold-out spend | meridian | 1 | – | full |
| 10 | forecast | Example plan, forecast + standard scenarios; saves the forecast model | meridian | 1 | fast | full |
| 11 | s15 | Overview KPIs, executive summary, answers to the brief (draft after stage 4, final after stage 10) | both | – | ✅ | ✅ |

- **Quick** (default): 2 fast fits. The Q4 hold-out still gives the headline accuracy.
- **Full**: reproduces every notebook result (9 + 1 fast exploration fits, 3 full fits).
- **Laptop timings** (Apple Silicon, from the root PLAN.md): fast fit ≈ 53 s, full fit ≈ 142 s. On Cloud Run x86 CPUs
  this is expected to be 2–4× slower, so Quick ≈ 5–10 min and Full ≈ 30–60 min. **This is measured first in M5.**
- Sampling settings stay editable under *Advanced* (chains, adapt, burn-in, kept draws).
- Only `validate` and `final fit` are critical. Any other stage that fails is marked failed with its error, and the
  run continues.
- Every run records its config, confirmed schema, seed, Meridian version and app version (the git SHA is passed to the
  image as a build argument, because the image has no `.git`).

---

## 9. API (all under `/api/v1`)

| Method | Path | Purpose | Guard |
| --- | --- | --- | --- |
| POST | `/runs` | Multipart upload of the `.xlsx` (≤ 10 MB). Validates it, infers data roles, returns a **draft** run (201) | code |
| PATCH | `/runs/{id}` | Edit data roles, profile and advanced settings of a draft run | code |
| POST | `/runs/{id}/start` | Queues analysis, then modelling. 202 with queue position; 429 when the Meridian queue is full | code |
| GET | `/runs` · `/runs/{id}` | List runs; status, queue position, stages, summary | – |
| POST | `/runs/{id}/cancel` · `/runs/{id}/activate` · DELETE `/runs/{id}` | Abort the jobs; mark as active model; delete the run and its files | code |
| GET | `/runs/{id}/sections` · `/runs/{id}/sections/{key}` | Section index (with availability); section JSON | – |
| GET | `/runs/{id}/artifacts` · `/runs/{id}/artifacts/{name}` | File list; download (in GCS mode, a redirect to a signed URL) | – |
| GET | `/runs/{id}/plan-template?days=42` | Example plan CSV that starts the day after the history | – |
| POST | `/runs/{id}/forecasts` | Plan as CSV or JSON, validated like `validate_plan`. 202; a `meridian` job refits on the plan window | code |
| GET | `/forecasts/{fid}` | Status, daily and weekly forecast, intervals | – |
| POST | `/forecasts/{fid}/scenarios` | Alternative plan on the same window (edits, channel multipliers, skipped send-outs). 202; `interactive` job | code |
| GET | `/scenarios/{sid}` | Status and result | – |
| POST | `/runs/{id}/optimisations` | Budget, fixed or flexible, per-channel bounds. 202; `interactive` job | code |
| GET | `/optimisations/{oid}` | Status and result | – |
| POST | `/auth/check` | Validates the access code (rate-limited) | – |
| GET | `/health` | Liveness and startup probe | – |

**Data model** (app tables; Procrastinate manages its own `procrastinate_*` tables):

- `runs`: id, name, filename, sha256, profile, config, schema, status (draft, queued, running, succeeded, failed,
  cancelled), job ids, heartbeat_at, summary, error, versions, is_active, timestamps.
- `stages`: run_id, key, status, started_at, finished_at, error.
- `forecasts`: id, run_id, plan name, status, plan path, summary, job id, heartbeat_at, error, timestamps.
- `scenarios`, `optimisations`: id, parent id, inputs, status, result path, error, timestamps.

Artifacts live in storage, not in the database.

---

## 10. Frontend: pages and navigation

| Group | Page | Content |
| --- | --- | --- |
| – | **Import** | Wizard: drag-and-drop upload → **Confirm data roles** → profile (Quick/Full) and advanced settings (hold-out, catalog shift, commission channels, ROI priors from lift tests, sampling) → Start. Live progress with queue position and stage stepper; run history with the active run starred |
| – | **Overview** | KPI tiles: hold-out MAE/mean vs the 15 % target with a pass badge, R², RMSE, total sales and spend, media-driven share, best ROI channel. Five key findings, the fit chart, the top optimiser move |
| Understand | **Data quality** | Workbook summary, data dictionary (`info` sheet), metric availability, missing days, value ranges, quality summary |
| | **Explore** | Tabs: Target · Channels · Spend · Efficiency · Correlations · Catalog · Readiness. All 01/01b charts and tables |
| | **Preparation** | Wide table preview and download, cost handling, features, channel importance, challenges, Meridian input roles and download |
| Model | **Meridian model** | Regression equation and spec, model selection, sensitivity, R-hat and health card, accuracy, fit and hold-out charts, residuals, robustness |
| | **Drivers & ROI** | ROI with intervals, contribution, marginal ROI, signal badges, weekly decomposition, adstock, response curves |
| | **Benchmark** | Naive / OLS / Meridian on two hold-outs, walk-forward CV, takeaways |
| Plan | **Optimiser** | Interactive: total budget, fixed or flexible, per-channel bounds → allocation chart and table, uplift. Catalog what-if |
| | **Forecast & scenarios** | Back-test with coverage. **Step 1, fit the plan window:** generated example, CSV upload or weekly grid → one refit (minutes). **Step 2, scenarios on that window:** edits and multipliers, seconds each; commission channels (Affiliate) are locked because they are controls. Up to 4 scenarios compared, CSV download |
| Reports | **Reports & downloads** | Meridian summary, optimisation and health card in sandboxed iframes; all files for the run |
| | **Answers to the brief** | Every brief question with its computed answer, the Google Cloud MLOps mapping (this app as Level 0→1), limitations, next steps |

**UI rules:**

- The theme copies the catalog's `theme.ts` structure. Channel colours are served per run, so chips and charts match.
- The Plotly chunk loads lazily, and charts render when scrolled into view.
- plotly.py 6 serialises NumPy arrays as base64 typed arrays (`bdata`). The plotly.js bundle is pinned to a version
  that reads them, and a contract test renders one figure of each builder.
- Light mode only, like the catalog.
- No Tchibo logo or brand colours. It is a case-study tool, labelled as such.

---

## 11. Google Cloud deployment

`deploy/deploy.sh` is adapted from the catalog's `deploy/run/deploy.sh`. It is idempotent and safe to re-run, needs no
local Docker, and has these subcommands: `all` (default), `images`, `jobs`, `service`, `seed`, `secrets`.

| Step | What | Detail |
| --- | --- | --- |
| 1 | Prerequisites | `GCP_PROJECT_ID` is **required**; there is no fallback to the active gcloud project (section 13) |
| 2 | APIs | run, sqladmin, artifactregistry, cloudbuild, secretmanager, storage, iam, cloudresourcemanager, logging, monitoring |
| 3 | Service accounts | API `mmm-studio-api`: cloudsql.client, secretmanager.secretAccessor, logWriter, `storage.objectAdmin` **on the bucket only**, `run.invoker` **on the three jobs only**. Worker `mmm-studio-worker`: cloudsql.client, secretmanager.secretAccessor, logWriter, metricWriter, `storage.objectAdmin` on the bucket. Build `mmm-studio-build`: run.builder (catalog's workaround) |
| 4 | Bucket | `${PROJECT}-mmm-studio`, uniform access, same region |
| 5 | Cloud SQL | PG16, `db-f1-micro`, zonal, backups. Small connection pools (API 5, each worker 2). Password kept only in the `DATABASE_URL` secret (catalog logic reused) |
| 6 | Images | Cloud Build on `e2-highcpu-8`: frontend (nginx), api (slim), worker (Meridian, ≈ 3 GB) |
| 7 | Migrate | One execution of `mmm-studio migrate`: app tables + Procrastinate schema |
| 8 | Jobs | `mmm-worker-analysis`, `mmm-worker-meridian`, `mmm-worker-interactive` (sizes and timeouts in section 3.4), worker image, Cloud SQL attached, max retries 0 |
| 9 | Service | YAML like the catalog's: two containers, `container-dependencies`, `invoker-iam-disabled` (the formare org enforces domain-restricted sharing), api 1 vCPU / 1 GiB, max 2 instances, startup CPU boost |
| 10 | Seed | Uploads `data/DataScienceMarketingUseCase.xlsx` through the API and starts one **Full** run. This seeds the demo and smoke-tests the cloud path |
| 11 | Summary | URL, log commands, cost note |

**Cost estimate** (on-demand list prices; to confirm with the pricing calculator for europe-west3):

| Item | Cost |
| --- | --- |
| Idle | Cloud SQL `db-f1-micro` ≈ €9–10 per month. Cloud Run service and jobs scale to zero; no broker; GCS and Artifact Registry well under €1 |
| Per run (8 vCPU / 16 GiB, about €0.6 per hour) | Quick ≈ €0.05–0.10; Full ≈ €0.30–0.60 |
| Interactive use (4 vCPU / 8 GiB, about €0.3 per hour) | Billed while the warm worker is up, including the 15 min idle tail |
| With Celery instead (section 3.5) | Add Memorystore ≈ €30–40 per month, plus always-on or autoscaler costs for workers |

**Guard rails:** one Meridian fit at a time and at most 3 queued; access code on every action that costs money;
upload size and row limits; no Meridian retries; job timeouts; optional budget alert, if you have billing permissions.

---

## 12. Milestones and acceptance criteria

| # | Milestone | Output | Done when |
| --- | --- | --- | --- |
| M1 | Foundations | `backend/` uv project, fixture and golden file, `core/` workbook, schema, tables, calendar, metrics; chart theme; report model | Schema inference returns the case-study channels and windows; first golden checks pass |
| M2 | Core port + pipeline + offline CLI | All of section 6.2 ported; s01–s15; `mmm-studio run --input … --out …` writes every section without a database | All fast golden checks pass; Full profile Meridian checks pass (8.53 % ± 0.5 pp, same ROI ranking); optimiser timing measured |
| M3 | Jobs + API | Postgres, Procrastinate, three workers, heartbeats, abort, all endpoints, `docker compose up` | `pytest` green against Postgres, including locks, cancel and worker-lost tests; upload → sections works in compose |
| M4 | Frontend | Layout, Import wizard, SectionRenderer, all pages, interactive Optimiser and Forecast & scenarios | `npm run build` green; every figure in Appendix B renders; screenshots reviewed (playwright-core recipe from the catalog) |
| M5 | Cloud | `deploy.sh`, three jobs, wake-up, timing check first | The seeded Full run succeeds in the cloud; Quick < 15 min; warm scenarios < 10 s; first scenario < 90 s |
| M6 | Docs + CI | `src/app/README.md`, `app-ci.yml`, link from the root README | CI green on a PR |
| M7 | Optional | Gemini narratives; forecast result check; Cloud Run GPU job; Meridian Scenario Planner export; Vertex AI Pipelines | When you ask for them |
| M8 | Optional | Notebooks import `mmm_studio.core` instead of `tchibo_mmm` (one source of modelling code) | When you ask for it |

**Overall acceptance:** uploading the workbook alone produces every chart, table and insight listed in Appendix B,
from the app's own code. Analysis sections appear within about 30 s of starting. A budget plan can be forecast,
compared across scenarios and optimised. The app runs with `docker compose up` and on Cloud Run, and nothing outside
`src/app/` changes except `.gitignore`, the root README link and the CI workflow.

---

## 13. Open decisions (your call)

1. **Task queue** (before M3). Recommended: Procrastinate on the app's Postgres (section 3.1). Alternative: Celery +
   Redis (section 3.5), which adds Memorystore and an always-on or autoscaled worker in the cloud.
2. **GCP project** (before M5). Recommended: `formare-dev`, the project the catalog's Cloud Run path targets. The
   active gcloud configuration points at `gcp-evolve-energy`, which looks like a client project, so the script will
   never default to it.
3. **Region.** Recommended: `europe-west3` (Frankfurt), close to Tchibo in Hamburg. The alternative is
   `europe-west4`, as used for the catalog.
4. **Access.** Recommended: anyone with the link can browse the demo run; uploads, forecasts, optimiser runs and
   deletes need an access code. Alternatives: fully private (IAP / your Google account only), or the catalog's
   Google sign-in.
5. **Who runs the deploy.** `deploy.sh` grants IAM roles and creates Cloud SQL, which costs about €10 per month. In
   the catalog project the auto-mode classifier blocked IAM grants, so you may need to run it yourself or approve it
   explicitly.
6. **Optional extras (M7, M8).** Left out unless you want them.

---

## Appendix A: Review round 1 (v1 → v2)

The first draft was reviewed against the catalog's own roadmap lessons, Cloud Run limits, the notebooks' actual code
and this laptop's setup. Rows 1, 4, 6 and 10 were revised again in round 2 (Appendix A2).

| # | Draft v1 | Problem found | v2 resolution |
| --- | --- | --- | --- |
| 1 | Pipeline in FastAPI `BackgroundTasks` | The catalog roadmap rejects it: jobs die with the instance and nothing retries them. Cloud Run also throttles CPU after the response | Cloud Run Job, same image; subprocess runner locally → **v3: task queue (A2 #4)** |
| 2 | Every upload re-runs all notebooks (13 Meridian fits) | 30–60 min before you see anything | Quick/Full profiles; fast sections first; Meridian sections unlock progressively |
| 3 | Channel lists, catalog, affiliate and dates copied from the case study | Any other workbook breaks or gives wrong results | Schema inference with case-study defaults → **v3: confirmed by the user (A2 #7)** |
| 4 | Light API image + heavy worker image | Every scenario becomes a job round-trip (1–2 min), not "seconds" | One image; lazy Meridian import in the API; model LRU cache → **v3: warm interactive worker, slim API (A2 #5)** |
| 5 | All results (chart JSON) in Postgres | Database bloat on `db-f1-micro`; 30 MB model files | Metadata in SQL, artifacts in GCS |
| 6 | Postgres required locally | The Docker daemon is not running on this Mac; Postgres setup slows iteration | SQLite + local folder → **v3: Postgres everywhere (A2 #8)** |
| 7 | Show the notebook PNGs | Static, and not regenerated for a new upload | Every figure rebuilt as an interactive Plotly chart from the upload |
| 8 | Reuse the notebooks' written insights | Numbers go stale for a different workbook | Templates filled with computed values |
| 9 | Charts rebuilt in TypeScript from raw data | Analysis logic duplicated in two languages | The backend emits Plotly figure JSON; one renderer |
| 10 | Free refactor of `tchibo_mmm` | Could silently change notebook results | Backward-compatible defaults + headless notebook test → **v3: port, no refactor (A2 #1–3)** |
| 11 | No access control | A public URL could start paid jobs at will | Access code, one job at a time, size limits, no retries |
| 12 | Deploy to the active gcloud project | It is `gcp-evolve-energy` (client project) | `GCP_PROJECT_ID` required; `formare-dev` recommended |
| 13 | Meridian HTML embedded directly | Same-origin script execution | Sandboxed iframe + CSP that allows only gstatic's Vega scripts |
| 14 | Cloud timings assumed from the laptop (142 s full fit) | Cloud Run x86 CPUs are slower than Apple Silicon | Timing check at the start of M5; editable sampling; GPU job as the fallback |
| 15 | Full Plotly bundle | About 3.5 MB of JavaScript | `plotly.js-cartesian-dist-min`, lazy chunk, render on scroll |
| 16 | Seed by copying `outputs/meridian_final.binpb` | No sections, and the cloud path stays untested | Seed with a real Full run in the cloud |

## Appendix A2: Review round 2 (v2 → v3)

Reviewed on 2026-11-02 against the code in `src/tchibo_mmm/`, the notebooks' code cells, and the goal of a standalone
app with a real background-job system.

| # | v2 | Problem found | v3 resolution |
| --- | --- | --- | --- |
| 1 | App imports `src/tchibo_mmm`; Docker context is `src/` | The app is not independent: its build, dependencies and behaviour are tied to the case-study package and the root `pyproject.toml` | Own uv project and build context in `src/app/`; no import of `tchibo_mmm` (section 1.3) |
| 2 | "`tchibo_mmm` stays the single home of the modelling code"; §5 only generalises the package | About 2,000 of 2,700 lines of analysis code live only in notebook cells (01b: 1,154 lines, no `tchibo_mmm` import). The OLS benchmark, HAC importance, VIF, STL/ACF, event study, model selection, health card, optimiser and back-test had no porting plan | Explicit port inventory into `mmm_studio.core` (section 6.2) |
| 3 | Parity proven by running notebooks headless in tests | Couples the app's CI to the notebooks; needs Meridian and minutes per run | Golden file recorded once, compared by the app's own tests (section 6.3) |
| 4 | Subprocess runner locally, Cloud Run Job in the cloud, 409 when busy | Two execution paths that behave differently; no queueing; a job that dies leaves the run "running" forever; the one-active-job check races | Procrastinate queues on the app's Postgres, same workers everywhere, locks, heartbeats, abort (section 3) |
| 5 | Scenarios and optimiser run inside the API (2 vCPU / 4 GiB, lazy TF import) | The API image carries ≈ 3 GB of TensorFlow; the first scenario pays the import; concurrent requests can run out of memory and take the frontend's backend with them | `interactive` worker with a warm model cache; slim API image that never imports Meridian |
| 6 | Fast stages run inside the 8 vCPU Meridian job | A second upload's EDA waits behind a 60-minute Full run | Separate `analysis` queue and worker |
| 7 | Data roles inferred and applied silently | Heuristics (< 10 % spend days, the name "affiliate", ratio > 2) change the model without you seeing them | **Confirm data roles** step; the confirmed schema is frozen into the run (section 7) |
| 8 | SQLite locally, Postgres in the cloud | API and worker writing at once hit "database is locked"; the queue needs Postgres anyway | Postgres everywhere: `docker compose` or Homebrew |
| 9 | Module globals generalised "with defaults" | `PAID_CHANNELS`, `CHANNEL_COLORS`, `PLAN_COLUMNS`, output paths and the hold-out are import-time constants; a long-running worker serving different workbooks cannot rely on them | Everything flows from `Schema` and `RunConfig` (section 6.1) |
| 10 | Forecast page with an "editable weekly grid" | Every new plan is a full Meridian refit (Meridian predicts only inside its fitted window), so editing the grid cannot be instant; scenarios cannot change Affiliate (a control) | Two-step page: fit the plan window once, then scenarios on that window; commission channels locked |
| 11 | Plotly figure JSON from Python | plotly.py 6 writes base64 typed arrays that older plotly.js bundles cannot read | Pinned bundle and a render contract test |
| 12 | Git SHA recorded per run | The image has no `.git` | SHA passed as a build argument |
| 13 | Stages stored as a JSON list on `runs` | Hard to query and to update per stage | `stages` table |

## Appendix B: Every notebook figure → app page

| App page › tab | Figures (`reports/figures/` · `eda/`) and tables |
| --- | --- |
| Data quality | `eda/metric_availability`, `eda/missing_days` · structural checks, value ranges, quality summary, data dictionary |
| Explore › Target | `01_total_sales` = `eda/target_timeseries`, `eda/target_distribution`, `eda/target_yoy`, `01_weekday_month` = `eda/target_seasonality`, `01_decomposition` = `eda/target_stl`, `eda/target_acf`, `eda/events_lift`, `eda/peak_season` · key figures, extreme days |
| Explore › Channels | `01_sales_vs_cost_share`, `eda/channel_share_growth`, `eda/channel_mix_monthly`, `eda/channel_small_multiples`, `eda/channel_weekday_index` · channel summary |
| Explore › Spend | `eda/spend_vs_sales_share`, `01_costs_by_channel` = `eda/spend_small_multiples`, `eda/spend_vs_sales_time`, `eda/catalog_sendouts`, `eda/spend_weekday_index` · spend summary |
| Explore › Efficiency | `eda/funnel_correlations`, `eda/efficiency_kpis`, `eda/social_regime_change`, `eda/affiliate_commission`, `eda/spend_response` · CPC / CPM / ROAS table |
| Explore › Correlations | `01_corr_matrix` = `eda/corr_matrix_costs` (Pearson/Spearman), `eda/corr_robustness`, `01_corr_lagged` = `eda/lag_correlation`, `01_corr_channel_sales` = `eda/corr_channel_sales`, `eda/corr_cost_vs_channel_sales` · VIF table |
| Explore › Catalog | `01_catalog_event_study` = `eda/catalog_event_study`, `01_catalog_halo` = `eda/catalog_halo`, `eda/catalog_decay`, `eda/catalog_natural_experiment`, `eda/catalog_lift_vs_circulation` |
| Explore › Readiness | MMM readiness table, naive baselines, implications table, answers Q1–Q3 |
| Preparation | `02_catalog_booking_vs_effect`, `02_catalog_adstock_shift`, `02_saturation`, `02_channel_importance` · wide table, cost summary, shift scan, features, adjusted R² ladder, importance table, Meridian roles |
| Meridian model | `03_fit_full_period`, `03_holdout_q4`, `03_residuals` · model selection, sensitivity, R-hat, scores, robustness, health card |
| Drivers & ROI | `03_roi`, `03_contribution_weekly`, `03_adstock`, `03_response_curves` · ROI table with signal badges |
| Optimiser | `03_budget_optimisation`, `03_whatif_catalog` · allocation table, interactive optimiser |
| Benchmark | `04_model_comparison` · hold-out comparison, walk-forward CV, takeaways |
| Forecast & scenarios | `05_backtest_q4`, `05_forecast_2025_01`, `05_scenarios` · back-test metrics and coverage, weekly plan, weekly forecast, scenario table |
| Reports | `meridian_summary.html`, `meridian_optimization.html`, `meridian_health_card.html` · CSV / JSON / model downloads |
| Answers to the brief | All written answers (01 §9, 01b §12, 02 §2–5, 03 §11), Google Cloud MLOps (root PLAN.md Phase 4), limitations and next steps |

`=` marks a figure from notebook 01 and its notebook 01b counterpart, which share one chart in the app.
