# MMM Studio — Independent Technical & Statistical Audit Summary

Audit date: 2026-11-03. Audited commit: `ace6bb0` (`main` branch, clean working tree).
Framework under wrapper: `google-meridian[schema]==2.1.0` (TensorFlow 2.21.0, ArviZ 0.19.0, FastAPI 0.162.0, Procrastinate 3.10.0, React 18.3).
No application source code was modified during this audit.

---

## Final Verdict

> **Can this application be trusted to produce statistically valid, reproducible, and commercially reliable Marketing Mix Modeling insights?**

**Conditional for the bundled Tchibo case-study workbook; No for arbitrary customer datasets or commercial multi-user deployment until P0/P1 findings are remediated.**

### Justification by Dimension

1. **Statistical Validity — Mixed (Strong Posterior Arithmetic, Optimistic Presentation & Edge-Case Fragility)**
   - **What is valid**: On the bundled 732-day Tchibo dataset, Meridian 2.1.0 is specified, sampled, and extracted accurately. Independent verification against `meridian.analysis.analyzer.Analyzer` on the stored 4-chain × 1,000-draw model (`run_f1e8df83da`) confirms that daily additive decomposition holds to floating-point precision (max absolute error `7.22e-9 €`, relative error `9.07e-15`), per-channel ROI means and 90% credible intervals match posterior draw ratios to 4 decimal places, zero-spend media channels produce `0.0` incremental outcome, response curves are strictly monotone and equal `1.0000×` table incremental outcome at `1.0×` spend, mROI matches finite-difference curve slopes within `0.5%`, and MCMC diagnostics show clean convergence ($\hat{R} \le 1.004$, bulk ESS $\ge 2,177$).
   - **What is invalid or overstated**:
     - **Unconditional narrative conclusions (`STAT-01`, `TXT-01`, `TXT-02`)**: `build_overview_section` (`pipeline/stages.py:466-477`) unconditionally asserts `"**Yes**: Meridian MMM forecasts the {holdout_label} hold-out with **{q_pct:.1f}% MAE / mean** ... — below the brief's 15 % threshold"`, even when `q_pct > 15%` (reproduced with `22.0% MAE / mean`).
     - **Short-dataset hold-out collapse (`STAT-02`)**: When `len(index) <= 2 * holdout_days`, `MeridianSpecConfig.from_role_config` (`core/meridian/spec.py:119-127`) falls back to hard-coded `HOLDOUT_Q4_2024 = ("2024-10-01", "2025-01-01")`. For any dataset not covering Q4 2024, `build_meridian_model` (`spec.py:258-269`) sets `holdout=None` (training on 100% of rows), and `build_meridian_model_section` (`analysis.py:297-302`) silently substitutes in-sample training metrics (`m_test = m_train`) as out-of-sample hold-out metrics.
     - **Silent constraint rewriting in the interactive optimiser (`STAT-03`)**: `optimise_hill_response_curves` (`core/meridian/optimise.py:117, 123-131, 157-159, 253-254`) silently scales per-channel bounds when the requested budget lies outside $[\sum \text{lo}_i, \sum \text{hi}_i]$—which also unlocks Catalog even when `include_catalog=False`—and rescales post-SLSQP allocations to hit the budget without re-checking bounds, returning `status="succeeded"` even when `scipy.optimize.minimize` fails (`res.success=False`).
     - **Demand-following leak in hold-out evaluation (`STAT-04`)**: Both the Q4 hold-out (`8.5%` MAE/mean) and the `"leak-free"` back-test (`9.2%` MAE/mean) feed realised Affiliate commission (`cost_affiliate`, $r = 0.80$ with Affiliate sales) and realised click-driven SEA spend into the test window while claiming `"genuine forecasts from the budget and the calendar alone"` (`analysis.py:515-516`). Refitting Meridian without the realised Affiliate commission increases Q4 hold-out MAE/mean from `8.56%` to `11.30%` and shifts Display ROI from `0.66` to `2.94`.
     - **Endogenous optimiser headline (`STAT-05`)**: Meridian's `BudgetOptimizer` reports `+11.1%` (`+€44.05M`) incremental sales lift under a green `"success"` badge (`stages.py:414-420`), of which `107%` (`+€47.01M`) comes from expanding SEA spend (`+29.9%`)—the very channel the application flags as an endogenous upper bound.
     - **Prior-dominated / null channels (`STAT-06`)**: Under default `LogNormal(0.2, 0.9)` ROI priors (`spec.py:239-248`), a synthetic channel with true ROI `0.00` receives posterior ROI `0.51` (`90% CI [0.12, 1.34]`, excluding zero), and on the case study PSM's posterior SD is `1.77×` its prior SD without any UI warning.
     - **Miscalibrated out-of-sample intervals & quantile summing (`STAT-11`, `STAT-12`)**: Nominal 90% prediction intervals cover only `79.6%` of Q4 2024 hold-out days and `80.6%` of back-test days, while horizon and weekly totals (`forecast.py:399-402, 583-595`, `tasks.py:537-538`) sum daily 5th and 95th percentiles, inflating independent noise over $T=42$ days by $\sqrt{42} \approx 6.48\times$. Furthermore, `extend_history` (`forecast.py:165-167`) recomputes `trend = np.arange(len(index)) / len(index)` (`features.py:58`) over the combined history+plan index, subtly rescaling historical trend values whenever future plan rows are appended.

