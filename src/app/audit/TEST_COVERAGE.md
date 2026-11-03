# Test Coverage Evaluation & Independent Verification Results

Audit date: 2026-11-03. Audited commit: `ace6bb0` (`main` branch).

---

## 1. Test Execution Summary During This Audit

All existing tests and independent audit verification scripts were executed against an isolated scratch environment without mutating the development database or stored run artifacts:

| Suite / Verification Script | Command Executed | Result |
|---|---|---|
| **Backend Fast Test Suite** | `cd src/app/backend && .venv/bin/pytest tests/ -m "not slow" -q` | **`30 passed`, `2 deselected`** (`10.19s`) |
| **Backend Slow Test Suite (Real Meridian MCMC)** | `cd src/app/backend && .venv/bin/pytest tests/ -m slow -q` | **`2 passed`** (`53s`) |
| **Backend Linter (Ruff)** | `cd src/app/backend && .venv/bin/ruff check mmm_studio tests` | **`All checks passed!`** |
| **Frontend TypeScript Compiler** | `cd src/app/frontend && ./node_modules/.bin/tsc --noEmit -p .` | **Exit `0` (0 errors)** |
| **Frontend Dependency Audit** | `cd src/app/frontend && npm audit --omit=dev` | **2 moderate advisories** (`react-router <= 7.17.0`, `SEC-05`) |
| **Independent Core & Optimiser Audit (`audit_core_checks.py`)** | `src/app/backend/.venv/bin/python src/app/audit/scripts/audit_core_checks.py` | **12 independent checks**: `11 FAIL` (reproducing `DATA-01..04`, `STAT-01..04`, `API-03`, `TXT-02`), `1 PASS` (`OPT-3` PCHIP error bound quantified at `2–4%`) |
| **Independent API & State Audit (`audit_api_checks.py`)** | `src/app/backend/.venv/bin/python src/app/audit/scripts/audit_api_checks.py` | **10 independent checks**: `10 FAIL` (reproducing `SEC-01`, `DATA-01`, `DATA-02`, `DATA-04`, `API-01..03`, `STAT-03`) |
| **Independent Posterior Identity Audit (`audit_meridian_posterior.py`)** | `src/app/backend/.venv/bin/python src/app/audit/scripts/audit_meridian_posterior.py` | **All 10 posterior identities PASS** on stored full run `run_f1e8df83da`; prior-dominance ratios quantified (`STAT-06`) |
| **Independent Synthetic & Counterfactual MCMC Fits (`audit_meridian_fits.py`)** | `src/app/backend/.venv/bin/python src/app/audit/scripts/audit_meridian_fits.py` | **3 FAST + 3 SMOKE Meridian fits**: synthetic ground-truth recovery (`STAT-06`), seed determinism (`max_abs_diff = 0.0`), Affiliate leakage (`8.56%` $\to$ `11.30%`, `STAT-04`) |
| **Independent Concurrency & Hold-Out Audit (`audit_race_and_holdout.py`)** | `src/app/backend/.venv/bin/python src/app/audit/scripts/audit_race_and_holdout.py` | **2 SMOKE pipelines**: reproduced job-order overwrite race (`API-05`) and 390-day short-dataset hold-out collapse (`STAT-02`) |

*Scope note*: Live Cloud Run / Cloud SQL / GCS infrastructure and browser Playwright automation were not executed; frontend findings (`UI-01..03`) were verified by static code inspection and TypeScript compilation.

---

## 2. Inventory & Assessment of the Existing Test Suite (`32` Tests)

