# MMM Studio (`src/app/`)

A standalone web app for the Tchibo marketing-mix case study. You upload the Excel workbook, and the app
re-implements notebooks 01–05 in its own code: EDA, feature preparation, an OLS benchmark, a Google Meridian MMM,
an optimiser, and forecasts with what-if scenarios. Results come back as interactive sections. It uses a FastAPI API,
Procrastinate workers on Postgres, and a React SPA.

- **Status (2026-11-02, second audit):** every number the app shows is now computed and matches notebooks 01–05
  (see "Parity with the notebooks" below). Fabricated fallbacks (Q3 hold-out, selection/sensitivity tables, overview
  and brief facts, optimiser ROIs, scenario mROIs, a placeholder forecast) were removed. The UI follows the Tchibo
  design tokens. Fast tests, ruff and `tsc` pass; a full-profile CLI run reproduces notebooks 03–05.
- `PLAN.md` is the design spec (v3).
- The main model is **Meridian**, a Bayesian MMM presented as a regression. OLS is only a benchmark.

## Ground rules

- **The app must stay self-contained.** Never import `tchibo_mmm`. Never read `notebooks/`, `outputs/`,
  `data/processed/` or `reports/` at runtime. The notebooks and `src/tchibo_mmm/` are the frozen case-study
  deliverable, so do not edit them. Outside `src/app/`, only `.gitignore`, a root README link and
  `.github/workflows/app-ci.yml` may change.
- `core/` (analytics) and `pipeline/` (stages → sections) must not import FastAPI or Procrastinate.
- Import Meridian, TensorFlow and JAX **only inside functions** under `core/meridian/`, and keep them lazy.
- **Results are data.** A stage returns a `Section` (`report/sections.py`) made of blocks: `markdown`, `kpis`,
  `chart` (a Plotly figure dict), `table` and `callout`. The frontend `SectionRenderer` renders any section
  generically, so a new analysis is a new section, not a new page.
- Charts are plain Plotly figure dicts built in `charts/builders.py`. The backend never imports plotly. Colours
  and the layout template live in `charts/theme.py`, and channel colours are keyed by display name.
- Before using a library, fetch its docs with `chub` (global CLAUDE.md). Procrastinate (pinned 3.10.0) has no
  chub entry, so use its official docs for that version.
- **No invented numbers.** Every figure in a KPI, callout, caption or table comes from the data or a model result
  (`report/facts.py`, `core/eda/notebook_stats.py`, the Meridian outputs). If a result does not exist yet, the
  section says it is pending; it never shows a default or a number copied from the notebooks.
- **Brand.** Colours follow Tchibo's design tokens from tchibo.de (gold `#8C723B`, blue `#0A63BD`, navy `#004266`,
  coral `#BE2D2F`, warm surfaces) in `frontend/src/theme.ts` and `charts/theme.py`; Figtree stands in for the
  proprietary Tchibo Espresso font. Never embed the Tchibo logo: the header uses an original bean mark and the app is
  labelled an independent case-study prototype.
- Commit only when asked. The repo works on `main`.

## Environment (this Mac)

```bash
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"   # uv, node, psql, chub
unset VIRTUAL_ENV                     # the shell points at another project's venv; uv warns otherwise
export UV_CACHE_DIR=/tmp/uv-cache     # what start.sh uses
```

- Postgres comes from Homebrew, socket `/tmp`, **port 5433**. It already has the databases `mmm_studio` (dev) and
  `mmm_studio_test` (tests). The default URL is `postgresql:///mmm_studio?host=/tmp&port=5433`.
- The Docker daemon is usually not running, so use native mode.
- The backend `.venv` has the `worker` extra installed. The API (`mmm_studio.main`) is slim and imports without
  `statsmodels`, `scipy`, `holidays`, or `meridian`, while the worker and test suite require `uv sync --extra worker`.

## Commands

