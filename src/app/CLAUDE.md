# MMM Studio (`src/app/`)

A standalone web app for the Tchibo marketing-mix case study. You upload the Excel workbook, and the app
re-implements notebooks 01–05 in its own code: EDA, feature preparation, an OLS benchmark, a Google Meridian MMM,
an optimiser, and forecasts with what-if scenarios. Results come back as interactive sections. It uses a FastAPI API,
Procrastinate workers on Postgres, and a React SPA.

- **Status (2026-11-02, second audit):** every number the app shows is now computed and matches notebooks 01–05
  (see "Parity with the notebooks" below). Fabricated fallbacks (Q3 hold-out, selection/sensitivity tables, overview
  and brief facts, optimiser ROIs, scenario mROIs, a placeholder forecast) were removed. The UI follows the Tchibo
  design tokens. Fast tests, ruff and `tsc` pass; a full-profile CLI run reproduces notebooks 03–05.
- **Status (2026-11-03, production hardening):** every finding of the audit in `audit/` (43 issues) is fixed; see
  "Resolved issues" 24. Every route needs the access code when one is set, settings are frozen once a run starts,
  wrong inputs and impossible requests fail loudly, and the Meridian job is chained after the analysis job.
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
- **Fail loudly, never adapt silently.** A short history, an infeasible budget, an unknown plan column, a conflicting
  duplicate row or a failed solver is an error with a plain-words reason (422/409 in the API, a failed stage in a
  job), never a relaxed constraint or a fallback value. `mmm_studio/errors.public_error` strips paths from messages.
- **Every conclusion is computed.** A sentence that states a verdict (target met, which model is better, which
  channel to test) is derived from the numbers it quotes and the run's channel roles (`ChannelNarrative`).
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
- **One Python environment: the repo-root `.venv`.** The root `pyproject.toml` installs the backend there editable,
  with its `worker` extra and `httpx` (group `app`), next to the notebook packages. There is no `backend/.venv`: from
  `src/app/backend` always pass `--project ../../..` to `uv run` (or call `../../../.venv/bin/<tool>`); a plain
  `uv run`/`uv sync` there creates a second 2 GB `backend/.venv` from the backend's own lock.
- The backend keeps its own `pyproject.toml` + `uv.lock` for the Docker images and CI. After changing backend
  dependencies, update both locks: `uv lock` in `src/app/backend` (writes only the lock, no venv) and
  `~/.local/bin/uv lock && ~/.local/bin/uv sync` in the repo root.
- The API (`mmm_studio.main`) is slim and imports without `statsmodels`, `scipy`, `holidays`, or `meridian`; the worker
  and test suite need the `worker` extra, which the root `.venv` has.

## Commands