| Test File | Test Count | What It Covers | Key Blind Spots |
|---|---:|---|---|
| `tests/core/test_workbook.py` | 3 | Golden profile numbers on `case_study.xlsx`; rejects non-zip/empty uploads; Affiliate role label | Does not test duplicate `(date, channel)` rows (`DATA-01`) or negative cost/sales values (`DATA-04`). |
| `tests/core/test_tables.py` | 2 | Wide panel shape (`732 × ...`), total sales/cost parity with notebook 01, calendar/naive columns | Asserts parity with notebook totals that already treat 44 missing channel-days as `0` sales (`DATA-03`). |
| `tests/core/test_preparation.py` | 1 | M0–M5 OLS ladder $R^2$/AIC parity with notebook 02 | Only tests default settings on the Tchibo fixture. |
| `tests/core/test_ols.py` | 1 | 4-model OLS benchmark MAE/mean parity with notebook 04 | Does not test whether OLS-2 updates when `in_home_shift_days != 2` (`STAT-10`). |
| `tests/core/test_eda.py` | 5 | Builds all 8 EDA sections; checks structural break p-values and attributed ROAS vs. notebooks 01/01b | Never runs on a workbook lacking Tchibo's channel slugs or 2023–2024 dates (`UI-03`, `AR-01`). |
| `tests/core/test_model_settings.py` | 7 | Default settings match notebook spec; custom priors/knots/lags reach `ModelSpec`; sampling guardrails | Only tests the 732-day Tchibo index, missing the `len(index) <= 2 * holdout_days` fallback branch (`STAT-02`). |
| `tests/test_pipeline_fast.py` | 1 | Runs `run_analysis_stages` (`s01`–`s11`) and checks section files exist | Does not inspect whether draft `brief` fabricates `"ROI ≈ 1"` (`TXT-02`). |
| `tests/test_api.py` | 3 | Happy-path upload $\to$ configure $\to$ start $\to$ sections $\to$ scenario/optimiser; write auth guard; queue cap | Explicitly encodes `GET /runs` returning `200` without `X-Access-Code` (`SEC-01`); never tests `PATCH` on a finished run (`API-01`) or re-run state cleanup (`API-02`). |
| `tests/test_jobs.py` | 2 | Worker executes analysis job; stale heartbeat sweeper marks dead `Run`/`Forecast` failed and frees lock | Never tests `run_meridian_job` finishing before `run_analysis_job` (`API-05`) or stale `ForecastScenario`/`OptimisationJob` rows (`API-07`). |
| `tests/test_meridian_html.py` | 3 | Post-processing of Meridian HTML reports (banner pruning, Catalog note injection) | Good unit coverage of HTML string transforms. |
| `tests/test_meridian_smoke.py` | 4 (2 `slow`) | Meridian table builder; scenario delta additivity; smoke MCMC fit (`s12`–`s15`); response-curve optimiser shifts spend toward higher mROI | Slow tests are excluded from `.github/workflows/app-ci.yml` (`-m "not slow"`). Optimiser test only checks a feasible budget (`STAT-03`). |

---

## 3. Existing Tests That Pass Despite Incorrect Business or Security Logic

1. **`tests/test_api.py:217-220` (`test_access_code_guard_rate_limiter_and_meridian_queue_cap`)**:
   - **Code**: `assert client.get("/api/v1/runs").status_code == 200` while `MMM_APP_ACCESS_CODE="secret-code"` and no `X-Access-Code` header is sent.
   - **Why it is flawed**: It codifies unauthenticated read access (`SEC-01`) as an asserted requirement while protecting only `POST`/`PATCH`/`DELETE`.
2. **`tests/test_meridian_smoke.py:181-209` (`test_response_curve_optimiser_moves_budget_to_the_higher_marginal_return`)**:
   - **Why it is flawed**: Tests only `total_budget = €100,000` with historical spend `€100,000` and bounds `[0.7, 1.3]`. Because the budget is feasible and `include_catalog=True`, it never exercises the silent bound-scaling branch (`optimise.py:123-131`), the fixed-Catalog unlocking bug (`optimise.py:117, 128-131`), or the post-SLSQP unclipped rescale (`optimise.py:159`, `STAT-03`).
3. **Notebook Parity Tests (`test_workbook.py`, `test_tables.py`, `test_eda.py`, `test_ols.py`)**:
   - **Why they are insufficient**: They assert exact numerical equality with the exploratory case-study notebooks, which themselves summed duplicates (`DATA-01`), zero-filled missing channel-days (`DATA-03`), and included realised Affiliate commissions in the Q4 hold-out (`STAT-04`).

---

## 4. Independent Verification Scripts & Synthetic Test Results (`src/app/audit/scripts/`)

We executed five standalone audit test scripts containing **35 independent assertions** across synthetic and case-study datasets:

### 4.1 `scripts/audit_core_checks.py` (12 Checks — 11 Confirmed Bugs Reproduced)