```bash
# Backend (from src/app/backend)
uv sync --extra worker
uv run pytest tests/ -m "not slow" -q        # fast test suite; needs Postgres (TEST_DATABASE_URL, default mmm_studio_test on :5433)
uv run pytest tests/test_meridian_smoke.py   # includes the @slow smoke MCMC fit (~1 min)
uv run ruff check mmm_studio tests
uv run python -m mmm_studio.cli run --input ../../../data/DataScienceMarketingUseCase.xlsx \
    --out /tmp/mmm_cli --skip-meridian       # offline pipeline: no DB, writes runs/offline/... under --out (~5 s)
#   Without --skip-meridian: --profile full ≈ 15–25 min (notebook-equal sampling + selection grid), quick ≈ 6 min, smoke ≈ 4 min.
#   CLI flags: --input / --workbook, --profile {full,quick,smoke} / --fast / --smoke (default profile is "full").
uv run uvicorn mmm_studio.main:app --reload --port 8000     # API (docs at /docs, routes under /api/v1)
uv run python -m mmm_studio.cli worker --queue meridian     # one per queue: analysis|meridian|interactive (or all), --burst, --idle-exit N
uv run python -m mmm_studio.cli migrate                     # create_all + Procrastinate schema (API/worker also do this at start)

# Frontend (from src/app/frontend)
npm install
npm run dev                          # :5173, proxies /api -> http://localhost:8000 (override with VITE_API_PROXY_TARGET)
./node_modules/.bin/tsc --noEmit -p .  # typecheck without writing files
npm run build                        # tsc -b + vite; rewrites the TRACKED tsconfig.tsbuildinfo, so don't commit that churn

# Everything natively: API :8000, one worker per queue, Vite on :5173 and :8080
./src/app/start.sh                   # from the repo root; Romanian log messages; kills whatever is on 8000/5173/8080
```

## Architecture

```text
backend/mmm_studio/            (flat package, NOT backend/src/mmm_studio as PLAN.md says)
  settings.py   pydantic-settings; every var accepts a bare or MMM_-prefixed name; reads .env, ../.env, ../../.env
  storage.py    ArtifactStorage -> LocalStorage | GCSStorage; get_storage() is lru_cached
  main.py       FastAPI app; lifespan runs init_db() + ensure_procrastinate_schema()
  cli.py        mmm-studio run | api | worker | migrate
  api/          router.py (/api/v1, /health, /auth/check), runs, sections, forecasts (+scenarios), optimiser, exports (artifacts), deps (access code, rate limit)
  db/           SQLAlchemy 2.0 models: runs, stages, forecasts, scenarios, optimisations. create_all only, no Alembic
  jobs/         app.py (Procrastinate app, defer_task, queue counts, stale sweep), tasks.py (5 tasks + LRU cache), worker.py, cloud_run.py (wake-up)
  pipeline/     context.py (RunContext: storage keys, save_section/dataframe/json, cancel + heartbeat), stages.py (s01–s16)
  core/         workbook.py (profile_workbook), schema.py (DataProfile, DataRoleConfig, ChannelConfig), tables.py, calendar.py,
                features.py (notebook controls: weekday/holiday/Christmas/Black Week, Fourier, trend, catalog in-home adstock,
                hold-out windows), metrics.py, preparation.py (nb 02), ols.py (nb 04), eda/* (+ notebook_stats.py: 01b tables),
                meridian/{spec,fit,selection,analysis,optimise,forecast}.py
  report/       sections.py (Section/Block models), narrative.py (formatters), facts.py (computed case facts for narratives)
  charts/       builders.py, theme.py
backend/tests/  core/ unit + golden tests, test_pipeline_fast (CLI), test_api, test_jobs (Postgres), test_meridian_smoke
  fixtures/case_study.xlsx, golden/case_study.json   (reference numbers from the notebooks)
frontend/src/   main.tsx, App.tsx (routes; selected run lives in React state, not the URL), theme.ts (PALETTE, light mode only)
  api/client.ts (all HTTP via native fetch + X-Access-Code from sessionStorage), hooks.ts (TanStack Query + polling), types.ts
  components/Sections/SectionRenderer.tsx (lazy Plotly), Layout/AppShell.tsx (nav, run switcher, cancel), Charts/PlotlyChart.tsx
  pages/        one per nav item; most pages are just useSection(runId, "<id>") + SectionRenderer
deploy/         deploy.sh (GCP formare-dev / europe-west3), nginx.conf (template: ${PORT}, ${API_ORIGIN})
```

**Run lifecycle:**

1. `POST /runs` takes the upload, runs `profile_workbook`, and creates a `draft` Run with its inferred
   `DataRoleConfig`.
2. `PATCH /runs/{id}` confirms the roles, profile and seed.
3. `POST /runs/{id}/start` defers **both** jobs at once:
   - `run_analysis_job` on the `analysis` queue, with lock `analysis:{id}`;
   - `run_meridian_job` on the `meridian` queue, with lock `meridian_singleton`. The start returns 429 when
     `todo + doing` on the meridian queue is ≥ `MMM_MAX_MERIDIAN_QUEUED`.
4. Each stage updates its `stages` row and writes `sections/{id}.json` plus `sections/_index.json`.
5. The UI polls: the run every 2 s, sections every 4 s.