```bash
# Backend (from src/app/backend; --project ../../.. = the repo-root .venv)
(cd ../../.. && ~/.local/bin/uv sync)        # root sync: every group is a default, incl. the backend (`app`)
uv run --project ../../.. pytest tests/ -m "not slow" -q        # fast test suite; needs Postgres (TEST_DATABASE_URL, default mmm_studio_test on :5433)
uv run --project ../../.. pytest tests/test_meridian_smoke.py   # includes the @slow smoke MCMC fit (~1 min)
uv run --project ../../.. ruff check mmm_studio tests
uv run --project ../../.. python -m mmm_studio.cli run --input ../../../data/DataScienceMarketingUseCase.xlsx \
    --out /tmp/mmm_cli --skip-meridian       # offline pipeline: no DB, writes runs/offline/... under --out (~5 s)
#   Without --skip-meridian: --profile full ≈ 15–25 min (notebook-equal sampling + selection grid), quick ≈ 6 min, smoke ≈ 4 min.
#   CLI flags: --input / --workbook, --profile {full,quick,smoke} / --fast / --smoke (default profile is "full").
uv run --project ../../.. uvicorn mmm_studio.main:app --reload --port 8000     # API (docs at /docs, routes under /api/v1)
uv run --project ../../.. python -m mmm_studio.cli worker --queue meridian     # one per queue: analysis|meridian|interactive (or all), --burst, --idle-exit N
uv run --project ../../.. python -m mmm_studio.cli migrate                     # Alembic upgrade + Procrastinate schema + register example forecasts

# Frontend (from src/app/frontend)
npm install
npm run dev                          # :5173, proxies /api -> http://localhost:8000 (override with VITE_API_PROXY_TARGET)
./node_modules/.bin/tsc --noEmit -p .  # typecheck without writing files
npm test                             # vitest unit tests (budget parsing, CSV escaping, number formats)
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
  errors.py     public_error(): exception text without file-system paths, for stage/run/job errors shown to users
  api/          router.py (/api/v1: /health and /auth/{session,login,check,logout} public; every other router behind
                require_auth), runs, sections, forecasts (+scenarios, run history), optimiser, exports (artifacts),
                deps (session cookie + X-Access-Code, failed-attempt rate limit by client IP, trusted proxy hops)
  db/           SQLAlchemy 2.0 models: runs, stages, forecasts, scenarios, optimisations; migrations/ (Alembic,
                0001_baseline); init_db() upgrades to head and stamps pre-migration databases at the baseline
  jobs/         app.py (Procrastinate app, defer_task, pooled queue queries, throttled stale sweep), tasks.py (5 tasks
                + LRU cache), registry.py (registers the example forecast), worker.py, cloud_run.py (wake-up only when
                no execution is running)
  pipeline/     context.py (RunContext: storage keys, save_section/dataframe/json, cancel + heartbeat), stages.py (s01–s16)
  core/         workbook.py (profile_workbook), schema.py (DataProfile, DataRoleConfig, ChannelConfig), model_settings.py (run
                settings: ModelSettings, sampling tables, limits, settings_changes), tables.py, calendar.py,
                features.py (notebook controls: weekday/holiday/Christmas/Black Week, Fourier, trend, catalog in-home adstock,
                hold-out windows), metrics.py, preparation.py (nb 02), ols.py (nb 04), eda/* (+ notebook_stats.py: 01b tables),
                meridian/{spec,fit,selection,analysis,optimise,forecast}.py
  report/       sections.py (Section/Block models), narrative.py (formatters), facts.py (computed case facts for narratives),
                meridian_html.py (post-processes Meridian's HTML reports; stdlib + charts/theme only, so the API can import it)
  charts/       builders.py, theme.py
backend/tests/  core/ unit + golden tests, test_pipeline_fast (CLI), test_api, test_jobs (Postgres), test_meridian_smoke
  fixtures/case_study.xlsx, golden/case_study.json   (reference numbers from the notebooks)
frontend/src/   main.tsx, App.tsx (routes; selected run lives in React state, not the URL), theme.ts (PALETTE, light mode only)
  api/client.ts (all HTTP via native fetch + X-Access-Code from sessionStorage), hooks.ts (TanStack Query + polling), types.ts
  components/Sections/SectionRenderer.tsx (lazy Plotly; header, empty states, "On this page"), Sections/OnThisPage.tsx,
                Layout/AppShell.tsx (sidebar, run switcher, cancel, route titles/scroll/focus), Layout/nav.tsx (sidebar config,
                findNavItem), Layout/PageHeader.tsx, Charts/PlotlyChart.tsx
                Import/* (the run pages' parts: UploadDropzone, RunTable, RunHeader, RunOutcome, PipelineProgress,
                DataProfileSummary, ConfirmDialog, RunPage, RunSettingsForm + settings/{Data,Channels,Model,Priors,Sampling}Tab,
                settings/draft.ts (draft, change counts, reset), format.ts with status/profile/role labels and duration formatting)
                Forecast/* (the planner: LeverPanel, ChannelEffects, figures.ts builds its Plotly figures client-side,
                format.ts)
  pages/        one per nav item; most pages are just useSection(runId, "<id>") + SectionRenderer
deploy/         deploy.sh (all | update | seed; GCP_PROJECT_ID=formare-dev, europe-west3, access code always: APP_ACCESS_CODE or a generated one),
                nginx.conf (template: ${PORT}, ${API_ORIGIN}; on Cloud Run the API is a sidecar at 127.0.0.1:8000)
```

**Run lifecycle:**

1. `POST /runs` takes the upload, runs `profile_workbook` (rejects conflicting duplicate rows, negative costs and
   histories shorter than 379 days; drops exact duplicates with a warning), and creates a `draft` Run with its inferred
   `DataRoleConfig`.