- **`DP-dup` (`DATA-01`)**: Appending 50 duplicate `(date, channel)` rows to `case_study.xlsx` yields `profile.duplicate_rows_count = 50` with **zero warnings** (`warnings` list unchanged) and silently inflates `total_sales` by **`+€4,259,262`** and `total_cost` by **`+€2,305,376`**.
- **`DP-neg` (`DATA-04`)**: Injecting 5 negative cost rows (`-€10,000`) yields `profile.negative_values_count = 5` with **zero warnings**.
- **`DP-miss` (`DATA-03`)**: Confirmed 44 days have missing channel rows (`TV: 38`, `Catalog: 4`, `Owned: 2`); `total_sales` on those 44 days is **`-11.7%`** below same-weekday adjacent weeks (worst day **`-42.1%`**), and `missing_<slug>` flags are absent from Meridian `control_cols`.
- **`OPT-1` (`STAT-03`)**: Calling `run_constrained_optimisation` with `include_catalog=False` and `total_budget = 1.5×` historical spend changes Catalog spend from **`€20,000,000` to `€26,582,278`** while returning `"include_catalog": False`.
- **`OPT-2` (`STAT-03`)**: Requesting `total_budget = 0.5×` historical spend with `min_multiplier=0.7, max_multiplier=1.3` returns `0.50×` for every channel (violating the `0.7×` lower bound) with no warning or error flag.
- **`OPT-3` (`STAT-09`)**: Evaluated PCHIP extrapolation at `2.9×–3.1×` spend against true analytical Hill curves; extrapolation underestimates true Hill response by `2.6%–4.2%`.
- **`OPT-4` (`API-03`)**: Passing `channel_bounds={"display": (5, 12)}` is interpreted as `€5–€12` (`×0.0000` multiplier) because `hi > 10.0`, whereas `(5, 10)` is interpreted as `5×–10×` multipliers.
- **`FC-1` (`DATA-02`)**: Uploading a budget plan with columns `['date', 'sea', 'catalg', 'display']` silently drops `'catalg'` and fills `catalog`, `affiliate`, `display_network`, `social_media`, and `psm` with **`€0`**.
- **`HO-1` (`STAT-02`)**: Calling `MeridianSpecConfig.from_role_config` on a 390-day panel (`2022-01-01..2023-01-25`) with `holdout_days=200` returns `cfg.holdout = ('2024-10-01', '2025-01-01')`.
- **`TXT-1` (`STAT-01`)**: Passing `holdout_mae_over_mean = 0.22` (`22.0%`) into `build_overview_section` produces: `"**Can daily sales be forecast from the marketing budget?** Yes: Meridian MMM forecasts the Q4 2024 hold-out with **22.0% MAE / mean** ... — below the brief's 15 % threshold."`
- **`TXT-2` (`TXT-02`)**: Calling `build_brief_section` with `roi_df=None` outputs `"30.5% of spend with revenue ROI ≈ 1"` for Catalog and hard-codes `"Affiliate, SEA and Catalog; the others cannot be told apart from zero"`.
- **`STAT-leak` (`STAT-04`)**: Removing realised `cost_affiliate` from OLS-2 increases Q4 hold-out MAE/mean from `9.6%` to `12.2%` (`13.8%` without SEA spend) and Q3 forward hold-out MAE/mean from `14.0%` to `15.6%` (`24.9%` without SEA spend).

### 4.2 `scripts/audit_api_checks.py` (10 End-to-End FastAPI Checks — 10 Confirmed Bugs Reproduced)