| Stages | Queue | Notes |
| --- | --- | --- |
| `s01_ingest` … `s11_benchmark` | analysis | `s01` is critical. `s11` also writes the draft `overview`, `brief` and `reports` |
| `s12_meridian_fit` | meridian | Critical. Final fit plus the forward (Q3) hold-out refit |
| `s13` … `s15_finalize` | meridian | ROI/optimiser/HTML reports; back-test + example-plan forecast + scenarios; `s15` rewrites `benchmark`, `overview`, `brief`, `reports` |
| `s16_model_selection` | meridian | Full profile only (else `skipped` via `StageSkipped`): 6 selection + 3 sensitivity fast fits; rewrites `meridian_model` |

If a non-critical stage fails, the failure is recorded on its stage row and swallowed (`_run_stage`).

**Section ids used by pages:** `overview`, `data_quality`,
`explore_{target,channels,spend,efficiency,correlations,catalog,readiness}`, `preparation`, `benchmark`,
`meridian_model`, `drivers_roi`, `optimiser`, `forecast_scenarios`, `reports`, `brief`.

**Tasks:**

| Task | Queue | Context |
| --- | --- | --- |
| `run_analysis_job`, `run_meridian_job` | analysis, meridian | `pass_context=True` |
| `run_forecast_job` | meridian | Meridian refit on the plan window |
| `run_scenario_job`, `run_optimiser_job` | interactive | Warm LRU cache of 2 entries, in process memory |

Task names are explicit strings: `mmm_studio.jobs.tasks.<fn>`.

**Storage layout:** everything for a run sits under `runs/{run_id}/`:

| Path | Contents |
| --- | --- |
| `input/workbook.xlsx` | The upload |
| `data/wide_model.parquet` | The modelling panel (full calendar). Older runs used `wide_2023_2024`; read it via `model_panel_key()` |
| `meta/` | `profile.json`, `config.json`, `summary.json`, `meridian_metrics.json` |
| `data/` | `*.parquet` and `*.csv` |
| `sections/` | `*.json` |
| `models/` | `meridian_final.binpb`, `meridian_forecast.binpb` (the example-plan forecast model, reused by scenarios) |
| `reports/` | `*.html` |
| `forecasts/{fid}/`, `scenarios/{sid}/`, `optimisations/{oid}/` | Per-request results |

**Settings:**

- Local storage defaults to `src/app/.app_data`. `start.sh` overrides it to the repo-root `.app_data/`; both are
  gitignored.
- Write and compute endpoints need the `X-Access-Code` header only when `MMM_APP_ACCESS_CODE` is set.

## Testing conventions

- `test_api.py` and `test_jobs.py` **drop and recreate all app tables and truncate the Procrastinate tables** in
  `TEST_DATABASE_URL`. Never point that variable at a database you care about.
- `get_settings`, `get_storage`, `get_engine`, `get_session_factory` and `get_procrastinate_app` are `lru_cache`d.
  After changing env vars, clear their caches (see the `api_env` fixture).
- Use the `smoke` profile or `SMOKE_SAMPLING` in tests. Mark anything that runs a real MCMC fit `@pytest.mark.slow`.
- Parity is checked against `tests/golden/case_study.json`, not by running notebooks. Case-study facts are in that
  file: 8,740 rows, 732 days, 12 channels (7 paid), and the hold-out reference values.

## Code style

- **Python 3.12.** Use ruff (`E`, `F`, `I`; line length 110), `from __future__ import annotations`, module
  docstrings, full type hints, pydantic v2 and SQLAlchemy 2.0 `Mapped[...]`.
- **Spelling.** Backend identifiers use British spelling (`optimiser`, `optimisation`). The frontend route is
  `/optimizer` and the file is `Optimizer.tsx`. Match whatever the file you are editing already uses.
- **TypeScript.** `strict`, `React.FC` components, MUI `sx` styling, colours from `PALETTE` in `theme.ts`.
  Every HTTP call goes in `api/client.ts`, every query hook in `api/hooks.ts`, and every API type in `api/types.ts`.
- **Brand tokens, no logo.** Use the Tchibo colours from `PALETTE` / `charts/theme.py`, never the Tchibo logo; the
  app is labelled an independent case-study prototype.

## Parity with the notebooks

- **Modelling window.** Notebooks 02–05 keep the isolated 2025-01-01 in every model, so `DataRoleConfig` defaults to
  `drop_trailing_isolated_days=False` and `holdout_days=93` (Q4 2024 through 1 Jan 2025). Yearly and monthly views use
  `core/features.complete_years()`.
- **Checked against the notebooks' own outputs** (2026-11-02, full run `run_f1e8df83da`): `outputs/meridian_metrics.json`
  within 0.5 %, `outputs/meridian_holdout_forecast.csv` (93 days) within 0.6 %, `outputs/forecast_2025-01.csv` (42 days)
  within 0.4 %, every table printed by notebooks 01–05, and every figure headline in `reports/figures` (verification
  scripts only; the app never reads these files).