2. `PATCH /runs/{id}` changes the name at any time, and roles/settings/profile/seed **only for drafts** (409 otherwise:
   a run's settings describe its results).
3. `POST /runs/{id}/start` takes the full settings (start or re-run, all-or-nothing): it validates them
   (`run_config_errors`: ≥ 365 training days, at least one media channel, ROI priors only for media), checks the
   Meridian capacity (queued/running fits + runs still in analysis ≥ `MMM_MAX_MERIDIAN_QUEUED` → 429) and leftover
   queued jobs (409), and only then stores the settings, clears a re-run's results (sections, data, models, reports,
   forecasts, scenarios, optimisations) and defers `run_analysis_job` (lock `analysis:{id}`). The analysis job defers
   `run_meridian_job` (lock `meridian_singleton`) when it finishes, so Meridian never runs before or alongside it.
   Inside a worker the Procrastinate app is already open (async pool), and `app.open()` raises `NotImplementedError`:
   `defer_task` defers through the open connector in that case; keep it that way.
4. Each stage updates its `stages` row and writes `sections/{id}.json` plus `sections/_index.json`.
5. The UI polls: the run every 2 s, sections every 4 s.

| Stages | Queue | Notes |
| --- | --- | --- |
| `s01_ingest` … `s11_benchmark` | analysis | `s01` is critical. `s11` writes the draft `overview`, `brief` and `reports` (the final ones when Meridian results exist) |
| `s12_meridian_fit` | meridian | Critical. Final fit plus the forward (Q3) hold-out refit |
| `s13` … `s15_finalize` | meridian | ROI (+ identification, P(ROI < 1)), optimiser with demand-following channels fixed + unconstrained, HTML reports; back-test (+ plan-time back-test) + example-plan forecast + scenarios; `s15` rewrites `benchmark`, `overview`, `brief`, `reports` |
| `s16_model_selection` | meridian | Full profile only (else `skipped` via `StageSkipped`): selection + sensitivity fast fits trained before and scored on the validation window (the quarter before the hold-out); rewrites `meridian_model` |

If a non-critical stage fails, the failure is recorded on its stage row and swallowed (`_run_stage`). Every Meridian
fit (main, forward, selection, back-test, example plan, custom plans) goes through `fit_meridian_isolated`: a spawned
child inside workers, with cancellation and `MMM_MERIDIAN_FIT_TIMEOUT_SECONDS`. The Meridian job registers the
example forecast (`jobs/registry.py`); GET requests never write.

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
- When `MMM_APP_ACCESS_CODE` is set, **every** route except `/api/v1/health` and `/auth/*` needs the HttpOnly session
  cookie (`POST /auth/login`) or the `X-Access-Code` header; the SPA shows a sign-in screen (`AuthGate`). `MMM_ENV=production`
  refuses to start without a code of ≥ 16 characters. Failed attempts: 5/min per client (`MMM_TRUSTED_PROXY_HOPS`
  picks the client from X-Forwarded-For) and 30/min in total.

## Testing conventions

- `test_api.py` and `test_jobs.py` **drop and recreate all app tables and truncate the Procrastinate tables** in
  `TEST_DATABASE_URL`. Never point that variable at a database you care about.
- `get_settings`, `get_storage`, `get_engine`, `get_session_factory` and `get_procrastinate_app` are `lru_cache`d.
  After changing env vars, clear their caches (see the `api_env` fixture).
- Use the `smoke` profile or `SMOKE_SAMPLING` in tests. Mark anything that runs a real MCMC fit `@pytest.mark.slow`.
- Parity is checked against `tests/golden/case_study.json`, not by running notebooks. Case-study facts are in that
  file: 8,740 rows, 732 days, 12 channels (7 paid), and the hold-out reference values. Parity tests use the
  `notebook_parity` fixture (`missing_row_controls=False`); the app's default adds missing-row controls.
- `tests/core/test_audit_remediations.py` holds one regression test per audit finding; keep them independent of the
  implementation (expected behaviour, not the current output).

## Code style