- **`SEC-1` (`SEC-01`)**: With `MMM_APP_ACCESS_CODE` set and no `X-Access-Code` header, `GET /api/v1/runs`, `GET /api/v1/runs/{id}`, `GET /api/v1/runs/{id}/artifacts/input/workbook.xlsx` (`399,012` bytes), and `GET .../meta/profile.json` all return **`HTTP 200`**.
- **`STATE-1` (`API-01`)**: `PATCH /api/v1/runs/{id}` on a `succeeded` run returns **`HTTP 200`**, keeps `status="succeeded"`, updates `run.config.model_settings.holdout_days` to `60` in Postgres while `meta/config.json` on disk stays `93` and all sections still reflect the 93-day fit.
- **`STATE-2` (`API-01`)**: When `PATCH` succeeds (`200`) and `POST /start` is rejected (`429` queue cap), the run remains `"succeeded"` with mutated settings (`include_trend=False`, `skip_meridian=False`).
- **`STATE-3` (`API-02`)**: Re-running a completed run (`POST /start` $\to$ `202`) leaves all `4` previous `OptimisationJob` rows and `1` custom `Forecast` row attached to the run.
- **`OPT-API-1..4` (`API-03`, `STAT-03`)**: Confirmed display-name bound keys (`{"Display": [0.5, 0.5]}`) are ignored (`multiplier = 0.7`), 1-element bounds (`{"display": [0.9]}`) and negative bounds (`{"display": [-5, 0.2]}`) return `202` $\to$ `"succeeded"`, and an infeasible `€50M` budget with `[0.9, 1.1]` bounds allocates `0.411×` to all 6 channels.

### 4.3 `scripts/audit_meridian_fits.py` & `scripts/audit_race_and_holdout.py`

- **Synthetic Ground-Truth Recovery (`STAT-06`)**: True ROIs `sea=3.0`, `psm=1.5` covered by 90% CIs (`[2.11, 3.61]`, `[0.26, 2.86]`); null channel `display=0.0` receives false-positive posterior ROI **`0.51 [0.12, 1.34]`**.
- **Meridian Affiliate Commission Refit (`STAT-04`)**: Removing `cost_affiliate` increases Meridian Q4 hold-out MAE/mean from **`8.56%` to `11.30%`**.
- **Job-Order Race (`API-05`)**: Running `execute_meridian_job` followed by `execute_analysis_job` overwrites `overview` KPI `"Q4 2024 hold-out MAE / mean = 8.5% (Target ≤ 15 % · Meridian MMM)"` with `"Q4 2024 hold-out MAE / mean = 9.6% (Target ≤ 15 % · OLS-2 Meridian inputs (Meridian pending))"` and deletes the `Meridian MMM` column from `benchmark`.
- **Short Dataset Hold-Out (`STAT-02`)**: 390-day dataset with `holdout_days=200` trains on all 390 days (`reported_holdout_days = 0`) and reports `train_mae_over_mean = 8.09%` as `holdout_mae_over_mean = 8.09%`.

---

## 5. Required New Tests to Verify Remediation (`T-01` – `T-43`)