2. **Implementation Correctness — Moderate (Silent Data & Input Coercions)**
   - Duplicate `(date, channel)` rows are counted (`workbook.py:194`) without warning and silently summed into `total_sales` and `cost_<slug>` (`tables.py:105-120`, `DATA-01`).
   - Missing channel-date rows on 44 days (38 TV, 4 Catalog, 2 Owned) are zero-filled in `total_sales` (`tables.py:123`), depressing daily sales by an average of `-11.7%` (down to `-42.1%`), while the `missing_<slug>` indicator columns created in `tables.py:141-152` are excluded from Meridian controls (`spec.py:187`, `DATA-03`).
   - Budget-plan uploads (`forecast.py:80-96`, `DATA-02`) silently drop misspelled channel columns and fill omitted paid channels with `€0` spend.
   - Optimiser budget parsing in the React UI (`Optimizer.tsx:47`, `UI-01`) strips non-digits via `replace(/[^0-9.]/g, "")`, turning `"121,8"` into `€1,218`, `"€ 50 M"` into `€50`, and `"121.800.000"` into `NaN`.

3. **Engineering Robustness — Needs Hardening (Mutable Run State, Job Races & Resource Leaks)**
   - `PATCH /api/v1/runs/{run_id}` (`api/runs.py:205-256`, `API-01`) mutates `run.config` in the database on `running` or `succeeded` runs without re-running the pipeline or updating `meta/config.json` on disk.
   - Re-running a completed run (`api/runs.py:283-293`, `API-02`) leaves stale custom forecasts, scenarios, and optimisations in Postgres and in the worker's `_INTERACTIVE_CACHE` (`jobs/tasks.py:585, 671`).
   - Starting analysis and Meridian jobs simultaneously (`api/runs.py:298-319`, `jobs/tasks.py:327-354`, `API-05`) creates a race where `run_meridian_job` runs `s01–s11` outside the `analysis:{run_id}` lock and a slower `run_analysis_job` subsequently overwrites the completed overview/benchmark/brief sections with `"Meridian pending"` drafts.
   - Every Meridian save/load call (`pipeline/stages.py:720, 1071`, `jobs/tasks.py:455, 519`, `PERF-03`) creates `tempfile.mkdtemp(prefix="mmm_serde_")` directories containing 15–50 MB `.binpb` files and never deletes them, leaking `/tmp` (tmpfs RAM on Cloud Run).

4. **Production Readiness & Security — Critical Blocker (`SEC-01`)**
   - Every read route (`GET /api/v1/runs`, `/runs/{id}`, `/sections/*`, `/artifacts/*` including the raw uploaded Excel workbook and `.binpb` models, `/forecasts/*`, `/optimisations/*`) omits `Depends(require_access_code)` (`SEC-01`). Worse, `deploy/deploy.sh:29, 254` deploys with `MMM_APP_ACCESS_CODE=""` by default, disabling authentication even on `POST`, `PATCH`, and `DELETE /runs/{id}`.
   - The `/api/v1/auth/check` rate limiter (`api/deps.py:19-61`, `SEC-02`) is not applied to write endpoints, increments on successful checks, and keys by `request.client.host` without `--proxy-headers` (`deploy/entrypoint.sh:7`), locking out all users behind the Nginx sidecar after 5 page loads in 60 seconds.

---

## Summary Table of Findings by Category and Severity