- **Python 3.12.** Use ruff (`E`, `F`, `I`; line length 110), `from __future__ import annotations`, module
  docstrings, full type hints, pydantic v2 and SQLAlchemy 2.0 `Mapped[...]`.
- **Spelling.** Backend identifiers use British spelling (`optimiser`, `optimisation`). The frontend route is
  `/optimizer` and the file is `Optimizer.tsx`. Match whatever the file you are editing already uses.
- **TypeScript.** `strict`, `React.FC` components, MUI `sx` styling, colours from `PALETTE` in `theme.ts`.
  Every HTTP call goes in `api/client.ts`, every query hook in `api/hooks.ts`, and every API type in `api/types.ts`.
- **Page anatomy.** Every page starts with `PageHeader` (eyebrow = its sidebar group from `nav.tsx`, h1, description,
  actions on the right); section pages get it from `SectionRenderer` (`headerActions`, `afterHeader` for page controls
  or notices, `headingLevel={2}` when the page has its own header, as on Explore and Forecast). Figures use `NUMERIC`
  (Figtree with tabular digits); `FONT_MONO` is only for code, paths and column names. New routes go in `nav.tsx`,
  which also drives the document title.
- **Brand tokens, no logo.** Use the Tchibo colours from `PALETTE` / `charts/theme.py`, never the Tchibo logo; the
  app is labelled an independent case-study prototype.

## Parity with the notebooks

- **Defaults vs notebooks.** Every modelling default is the notebooks' value except `missing_row_controls` (on by
  default: the notebooks read days with missing workbook rows as sales drops). Switch it off for exact parity.
- **Modelling window.** Notebooks 02–05 keep the isolated 2025-01-01 in every model, so `DataRoleConfig` defaults to
  `drop_trailing_isolated_days=False` and `holdout_days=93` (Q4 2024 through 1 Jan 2025). Yearly and monthly views use
  `core/features.complete_years()`.
- **Checked against the notebooks' own outputs** (2026-11-02, full run `run_f1e8df83da`): `outputs/meridian_metrics.json`
  within 0.5 %, `outputs/meridian_holdout_forecast.csv` (93 days) within 0.6 %, `outputs/forecast_2025-01.csv` (42 days)
  within 0.4 %, every figure headline in `reports/figures`, and every number printed by notebooks 01–05: 1,614 of
  1,620 match; the other 6 are notebook 04's Meridian row (MCMC noise ≤ 0.1 %) and pandas' 7-digit display of three
  raw cost values (verification scripts only; the app never reads these files). This check predates `half_day`.