| Test ID | Target Layer | Test Specification | Verifies Finding |
|---|---|---|---|
| `T-01` | Unit (`workbook.py`) | Upload with duplicate `(date, channel)` rows raises `WorkbookValidationError` (HTTP 422) or emits blocking warning; `total_sales` is not inflated. | `DATA-01` |
| `T-02` | Unit (`workbook.py`, `target.py`) | Upload with negative `costs` or `sales_by_channel <= 0` raises `WorkbookValidationError` or handles non-positive values without crashing `np.log(y)`. | `DATA-04` |
| `T-03` | Unit (`spec.py`) | Days with missing channel rows include `missing_<slug>` indicator controls in Meridian `control_cols` (or are imputed/masked). | `DATA-03` |
| `T-04` | Unit (`forecast.py`) | `load_budget_plan` / `validate_plan` rejects plans containing unknown columns or missing active paid channels with HTTP 422. | `DATA-02` |
| `T-05` | Unit (`spec.py`) | `MeridianSpecConfig.from_role_config` on a 390-day `2022–2023` index with `holdout_days=200` either raises HTTP 422 (insufficient training window) or slices the final 200 days of `index`, never `HOLDOUT_Q4_2024`. | `STAT-02`, `DATA-05` |
| `T-06` | Unit (`analysis.py`) | `build_meridian_model_section` with an empty `test_df` never substitutes `m_train` for `m_test`. | `STAT-02` |
| `T-07` | Unit (`optimise.py`) | `optimise_hill_response_curves` with `budget < sum(lo)` or `budget > sum(hi)` raises a `ValueError` (HTTP 422) stating the feasible budget interval $[\sum \text{lo}, \sum \text{hi}]$. | `STAT-03` |
| `T-08` | Unit (`optimise.py`) | `run_constrained_optimisation` with `include_catalog=False` keeps `optimal_spend == historical_spend` for Catalog across all feasible budgets, and post-SLSQP allocations satisfy `lo <= x_opt <= hi` to `1e-6`. | `STAT-03` |
| `T-09` | Unit (`api/optimiser.py`) | `OptimisationCreate` normalises display names to slugs, rejects unknown channels, rejects `lo < 0` or `lo > hi` with HTTP 422, and enforces a single explicit unit (`multiplier` vs. `eur`). | `API-03` |
| `T-10` | Unit (`api/optimiser.py`, `optimise.py`) | `max_multiplier` is capped at the response-curve grid maximum (`2.0×`), and summary keys distinguish `incremental_media_sales` from `total_sales`. | `STAT-09` |
| `T-11` | Unit (`stages.py`) | `build_overview_section` with `holdout_mae_over_mean = 0.22` renders `"No — exceeds the 15% threshold"` and sets KPI status to `"warning"`/`"error"`. | `STAT-01` |
| `T-12` | Unit (`stages.py`) | `build_brief_section(roi_df=None)` renders `"Meridian ROI pending"` for Catalog and derives Q2 channel importance dynamically from `importance_df`. | `TXT-02` |
| `T-13` | Unit (`ols.py`, `stages.py`) | `build_benchmark_section` dynamically branches narrative callouts based on whether `meridian_mae <= ols2_mae` on primary and forward hold-outs. | `TXT-01` |
| `T-14` | Unit (`features.py`, `forecast.py`) | `extend_history` preserves the exact historical `trend` values on `wide` when future plan rows are appended, and `evaluate_forecast_scenario` computes `pred_draws` draw-by-draw (`base_pred_draws + (alt_expected - base_expected)`). | `STAT-11`, `STAT-12` |
| `T-20` | API (`test_api.py`) | With `MMM_APP_ACCESS_CODE` set, every `GET` endpoint under `/api/v1/runs*`, `/sections*`, `/forecasts*`, `/optimisations*`, `/artifacts*` returns `HTTP 401` without `X-Access-Code`, and invalid codes on write endpoints trigger the rate limiter. | `SEC-01`, `SEC-02` |
| `T-21` | API (`test_api.py`) | `PATCH /api/v1/runs/{id}` when `run.status != "draft"` returns `HTTP 409`. | `API-01` |
| `T-22` | API (`test_api.py`) | Atomic `POST /api/v1/runs/{id}/rerun` endpoint validates settings and queue capacity before mutating `run.config` or `run.status`. | `API-01` |
| `T-23` | API (`test_api.py`) | Re-running a run deletes or marks stale all prior `Forecast`, `ForecastScenario`, and `OptimisationJob` rows and invalidates `_INTERACTIVE_CACHE`. | `API-02` |
| `T-24` | API (`test_api.py`) | Deferring a duplicate job lock catches `AlreadyEnqueued` before committing `run.status = "queued"` and returns `HTTP 409`. | `API-04` |
| `T-25` | Worker (`test_jobs.py`) | `run_meridian_job` and `run_analysis_job` cannot overwrite completed Meridian sections with `"Meridian pending"` drafts, and `tempfile.mkdtemp` directories are cleaned up in `finally` blocks. | `API-05`, `PERF-03` |
| `T-30` | Statistical (`slow`) | Synthetic recovery test asserts `prior_sd` vs. `posterior_sd` shrinkage ratio is computed and channels with `post_sd / prior_sd > 0.8` or $\mathbb{P}(\text{ROI} < 1) > 0.5$ are flagged in `extract_roi_summary`. | `STAT-06` |
| `T-37` | Statistical (`slow`) | Back-test card and forecast response compute horizon total 90% PI from the 5th/95th percentiles of `pred_draws.sum(axis=1)` rather than summing daily quantiles. | `STAT-11`, `STAT-12` |
| `T-38` | Statistical (`slow`) | Back-test section reports both `"With realised commission/search spend"` and `"Plan-only (ex-ante controls)"` hold-out MAE/mean. | `STAT-04` |
| `T-40` | Frontend (`vitest`) | Budget input parser in `Optimizer.tsx` parses `"121,8"`, `"121.8M"`, `"121.800.000"`, and `"€ 50 M"` accurately or rejects ambiguous strings with validation feedback. | `UI-01` |