- **Reference numbers** live in `tests/golden/case_study.json` and are asserted by the fast tests (EDA, naive baselines,
  preparation ladder 0.74→0.90, OLS benchmark 34.8/10.7/9.6 % and walk-forward 9.5 %, example plan €8,008,549).
- **Meridian (full profile, seed 0)** reproduces notebook 03 within MCMC noise: Q4 MAE/mean 8.54 % (nb 8.53 %), R² 0.888,
  Q3 forward 10.96 % (nb 10.98 %), ROI SEA 8.40 / Catalog 0.97, optimiser +11.1 % on €121.8M, catalog what-if ratio 0.95,
  and notebook 05's forecast €42.58M with the same scenarios (skip 30 Jan catalog, 20 % Display → SEA, +10 % digital).
- Scenarios run on the fitted forecast model (`model_scenario_from_multipliers`); the marginal-ROI approximation is
  only a fallback when that model is missing and uses the run's own posterior mROIs.

## Resolved issues (verified on 2026-11-02)

1. `backend/Dockerfile` uses `python:3.12-slim` and installs `--extra worker` on the `worker` stage while keeping the `api` stage slim.
2. `deploy.sh` builds separate `api` and `worker` images from `src/app/backend` via temporary Cloud Build configs with explicit `--target api` and `--target worker`.
3. `cloud_run.wake_worker` accepts `"cloudrun"`, `"true"`, `"1"`, and `"yes"`, and `Settings` defaults to `mmm-studio-worker-{analysis,meridian,interactive}`.
4. `docker-compose.yml` runs one worker per queue (`worker`, `worker-meridian`, `worker-interactive`); `start.sh` and `Procfile.dev` do the same, so scenarios never wait behind a Meridian fit.
5. `mmm_studio.main` lazy-imports heavy analytics/model packages so importing the API never loads `statsmodels`, `scipy`, `holidays`, `meridian`, `tensorflow`, or `jax`.
6. The stale-run sweeper only fails runs that have a `running` stage and a stale heartbeat; a run waiting between its analysis and Meridian jobs is left alone.
7. Starting a queued/running run returns 409; a re-run resets stage rows and clears the previous results; cancelling a finished run returns 409.
8. Scenario results carry `delta_sales_pct` and `channel_summary` (the Forecast page used to crash); the optimiser and scenarios return 409 until Meridian results exist.
9. `german_holiday_flags` uses `dateutil.easter` and the real Black Friday (day after the 4th Thursday; it was a week early in 2024); `delayed_binomial_adstock` now peaks at `peak_lag`.
10. `release_stalled_jobs()` (sweeper and worker start-up) fails Procrastinate jobs left in `doing` by a killed worker, so `meridian_singleton` is released; before, one killed Meridian worker blocked every later Meridian job.
11. The response-curve optimiser solves in € millions with analytic PCHIP gradients; on raw euros SLSQP saw a zero gradient and returned the historical allocation (+0.0 %).
12. Scenario multipliers are normalised to channel slugs in the API (a display name such as `"Display"` works); an
    unknown channel or a negative multiplier is a 422. Before, any key other than the slug was silently ignored and
    the scenario reported "+€0".
13. Frontend: Markdown is rendered (it was shown raw), sections poll while a run is active, a temporary drawer gives mobile navigation, the selected run persists, destructive actions are confirmed, tables are labelled scrollable regions with `scope`, and focus is visible. Table headers, row headers and text
    over 40 characters wrap (walk-forward folds are labelled "15 Jan 2024"; tables with ≥ 10 columns use tighter cells),
    so every table fits its card at 1280 px; below a 600 px container each row becomes a labelled card
    (`data-label` + `@container`).
14. EDA results the notebooks print but the app had left out are now computed: sales lost on missing channel-days
    (01b), daily sales by year (01b), average catalog send-out per year and the top-25-days / September contrast
    (01, 01b), and the €118,094/day error budget (01). The catalog event-study peak line is read from the data (it was
    fixed at +3), and the shift scan shows 3 decimals like notebook 02 (0.585, which 2-decimal rounding showed as 0.59).
15. Every figure in `reports/figures` (27 + 34 EDA) now has an app chart with the same analysis: the catalog natural
    experiment is notebook 01b's 42-day gap (September 2023 €473.1k/day vs 2024 €980.4k/day; the matched-Thursday chart
    moved to `catalog_matched_thursdays`), the channel-sales and spend × channel-sales correlation matrices were added,
    adstock and response curves show the 90 % credible band (and the actual-spend dot), and the Meridian accuracy
    table has notebook 03's "All Data" row (R² 0.902). Captions that stated data facts as fixed text (catalog ROI ≈ 1,
    Affiliate CTR > 100 %, weekday peaks, "Thursday send-out") are computed.