- **`half_day` control (2026-11-04).** The Christmas group also flags 24 and 31 Dec (Google's half-day holidays), as
  `tchibo_mmm.features.calendar_controls` does; notebooks 02–05 were re-run with it and the golden numbers updated.
- **Reference numbers** live in `tests/golden/case_study.json` and are asserted by the fast tests (EDA, naive baselines,
  preparation ladder 0.74→0.90, OLS benchmark 34.8/10.7/9.6 % and walk-forward 9.5 %, example plan €8,008,549).
- **Meridian (CLI full profile, seed 0, default settings, 2026-11-04)** vs the re-run notebooks: Q4 MAE/mean 8.54 %
  (nb 8.49 %), R² 0.888 (nb 0.889), Q3 forward 11.05 % (nb 10.88 %), ROI SEA 8.53 (nb 8.32) / Catalog 0.96 (nb 0.97),
  optimiser +11.0 % (nb +11.2 %) on €121.8M, catalog what-if ratio 0.95 (nb 0.95), and notebook 05's forecast €42.61M
  (nb €42.59M) with the same scenarios within 1 % (skip 30 Jan catalog, 20 % Display → SEA, +10 % digital). The
  Meridian input table equals `data/processed/meridian_input.csv` except the three `missing_*` controls that the default
  `missing_row_controls` adds.
- Scenarios run on the fitted forecast model (`model_scenario_from_multipliers`); the marginal-ROI approximation is
  only a fallback when that model is missing and uses the run's own posterior mROIs.

## Run settings

- Everything a user can change lives in the run's `DataRoleConfig`: `holdout_days`, `drop_trailing_isolated_days`, per
  channel `role` / `adstock_shape` / `saturation` / `in_home_shift_days` / `demand_following` (`inferred_role` and
  `inferred_demand_following` keep the inference; a channel is demand-following when its daily cost correlates ≥ 0.9
  with its own clicks — SEA and PSM in the case study), `missing_row_controls`, and
  `model_settings` (`core/model_settings.py`: calendar controls, Fourier pairs, trend, max lag, baseline knots / automatic
  knots, Hill before adstock, media prior type, ROI priors (Meridian default, attributed ROAS, custom per channel),
  custom sampling, forward hold-out, selection grid and shifts). Every default is the notebooks' value, so a run on the
  defaults reproduces notebooks 01–05; `settings_changes()` lists what differs (API `settings_changes`, the Runs table,
  and the "Settings used" block of `meridian_model`).
- `MeridianSpecConfig.from_role_config(rc, wide=…)` turns them into the spec; build every Meridian table with
  `meridian_table(wide, cfg)`. Calendar/seasonality toggles apply to the OLS benchmark too, not to the preparation ladder.
- Not exposed on purpose: `media_effects_dist` and the geo options (Meridian fixes them for a national model), reach &
  frequency / organic media (no such data). `GET /runs/settings/defaults` serves defaults and limits to the form.

## Resolved issues (verified on 2026-11-02)

1. `backend/Dockerfile` uses `python:3.12-slim` and installs `--extra worker` on the `worker` stage while keeping the `api` stage slim.
2. `deploy.sh` builds the `api`, `worker` and `web` images in one temporary Cloud Build config (explicit `--target api` /
   `--target worker`, build SA `mmm-studio-build`). Containers run the venv's programs directly (`PATH=/app/.venv/bin`);
   `uv run` at start-up would sync the `dev` group into the image on every cold start. The old two-service layout
   proxied to the API's run.app URL with the web host's `Host` header, which Cloud Run cannot route, so the API is now a
   sidecar of one service, and `src/app/.gcloudignore` keeps any stray `backend/.venv` out of the upload.
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
16. The last notebook tables the app lacked are computed: daily-sales quartiles (01), mean daily spend by weekday in €
    (01), mean catalog sales by day since send-out (01), spend per channel and year (01b), metric ranges per channel
    (01b), and the quarterly CPC / CPM / CTR / attributed-ROAS table plus the monthly ROAS panel (01b).
17. Affiliate's role is "commission" (it was "paid", so the Import page said "gets an ROI" while Meridian always used it
    as a control). `ChannelConfig` reads a stored "paid" Affiliate as commission and the run API returns the
    re-validated role config, so older runs show the role the model uses. The Import page's channel-role table shows
    each rationale under its role (the separate "Why" column was squeezed to one word per line).
18. axe-core (WCAG 2.1 A/AA + best practice) on every page at 1440 and 390 px: navigation links are wrapped in `<li>`,
    the plan upload is a real button (it was a `<label role="button">`), the downloads table has its own landmark
    name, and scrollable code blocks are focusable. With no runs, the header's "Run" label no longer overlaps "No run
    yet" and the empty runs table says so (checked by uploading and deleting a run on an isolated test-DB stack).
19. Meridian's three HTML reports are post-processed by `report/meridian_html.polish_meridian_report` (Meridian itself
    is untouched). It loads Figtree (the reports ask for Roboto/Google Sans and never load them, so text was serif),
    and wraps `vegaEmbed` to set container-width charts, wrap the optimisation response-curve facet into the columns
    that fit (it was clipped below ~1,100 px), give the 90-row control-correlation chart a row step, and apply the
    app's channel colours with a grey baseline. It also shows the last modelled day in the period chip (Meridian adds
    one interval), swaps "$" for "€" and rounds float noise (ROI "3.3000000000000003"). `generate_meridian_html_reports`
    polishes at write time; `GET /artifacts/reports/*.html` polishes older reports on the fly (a no-op once the
    `mmm-studio-report-polish` meta marker is present). New fits set `InputData.currency_code = "EUR"`, so Meridian
    writes € itself. The report iframe allows popups and downloads, so doc links and the chart export menu work.
