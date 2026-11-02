# MMM Studio (`src/app/`)

A standalone web app for the Tchibo marketing-mix case study. You upload the Excel workbook, and the app
re-implements notebooks 01–05 in its own code: EDA, feature preparation, an OLS benchmark, a Google Meridian MMM,
an optimiser, and forecasts with what-if scenarios. Results come back as interactive sections. It uses a FastAPI API,
Procrastinate workers on Postgres, and a React SPA.

- **Status (2026-11-02):** implemented in commit `6ad7afd add webapp` and audited/fixed so all known deploy, backend,
  frontend/backend contract, and documentation issues are resolved. The fast tests, Meridian smoke test, ruff, and
  `tsc` all pass.
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
    --out /tmp/mmm_cli --skip-meridian       # offline pipeline: no DB, writes runs/offline/... under --out
#   CLI flags: --input / --workbook, --profile {full,quick,smoke} / --fast / --smoke (default profile is "full").
uv run uvicorn mmm_studio.main:app --reload --port 8000     # API (docs at /docs, routes under /api/v1)
uv run python -m mmm_studio.cli worker --queue all          # also: analysis|meridian|interactive, --burst, --idle-exit N
uv run python -m mmm_studio.cli migrate                     # create_all + Procrastinate schema (API/worker also do this at start)

# Frontend (from src/app/frontend)
npm install
npm run dev                          # :5173, proxies /api -> http://localhost:8000 (override with VITE_API_PROXY_TARGET)
./node_modules/.bin/tsc --noEmit -p .  # typecheck without writing files
npm run build                        # tsc -b + vite; rewrites the TRACKED tsconfig.tsbuildinfo, so don't commit that churn

# Everything natively: API :8000, worker on all queues, Vite on :5173 and :8080
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
  pipeline/     context.py (RunContext: storage keys, save_section/dataframe/json, cancel + heartbeat), stages.py (s01–s15)
  core/         workbook.py (profile_workbook), schema.py (DataProfile, DataRoleConfig, ChannelConfig), tables.py, calendar.py,
                metrics.py, preparation.py, ols.py, eda/*, meridian/{spec,fit,analysis,optimise,forecast}.py
  charts/       builders.py, theme.py
  report/       sections.py (Section/Block models), narrative.py (formatters)
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
| `s12_meridian_fit` … `s15_finalize` | meridian | `s12` is critical. `s15` rewrites `benchmark`, `overview`, `brief` and `reports` with the Meridian numbers |

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
| `meta/` | `profile.json`, `config.json`, `summary.json`, `meridian_metrics.json` |
| `data/` | `*.parquet` and `*.csv` |
| `sections/` | `*.json` |
| `models/` | `meridian_final.binpb`, `meridian_forecast.binpb` |
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
- **No Tchibo logo or brand colours.** The app is labelled as a case-study tool.

## Resolved issues (verified on 2026-11-02)

1. `backend/Dockerfile` uses `python:3.12-slim` and installs `--extra worker` on the `worker` stage while keeping the `api` stage slim.
2. `deploy.sh` builds separate `api` and `worker` images from `src/app/backend` via temporary Cloud Build configs with explicit `--target api` and `--target worker`.
3. `cloud_run.wake_worker` accepts `"cloudrun"`, `"true"`, `"1"`, and `"yes"`, and `Settings` defaults to `mmm-studio-worker-{analysis,meridian,interactive}`.
4. `docker-compose.yml` and `frontend/Dockerfile` both use `src/app` as build context, pass `API_ORIGIN: http://api:8080`, and publish the web container on host port `8080`.
5. `.github/workflows/app-ci.yml` installs `--extra worker` before running `ruff` and `pytest`.
6. `mmm_studio.main` lazy-imports heavy analytics/model packages so importing the API never loads `statsmodels`, `scipy`, `holidays`, `meridian`, `tensorflow`, or `jax`.
7. Confirmed `DataRoleConfig` flows into `add_calendar_and_adstock_features`, `compute_preparation_ols_ladder`, `evaluate_ols_suite`, `MeridianSpecConfig.from_role_config`, `validate_plan`, `build_example_plan`, and dynamic scenario labels (`skip_catalog_label`).
8. `overview`, `brief`, `benchmark`, and `optimiser` sections compute KPIs and narrative metrics dynamically from OLS/Meridian outputs rather than hardcoded case-study numbers.
9. `s12_meridian_fit` and `s14_forecast` support isolated child-process fits (`fit_meridian_isolated`) when `ctx.spawn_meridian_child` is enabled (`execute_meridian_job`), raising `InterruptedError` on cancellation.
10. `execute_meridian_job` waits up to 60 s for an active `analysis` job to write `data/wide_2023_2024.parquet` before falling back to running `run_analysis_stages(ctx)`.
11. Stale runs are swept across `list_runs`, `get_run`, `list_sections`, and `list_optimisations`.
12. Backend and frontend contracts match across `KpiItem` (`status` + `tone`), `CalloutBlock` (`tone` + `severity`), `ChartBlock` (`alt_figures` synced to `figure.variants`), `TableBlock` (`format` formatting in `SectionRenderer.tsx`), `/auth/check` (`protected` + `required`), `/runs/{id}/sections` (`status` + `run_status`), `/runs/{id}/forecasts`, and `/runs/{id}/optimisations`.