| Category | Issues | Critical | High | Medium | Low |
|---|---:|---:|---:|---:|---:|
| Meridian integration | 4 | 0 | 0 | 0 | 4 |
| Statistical correctness | 15 | 0 | 6 | 7 | 2 |
| Data processing | 5 | 0 | 2 | 2 | 1 |
| Backend/API | 7 | 0 | 1 | 5 | 1 |
| Frontend/UI | 3 | 0 | 0 | 1 | 2 |
| Performance | 4 | 0 | 0 | 4 | 0 |
| Security | 5 | 1 | 0 | 1 | 3 |
| **Total** | **43** | **1** | **9** | **20** | **13** |

*(In addition to the 43 confirmed bugs above, `BUG_REPORT.md` documents 2 potential bugs requiring validation on non-case-study data, 7 architectural risks `AR-01`..`AR-07`, and 5 improvement recommendations `IR-01`..`IR-05`.)*

---

## Complete Index of Confirmed Findings

| ID | Severity | Category | Short Title | Primary Location |
|---|---|---|---|---|
| `SEC-01` | **P0 — Critical** | Security | Unauthenticated read/download endpoints & empty access code in deploy script | `api/runs.py:173-387`, `deploy/deploy.sh:29,254` |
| `STAT-01` | **P1 — High** | Statistical correctness | Executive summary unconditionally claims hold-out MAE/mean is `< 15%` | `pipeline/stages.py:466-477` |
| `STAT-02` | **P1 — High** | Statistical correctness | Short datasets fall back to hard-coded Q4 2024 dates and report training error as hold-out error | `core/meridian/spec.py:119-127, 258-269`, `analysis.py:297-302` |
| `STAT-03` | **P1 — High** | Statistical correctness | Interactive optimiser silently rewrites infeasible bounds, unlocks fixed Catalog, and ignores SLSQP failures | `core/meridian/optimise.py:117-166, 253-254` |
| `STAT-04` | **P1 — High** | Statistical correctness | Hold-out and back-test feed realised Affiliate commission (`r=0.80` with sales) while claiming budget-only forecast | `core/meridian/spec.py:98-102`, `pipeline/stages.py:925`, `analysis.py:515-516` |
| `STAT-05` | **P1 — High** | Statistical correctness | Optimiser headline (`+11.1%` sales) is `107%` driven by endogenous SEA spend (`+€47.01M` of `+€44.05M`) | `core/meridian/optimise.py:391-417`, `pipeline/stages.py:414-420` |
| `STAT-06` | **P1 — High** | Statistical correctness | Strictly positive `LogNormal` ROI priors assign positive ROI (`0.51 [0.12, 1.34]`) to zero-effect channels without flagging prior dominance | `core/meridian/spec.py:239-248`, `analysis.py:65-91` |
| `DATA-01` | **P1 — High** | Data processing | Duplicate `(date, channel)` rows are counted without warning and silently summed into sales and spend | `core/workbook.py:194`, `core/tables.py:105-120` |
| `DATA-02` | **P1 — High** | Data processing | Budget-plan loader silently ignores unknown/misspelled columns and zero-fills omitted paid channels | `core/meridian/forecast.py:80-96` |
| `API-01` | **P1 — High** | Backend/API | `PATCH /runs/{id}` mutates `running`/`succeeded` runs in DB without re-running or syncing `meta/config.json` | `api/runs.py:205-256`, `pages/RunSetup.tsx:36-47` |
| `SEC-02` | **P2 — Medium** | Security | Rate limiter misses write routes, counts valid checks, leaks memory, and locks out all users behind Nginx | `api/deps.py:19-61`, `api/router.py:30-42`, `deploy/entrypoint.sh:7` |
| `STAT-07` | **P2 — Medium** | Statistical correctness | Model selection grid (`catalog_shift × affiliate_role`) is tuned on the reported Q4 2024 test window | `core/meridian/selection.py:101-116` |
| `STAT-08` | **P2 — Medium** | Statistical correctness | `attributed_roas` empirical Bayes priors compute channel ROAS over the full panel including the hold-out window | `core/meridian/spec.py:143-155`, `selection.py:121` |
| `STAT-09` | **P2 — Medium** | Statistical correctness | Interactive optimiser extrapolates PCHIP beyond `2×` curve grid (up to `5×`) and mislabels incremental media sales as total sales | `core/meridian/optimise.py:106-108, 223-224`, `api/optimiser.py:24-25` |
| `STAT-11` | **P2 — Medium** | Statistical correctness | Nominal 90% prediction intervals achieve `~80%` out-of-sample coverage and collapse parameter uncertainty on scenario deltas | `core/meridian/forecast.py:170-198, 321-323` |
| `STAT-12` | **P2 — Medium** | Statistical correctness | Horizon/weekly intervals sum daily quantiles ($\sqrt{T}$ inflation), and `extend_history` shrinks KPI SD and rescales linear `trend` | `core/meridian/forecast.py:165-167, 399-402`, `core/features.py:54-58` |
| `TXT-01` | **P2 — Medium** | Statistical correctness | Benchmark narrative unconditionally states Meridian beats OLS-2 even when Meridian's hold-out MAE is higher | `core/ols.py:208-219`, `pipeline/stages.py:610-628` |
| `TXT-02` | **P2 — Medium** | Statistical correctness | Draft management brief fabricates `"Catalog revenue ROI ≈ 1"` before Meridian runs and hard-codes channel importance | `pipeline/stages.py:216-225, 290, 307` |
| `DATA-03` | **P2 — Medium** | Data processing | Missing channel-date rows depress `total_sales` on 44 days (`-11.7%` mean) and `missing_<slug>` flags are omitted from Meridian controls | `core/tables.py:123-152`, `core/meridian/spec.py:187` |
| `DATA-04` | **P2 — Medium** | Data processing | Negative cost/sales values are accepted without warning, and `total_sales <= 0` crashes `explore_target` on `np.log(y)` | `core/workbook.py:195-197`, `core/eda/target.py:39` |
| `API-02` | **P2 — Medium** | Backend/API | Re-running a run preserves stale custom forecasts, scenarios, optimisations, and `_INTERACTIVE_CACHE` entries | `api/runs.py:283-293`, `jobs/tasks.py:585, 671` |
| `API-03` | **P2 — Medium** | Backend/API | Optimiser `channel_bounds` ignores display-name keys, accepts negative/reversed bounds, and mixes multiplier vs EUR units at `10.0` | `api/optimiser.py:21-27`, `core/meridian/optimise.py:255-261` |
| `API-04` | **P2 — Medium** | Backend/API | Re-running while a queued job holds the lock raises unhandled `AlreadyEnqueued` (`HTTP 500`) after setting `run.status="queued"` | `api/runs.py:283-319`, `jobs/app.py:117-123` |
| `API-05` | **P2 — Medium** | Backend/API | Job ordering race between `run_meridian_job` and `run_analysis_job` overwrites completed Meridian overview/benchmark with `"pending"` drafts | `jobs/tasks.py:327-354`, `pipeline/stages.py:803-806`, `context.py:78-96` |
| `API-06` | **P2 — Medium** | Backend/API | `GET` endpoints perform unguarded database/storage writes (`sweep_stale_runs`, `_ensure_default_forecast`) | `api/runs.py:181, 200`, `api/forecasts.py:42-119` |
| `PERF-01` | **P2 — Medium** | Performance | Synchronous Excel/CSV parsing and disk I/O inside `async def` handlers block the FastAPI event loop | `api/runs.py:92-170`, `api/forecasts.py:130-189, 250-292` |
| `PERF-02` | **P2 — Medium** | Performance | `sweep_stale_runs` and `get_queue_info` open 2–4 raw unpooled `psycopg.connect` TCP connections per request | `jobs/app.py:50-103, 126-222` |
| `PERF-03` | **P2 — Medium** | Performance | `forecast_budget_plan` runs MCMC in-process (no cancel/timeout), and `tempfile.mkdtemp` leaks 15–50 MB `.binpb` files in `/tmp` | `core/meridian/forecast.py:214-218`, `pipeline/stages.py:720, 1071`, `jobs/tasks.py:455, 519` |
| `PERF-04` | **P2 — Medium** | Performance | `wake_worker` launches a new 4-vCPU / 8-GiB Cloud Run Job execution on every interactive slider tweak | `jobs/cloud_run.py:23-38`, `jobs/app.py:122` |
| `UI-01` | **P2 — Medium** | Frontend/UI | Optimiser budget input `replace(/[^0-9.]/g, "")` silently corrupts comma decimals (`"121,8"` → `€1,218`) and unit suffixes (`"50M"` → `€50`) | `frontend/src/pages/Optimizer.tsx:47` |
| `STAT-10` | **P3 — Low** | Statistical correctness | OLS-2 `"Meridian inputs"` uses hard-coded `shift=2, decay=0.8` instead of the run's configured `in_home_shift_days` | `core/ols.py:59-61`, `core/features.py:86-94` |
| `STAT-13` | **P3 — Low** | Statistical correctness | Selection grid sets `as_controls=()` for `role="media"` and ignores custom/attributed priors for Affiliate | `core/meridian/selection.py:103`, `spec.py:130-137, 224` |
| `MER-01` | **P3 — Low** | Meridian integration | `custom_roi_priors` keys are not validated against active paid media channels | `core/model_settings.py:57-65`, `core/meridian/spec.py:157-162` |
| `MER-02` | **P3 — Low** | Meridian integration | `from_role_config` mutates process-global `CHANNEL_DISPLAY_NAMES` dictionary | `core/meridian/spec.py:31-39, 90-91` |
| `MER-03` | **P3 — Low** | Meridian integration | UI exposes `paid` role for Affiliate (`cost_affiliate`), but `spec.py:101` and `schema.py:146` silently force it back to a control | `core/schema.py:64-65, 146`, `core/meridian/spec.py:101` |
| `MER-04` | **P3 — Low** | Meridian integration | Any channel with $\le 15\%$ active days is auto-classified as `print_event`; multiple `print_event` channels collide | `core/schema.py:326`, `core/meridian/spec.py:106-118`, `core/tables.py:164` |
| `DATA-05` | **P3 — Low** | Data processing | No minimum history length check against `holdout_days`; `structural_break_year` and `allow_single_year` are dead fields | `core/schema.py:116-119`, `core/model_settings.py:34` |
| `API-07` | **P3 — Low** | Backend/API | Stale-job sweeper never cleans stuck `ForecastScenario` or `OptimisationJob` rows, and `meridian_queue_load` fails open (`0, 0`) on DB error | `jobs/app.py:126-222, 68-69` |
| `UI-02` | **P3 — Low** | Frontend/UI | Currency formatting boundary produces `"€1,000.0k"` / `"€1000k"` between `999,950` and `999,999.9` | `report/narrative.py:12-23`, `components/Forecast/fmt.ts:3-10` |
| `UI-03` | **P3 — Low** | Frontend/UI | Missing case-study channels render literal `"nan×"` / `"nan%"` in callouts; `"Media share of total sales"` divides by modelled expected outcome | `core/eda/spend.py:92-97`, `core/meridian/analysis.py:372-377` |
| `SEC-03` | **P3 — Low** | Security | `_check_xlsx_archive` checks spoofable `ZipInfo.file_size` headers rather than streaming decompressed bytes | `core/workbook.py:43-52` |
| `SEC-04` | **P3 — Low** | Security | Raw internal exception strings and file paths are returned in HTTP 422/500 responses and persisted in `run.error` | `api/runs.py:120, 146, 224`, `jobs/tasks.py:282, 412` |
| `SEC-05` | **P3 — Low** | Security | `react-router` advisories (`GHSA-2w69-qvjg-hvjx`), unpinned `uv:latest`, CLI DB password in `deploy.sh`, unescaped CSV formula export | `frontend/package.json`, `Dockerfile:1`, `deploy/deploy.sh:249-254`, `SectionRenderer.tsx:261-273` |

---

## Audit Deliverables Index

All six deliverables are located in `/audit/` (and mirrored in `src/app/audit/`):

1. [`AUDIT_SUMMARY.md`](AUDIT_SUMMARY.md) — Executive summary, final trustworthiness verdict, and severity breakdown.
2. [`ARCHITECTURE_REVIEW.md`](ARCHITECTURE_REVIEW.md) — Full stack map, module inventory, workflow trace, and Meridian 2.1.0 API audit.
3. [`BUG_REPORT.md`](BUG_REPORT.md) — Exhaustive 11-field report for all 43 confirmed bugs, 2 potential bugs, 7 architectural risks, and 5 improvement recommendations.
4. [`STATISTICAL_VALIDATION.md`](STATISTICAL_VALIDATION.md) — Empirical posterior verification, synthetic ground-truth recovery, hold-out leakage quantification, and interval calibration.
5. [`TEST_COVERAGE.md`](TEST_COVERAGE.md) — Evaluation of the 32 existing tests, false-confidence tests, executed audit scripts, and 29 new test specifications.
6. [`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md) — Phased remediation roadmap (Phases 0–3) with effort estimates and PR sequencing.