20. (Superseded by 22.) The Import page was a master-detail layout: the upload zone and run list on the left; on the right, the selected run's
    header (lifecycle stepper, actions menu), its outcome (results-ready with hold-out MAE / mean and max R-hat from
    `run.summary`, failed or cancelled with a re-run action), pipeline lanes per queue with live stage durations from
    `started_at`/`finished_at`, the workbook profile, and the settings form (Meridian switch, sampling profiles as radio
    cards, channel roles with "Changed" tags and undo, a sticky start bar). Re-runs are confirmed. After an upload the
    page waits for the refreshed run list before selecting the new draft (otherwise AppShell's fallback reset the
    selection). Visually hidden text inside a `Collapse` needs `position: relative` on the Collapse, or the absolute
    spans escape the clip and lengthen the page. MUI dialogs focus their container, so the safe button is focused in
    `TransitionProps.onEntered`.
21. The Forecast page is a planner: budget levers on the left (sticky; ±100 % sliders with a % field, quick scenarios
    including notebook 05's "+10 % digital" and "20 % Display → SEA"), and on the right the KPI strip (plan, scenario
    with its 90 % interval, spend, Δ sales / Δ spend, same weekdays last year), one chart with the run's history
    (`GET /runs/{id}/history`, 6 weeks … all) leading into the forecast, the scenario and up to two saved scenarios,
    plus a spend panel. A lever change is evaluated on its own 450 ms after the last edit (one scenario per
    multiplier set, cached by key; the previous result stays dimmed until the new one lands). Scenario payloads carry
    each media channel's own effect (`channel_effect_draws`: Meridian `incremental_outcome` for the scenario minus the
    plan; media effects are additive, so they sum exactly to the scenario's Δ, with 90 % intervals from the draws),
    `channel_daily_delta` (the chart hover) and `plan_daily` / `scenario_plan_daily`; older stored forecast results get
    `plan_daily` from their plan file on read. The interactive worker caches the plan's draws, so a warm scenario
    takes about 1.3 s. Saved scenarios are per viewer (`localStorage`); the chart's overlapping line colours passed the
    dataviz palette validator, which is why only two saved scenarios are drawn.
22. (2026-11-03) The crowded Import page is split into three sidebar pages that follow the stepper: **Runs** (`/runs`:
    upload + a full-width runs table with Open / Make default / Delete per row and a "Custom · n" settings tag),
    **Run setup** (`/setup`: the settings form in tabs Data · Channels · Model · Priors · Sampling, each with a change
    count, "Reset to notebook defaults", a sticky Start / Re-run bar; inactive tab panels are `hidden="until-found"` so
    find in page reaches them, and an invalid field in a hidden tab switches to it before the browser reports it) and
    **Run status** (`/status`: header with linked stepper, outcome, pipeline lanes open by default, cancel). `/import`
    redirects to `/runs`. The sidebar shows "Review" next to Run setup for a draft and the stage count next to Run status
    while a run is active. The outcome's hold-out labels come from the summary (`holdout_label`, `forward_label`; older
    runs get them from `meta/meridian_metrics.json` on `GET /runs/{id}`).
23. (2026-11-03) Design pass toward common analytics-dashboard patterns: one `PageHeader` on every page (the optimiser's
    "Try your own constraints" and the forecast planner had their card above or instead of the page title; Explore's
    tabs sat above its h1); the overview is the sidebar's home item and the whole sidebar fits a 900 px viewport (it
    scrolled before) with its brand row level with the app bar; the run picker is a context switcher with the label
    inside; the access-code button shows only when `GET /health` says `auth_required` (or a request was refused);
    "Upload workbook" is an outlined shortcut hidden on `/runs`. A link to another page scrolls to the top and focuses
    `#main-content` (before, the new page opened at the old scroll position), and each route sets `document.title`.
    Sections with ≥ 4 titled blocks get "On this page": a sticky rail at xl (≥ 1536 px, where the content column is
    still wide enough for every table) with native `scroll-target-group` / `:target-current` and an
    IntersectionObserver fallback, and a floating menu below xl. KPI values and table figures use Figtree's tabular
    digits instead of JetBrains Mono (text KPIs such as "Met" or "Display → SEA" and labels such as "25th percentile"
    were set in mono); KPI tiles are two per row on phones. Raw statuses ("succeeded") show their labels ("Finished").
24. (2026-11-03) Production hardening after the audit in `audit/` (`BUG_REPORT.md` IDs):
    - Security: auth on every route with an HttpOnly SameSite=Strict session cookie (SEC-01), failed-attempt limits
      by real client IP and globally (SEC-02), streamed zip-size check (SEC-03), path-free error messages (SEC-04),
      pinned uv image, react-router 7, Vite 7, fail-closed production start, nginx security headers (SEC-05).
    - Statistics: the hold-out always comes from the data and a missing hold-out is an error (STAT-02); the executive
      summary, benchmark and brief conclusions follow the numbers (STAT-01, TXT-01, TXT-02); the optimiser raises on
      infeasible budgets, bounds beyond the curves and solver failures, fixes print channels exactly (STAT-03,
      STAT-09) and headlines the result with demand-following channels held fixed, with the unconstrained lift and
      its upper-bound share next to it (STAT-05: 107 % of the 11.1 % came from SEA); ROI table shows P(ROI < 1) and
      an identification flag from the prior/posterior interval width on the log scale (STAT-06); a plan-time
      back-test replaces realised commission with its recent run-rate × last year's seasonal ratio (STAT-04; it
      overestimated Q4 2024 Affiliate by ~17 %, so the plan-time figure is the honest, worse number); selection and sensitivity fits are
      scored on the validation window (STAT-07); attributed-ROAS priors use training days only (STAT-08); intervals
      of totals come from the draws and the band shows its back-test coverage (STAT-11, STAT-12); OLS-2 uses the
      run's in-home shift and missing-row controls (STAT-10); the grid flips only commission channels (STAT-13).
    - Data: conflicting duplicates and negative costs are rejected, exact duplicates dropped (DATA-01, DATA-04);
      plans need every channel column and no unknown ones (DATA-02); missing-row controls (DATA-03); minimum
      history (DATA-05); regime breaks are detected from the data instead of fixed dates (AR-01).
    - API/jobs: frozen settings and an atomic start/re-run (API-01), re-runs clear derived results (API-02), typed
      optimiser bounds with display names (API-03), 409 instead of 500 for leftover queued jobs (API-04), chained
      Meridian job (API-05), read-only GETs (API-06), stuck interactive jobs swept and fail-closed queue counts
      (API-07); sync upload handlers (PERF-01), pooled and throttled queue queries (PERF-02), isolated fits with
      timeouts and temp cleanup (PERF-03), wake-ups only without a running execution (PERF-04); Alembic (AR-05).
    - UI: sign-in screen and sign-out, single start request, budget input in € million (UI-01), formatting
      boundaries (UI-02), demand-following and missing-row settings, total intervals and measured coverage.
    - Not real (Gemini's report): `extend_history` does not rescale the trend (`features.seasonality_controls` uses days
      since the first day), and scenario prediction draws were already draw-aligned.
25. (2026-11-03) The Forecast page was decluttered on the user's request ("keep only the most important data"). Removed:
    the granularity strip (it also had invented fallback counts), the duplicate "New custom plan" button and chip, the
    mode radio cards (now two small toggles), period preset chips, "n/n channels active", Plan mix / Equal split
    (`splitMultipliersEqually` deleted), per-channel checkboxes, Daily/share chips, per-lever sales-effect lines (the
    effects chart shows them), the empty saved-scenarios card and long helper texts; "Same weekdays last year" is a line
    in the plan tile. The panel order is period → quick-scenario dropdown → channels → total media budget (the user
    asked for the total below the last channel and the presets in a dropdown). A lever with no spend in the chosen days is disabled (it used to scale the full-plan spend), and
    quick scenarios use the chosen period's spend. The `forecast_scenarios` section now has two KPI blocks with ids
    (`forecast_accuracy_kpis`, `forecast_plan_kpis`; `KpisBlock.id` is optional): the page shows only the accuracy KPIs
    and the back-test chart, and every other block (example plan, weekly tables, notebook 05 scenarios) sits in a
    collapsed accordion, so parity content stays in the app. `PlotlyChart` sets `automargin` on y axes, so long
    horizontal-bar labels are no longer cut off.
