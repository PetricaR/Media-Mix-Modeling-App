# Comprehensive Bug Report — MMM Studio (`google-meridian==2.1.0`)

Audit date: 2026-11-03. Audited commit: `ace6bb0` (`main` branch, clean working tree).
All backend file paths are relative to `src/app/backend/mmm_studio/` unless prefixed with `frontend/`, `deploy/`, or `backend/`.

---

## Classification & Summary Table

Every issue below is classified into one of four categories as required by the audit specification:
1. **Section 1 — Confirmed Bugs (`43` issues)**: Verified via executed reproduction scripts (`src/app/audit/scripts/`) or deterministic static path trace. Every confirmed bug includes all **11 mandatory audit fields**.
2. **Section 2 — Potential Bugs Requiring Further Validation (`2` issues)**: Plausible failure modes whose empirical magnitude depends on external workloads or specific multi-channel configurations.
3. **Section 3 — Architectural Risks (`7` issues, `AR-01`–`AR-07`)**: Structural design choices that limit generalisation, multi-tenancy, or operational resilience.
4. **Section 4 — Improvement Recommendations (`5` items, `IR-01`–`IR-05`)**: Statistical and UX enhancements beyond bug remediation.

| Category | Issues | Critical (`P0`) | High (`P1`) | Medium (`P2`) | Low (`P3`) |
|---|---:|---:|---:|---:|---:|
| Meridian integration | 4 | 0 | 0 | 0 | 4 |
| Statistical correctness | 15 | 0 | 6 | 7 | 2 |
| Data processing | 5 | 0 | 2 | 2 | 1 |
| Backend/API | 7 | 0 | 1 | 5 | 1 |
| Frontend/UI | 3 | 0 | 0 | 1 | 2 |
| Performance | 4 | 0 | 0 | 4 | 0 |
| Security | 5 | 1 | 0 | 1 | 3 |
| **Total** | **43** | **1** | **9** | **20** | **13** |

---

## Section 1 — Confirmed Bugs (43 Issues)

### P0 — Critical (1 Issue)

#### `SEC-01` — Unauthenticated Read/Download Endpoints & Empty Default Access Code in Deployment

1. **Unique issue ID**: `SEC-01`
2. **Severity**: `P0 — Critical` (Confirmed & reproduced via `scripts/audit_api_checks.py` check `SEC-1`)
3. **Affected file and exact line number**:
   - `api/deps.py:43-61` (`require_access_code` defined but attached only to write routes)
   - `api/runs.py:173-202` (`list_runs`, `get_run` — no `Depends(require_access_code)`)
   - `api/sections.py:18-69` (`list_sections`, `get_section` — unauthenticated)
   - `api/exports.py:43-114` (`list_artifacts`, `download_artifact` — unauthenticated download of `input/workbook.xlsx` and `models/*.binpb`)
   - `api/forecasts.py:122-127, 192-214, 305-320` (`list_forecasts`, `get_forecast`, `get_scenario` — unauthenticated)
   - `api/optimiser.py:48-61, 107-122` (`list_optimisations`, `get_optimisation` — unauthenticated)
   - `deploy/deploy.sh:29, 254` (`APP_ACCESS_CODE="${APP_ACCESS_CODE:-}"` defaults to empty on Cloud Run)
4. **Description of the problem**: Even when `MMM_APP_ACCESS_CODE` is configured, `Depends(require_access_code)` is attached only to `POST`, `PATCH`, and `DELETE` endpoints. Every `GET` endpoint—including run listing, executive report sections, and raw artifact downloads (`input/workbook.xlsx` containing daily channel sales and spend, plus serialized `.binpb` Meridian models)—is publicly accessible without an `X-Access-Code` header. Furthermore, `deploy/deploy.sh` deploys the Cloud Run service with `--allow-unauthenticated` and an empty `MMM_APP_ACCESS_CODE` by default, leaving even `DELETE /api/v1/runs/{run_id}` open to anonymous callers.
5. **Why it is a bug**: Marketing Mix Modeling workbooks contain confidential daily revenue, marketing spend, and media agency rate data. Leaving all read and artifact download endpoints unauthenticated exposes proprietary financial data to anyone who can reach the URL.
6. **Steps to reproduce**:
   1. Start the API with `MMM_APP_ACCESS_CODE=s3cret-code`.
   2. Upload a workbook via `POST /api/v1/runs` with header `X-Access-Code: s3cret-code`.
   3. Issue `GET /api/v1/runs`, `GET /api/v1/runs/{id}`, and `GET /api/v1/runs/{id}/artifacts/input/workbook.xlsx` **without** the `X-Access-Code` header (automated in `scripts/audit_api_checks.py`).
7. **Expected behavior**: All endpoints under `/api/v1/*` (except `/api/v1/health` and `/api/v1/auth/check`) return `HTTP 401 Unauthorized` when `MMM_APP_ACCESS_CODE` is set and the request omits a valid `X-Access-Code`.
8. **Actual behavior**: All four unauthenticated `GET` requests return `HTTP 200 OK`, and `GET /api/v1/runs/{id}/artifacts/input/workbook.xlsx` streams all `399,012` bytes of the confidential Excel workbook.
9. **Business or statistical impact**: Complete disclosure of confidential marketing spend, daily channel revenue, and strategic budget plans across all uploaded runs; unauthenticated run deletion in default Cloud Run deployments.
10. **Recommended solution**: Attach `dependencies=[Depends(require_access_code)]` at the router level in `api/router.py` (exempting only `/health` and `/auth/check`), require a non-empty `APP_ACCESS_CODE` or Cloud Run IAP in `deploy/deploy.sh`, and update `tests/test_api.py:217-220`.
11. **Test required to verify the fix**: `T-20` — Assert that with `MMM_APP_ACCESS_CODE` configured, every `GET` route under `/api/v1/runs*`, `/sections*`, `/forecasts*`, `/optimisations*`, and `/artifacts*` returns `HTTP 401` without `X-Access-Code`.

---

### P1 — High (9 Issues)

#### `STAT-01` — Executive Overview Unconditionally Claims Hold-Out MAE/Mean Is `< 15%`

1. **Unique issue ID**: `STAT-01`
2. **Severity**: `P1 — High` (Confirmed & reproduced via `scripts/audit_core_checks.py` check `TXT-1`)
3. **Affected file and exact line number**: `pipeline/stages.py:466-477` (specifically line 470: `" — below the brief's 15 % threshold. "`)
4. **Description of the problem**: In `build_overview_section`, the executive callout answering *"Can daily sales be forecast from the marketing budget?"* sets `status = "success" if q_pct <= 15.0 else "warning"`, but the prose string on lines 467–473 is hardcoded to say `"**Yes**: Meridian MMM forecasts the {holdout_label} hold-out with **{q_pct:.1f}% MAE / mean** and the {forward_label} with **{f_pct:.1f}%** — below the brief's 15 % threshold."` regardless of the value of `q_pct` or `f_pct`.
5. **Why it is a bug**: When a model fails the 15% accuracy threshold (e.g. `22.0%`), the narrative explicitly contradicts the number inside the same sentence.
6. **Steps to reproduce**: Call `build_overview_section(wide, facts, ols_res, {"holdout_q4_2024": {"MAE_over_mean": 0.22, "R2": -0.1, "MAE": 500000, "RMSE": 600000, "MAPE": 0.22, "bias": 10000}, "forward_q3_2024": {"MAE_over_mean": 0.22}})` (reproduced in `scripts/audit_core_checks.py`).
7. **Expected behavior**: When `q_pct > 15.0` or `f_pct > 15.0`, the executive answer states `"**No** (or **Partially**): Meridian MMM's hold-out MAE / mean is **{q_pct:.1f}%** — exceeding the brief's 15 % threshold."`
8. **Actual behavior**: Outputs `"**Can daily sales be forecast from the marketing budget?** Yes: Meridian MMM forecasts the Q4 2024 hold-out with **22.0% MAE / mean** and the Q3 2024 forward hold-out with **22.0%** — below the brief's 15 % threshold."`
9. **Business or statistical impact**: Decision-makers reading the Executive Overview receive an affirmative `"Yes — below 15%"` sign-off on an inaccurate model.
10. **Recommended solution**: Branch the narrative template in `pipeline/stages.py:466-477` on `q_pct <= 15.0` and `f_pct <= 15.0`, as well as whether `q_pct <= ols2_q4`.
11. **Test required to verify the fix**: `T-11` — Unit test passing `MAE_over_mean = 0.22` into `build_overview_section` and asserting the callout text states the 15% target was not met.

---

#### `STAT-02` — Short Datasets Fall Back to Hard-Coded Q4 2024 Dates and Report Training Error as Hold-Out Error

1. **Unique issue ID**: `STAT-02`
2. **Severity**: `P1 — High` (Confirmed & reproduced via `scripts/audit_race_and_holdout.py` and `scripts/audit_core_checks.py` check `HO-1`)
3. **Affected file and exact line number**:
   - `core/meridian/spec.py:119-127` (`if index is not None and len(index) > 2 * holdout_days: ... else: holdout = HOLDOUT_Q4_2024`)
   - `core/meridian/spec.py:258-269` (`if start <= end:` else `holdout_spec = None`)
   - `core/meridian/analysis.py:297-302` (`m_test = ... if len(test_df) else m_train`)
4. **Description of the problem**: When `len(index) <= 2 * holdout_days` (for example, a 390-day dataset with `holdout_days = 200`), `MeridianSpecConfig.from_role_config` ignores the dataset's actual dates and falls back to `HOLDOUT_Q4_2024 = ("2024-10-01", "2025-01-01")`. If the dataset ends before `2024-10-01` (or starts after `2025-01-01`), `build_meridian_model` (`spec.py:263-268`) clamps `start = max("2024-10-01", t_min)` and `end = min("2025-01-01", t_max)`, finds `start > end`, and sets `holdout_spec = None`—training Meridian on **100% of the data**. Then `build_meridian_model_section` (`analysis.py:297-302`) sees `len(test_df) == 0`, executes `m_test = m_train`, and reports in-sample training error as out-of-sample hold-out error (`"Target ≤ 15 % · met"`).
5. **Why it is a bug**: In-sample training error is silently substituted for out-of-sample hold-out error with `reported_holdout_days = 0`, while the OLS benchmark in the same run evaluates on a real 200-day hold-out (`features.holdout_windows`).
6. **Steps to reproduce**: Slice `case_study.xlsx` to 390 days (`2022-01-01..2023-01-25`), set `holdout_days = 200`, fit Meridian (`SMOKE`), and call `build_meridian_model_section` (automated in `scripts/audit_race_and_holdout.py`).
7. **Expected behavior**: Either reject `holdout_days` when it leaves insufficient training history (HTTP 422), or slice the last `holdout_days` of `index`; never fall back to `HOLDOUT_Q4_2024` or substitute `m_train` for `m_test`.
8. **Actual behavior**: `cfg.holdout` becomes `('2024-10-01', '2025-01-01')`, `reported_holdout_days = 0`, and the primary KPI card renders `"hold-out hold-out MAE / mean = 8.1% (Target ≤ 15 % · met)"`, identical to `train_mae_over_mean = 8.09%`.
9. **Business or statistical impact**: Overfitted in-sample accuracy is presented as validated out-of-sample forecasting performance on any short or non-2024 dataset.
10. **Recommended solution**: Remove the `HOLDOUT_Q4_2024` fallback in `spec.py:127`, enforce `len(index) >= 365 + holdout_days` at validation time, and raise an error (or mark hold-out metrics `None`) in `analysis.py:297-302` if `test_df` is empty.
11. **Test required to verify the fix**: `T-05` and `T-06`.

---

#### `STAT-03` — Interactive Budget Optimiser Silently Rewrites Infeasible Bounds, Unlocks Fixed Catalog, and Ignores SLSQP Failure

1. **Unique issue ID**: `STAT-03`
2. **Severity**: `P1 — High` (Confirmed & reproduced via `scripts/audit_core_checks.py` checks `OPT-1`/`OPT-2` and `scripts/audit_api_checks.py` check `OPT-API-4`)
3. **Affected file and exact line number**:
   - `core/meridian/optimise.py:117` (`lo[i] = hi[i] = c_sp` when `include_catalog=False`)
   - `core/meridian/optimise.py:123-131` (scales all `lo` by `(budget / lo.sum()) * 0.95` or all `hi` by `(budget / hi.sum()) * 1.05`)
   - `core/meridian/optimise.py:149-160` (`res.success` ignored; `x_opt` clipped on line 157 and then rescaled `x_opt * (budget / x_opt.sum())` without re-clipping on line 159)
   - `core/meridian/optimise.py:253-254` (`run_constrained_optimisation` sets `bounds["catalog"] = (c_sp, c_sp)`)
4. **Description of the problem**: `optimise_hill_response_curves` contains three compounding constraint bugs:
   - First, when `total_budget < lo.sum()` or `total_budget > hi.sum()`, lines 123–131 silently scale **every** channel's lower or upper bound until the budget fits, without raising an error or setting an infeasibility flag.
   - Second, when the user disables Catalog optimisation (`include_catalog=False`), Catalog's bounds are set to `(c_sp, c_sp)`. When `total_budget > hi.sum()`, lines 128–131 scale `hi` upward while `lo` remains `c_sp`, unlocking Catalog and reallocating spend to it while returning `"include_catalog": False`.
   - Third, after running SLSQP (`line 149`), if `res.success` is `False` it silently falls back to `x0` (`line 157`), clips to `[lo, hi]`, and then multiplies all channels by `budget / x_opt.sum()` (`line 159`) without re-clipping, pushing boundary-pinned channels outside `[lo, hi]`.
5. **Why it is a bug**: A constrained budget optimiser must either satisfy the user's explicit channel bounds and channel locks or report that the constraints are mutually infeasible. Silently returning bound-violating allocations as `"succeeded"` invalidates the tool's core mathematical contract.
6. **Steps to reproduce**:
   1. Call `POST /api/v1/runs/{id}/optimisations` with `{"total_budget": 50000000, "min_multiplier": 0.9, "max_multiplier": 1.1}` on a run with `€121.8M` historical spend (`OPT-API-4`).
   2. Call `run_constrained_optimisation` with `include_catalog=False` and `total_budget = 1.5×` historical spend (`OPT-1`).
7. **Expected behavior**: Request 1 returns HTTP 422 explaining that `min_multiplier=0.9` requires at least `€109.6M`. Request 2 keeps Catalog spend fixed at `€20,000,000` (or returns HTTP 422 if other channels cannot absorb the budget within their upper bounds).
8. **Actual behavior**: Request 1 succeeds (`status="succeeded"`) and allocates **`0.411×`** to all 6 channels. Request 2 increases locked Catalog spend from **`€20,000,000` to `€26,582,278`** while reporting `"include_catalog": False`.
9. **Business or statistical impact**: Media planners receive erroneous budget allocations that violate contractual commitments (e.g. locked print catalog budgets or minimum agency spend commitments).
10. **Recommended solution**: Remove lines 123–131 and line 159 in `core/meridian/optimise.py`; subtract fixed channels (`include_catalog=False` or `lo == hi`) from the free budget before solving; validate `lo_free.sum() <= free_budget <= hi_free.sum()` up front (raising HTTP 422 with the feasible range); and verify `res.success` and constraint tolerances after SLSQP.
11. **Test required to verify the fix**: `T-07` and `T-08`.

---

#### `STAT-04` — Hold-Out and Back-Test Accuracy Rely on Realised Demand-Following Costs (`cost_affiliate`, `cost_sea`)

1. **Unique issue ID**: `STAT-04`
2. **Severity**: `P1 — High` (Confirmed & reproduced via `scripts/audit_meridian_fits.py` and `scripts/audit_core_checks.py` check `STAT-leak`)
3. **Affected file and exact line number**:
   - `core/meridian/spec.py:98-102` (`cost_affiliate` included as a control across all training and hold-out days)
   - `pipeline/stages.py:925` (back-test plan uses `wide_model.loc[bt_start:bt_end]` containing realised Q4 `cost_affiliate` and `cost_sea`)
   - `core/meridian/analysis.py:515-516` (claims *"These are genuine forecasts from the budget and the calendar, not retrospective fits"*)
4. **Description of the problem**: Affiliate cost (`cost_affiliate`) is a commission paid on realised Affiliate orders ($\text{corr}(\text{cost\_affiliate}, \text{sales\_affiliate}) = 0.797$), and SEA cost (`cost_sea`) is billed per click ($\text{corr}(\text{cost\_sea}, \text{clicks\_sea}) = 0.959$). Both the Q4 2024 hold-out (`8.5%` MAE/mean) and the `"leak-free"` back-test (`9.2%` MAE/mean) feed the **realised** daily Q4 values of `cost_affiliate` and `cost_sea` into the model while claiming the forecast is generated from an ex-ante marketing budget and calendar alone.
5. **Why it is a bug**: At budget-planning time (6–12 weeks ahead), a planner does not know realised daily Affiliate commissions or realised daily search click volume—those quantities are contemporaneous functions of realised demand.
6. **Steps to reproduce**: Run `scripts/audit_meridian_fits.py` (which refits Meridian under FAST sampling with `seed=0` with and without `cost_affiliate`) and `scripts/audit_core_checks.py` (`STAT-leak`).
7. **Expected behavior**: Hold-out evaluation either replaces demand-following controls in the test window with ex-ante planned/climatological values, or explicitly labels the `8.5%` / `9.2%` metric as conditional on realised commission/search volume alongside a true plan-only metric.
8. **Actual behavior**: Removing realised `cost_affiliate` increases Meridian Q4 hold-out MAE/mean from **`8.56%` to `11.30%`** (and moves Display ROI from `0.66` to `2.94`); in OLS-2, Q3 forward hold-out MAE/mean rises from `14.0%` to `15.6%` without Affiliate commission and to `24.9%` without realised SEA spend.
9. **Business or statistical impact**: Overstates true ex-ante budget-forecasting accuracy by `2.7–10.9 percentage points` and makes paid media ROIs sensitive to an endogenous control.
10. **Recommended solution**: Add a `"Plan-only (ex-ante)"` back-test metric that substitutes historical same-weekday/seasonal expectations for `cost_affiliate` (and optionally `cost_sea`) in the test window, and revise the claim at `analysis.py:515-516`.
11. **Test required to verify the fix**: `T-38`.

---

#### `STAT-05` — Native Optimiser Headline Lift (`+11.1%`) Comes `107%` from SEA, Which the App Flags as Non-Causal

1. **Unique issue ID**: `STAT-05`
2. **Severity**: `P1 — High` (Confirmed & reproduced on stored full run `run_f1e8df83da`)
3. **Affected file and exact line number**:
   - `core/meridian/optimise.py:391-417` (`KPICard` `"Incremental sales, optimised"` rendered with `status="success"`)
   - `pipeline/stages.py:414-420` (`build_overview_section` quotes the `+11.1%` lift as a headline commercial recommendation)
4. **Description of the problem**: Meridian's native `BudgetOptimizer` (`optimise.py:25-48`) reallocates the `€121.8M` budget within $\pm 30\%$ per channel and reports **`+€44.05M` (`+11.1%`)** incremental sales lift. Inspecting the per-channel table (`optimiser.json`) shows that expanding `sea` by `+29.9%` (`+€11.54M`) generates **`+€47.01M` (`106.7%` of the total lift)**, while all other five channels combined net **`-€2.97M`**. Even though the app's own Drivers & ROI section warns that SEA's ROI (`8.40`) is an endogenous upper bound driven by brand/product search demand, the optimiser badges `+11.1%` as `"success"` (green) and promotes it to the Executive Overview.
5. **Why it is a bug**: Presenting a budget reallocation whose entire positive lift derives from a channel the application itself diagnoses as endogenously inflated gives leadership a contradictory, high-risk financial recommendation.
6. **Steps to reproduce**: Inspect `sections/optimiser.json` and `sections/overview.json` in `run_f1e8df83da` (verified in `scripts/audit_meridian_posterior.py`).
7. **Expected behavior**: The optimiser section decomposes the lift by channel, runs a companion optimisation holding `sea` capped at `1.0×` historical spend, and sets the headline KPI badge to `"warning"` whenever $>50\%$ of the lift comes from a demand-following channel.
8. **Actual behavior**: Renders a green `"success"` KPI card (`"+€44.1M vs current (+11.1%)"`) on both the Optimiser and Overview pages.
9. **Business or statistical impact**: Encourages shifting `€11.5M` out of Display, Social Media, and Display Network into SEA based on reverse-causality search clicks.
10. **Recommended solution**: Compute and display a `"SEA-capped (≤ 1.0×)"` optimisation alongside the unconstrained run in `core/meridian/optimise.py:312-430`, and set `status="warning"` when endogenous channels dominate the lift.
11. **Test required to verify the fix**: Unit test on `build_optimiser_section` verifying that when `sea` accounts for $>50\%$ of `delta_sales`, the KPI status is `"warning"` and the non-SEA lift is explicitly reported.

---

#### `STAT-06` — Null-Effect Channels Receive Positive ROI Excluding Zero, and Prior-Dominated Channels Are Not Flagged

1. **Unique issue ID**: `STAT-06`
2. **Severity**: `P1 — High` (Confirmed & reproduced via `scripts/audit_meridian_fits.py` and `scripts/audit_meridian_posterior.py`)
3. **Affected file and exact line number**:
   - `core/meridian/spec.py:239-248` (`tfp.distributions.LogNormal(0.2, 0.9)` applied to every paid media channel)
   - `core/meridian/analysis.py:65-91, 360-460` (`extract_roi_summary` and `build_drivers_section` omit $\mathbb{P}(\text{ROI} < 1)$ and prior-vs-posterior shrinkage)
4. **Description of the problem**: Meridian's default `LogNormal(0.2, 0.9)` ROI prior has strictly positive support $(0, \infty)$ with prior mean `1.83` and 90% prior interval `[0.28, 5.38]`. When a channel has zero true causal effect or very small spend (`0.16%` of budget for `psm`), the posterior remains positive and prior-dominated. On a 730-day synthetic dataset where `display` has **true ROI = `0.00`**, the pipeline estimates `display` ROI = **`0.51` (`90% CI [0.12, 1.34]`)**. On the stored Tchibo run (`run_f1e8df83da`), `psm` has posterior SD / prior SD = **`1.77`** (its posterior is wider than its prior) and `display_network` has ratio **`0.64`**, yet both are displayed in the ROI table identically to well-identified channels (`catalog` ratio `0.03`).
5. **Why it is a bug**: Without reporting $\mathbb{P}(\text{ROI} < 1)$ or flagging channels whose posterior is uninformative relative to the prior, users cannot distinguish data-driven ROI estimates from prior echoes.
6. **Steps to reproduce**: Run `scripts/audit_meridian_fits.py` (synthetic ground-truth recovery) and `scripts/audit_meridian_posterior.py` (`roi_prior_vs_posterior`).
7. **Expected behavior**: The ROI table includes $\mathbb{P}(\text{ROI} < 1)$ (probability of losing money) and an identification badge based on posterior-to-prior SD shrinkage ($\text{SD}_{\text{post}} / \text{SD}_{\text{prior}}$).
8. **Actual behavior**: `display` (true ROI `0.0`) is reported with ROI `0.51 [0.12, 1.34]`; on the case study, `psm` (`ROI = 2.52 [0.31, 8.44]`, $\text{SD}_{\text{post}}/\text{SD}_{\text{prior}} = 1.77$) is presented without any prior-dominance indicator.
9. **Business or statistical impact**: Marketing teams may increase spend on noise channels (`psm` receives `+30%` in the native optimiser) or retain unprofitable channels (`display` has $\mathbb{P}(\text{ROI}<1) = 84.3\%$, `social_media` $78.7\%$, `catalog` $72.0\%$).
10. **Recommended solution**: Compute `p_roi_lt_1` and `post_sd_over_prior_sd` from `mmm.inference_data.prior` and `posterior` in `extract_roi_summary` (`analysis.py:65-91`), display them in the `drivers_roi` table, and flag channels with `post_sd_over_prior_sd > 0.75`.
11. **Test required to verify the fix**: `T-30`.

---

#### `DATA-01` — Duplicate `(date, channel)` Rows Are Counted Without Warning and Silently Summed

1. **Unique issue ID**: `DATA-01`
2. **Severity**: `P1 — High` (Confirmed & reproduced via `scripts/audit_core_checks.py` check `DP-dup` and `scripts/audit_api_checks.py` check `DQ-API`)
3. **Affected file and exact line number**:
   - `core/workbook.py:194` (`duplicate_rows = int(raw.duplicated(subset=[mapping.date, mapping.channel]).sum())`)
   - `core/workbook.py:199-214` (warnings list constructed without checking `duplicate_rows > 0`)
   - `core/tables.py:105-120` (`raw.pivot_table(..., aggfunc="sum")`)
4. **Description of the problem**: `profile_workbook` counts duplicate `(date, channel)` pairs into `profile.duplicate_rows_count`, but emits no validation error or warning when `duplicate_rows_count > 0`. Subsequently, `build_daily_wide_table` pivots the long table with `aggfunc="sum"`, silently double-counting every duplicated row's sales, spend, impressions, and clicks.
5. **Why it is a bug**: Duplicate rows in marketing exports almost always arise from accidental double-appends during ETL rather than sub-daily splits intended for summation. Silent summation corrupts both the target KPI (`total_sales`) and channel spend.
6. **Steps to reproduce**: Append the first 50 rows of `case_study.xlsx` to the end of the sheet and call `profile_workbook` / `build_daily_wide_table` (automated in `scripts/audit_core_checks.py` `DP-dup`).
7. **Expected behavior**: `POST /api/v1/runs` rejects workbooks with duplicate `(date, channel)` rows (`HTTP 422`) or emits a prominent blocking warning requiring explicit user deduplication.
8. **Actual behavior**: Upload succeeds (`HTTP 201`), `warnings` contains no mention of duplicates, `total_sales` is inflated by **`+€4,259,262`**, and `total_cost` is inflated by **`+€2,305,376`**.
9. **Business or statistical impact**: Corrupts total revenue, channel spend, attributed ROAS, and all downstream MMM parameters.
10. **Recommended solution**: Raise `WorkbookValidationError` (or add a blocking warning and deduplicate via `.drop_duplicates(subset=[mapping.date, mapping.channel], keep="first")` when requested) in `core/workbook.py:194-215`.
11. **Test required to verify the fix**: `T-01`.

---

#### `DATA-02` — Budget-Plan Loader Silently Ignores Misspelled Columns and Zero-Fills Omitted Paid Channels

1. **Unique issue ID**: `DATA-02`
2. **Severity**: `P1 — High` (Confirmed & reproduced via `scripts/audit_core_checks.py` check `FC-1` and `scripts/audit_api_checks.py` check `FC-API`)
3. **Affected file and exact line number**:
   - `core/meridian/forecast.py:80-96` (`load_budget_plan` ignores unrecognized columns and fills any absent expected channel with `0.0` at lines 90–91)
   - `core/meridian/forecast.py:99-155` (`validate_plan` never checks that the uploaded file contained the expected paid channel columns before zero-filling)
4. **Description of the problem**: When a user uploads a custom budget plan CSV/XLSX (`POST /api/v1/runs/{run_id}/forecasts`), `load_budget_plan` matches column headers against `expected_channels` (`paid_channels + as_controls`). Any column with a typo (e.g. `catalgo` or `Catalog Spend`) is silently discarded, and any omitted channel is created as a column of `0.0` before `validate_plan` runs.
5. **Why it is a bug**: A single column header typo or naming mismatch causes the model to forecast future sales under **zero spend** for that channel while reporting `status="succeeded"`.
6. **Steps to reproduce**: Upload a budget plan with columns `['date', 'sea', 'catalgo', 'display']` to `POST /api/v1/runs/{id}/forecasts` (`scripts/audit_api_checks.py` `FC-API`).
7. **Expected behavior**: `load_budget_plan` / `validate_plan` returns `HTTP 422` listing unrecognized columns (`['catalgo']`) and missing paid channels (`['catalog', 'display_network', 'social_media', 'psm', 'affiliate']`).
8. **Actual behavior**: Returns `HTTP 202 Accepted` and forecasts the 42-day window with `€0` spend for `catalog`, `affiliate`, `display_network`, `social_media`, and `psm`.
9. **Business or statistical impact**: Produces severely distorted revenue forecasts when users upload spreadsheets with slightly different column names.
10. **Recommended solution**: In `load_budget_plan` (`forecast.py:80-96`), record `unknown_columns` and `missing_channels` before zero-filling and reject them in `validate_plan` (`HTTP 422`).
11. **Test required to verify the fix**: `T-04`.

---

#### `API-01` — `PATCH /runs/{id}` Mutates `running`/`succeeded` Runs in Postgres Without Re-Running or Updating `meta/config.json`

1. **Unique issue ID**: `API-01`
2. **Severity**: `P1 — High` (Confirmed & reproduced via `scripts/audit_api_checks.py` checks `STATE-1` and `STATE-2`)
3. **Affected file and exact line number**:
   - `api/runs.py:205-256` (`update_run_config` has no check on `run.status` and writes only to Postgres `run.config`, never `meta/config.json`)
   - `frontend/src/pages/RunSetup.tsx:36-47` (`handleRerun` calls `updateRun.mutateAsync(...)` followed by `startRun.mutateAsync(...)` as two separate HTTP requests)
   - `jobs/tasks.py:439-442` (`execute_scenario_job` reads `meta/config.json`) vs. `jobs/tasks.py:487-504` (`execute_forecast_job` reads `run.config` from Postgres)
4. **Description of the problem**: `PATCH /api/v1/runs/{run_id}` allows mutating `role_config`, `model_settings`, `sampling_profile`, `seed`, and `skip_meridian` on a run whose status is already `queued`, `running`, or `succeeded`. Moreover, the UI's "Save & Re-run" button sends `PATCH /runs/{id}` first and `POST /runs/{id}/start` second. If `POST /start` fails (e.g., `HTTP 429` queue cap or `HTTP 500` from `API-04`), or if a client calls `PATCH` directly, the database `run.config` displays the new settings while the stored sections, models, and `meta/config.json` reflect the old run.
5. **Why it is a bug**: Breaks provenance and reproducibility: the UI badges a finished run as having been fit with settings (`holdout_days=60`, `seed=99`, `n_fourier=4`) that were never executed, and subsequent custom forecast jobs (`execute_forecast_job`) read the mutated DB config while scenario jobs (`execute_scenario_job`) read the old `meta/config.json`.
6. **Steps to reproduce**:
   1. On a `succeeded` run (`holdout_days=93`), call `PATCH /api/v1/runs/{id}` with `{"model_settings": {"holdout_days": 60, "n_fourier": 4}, "seed": 99}` (`STATE-1`).
   2. Set `max_queued_meridian_runs=0`, call `PATCH` (`200 OK`), then call `POST /start` (`429 Too Many Requests`, `STATE-2`).
7. **Expected behavior**: `PATCH /api/v1/runs/{id}` returns `HTTP 409 Conflict` when `run.status != "draft"`; re-running with new settings is performed via a single atomic `POST /api/v1/runs/{id}/rerun` endpoint that validates queue capacity before mutating `run.config`.
8. **Actual behavior**: `PATCH` returns `HTTP 200 OK` on a `succeeded` run; `run.settings_changes` immediately reports `['Hold-out length=60 days', 'Random seed=99', 'Yearly Fourier pairs=4']` while all charts/metrics remain from the 93-day fit and `meta/config.json` still has `holdout_days=93`.
9. **Business or statistical impact**: Corrupts the audit trail between model configuration and model outputs, and causes downstream custom forecasts to fit with a different specification than the parent run.
10. **Recommended solution**: Reject `PATCH /runs/{id}` when `run.status != "draft"` (`HTTP 409`), add an atomic `POST /runs/{id}/rerun` endpoint, and unify configuration reads on a single versioned source of truth.
11. **Test required to verify the fix**: `T-21`, `T-22`, and `T-26`.

---

### P2 — Medium (20 Issues)

#### `SEC-02` — Rate Limiter Misses Write Routes, Counts Valid Checks, Leaks Memory, and Locks Out All Users Behind Nginx

1. **Unique issue ID**: `SEC-02`
2. **Severity**: `P2 — Medium` (Confirmed via code inspection)
3. **Affected file and exact line number**:
   - `api/deps.py:19-61` (`_AUTH_ATTEMPTS`, `check_auth_rate_limit`, `require_access_code`)
   - `api/router.py:30-42` (`auth_check` calls `check_auth_rate_limit(request)` before checking if `protected` is even `True` or if the code is valid)
   - `deploy/entrypoint.sh:7` (`uvicorn mmm_studio.main:app --host 127.0.0.1 --port 8000` without `--proxy-headers`)
4. **Description of the problem**:
   1. `check_auth_rate_limit` (`5` requests per `60s`) is **only** called on `GET /api/v1/auth/check`, never inside `require_access_code`, allowing unlimited brute-forcing of `X-Access-Code` against `POST /api/v1/runs`.
   2. `GET /api/v1/auth/check` increments `_AUTH_ATTEMPTS` on **every** call—even when `MMM_APP_ACCESS_CODE` is unset (`protected=False`) or when the user supplies a valid code.
   3. In Docker/Cloud Run (`deploy/entrypoint.sh:7`), `uvicorn` runs on `127.0.0.1:8000` behind Nginx without `--proxy-headers`, so `request.client.host` is always `"127.0.0.1"` for **all** users globally. Opening or refreshing 6 browser tabs within 60 seconds returns `HTTP 429` for every user of the deployment.
   4. `_AUTH_ATTEMPTS: dict[str, deque[float]]` (`deps.py:21`) never evicts keys, growing unboundedly over time.
5. **Why it is a bug**: Fails to prevent brute-force attacks on write routes while causing a global denial-of-service on `/api/v1/auth/check` after 5 page loads per minute behind Nginx.
6. **Steps to reproduce**: Call `GET /api/v1/auth/check` 6 times within 60 seconds with a valid `X-Access-Code` header (or with `MMM_APP_ACCESS_CODE=""`). The 6th call returns `HTTP 429 Too Many Requests`.
7. **Expected behavior**: Rate limiting records only **failed** authentication attempts across all protected routes, keys by forwarded client IP (`--proxy-headers`), and bounds `_AUTH_ATTEMPTS` size.
8. **Actual behavior**: Valid `/auth/check` requests exhaust the shared `"127.0.0.1"` bucket after 5 calls in 60s, while `POST /api/v1/runs` allows unlimited invalid guesses.
9. **Business or statistical impact**: Locks out legitimate users refreshing the app while providing zero brute-force protection on protected endpoints.
10. **Recommended solution**: Move rate-limit recording into `require_access_code` and `auth_check` **only when `hmac.compare_digest` fails**, bound `_AUTH_ATTEMPTS` to `4,096` keys, and add `--proxy-headers --forwarded-allow-ips="*"` to `deploy/entrypoint.sh:7`.
11. **Test required to verify the fix**: `T-20` (extended to assert 10 valid `/auth/check` calls succeed while 6 invalid `POST /runs` calls return `HTTP 429`).

---

#### `STAT-07` — Model Specification Selection Grid Is Scored on the Reported Q4 2024 Hold-Out Test Window

1. **Unique issue ID**: `STAT-07`
2. **Severity**: `P2 — Medium` (Confirmed via code inspection of `core/meridian/selection.py`)
3. **Affected file and exact line number**:
   - `core/meridian/selection.py:101-116` (`run_specification_selection` fits each `(catalog_shift, affiliate_role)` candidate with `base_cfg.holdout` and ranks them by `m_test.mae_over_mean` on that same hold-out)
   - `core/meridian/analysis.py:290-316` (reports that same hold-out's `m_test.mae_over_mean` as the headline out-of-sample test error)
4. **Description of the problem**: `run_specification_selection` evaluates the 6-cell specification grid (`catalog_shift ∈ {0, 2, 5}` × `affiliate_role ∈ {"control", "media"}`) on the primary hold-out window (Q4 2024) and selects `k = 2, affiliate = control` because it achieves the lowest Q4 2024 hold-out MAE/mean (`8.5%`). That same Q4 2024 MAE/mean is then reported as the model's out-of-sample test accuracy.
5. **Why it is a bug**: Selecting structural hyperparameters on the final test window rather than a separate validation window introduces model-selection optimism into the test error estimate.
6. **Steps to reproduce**: Inspect `run_specification_selection` (`selection.py:101-116`) and `sections/meridian_model.json` on `run_f1e8df83da`.
7. **Expected behavior**: Structural model selection is scored on a pre-test validation window (such as the Q3 2024 forward hold-out) or via walk-forward cross-validation, keeping the final Q4 2024 hold-out strictly untouched until final evaluation.
8. **Actual behavior**: Both model selection and headline out-of-sample reporting evaluate on `base_cfg.holdout` (Q4 2024).
9. **Business or statistical impact**: Makes the primary hold-out MAE/mean (`8.5%`) optimistically biased relative to the untouched Q3 2024 forward hold-out (`11.0%`).
10. **Recommended solution**: Score `run_specification_selection` (`selection.py:101-116`) on the forward hold-out window (`forward_holdout` from `features.holdout_windows`) instead of `base_cfg.holdout`.
11. **Test required to verify the fix**: Unit test asserting `run_specification_selection` fits candidate models with `holdout = forward_holdout` rather than `base_cfg.holdout`.

---

#### `STAT-08` — `attributed_roas` Empirical Bayes Priors Compute Channel ROAS Across the Hold-Out Window

1. **Unique issue ID**: `STAT-08`
2. **Severity**: `P2 — Medium` (Confirmed via code inspection)
3. **Affected file and exact line number**:
   - `core/meridian/spec.py:143-155` (`attributed_roas_priors(wide, paid_channels)` sums `wide[s_col].sum() / wide[c_col].sum()` over all rows of `wide`)
   - `core/meridian/spec.py:128-137` (`from_role_config` passes the full `wide` dataframe into `attributed_roas_priors`)
   - `core/meridian/selection.py:121` (`run_sensitivity_grid` calls `attributed_roas_priors(wide, base_cfg.paid_channels)` on the full panel)
4. **Description of the problem**: When a user selects `roi_prior_source = "attributed_roas"` (or during the `s16` prior sensitivity study), `attributed_roas_priors` computes each channel's attributed ROAS over the **entire** `wide` panel—including the hold-out test period—to centre the `LogNormal` ROI prior.
5. **Why it is a bug**: Using hold-out period `sales_<slug>` and `cost_<slug>` to set the prior median leaks test-period outcomes into the training prior.
6. **Steps to reproduce**: Call `MeridianSpecConfig.from_role_config(role_config, wide, settings=ModelSettings(roi_prior_source="attributed_roas"))` and compare `cfg.roi_prior` against ROAS computed on `wide.iloc[:-93]`.
7. **Expected behavior**: `attributed_roas_priors` restricts summation to training rows (`wide.loc[wide.index < holdout_start]`).
8. **Actual behavior**: Sums `sales_<slug>` and `cost_<slug>` across all 732 days including the 93 hold-out days.
9. **Business or statistical impact**: Leaks test-window channel sales into the Bayesian prior during hold-out evaluation and sensitivity testing.
10. **Recommended solution**: Slice `train_wide = wide.loc[(wide.index < holdout[0]) | (wide.index > holdout[1])]` before calling `attributed_roas_priors` in `spec.py:130` and `selection.py:121`.
11. **Test required to verify the fix**: Unit test modifying hold-out period sales and verifying that `attributed_roas_priors` remains completely unchanged.

---

#### `STAT-09` — Interactive Optimiser Extrapolates PCHIP Splines Beyond `2×` Curve Support (Up to `5×`) and Mislabels Incremental Media Sales as Total Sales

1. **Unique issue ID**: `STAT-09`
2. **Severity**: `P2 — Medium` (Confirmed & reproduced via `scripts/audit_core_checks.py` check `OPT-3` and code inspection)
3. **Affected file and exact line number**:
   - `core/meridian/analysis.py:130` (`response_curves` evaluated on `multipliers = np.round(np.arange(0.0, 2.05, 0.05), 4)`)
   - `api/optimiser.py:24-25` (`max_multiplier: float = Field(default=1.3, ge=0.0, le=5.0)`)
   - `core/meridian/optimise.py:106-108` (`PchipInterpolator(x_u, y_u, extrapolate=True)`)
   - `core/meridian/optimise.py:223-224` (`"historical_total_sales": cur_inc_total, "optimal_total_sales": opt_inc_total`)
   - `core/meridian/optimise.py:324-325` (`build_optimiser_section` hardcodes `0.7 * c_sp` and `1.3 * c_sp` and omits `mroi`/CIs when `opt_alloc_df` is present)
4. **Description of the problem**:
   1. Meridian's response curves are extracted only over `[0.0×, 2.0×]` historical spend (`analysis.py:130`), but `OptimisationCreate` (`api/optimiser.py:24-25`) permits `max_multiplier` up to `5.0×`, causing `PchipInterpolator(..., extrapolate=True)` (`optimise.py:106-108`) to extrapolate cubic polynomials far beyond the evaluated Hill posterior.
   2. In `optimise_hill_response_curves` (`optimise.py:223-224`), `cur_inc_total` and `opt_inc_total` are the sums of **paid media incremental sales only** (`~€397M` over 2 years), omitting baseline sales (`~€186M`), yet they are returned under the keys `"historical_total_sales"` and `"optimal_total_sales"`.
5. **Why it is a bug**: Extrapolating a piecewise cubic spline outside `[0, 2×]` diverges from the saturating Hill curve (`2.6%–4.2%` error at `3×` in `OPT-3`, larger at `5×`), and labelling media incremental outcome as `"total_sales"` misstates both total revenue and percentage lift.
6. **Steps to reproduce**: Run `scripts/audit_core_checks.py` (`OPT-3`) and inspect `optimise_hill_response_curves` return keys (`optimise.py:221-235`).
7. **Expected behavior**: `max_multiplier` in `api/optimiser.py:24-25` is capped at `2.0` (or response curves are extracted up to `5.0`), `PchipInterpolator` uses `extrapolate=False`, and the summary keys are named `historical_incremental_sales` / `optimal_incremental_sales`.
8. **Actual behavior**: Allows `5.0×` PCHIP extrapolation and labels media-only incremental sales as `historical_total_sales` / `optimal_total_sales`.
9. **Business or statistical impact**: Unreliable response-curve extrapolation at high spend multipliers and confusing revenue denominators in API responses.
10. **Recommended solution**: Cap `max_multiplier <= 2.0` in `api/optimiser.py:24-25` (or extend `analysis.py:130` grid), set `extrapolate=False` after clamping inputs, and rename the summary fields in `optimise.py:223-224`.
11. **Test required to verify the fix**: `T-10`.

---

#### `STAT-11` — Nominal 90% Prediction Intervals Cover Only `~80%` Out-of-Sample and Collapse Scenario Delta Uncertainty

1. **Unique issue ID**: `STAT-11`
2. **Severity**: `P2 — Medium` (Confirmed & reproduced on stored full run `run_f1e8df83da`)
3. **Affected file and exact line number**:
   - `core/meridian/forecast.py:170-198` (`_prediction_draws` and `_summarise_draws`)
   - `core/meridian/forecast.py:321-323` (`shift = (alt_expected - base_expected).mean(axis=0); pred_draws = base_forecast.pred_draws + shift[None, :]`)
   - `core/meridian/forecast.py:658` (narrative recommends the 90% band directly for operational planning)
4. **Description of the problem**:
   1. On `run_f1e8df83da`, the nominal 90% posterior predictive interval `[prediction_lo, prediction_hi]` covers `91.7%` of training days (`639` days), but only **`79.6%`** of Q4 2024 hold-out days (`74 / 93`) and **`80.6%`** of leak-free back-test days (`75 / 93`) due to an unmodelled level offset (`+€68.9k/day` mean bias) and iid Gaussian noise assumptions.
   2. In `evaluate_forecast_scenario` (`forecast.py:321-323`), when evaluating an alternative spend scenario, instead of adding the draw-by-draw posterior difference `alt_expected - base_expected` (`shape: (n_draws, n_days)`) to `base_forecast.pred_draws`, line 321 collapses `alt_expected - base_expected` to its scalar posterior mean `shift = (alt_expected - base_expected).mean(axis=0)` (`shape: (n_days,)`) and shifts all `base_forecast.pred_draws` by that constant! This completely discards the posterior parameter uncertainty of the media change from the scenario's prediction interval (`prediction_lo`, `prediction_hi`).
5. **Why it is a bug**: Out-of-sample 90% intervals undercover by ~10 percentage points, and scenario prediction intervals fail to widen when a user scales up a high-uncertainty channel (such as `psm` or `display_network`).
6. **Steps to reproduce**: Check `meridian_model.json` / `forecast.json` coverage metrics on `run_f1e8df83da`, and inspect `forecast.py:321-323` when `psm` is scaled to `2.0×`.
7. **Expected behavior**: Scenario prediction draws are computed draw-by-draw (`pred_draws = base_forecast.pred_draws + (alt_expected - base_expected)`), and out-of-sample intervals either include a back-test calibration factor or state their empirical coverage (`~80%`) in the planner UI.
8. **Actual behavior**: `pred_draws` is shifted by a constant mean vector (`shift[None, :]`), so `prediction_hi - prediction_lo` is identical at `0.0×` and `2.0×` spend for every channel; empirical hold-out coverage is `79.6%`.
9. **Business or statistical impact**: Planners under-budget for downside revenue risk and see zero uncertainty increase when doubling spend in weakly identified channels.
10. **Recommended solution**: Replace `shift = (alt_expected - base_expected).mean(axis=0)` at `forecast.py:321-323` with draw-aligned `pred_draws = base_forecast.pred_draws + (alt_expected - base_expected)`, and surface empirical back-test coverage alongside the 90% band label.
11. **Test required to verify the fix**: `T-14` and `T-37`.

---

#### `STAT-12` — Horizon/Weekly Intervals Sum Daily Quantiles ($\sqrt{T}$ Inflation), and `extend_history` Shrinks KPI SD & Rescales Historical `trend`

1. **Unique issue ID**: `STAT-12`
2. **Severity**: `P2 — Medium` (Confirmed via mathematical & code inspection)
3. **Affected file and exact line number**:
   - `core/meridian/forecast.py:399-402` (`"prediction_lo_total": float(alt_daily["prediction_lo"].sum())`, `"prediction_hi_total": float(alt_daily["prediction_hi"].sum())`)
   - `core/meridian/forecast.py:583-595` (`weekly = daily.resample("W-SUN").sum()` sums `prediction_lo` and `prediction_hi`)
   - `jobs/tasks.py:537-538` (`"prediction_90_lo": float(daily["prediction_lo"].sum())`)
   - `core/meridian/forecast.py:165-167, 656-657` (`extend_history` fills plan `total_sales` with training mean and calls `add_calendar_and_adstock_features(combined)`)
   - `core/features.py:58` (`out["trend"] = np.arange(len(index)) / max(len(index), 1)`)
4. **Description of the problem**:
   1. **Quantile summation (`forecast.py:399-402, 583-595`, `tasks.py:537-538`)**: Weekly and 42-day horizon prediction interval bounds (`prediction_lo_total`, `prediction_hi_total`, `prediction_90_lo`, `prediction_90_hi`) are computed by **summing the daily 5th and 95th percentiles**. Because independent daily observation noise $\epsilon_t \sim \mathcal{N}(0, \sigma^2)$ averages out over time ($\text{SD}(\sum_{t=1}^T \epsilon_t) = \sqrt{T}\sigma$, not $T\sigma$), summing daily quantiles overstates the observation-noise width over $T=42$ days by $\sqrt{42} \approx 6.48\times$.
   2. **Historical `trend` rescaling (`forecast.py:167`, `features.py:58`)**: `add_calendar_and_adstock_features` defines `trend = np.arange(len(index)) / len(index)` (whereas `src/tchibo_mmm/features.py:74` used `(index - index[0]).days / 365.25` and `DataTab.tsx:130` documents `"years since the first day"`). When `extend_history` appends $H=42$ future days to $N=732$ historical days and re-calls `add_calendar_and_adstock_features(combined)`, the denominator changes from $732$ to $774$, rescaling all historical `trend` values.
   3. **KPI standard deviation shrinkage (`forecast.py:165, 656-657`)**: Filling $H$ future rows with `wide["total_sales"].mean()` preserves the sample mean of `total_sales` across $N+H$ rows, but shrinks the sample standard deviation inside Meridian's `KpiTransformer` by $\sqrt{\frac{N-1}{N+H-1}}$ (`-2.75%` for 42 days, `-5.63%` for 90 days), contradicting `forecast.py:656-657` (*"Meridian's KPI scaling is unchanged"*).
5. **Why it is a bug**: Summing daily quantiles violates basic probability theory ($\mathcal{Q}_q(\sum_t Y_t) \ne \sum_t \mathcal{Q}_q(Y_t)$), and `extend_history` alters historical `trend` features and KPI standard deviation scaling.
6. **Steps to reproduce**: Compare `daily["prediction_hi"].sum() - daily["prediction_lo"].sum()` (`€23.8M` width on the 42-day default forecast) against `np.quantile(pred_draws.sum(axis=1), 0.95) - np.quantile(pred_draws.sum(axis=1), 0.05)`, and compare `wide["trend"].iloc[731]` before and after `extend_history`.
7. **Expected behavior**: Horizon and weekly 90% prediction intervals are computed as quantiles of the draw-level sums (`pred_draws.sum(axis=1)`), and `trend` is anchored to the historical start date (`(index - index[0]).days / 365.25`) so appending future rows never alters historical feature values.
8. **Actual behavior**: Sums daily quantiles (`prediction_lo.sum()`), rescales `trend` by $N/(N+H)$, and claims KPI scaling is strictly unchanged.
9. **Business or statistical impact**: Horizon total prediction intervals (`prediction_90_lo`/`hi`) are artificially wide by several million euros, distorting risk ranges in executive forecast summaries.
10. **Recommended solution**: Compute horizon and weekly interval bounds from `np.quantile(pred_draws.sum(axis=1), [0.05, 0.95])` in `forecast.py` and `tasks.py`, and change `core/features.py:58` to `(index - index[0]).days / 365.25` (or anchor the denominator to the training length).
11. **Test required to verify the fix**: `T-14` and `T-37`.

---

#### `TXT-01` — Benchmark Narrative Unconditionally Claims Meridian Beats or Matches OLS-2

1. **Unique issue ID**: `TXT-01`
2. **Severity**: `P2 — Medium` (Confirmed via code inspection of `core/ols.py` and `pipeline/stages.py`)
3. **Affected file and exact line number**:
   - `core/ols.py:208-219` (`build_benchmark_section` hardcodes `"Naive forecasts fail the target"`, `"OLS-1 (raw log-costs) is unreliable"`, `"OLS-2 (Meridian inputs) is a strong, fast baseline"`)
   - `pipeline/stages.py:610-628` (`_augment_benchmark_with_meridian` says `"Meridian is close to the OLS benchmark on the hold-out and adds causal response curves ... which is why it is the main model"` whenever `meridian_mae > ols2_mae`, regardless of how much worse Meridian is)
4. **Description of the problem**: Both the initial OLS benchmark narrative (`core/ols.py:208-219`) and the post-Meridian augmentation (`pipeline/stages.py:610-628`) use fixed qualitative conclusions that do not check whether Naive actually failed the 15% target, whether OLS-1 actually had higher error than OLS-2, or whether Meridian's hold-out MAE/mean exceeded the 15% threshold (`e.g. 25%` vs. OLS-2 `10%`).
5. **Why it is a bug**: On non-case-study datasets (or poor Meridian configurations), the benchmark narrative asserts that Meridian is `"close to the OLS benchmark"` and that Naive `"fails the target"` even when the table directly above shows the opposite.
6. **Steps to reproduce**: Call `_augment_benchmark_with_meridian` with `meridian_metrics={"holdout_q4_2024": {"MAE_over_mean": 0.30}}` and `ols2_mae = 0.096`.
7. **Expected behavior**: Callout text and status branch on the actual error gap (`meridian_mae - ols2_mae`) and threshold (`<= 0.15`).
8. **Actual behavior**: Renders `"Meridian is close to the OLS benchmark on the hold-out..."` even when Meridian's error is `30%` vs. `9.6%`.
9. **Business or statistical impact**: Misleads analysts comparing model families when Meridian underperforms linear baselines.
10. **Recommended solution**: Condition the takeaways in `core/ols.py:208-219` and `pipeline/stages.py:610-628` on the numeric comparisons (`m_mae <= 0.15`, `m_mae <= ols2_mae + 0.02`, etc.).
11. **Test required to verify the fix**: `T-13`.

---

#### `TXT-02` — Draft Management Brief Fabricates `"Catalog revenue ROI ≈ 1"` Before Meridian Runs and Hard-Codes Channel Importance

1. **Unique issue ID**: `TXT-02`
2. **Severity**: `P2 — Medium` (Confirmed & reproduced via `scripts/audit_core_checks.py` check `TXT-2`)
3. **Affected file and exact line number**:
   - `pipeline/stages.py:216-225` (`cat_roi = ... if roi_df is not None ... else "≈ 1"`)
   - `pipeline/stages.py:290` (`f"{cat_share:.1f}% of spend with revenue ROI {cat_roi}; attributed ROAS {cat_roas:.2f} misses the halo."`)
   - `pipeline/stages.py:307` (`"Most important channels (preliminary OLS with HAC errors): Affiliate, SEA and Catalog; the others cannot be told apart from zero."`)
4. **Description of the problem**: When `build_brief_section` runs at stage `s11` (before Meridian fits, or permanently when `skip_meridian=True`), `roi_df` is `None`. Instead of displaying `"pending"`, line 224 sets `cat_roi = "≈ 1"`, fabricating a Meridian ROI estimate before any Bayesian model has been fit. In addition, line 307 hardcodes `"Affiliate, SEA and Catalog; the others cannot be told apart from zero"` instead of reading the computed HAC t-statistics from `s10_preparation`.
5. **Why it is a bug**: Violates data integrity by presenting a hardcoded case-study ROI (`"≈ 1"`) and hardcoded channel ranking on any dataset or draft run.
6. **Steps to reproduce**: Call `build_brief_section(wide, facts, ols_res, meridian_metrics=None, roi_df=None, opt_summary=None)` (automated in `scripts/audit_core_checks.py` `TXT-2`).
7. **Expected behavior**: When `roi_df is None`, Catalog ROI is shown as `"pending Meridian fit"`, and Q2 channel importance is formatted dynamically from the `compute_channel_importance` table.
8. **Actual behavior**: Outputs `"30.5% of spend with revenue ROI ≈ 1"` and `"Most important channels (preliminary OLS with HAC errors): Affiliate, SEA and Catalog; the others cannot be told apart from zero."`
9. **Business or statistical impact**: Users running `skip_meridian=True` (or viewing the brief while Meridian is queued) see fabricated ROI numbers and hardcoded channel names.
10. **Recommended solution**: Replace `else "≈ 1"` at `stages.py:224` with `else "pending"`, and pass `importance_df` into `build_brief_section` to populate line 307 dynamically.
11. **Test required to verify the fix**: `T-12`.

---

#### `DATA-03` — Missing Channel Rows Become Zero Sales in `total_sales` Without a Meridian Control Flag

1. **Unique issue ID**: `DATA-03`
2. **Severity**: `P2 — Medium` (Confirmed & reproduced via `scripts/audit_core_checks.py` check `DP-miss`)
3. **Affected file and exact line number**:
   - `core/tables.py:123` (`wide["total_sales"] = wide[sales_cols].sum(axis=1)`)
   - `core/tables.py:141-152` (creates `missing_<slug>` indicator columns in `wide`)
   - `core/meridian/spec.py:187` (`control_cols` includes calendar, Fourier, trend, and `cost_affiliate`, but **omits** `missing_<slug>`)
   - `pipeline/stages.py:438` (narrative states missing channel-days are `"flagged so they do not distort the model"`)
4. **Description of the problem**: Because `total_sales` is constructed by summing `sales_by_channel` across all channels present on date $t$, any day where a channel row is missing from the raw export contributes `0` for that channel's sales. In the Tchibo workbook, 44 days have at least one missing channel row (`TV: 38 days`, `Catalog: 4 days`, `Owned: 2 days`, including `2024-11-13` inside the Q4 hold-out). On those 44 days, `total_sales` is on average **`-11.7%` lower** than same-weekday neighbours (down to **`-42.1%`** on days when `Owned` or `Catalog` is missing). Although `build_daily_wide_table` creates `missing_<slug>` indicator columns, `build_meridian_table` (`spec.py:187`) never passes them to Meridian as control variables.
5. **Why it is a bug**: Missing channel reporting rows create artificial downward spikes in the national revenue target `total_sales` that Meridian attempts to explain via media spend or residual variance $\sigma$, while the UI claims those days are controlled for.
6. **Steps to reproduce**: Run `scripts/audit_core_checks.py` (`DP-miss`).
7. **Expected behavior**: Either pass non-trivial `missing_<slug>` indicator columns (for channels with missing rows and non-zero sales) as controls in `spec.py:187`, or impute missing channel sales before summing `total_sales`.
8. **Actual behavior**: `missing_<slug>` columns are dropped when building the Meridian input table (`spec.py:187`).
9. **Business or statistical impact**: Introduces `-11.7%` target measurement error on 44 days (`6%` of the panel), inflating residual variance $\sigma$ and distorting hold-out error on `2024-11-13`.
10. **Recommended solution**: Include active `missing_<slug>` columns in `control_cols` in `core/meridian/spec.py:187` (and `core/ols.py:59-61`).
11. **Test required to verify the fix**: `T-03`.

---

#### `DATA-04` — Negative Cost/Sales Values Are Accepted Without Warning, and `total_sales <= 0` Crashes `explore_target`

1. **Unique issue ID**: `DATA-04`
2. **Severity**: `P2 — Medium` (Confirmed & reproduced via `scripts/audit_core_checks.py` check `DP-neg` and `scripts/audit_api_checks.py` check `DQ-API`)
3. **Affected file and exact line number**:
   - `core/workbook.py:195-197` (`neg_count` computed across numeric columns)
   - `core/workbook.py:199-214` (no validation error or warning emitted when `neg_count > 0`)
   - `core/eda/target.py:39` and `core/eda/notebook_stats.py:157` (`np.log(y)` executed without floor on `total_sales`)
4. **Description of the problem**: `profile_workbook` counts negative values in `costs`, `sales_by_channel`, `impressions`, and `clicks` (`profile.negative_values_count`), but never warns or rejects the upload. Negative media spend violates Meridian's Hill/adstock domain ($x_{t,m} \ge 0$), and if a day's net `total_sales <= 0` (e.g. due to accounting adjustments or refunds), `np.log(y)` in `core/eda/target.py:39` and `notebook_stats.py:157` produces `NaN`/`-inf`, crashing the Skewness/Jarque-Bera calculation in `s03_target`.
5. **Why it is a bug**: Negative media spend is invalid input for Meridian's Hill saturation ($x^\alpha / (ec^\alpha + x^\alpha)$ is undefined for $x < 0$ and non-integer $\alpha$), and non-positive daily sales crash `s03_target`.
6. **Steps to reproduce**: Set 5 cost entries in `case_study.xlsx` to `-10000` and upload (`scripts/audit_api_checks.py` `DQ-API`).
7. **Expected behavior**: Reject negative `costs`, `impressions`, or `clicks` at upload (`HTTP 422`), warn on negative channel sales, and guard `np.log(np.maximum(y, 1.0))` in `target.py:39`.
8. **Actual behavior**: Upload returns `HTTP 201` with zero warnings concerning negative values.
9. **Business or statistical impact**: Silent ingestion of invalid negative spend or runtime crash in `s03_target` / `s12_meridian_fit`.
10. **Recommended solution**: Reject negative media spend/impressions/clicks in `profile_workbook` (`workbook.py:195-215`) and clamp `np.log(np.maximum(y, 1.0))` in `eda/target.py:39` and `notebook_stats.py:157`.
11. **Test required to verify the fix**: `T-02`.

---

#### `API-02` — Re-Running a Run Preserves Stale Custom Forecasts, Scenarios, Optimisations, and `_INTERACTIVE_CACHE` Entries

1. **Unique issue ID**: `API-02`
2. **Severity**: `P2 — Medium` (Confirmed & reproduced via `scripts/audit_api_checks.py` check `STATE-3`)
3. **Affected file and exact line number**:
   - `api/runs.py:283-293` (`start_run` deletes `sections`, `data`, `models`, `reports`, and only the `is_default == True` `Forecast` row)
   - `jobs/tasks.py:585, 671` (`execute_scenario_job` and `execute_optimiser_job` accept `cache_version: str = ""`)
   - `api/forecasts.py:229` and `api/optimiser.py:67` (neither caller passes `cache_version`, so `_INTERACTIVE_CACHE` keys end with `""`)
4. **Description of the problem**: When a user re-runs an existing run (`POST /api/v1/runs/{run_id}/start`), lines 283–293 delete the default forecast row, but leave all custom `Forecast` rows, `ForecastScenario` rows, and `OptimisationJob` rows in Postgres (along with their `forecasts/`, `scenarios/`, and `optimisations/` storage files). Even worse, `execute_scenario_job` and `execute_optimiser_job` cache loaded models and response curves in `_INTERACTIVE_CACHE` using key `f"{run_id}:{forecast_id}:{cache_version}"` (`tasks.py:599, 680`), but `api/forecasts.py:229` and `api/optimiser.py:67` never pass `cache_version`! Thus, after a re-run finishes, new optimisations and default-forecast scenarios hit the stale in-memory cache from the **previous** model fit.
5. **Why it is a bug**: Users inspecting the Optimiser or Forecast pages after a re-run see optimisations and scenarios computed from the old model alongside the new model, and new interactive requests serve stale cached curves/models.
6. **Steps to reproduce**: Run `scripts/audit_api_checks.py` (`STATE-3`): create custom forecasts and optimisations on a completed run, then call `POST /api/v1/runs/{id}/start`.
7. **Expected behavior**: Re-running purges (or archives by `model_version`) all `Forecast`, `ForecastScenario`, and `OptimisationJob` rows and storage prefixes for that `run_id`, and passes `run.updated_at.isoformat()` as `cache_version`.
8. **Actual behavior**: All 4 `OptimisationJob` rows and 1 custom `Forecast` row remain active after re-run, and `cache_version` is always `""`.
9. **Business or statistical impact**: Stale optimisations and scenarios from superseded model specifications are mixed with the new run's results.
10. **Recommended solution**: Delete all `Forecast`, `ForecastScenario`, and `OptimisationJob` rows (and `forecasts/`, `scenarios/`, `optimisations/` storage prefixes) on re-run in `api/runs.py:283-293`, and pass `cache_version=run.updated_at.isoformat()` in `api/forecasts.py:229` and `api/optimiser.py:67`.
11. **Test required to verify the fix**: `T-23`.

---

#### `API-03` — Optimiser `channel_bounds` Ignores Display-Name Keys, Accepts Negative/Reversed Bounds, and Mixes Units at `10.0`

1. **Unique issue ID**: `API-03`
2. **Severity**: `P2 — Medium` (Confirmed & reproduced via `scripts/audit_api_checks.py` checks `OPT-API-1..3` and `scripts/audit_core_checks.py` check `OPT-4`)
3. **Affected file and exact line number**:
   - `api/optimiser.py:21-27` (`channel_bounds: dict[str, list[float]] | None = None` — no element count, sign, ordering, or slug validation)
   - `jobs/tasks.py:686-691` (`if isinstance(pair, list | tuple) and len(pair) == 2:` silently drops malformed entries)
   - `core/meridian/optimise.py:255-261` (`if hi_m <= 10.0:` treats bounds as multipliers, else as raw EUR amounts)
4. **Description of the problem**:
   1. Unlike `api/forecasts.py:346-364` (`_normalise_multipliers`), `api/optimiser.py` does not normalise channel keys or reject unknown channels. Passing `{"Display": [0.5, 0.5]}` (display name instead of slug `"display"`) is silently ignored.
   2. Single-element lists (`{"display": [0.9]}`), negative bounds (`{"display": [-5, 0.2]}`), and inverted bounds (`{"display": [1.5, 0.5]}`) are accepted with `HTTP 202`.
   3. `optimise.py:257` guesses the unit of `channel_bounds[ch]` from `if hi_m <= 10.0:`: `(0.5, 10.0)` is interpreted as `0.5×–10.0×` historical spend (`€14.9M–€298M`), whereas `(0.5, 10.1)` is suddenly interpreted as **`€0.50 to €10.10` total spend** (`0.0000×`)!
5. **Why it is a bug**: Silent dropping of display-name keys, acceptance of negative/inverted bounds, and a discontinuous unit switch at `10.0` cause the optimiser to solve a completely different problem than requested.
6. **Steps to reproduce**: Run `scripts/audit_api_checks.py` (`OPT-API-1`, `OPT-API-2`, `OPT-API-3`) and `scripts/audit_core_checks.py` (`OPT-4`).
7. **Expected behavior**: `OptimisationCreate` validates that every key in `channel_bounds` resolves to an active paid channel slug, `len(pair) == 2`, `0.0 <= lo <= hi <= 2.0`, and uses a single unambiguous unit (multipliers of historical spend).
8. **Actual behavior**: `{"Display": [0.5, 0.5]}` yields `0.7×`; `{"display": [0.9]}` and `{"display": [-5, 0.2]}` return `HTTP 202` $\to$ `"succeeded"`; `(5, 12)` sets Display spend to `€12`.
9. **Business or statistical impact**: API consumers or custom integrations receive silently unconstrained or `€12`-capped allocations.
10. **Recommended solution**: Add a Pydantic `@field_validator("channel_bounds")` in `api/optimiser.py` that normalises slugs against the run's paid channels, enforces `0.0 <= lo <= hi <= 2.0`, and removes the `hi_m <= 10.0` heuristic in `optimise.py:257`.
11. **Test required to verify the fix**: `T-09`.

---

#### `API-04` — Re-Running While a Previous Job Lock Is Queued Raises Unhandled `AlreadyEnqueued` (`HTTP 500`) After Setting `run.status = "queued"`

1. **Unique issue ID**: `API-04`
2. **Severity**: `P2 — Medium` (Confirmed & reproduced during audit testing)
3. **Affected file and exact line number**:
   - `api/runs.py:283-319` (`start_run` resets `run.status = "queued"` and flushes DB changes at lines 294–302 **before** calling `defer_task` at lines 305–318)
   - `jobs/app.py:117-123` (`defer_task` sets `queueing_lock=lock` without catching `procrastinate.exceptions.AlreadyEnqueued`)
4. **Description of the problem**: When `start_run` is called on a failed or cancelled run whose previous `run_meridian_job` (or `run_analysis_job`) is still in `todo` status in `procrastinate_jobs` with `queueing_lock = "meridian:{run_id}"`, Procrastinate raises `procrastinate.exceptions.AlreadyEnqueued`. Because `start_run` already deleted old sections and mutated `run.status = "queued"` before calling `defer_task` (or when `run_analysis_job` succeeds in deferring at line 305 and `run_meridian_job` raises `AlreadyEnqueued` at line 312), the endpoint crashes with an unhandled `HTTP 500 Internal Server Error`.
5. **Why it is a bug**: A normal user retry action should never return an unhandled `HTTP 500` or leave partial jobs enqueued.
6. **Steps to reproduce**: Trigger a failure in `s01_ingest` while `run_meridian_job` remains queued behind another Meridian run, then call `POST /api/v1/runs/{id}/start`.
7. **Expected behavior**: Cancel any lingering `todo` jobs for `run_id` in `procrastinate_jobs` before re-enqueueing, or catch `AlreadyEnqueued` before mutating `run.status` and return `HTTP 409 Conflict`.
8. **Actual behavior**: Raises unhandled `procrastinate.exceptions.AlreadyEnqueued` (`HTTP 500`).
9. **Business or statistical impact**: Breaks run recovery when a user retries a run while the Meridian queue is backed up.
10. **Recommended solution**: Cancel existing `todo` Procrastinate jobs for `run_id` (or catch `AlreadyEnqueued` and roll back the session, returning `HTTP 409`) in `api/runs.py:294-319` and `jobs/app.py:117-123`.
11. **Test required to verify the fix**: `T-24`.

---

#### `API-05` — Concurrency Race Between `run_meridian_job` and `run_analysis_job` Overwrites Final Sections with `"Meridian pending"` Drafts

1. **Unique issue ID**: `API-05`
2. **Severity**: `P2 — Medium` (Confirmed & reproduced via `scripts/audit_race_and_holdout.py`)
3. **Affected file and exact line number**:
   - `api/runs.py:305-318` (defers `run_analysis_job` and `run_meridian_job` simultaneously)
   - `jobs/tasks.py:327-354` (`execute_meridian_job` waits up to 60s if a stage is `"running"`, then calls `run_analysis_stages` itself **without** acquiring the `analysis:{run_id}` lock)
   - `pipeline/stages.py:803-806` (`s11_ols_benchmark` unconditionally writes draft `overview`, `brief`, and `reports` sections with `meridian_metrics=None`)
   - `pipeline/context.py:78-96` and `storage.py:72-74` (concurrent `save_section` calls write to the same temporary file path `sections/_index.json.tmp`)
4. **Description of the problem**: `POST /runs/{id}/start` enqueues `run_analysis_job` (on queue `analysis`) and `run_meridian_job` (on queue `meridian`) at the same time. If the `meridian` worker picks up `run_meridian_job` before the `analysis` worker marks a stage `"running"` (or if the `analysis` worker is busy with another run for $>60\text{s}$), `execute_meridian_job` sees `any_running == False` (`tasks.py:336`) and executes `run_analysis_stages` (`s01–s11`) followed by `run_meridian_stages` (`s12–s16`). When the `analysis` worker subsequently runs `execute_analysis_job`, it re-executes `s01–s11`, and stage `s11` (`stages.py:803-806`) **overwrites** `sections/overview.json`, `sections/benchmark.json`, `sections/brief.json`, and `sections/reports.json` with `"Meridian pending"` drafts! Furthermore, when both workers run `save_section` concurrently, both write to `sections/_index.json.tmp` (`storage.py:72`), risking `FileNotFoundError` on `tmp.replace(target)`.
5. **Why it is a bug**: A completed run (`status="succeeded"`) loses its Meridian headline KPIs, benchmark row, and executive brief whenever `execute_analysis_job` finishes `s11` after `execute_meridian_job` finishes `s15`.
6. **Steps to reproduce**: Execute `execute_meridian_job(run_id)` followed by `execute_analysis_job(run_id)` (automated in `scripts/audit_race_and_holdout.py`).
7. **Expected behavior**: `run_meridian_job` is deferred by `execute_analysis_job` upon completing `s11` (or `s11` checks `ctx.has_meta("meridian_metrics.json")` before writing draft sections), and `LocalBackend` uses unique temp filenames (`f".{target.name}.{uuid4().hex}.tmp"`).
8. **Actual behavior**: After `execute_meridian_job`, `overview` shows `"Q4 2024 hold-out MAE / mean = 8.5% (Target ≤ 15 % · Meridian MMM)"` and `benchmark_has_meridian = True`. After `execute_analysis_job`, `overview` reverts to `"9.6% (Target ≤ 15 % · OLS-2 Meridian inputs (Meridian pending))"` and `benchmark_has_meridian = False`.
9. **Business or statistical impact**: Completed 20-minute Meridian runs silently revert their executive summary and benchmark pages to OLS-only drafts.
10. **Recommended solution**: Defer `run_meridian_job` at the end of `execute_analysis_job` (instead of simultaneously in `api/runs.py:312`), guard `stages.py:803-806` with `if not ctx.has_meta("meridian_metrics.json"):`, and use per-write UUID temp files in `storage.py:72`.
11. **Test required to verify the fix**: `T-25`.

---

#### `API-06` — `GET` Endpoints Perform Unguarded Database & Storage Writes (`_ensure_default_forecast`, `sweep_stale_runs`)

1. **Unique issue ID**: `API-06`
2. **Severity**: `P2 — Medium` (Confirmed via code inspection)
3. **Affected file and exact line number**:
   - `api/forecasts.py:42-119, 125` (`GET /api/v1/runs/{run_id}/forecasts` calls `_ensure_default_forecast`, which writes `forecasts/{id}/result.json` and inserts `Forecast` and `ForecastScenario` rows into Postgres)
   - `api/runs.py:181, 200`, `api/sections.py:21, 56`, `api/forecasts.py:123` (every `GET` calls `sweep_stale_runs()`)
4. **Description of the problem**: `GET /api/v1/runs/{run_id}/forecasts` is not idempotent or read-only: when no `is_default` `Forecast` row exists, `_ensure_default_forecast` reads `sections/forecast.json`, writes `forecasts/{id}/result.json`, and inserts a `Forecast` row and 4 `ForecastScenario` rows without a database uniqueness constraint on `(run_id, is_default=True)`. Two concurrent `GET /forecasts` requests can create duplicate default forecasts. Similarly, every `GET /runs`, `GET /runs/{id}`, `GET /sections`, and `GET /forecasts` executes `sweep_stale_runs()`, mutating `runs`, `stages`, `forecasts`, and `procrastinate_jobs`.
5. **Why it is a bug**: Violates HTTP `GET` safety/idempotency and creates race conditions under concurrent browser polling.
6. **Steps to reproduce**: Issue two concurrent `GET /api/v1/runs/{id}/forecasts` requests on a CLI-generated run whose `Forecast` DB row has not yet been seeded.
7. **Expected behavior**: Default `Forecast` and `ForecastScenario` rows are created at the end of stage `s14_forecast` inside the worker, and `sweep_stale_runs()` runs on a throttled background timer rather than inside `GET` handlers.
8. **Actual behavior**: `GET` handlers write to artifact storage and commit DB mutations on every poll.
9. **Business or statistical impact**: Duplicate default forecasts and unnecessary DB write lock contention during UI polling.
10. **Recommended solution**: Persist the default `Forecast` row during `execute_meridian_job` after `s14_forecast`, add a partial unique index on `forecasts(run_id) WHERE is_default = true`, and throttle `sweep_stale_runs()` to at most once every 30 seconds.
11. **Test required to verify the fix**: Unit test verifying `GET /forecasts` under concurrent requests creates at most one default `Forecast` row.

---

#### `PERF-01` — Synchronous Excel/CSV Parsing and Disk I/O Inside `async def` Handlers Block the FastAPI Event Loop

1. **Unique issue ID**: `PERF-01`
2. **Severity**: `P2 — Medium` (Confirmed via code inspection)
3. **Affected file and exact line number**:
   - `api/runs.py:92-170` (`async def create_run` calls synchronous `read_raw_dataframe` and `profile_workbook` on up to 50 MB `.xlsx` files)
   - `api/forecasts.py:130-189, 250-292` (`async def create_forecast` and `async def preview_plan` call synchronous `load_budget_plan` and `validate_plan`)
4. **Description of the problem**: FastAPI executes `async def` route handlers directly on the single main `asyncio` event loop thread. Calling CPU-intensive synchronous `openpyxl` workbook parsing (`read_raw_dataframe`, `profile_workbook`), pandas pivots, and synchronous GCS/filesystem writes inside `async def` blocks the entire Uvicorn worker process for 1–5 seconds per upload.
5. **Why it is a bug**: While one user uploads a workbook, every other request to that API container (including `/health` probes and `2.5s` UI polls) stalls completely.
6. **Steps to reproduce**: Upload `case_study.xlsx` via `POST /api/v1/runs` while measuring concurrent `GET /api/v1/health` latency on the same Uvicorn worker.
7. **Expected behavior**: Either declare synchronous endpoints as `def` (so FastAPI dispatches them to AnyIO's worker threadpool) or wrap `profile_workbook` / `load_budget_plan` in `await run_in_threadpool(...)`.
8. **Actual behavior**: Runs `openpyxl` parsing synchronously on the main event loop thread.
9. **Business or statistical impact**: Degrades API responsiveness and risks Cloud Run health-check timeouts during large file uploads.
10. **Recommended solution**: Change `async def` to `def` on handlers that use synchronous SQLAlchemy sessions (`Session = Depends(get_session)`) and synchronous file parsing (`api/runs.py`, `api/forecasts.py`, `api/optimiser.py`, `api/sections.py`, `api/exports.py`), or offload `await file.read()` followed by `await run_in_threadpool(...)`.
11. **Test required to verify the fix**: Concurrent `GET /api/v1/health` latency test during `POST /api/v1/runs`.

---

#### `PERF-02` — `sweep_stale_runs` and `get_queue_info` Open 2–4 Raw Unpooled `psycopg.connect` Connections on Every Poll

1. **Unique issue ID**: `PERF-02`
2. **Severity**: `P2 — Medium` (Confirmed via code inspection of `jobs/app.py` and `api/runs.py`)
3. **Affected file and exact line number**:
   - `jobs/app.py:50-103` (`meridian_queue_load` and `get_queue_info` open raw `psycopg.connect(_psycopg_conninfo(url), connect_timeout=2)` connections)
   - `jobs/app.py:126-222` (`_cleanup_procrastinate_jobs` and `sweep_stale_runs` open raw `psycopg.connect` connections)
   - `api/runs.py:85, 181, 200` (`_serialize_run` calls `get_queue_info(run.id)` for **every** active run in `list_runs`, after `list_runs` already called `sweep_stale_runs()`)
4. **Description of the problem**: Instead of using the SQLAlchemy connection pool (`db/session.py:21-33`), `jobs/app.py` opens a brand-new raw TCP/SSL PostgreSQL connection via `psycopg.connect(...)` on every call to `sweep_stale_runs()` and `get_queue_info()`. Because the React UI polls `GET /api/v1/runs/{run_id}` and `GET /api/v1/runs/{run_id}/sections` every `2.5s`, each open browser tab triggers 3–5 unpooled `psycopg.connect` handshakes every 2.5 seconds (and $1 + N_{\text{active}}$ connections on `GET /api/v1/runs`).
5. **Why it is a bug**: Bypasses SQLAlchemy's connection pool, adding TCP/TLS connection overhead to every poll and risking `FATAL: remaining connection slots are reserved` on Cloud SQL (`db-f1-micro` / small tiers).
6. **Steps to reproduce**: Inspect `jobs/app.py:54, 80, 146, 179` and trace a single `GET /api/v1/runs` call when 2 runs are active.
7. **Expected behavior**: All queue inspection and stale-run sweep queries execute through the pooled SQLAlchemy `get_engine()` / `Session`.
8. **Actual behavior**: Opens and closes multiple raw `psycopg.connect` sockets per HTTP request.
9. **Business or statistical impact**: High latency and database connection exhaustion under multi-tab or multi-user usage.
10. **Recommended solution**: Replace `psycopg.connect(_psycopg_conninfo(url))` in `jobs/app.py` with `with get_engine().begin() as conn:` using SQLAlchemy `text(...)` queries, and batch `get_queue_info` across active runs in `list_runs`.
11. **Test required to verify the fix**: Unit test asserting `psycopg.connect` is never called during `GET /api/v1/runs` and `GET /api/v1/runs/{id}`.

---

#### `PERF-03` — In-Process MCMC Fits in `forecast_budget_plan` and Uncleaned `tempfile.mkdtemp` Directories Leaking 15–50 MB `.binpb` Files in `/tmp`

1. **Unique issue ID**: `PERF-03`
2. **Severity**: `P2 — Medium` (Confirmed via code inspection of `core/meridian/forecast.py`, `pipeline/stages.py`, and `jobs/tasks.py`)
3. **Affected file and exact line number**:
   - `core/meridian/forecast.py:214-218` (`forecast_budget_plan` calls `fit_meridian` in-process instead of `fit_meridian_isolated`)
   - `pipeline/stages.py:720-724` (`_save_meridian_to_storage` calls `Path(tempfile.mkdtemp(prefix="mmm_serde_"))` and never removes it)
   - `pipeline/stages.py:1071-1074` (`_load_meridian_from_storage` calls `Path(tempfile.mkdtemp(prefix="mmm_serde_"))` and never removes it)
   - `jobs/tasks.py:455-459` and `jobs/tasks.py:519-523` (`execute_scenario_job` and `execute_forecast_job` call `Path(tempfile.mkdtemp(prefix="mmm_serde_"))` and never remove it)
4. **Description of the problem**:
   1. While `s12_meridian_fit` and `s16_selection` run Meridian MCMC inside a spawned child process (`fit_meridian_isolated`) so that TensorFlow/XLA graphs (~2–4 GiB RAM) are freed on child exit and jobs can be cancelled mid-sampling, `forecast_budget_plan` (`forecast.py:214-218` — called twice in `s14_forecast` and in every `execute_forecast_job`) calls `fit_meridian` **in-process** inside the long-lived worker, with no cancellation check, no timeout, and permanent TF/XLA heap retention.
   2. Every single time a Meridian `.binpb` model (15–50 MB each) is saved to or loaded from `ArtifactStorage` (`stages.py:720, 1071`, `tasks.py:455, 519`), the code creates a directory via `tempfile.mkdtemp(prefix="mmm_serde_")`, writes `model.binpb` into it, and **never deletes the directory**. On Cloud Run, `/tmp` is an in-memory `tmpfs` backed by the container's 8 GiB RAM limit.
5. **Why it is a bug**: Uncleaned `/tmp/mmm_serde_*` directories plus in-process TensorFlow/XLA allocations steadily consume worker RAM until the container hits an Out-Of-Memory (OOM) crash, and custom forecast jobs cannot be cancelled while sampling.
6. **Steps to reproduce**: Inspect `stages.py:720-724, 1071-1074` and `tasks.py:455-459, 519-523`: `tempfile.mkdtemp` has no matching `shutil.rmtree`.
7. **Expected behavior**: All `.binpb` serialization helpers use `with tempfile.TemporaryDirectory(prefix="mmm_serde_") as tmp_dir:`, and `forecast_budget_plan` uses `fit_meridian_isolated`.
8. **Actual behavior**: Leaks 3–5 `/tmp/mmm_serde_*` directories (~60–200 MB) per pipeline run and runs forecast MCMC in-process without cancellation or timeout.
9. **Business or statistical impact**: Causes worker OOM kills on Cloud Run and prevents cancelling long-running forecast refits.
10. **Recommended solution**: Replace `Path(tempfile.mkdtemp(prefix="mmm_serde_"))` with `with tempfile.TemporaryDirectory(prefix="mmm_serde_") as tmp_dir:` in `stages.py:720, 1071` and `tasks.py:455, 519`, and route `forecast_budget_plan` (`forecast.py:214-218`) through `fit_meridian_isolated`.
11. **Test required to verify the fix**: `T-25` (asserting zero `/tmp/mmm_serde_*` directories remain after save/load).

---

#### `PERF-04` — `wake_worker` Spawns a New 4-vCPU / 8-GiB Cloud Run Job Execution on Every Interactive Slider Change

1. **Unique issue ID**: `PERF-04`
2. **Severity**: `P2 — Medium` (Confirmed via code inspection of `jobs/cloud_run.py` and `jobs/app.py`)
3. **Affected file and exact line number**:
   - `jobs/app.py:122` (`defer_task` unconditionally calls `wake_worker(queue)`)
   - `jobs/cloud_run.py:23-38` (`wake_worker` posts `jobs/{job_name}:run` to the Cloud Run v2 REST API on every call)
   - `deploy/deploy.sh:62-64` (interactive worker configured with `4 vCPU / 8 GiB RAM` and `MMM_WORKER_IDLE_TIMEOUT=300`)
4. **Description of the problem**: Every `POST /scenarios` or `POST /optimisations` call invokes `defer_task`, which immediately calls `wake_worker("interactive")`. `wake_worker` does not check whether an `interactive` Cloud Run Job execution is already running (even though a started worker stays alive for `300s` polling the queue). Instead, it fires a brand-new Cloud Run Job execution (`jobs/{job}:run`) on every request.
5. **Why it is a bug**: If a user tweaks the forecast or optimiser sliders 10 times in two minutes, Cloud Run launches 10 concurrent 4-vCPU / 8-GiB containers—each incurring a cold start (empty `_INTERACTIVE_CACHE`) and billing for 300 seconds of idle wait before exiting.
6. **Steps to reproduce**: Inspect `jobs/cloud_run.py:23-38` and `jobs/app.py:122`.
7. **Expected behavior**: `wake_worker` checks whether a worker on `queue` has reported a recent heartbeat (or debounces wake calls per queue with a 60s cooldown) before starting a new Cloud Run Job execution.
8. **Actual behavior**: Triggers a new Cloud Run Job execution on every `defer_task` call.
9. **Business or statistical impact**: Unnecessary cold-start latency (defeating `_INTERACTIVE_CACHE`) and 10× Cloud Run compute billing amplification.
10. **Recommended solution**: Add an in-memory / DB cooldown check in `wake_worker` (`jobs/cloud_run.py:23-38`) so a new Cloud Run Job execution is requested only when no worker on that queue is currently active.
11. **Test required to verify the fix**: Unit test mocking `urllib.request.urlopen` and verifying that two `wake_worker("interactive")` calls within 30 seconds trigger only one HTTP POST.

---

#### `UI-01` — Optimiser Budget Field `replace(/[^0-9.]/g, "")` Turns `"121,8"` into `€1,218` and `"€ 50 M"` into `€50`

1. **Unique issue ID**: `UI-01`
2. **Severity**: `P2 — Medium` (Confirmed & reproduced via static analysis and Node evaluation)
3. **Affected file and exact line number**: `frontend/src/pages/Optimizer.tsx:47` (`const parsed = customBudget.trim() ? Number(customBudget.replace(/[^0-9.]/g, "")) : histSpend;`)
4. **Description of the problem**: The custom budget text field in `Optimizer.tsx` strips every character except ASCII digits and `.` via `customBudget.replace(/[^0-9.]/g, "")` and passes the result to `Number(...)`:
   - A German decimal comma `"121,8"` becomes `"1218"` $\to$ **`€1,218`** (instead of `€121.8M` or a validation error).
   - A shorthand `"€ 50 M"` or `"50 Mio"` becomes `"50"` $\to$ **`€50`**.
   - Scientific notation `"1.5e8"` becomes `"1.58"` $\to$ **`€1.58`**.
   - German thousands separators `"121.800.000"` become `"121.800.000"` $\to$ `NaN`.
   Combined with `STAT-03` (which silently scales channel bounds down to match any positive budget), entering `"€ 50 M"` submits `total_budget = 50` and returns a `"succeeded"` optimisation allocating `€50` across all 6 channels!
5. **Why it is a bug**: Corrupts standard European number formats and unit suffixes into tiny EUR amounts without warning.
6. **Steps to reproduce**: Enter `"121,8"` or `"50M"` into the Total Budget field on `/runs/:runId/optimiser` and click Run Optimisation.
7. **Expected behavior**: Parse explicit suffixes (`M`/`k`), support or reject comma vs. dot separators unambiguously, require `total_budget >= 1000`, and display a formatted preview (e.g. `= €121,800,000`) beneath the input before submission.
8. **Actual behavior**: Sends `total_budget: 1218` or `total_budget: 50` to `POST /api/v1/runs/{id}/optimisations`.
9. **Business or statistical impact**: Silent 1,000,000× budget mis-scaling for users typing `"50M"` or German decimal commas.
10. **Recommended solution**: Replace line 47 in `frontend/src/pages/Optimizer.tsx` with a dedicated `parseBudgetInput(raw: string)` helper that handles `M`/`k` suffixes, rejects ambiguous multi-dot/comma strings, and previews the parsed EUR value in the UI.
11. **Test required to verify the fix**: `T-40`.


---

### P3 — Low (13 Confirmed Bugs)

#### `STAT-10` — OLS-2 Transformed Features Hardcode `shift=2, decay=0.8` Instead of `settings.in_home_shift_days` / `adstock_shape`

1. **Unique issue ID**: `STAT-10`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis)
3. **Affected file and exact line number**:
   - `core/ols.py:59-61` (`fit_ols_stages` calls `build_ols2_design(df, media_cols=media_cols, control_cols=control_cols, fourier_order=settings.seasonality_harmonics)` without passing `settings.in_home_shift_days` or `settings.adstock_shape`)
   - `core/features.py:86-94` (`build_ols2_design` defaults to `catalog_shift: int = 2, catalog_decay: float = 0.8` and uses a one-parameter exponential geometric filter `s_t = x_{t-shift} + decay * s_{t-1}` rather than `geometric_adstock` or `weibull_pdf_adstock`)
   - `core/tables.py:164` (`catalog_shift(df[c], shift=2)` hardcoded to `shift=2`)
4. **Description of the problem**: When a user changes `in_home_shift_days` (e.g., from `2` to `4`) or `adstock_shape` in the UI, the Meridian specification uses the configured shift (`spec.py:114`), whereas the OLS-2 stage (`core/ols.py:59-61`) and the regression table summary (`core/tables.py:164`) remain hardcoded to `shift=2, decay=0.8`.
5. **Why it is a bug**: Causes the frequentist OLS-2 comparison table (`Stage 03 — Regression Hierarchy`) to evaluate a different print-event shift than the Bayesian Meridian model it is compared against.
6. **Steps to reproduce**: Configure `in_home_shift_days = 4` in `ModelSettings` and inspect `fit_ols_stages(df, settings, schema)`.
7. **Expected behavior**: `fit_ols_stages` and `tables.py` pass `catalog_shift=settings.in_home_shift_days` to `build_ols2_design` and `catalog_shift`.
8. **Actual behavior**: Always uses `shift=2`.
9. **Business or statistical impact**: Minor inconsistency between the frequentist OLS-2 sanity check and the Bayesian Meridian model when `in_home_shift_days != 2`.
10. **Recommended solution**: Pass `catalog_shift=settings.in_home_shift_days` into `build_ols2_design` (`core/ols.py:59-61`) and `core/tables.py:164`.
11. **Test required to verify the fix**: Unit test verifying that changing `settings.in_home_shift_days` changes the OLS-2 design matrix column for print-event channels.

---

#### `STAT-13` — Model Selection Grid Ignores Custom `as_controls` and Custom ROI Priors When Evaluating `role="media"` Candidates

1. **Unique issue ID**: `STAT-13`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis)
3. **Affected file and exact line number**:
   - `core/meridian/selection.py:103` (`as_controls = list(cfg.default_control_channels) if role == "control" else []`)
   - `core/meridian/spec.py:130-137, 224` (`build_custom_spec` only applies the `(0.3, 0.25)` attributed prior to `cost_affiliate` and does not read `settings.custom_roi_priors`)
4. **Description of the problem**: In `run_model_selection`, when testing the `"media"` role candidate (`selection.py:103`), `as_controls` is set to `[]` (empty list), which reclassifies **every** control channel (not just Affiliate) as paid media. Furthermore, `build_custom_spec` hardcodes `if ch == "cost_affiliate": mu.append(0.3); sigma.append(0.25)` (`spec.py:133-135`), ignoring any non-Tchibo control channel or user-specified `settings.custom_roi_priors`.
5. **Why it is a bug**: If a user configures a second control channel (or uploads a dataset where the control channel is not named `"cost_affiliate"`), the selection grid's `"media"` variant promotes all control channels to paid media with generic uninformative priors.
6. **Steps to reproduce**: Inspect `core/meridian/selection.py:101-109` and `core/meridian/spec.py:124-137`.
7. **Expected behavior**: The selection grid should toggle only the target ambiguous channel(s) and respect `settings.custom_roi_priors`.
8. **Actual behavior**: Sets `as_controls=[]` when `role == "media"` and hardcodes `"cost_affiliate"` in `build_custom_spec`.
9. **Business or statistical impact**: Distorts the 6-candidate LOO-IC comparison table when custom control roles or custom ROI priors are used.
10. **Recommended solution**: Parameterize the target channel in `run_model_selection` and apply `settings.custom_roi_priors` in `build_custom_spec`.
11. **Test required to verify the fix**: Unit test checking `build_custom_spec` with custom control channels and custom priors.

---

#### `MER-01` — `custom_roi_priors` Accepts Unknown or Control Channel Keys Without Validation and Silently Ignores Them

1. **Unique issue ID**: `MER-01`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis)
3. **Affected file and exact line number**:
   - `core/model_settings.py:57-65` (`ModelSettings.custom_roi_priors: dict[str, PriorPair]`)
   - `core/meridian/spec.py:157-162` (`for ch in cfg.paid_channels: if ch in settings.custom_roi_priors: ...`)
4. **Description of the problem**: `ModelSettings.custom_roi_priors` accepts arbitrary dictionary keys without checking whether each key matches an active paid media channel in the dataset schema (`cfg.paid_channels`). If an analyst passes a display name (`"TV"` instead of `"cost_tv"`), a typo (`"cost_search"` instead of `"cost_sea"`), or a control channel (`"cost_affiliate"`), `build_spec_from_settings` (`spec.py:157-162`) silently skips the key and falls back to the default `LogNormal(0.2, 0.9)` prior.
5. **Why it is a bug**: Silent fallback to default priors when a user believes they have constrained a channel's ROI prior.
6. **Steps to reproduce**: Call `build_spec_from_settings(df, ModelSettings(custom_roi_priors={"TV": (1.5, 0.3)}), cfg)` and inspect the resulting `roi_m` prior tensor.
7. **Expected behavior**: Raise a `ValueError` (HTTP `422`) when any key in `custom_roi_priors` is not in `cfg.paid_channels`.
8. **Actual behavior**: Silently ignores unknown or control channel keys.
9. **Business or statistical impact**: Custom prior elicitation can be silently dropped due to column-key vs. display-name confusion.
10. **Recommended solution**: Validate `unknown = set(settings.custom_roi_priors) - set(cfg.paid_channels)` at the start of `build_spec_from_settings` and in `api/runs.py` before creating a run.
11. **Test required to verify the fix**: Unit test asserting `ValueError` when an unknown channel key is supplied in `custom_roi_priors`.

---

#### `MER-02` — `MeridianSpecConfig.from_role_config` Mutates Process-Global `CHANNEL_DISPLAY_NAMES` Dictionary

1. **Unique issue ID**: `MER-02`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis)
3. **Affected file and exact line number**: `core/meridian/spec.py:31-39, 90-91` (`CHANNEL_DISPLAY_NAMES[ch.column] = ch.display_name`)
4. **Description of the problem**: `CHANNEL_DISPLAY_NAMES` is a module-level global dictionary in `core/meridian/spec.py:31-39`. Every call to `MeridianSpecConfig.from_role_config(schema)` mutates this global dictionary in place (`spec.py:90-91`).
5. **Why it is a bug**: In a multi-user web process or multi-task worker where two datasets share a column name (e.g., `cost_brand`) with different user-configured display names, concurrent runs overwrite each other's display labels globally.
6. **Steps to reproduce**: Call `MeridianSpecConfig.from_role_config` with two `DatasetSchema` objects having different `display_name` values for the same column name.
7. **Expected behavior**: Store `display_names: dict[str, str]` as an immutable field on `MeridianSpecConfig` (with fallback to `CHANNEL_DISPLAY_NAMES.get(ch, ch)`).
8. **Actual behavior**: Mutates the module-level `CHANNEL_DISPLAY_NAMES` dictionary in place.
9. **Business or statistical impact**: Cross-run label contamination in multi-dataset deployments.
10. **Recommended solution**: Add `display_names: tuple[tuple[str, str], ...]` or a lookup method on `MeridianSpecConfig` and never mutate `CHANNEL_DISPLAY_NAMES`.
11. **Test required to verify the fix**: Unit test verifying `CHANNEL_DISPLAY_NAMES` is unchanged after `MeridianSpecConfig.from_role_config(custom_schema)`.

---

#### `MER-03` — Schema Role Config Allows Setting `cost_affiliate` to `"paid"`, Which `spec.py` and `schema.py` Silently Override Back to Control

1. **Unique issue ID**: `MER-03`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis)
3. **Affected file and exact line number**:
   - `core/schema.py:64-65, 146` (`if ch.role == "control" or ch.column == "cost_affiliate"`)
   - `core/meridian/spec.py:101` (`if ch.role == "control" or ch.column == "cost_affiliate": controls.append(ch.column)`)
   - `frontend/src/components/Upload/RoleMapper.tsx:53-57` (dropdown allows user to select `"paid"` for `cost_affiliate`)
4. **Description of the problem**: The frontend `RoleMapper.tsx` dropdown allows the user to change the role of `cost_affiliate` from `"control"` to `"paid"`. However, both `DatasetSchema.control_channels` (`core/schema.py:146`) and `MeridianSpecConfig.from_role_config` (`core/meridian/spec.py:101`) contain a hardcoded `or ch.column == "cost_affiliate"` check that forces `cost_affiliate` into `default_control_channels` even when `ch.role == "paid"`.
5. **Why it is a bug**: Violates the user's explicit role mapping in the UI without any warning.
6. **Steps to reproduce**: Construct a `DatasetSchema` with `ChannelRole(column="cost_affiliate", display_name="Affiliate", role="paid")` and call `MeridianSpecConfig.from_role_config(schema)`. Observe `cost_affiliate` is still placed in `default_control_channels` (`('cost_affiliate',)`).
7. **Expected behavior**: Default `cost_affiliate` to `role="control"` during initial workbook auto-detection (`workbook.py:194`), but respect `ch.role` in `schema.py` and `spec.py` if the user explicitly overrides it.
8. **Actual behavior**: Hardcodes `or ch.column == "cost_affiliate"` in `schema.py:146` and `spec.py:101`.
9. **Business or statistical impact**: Users cannot override `cost_affiliate` via the RoleMapper UI (`RoleMapper.tsx`), only via the `affiliate_as_control` toggle in `RunConfigForm`.
10. **Recommended solution**: Remove `or ch.column == "cost_affiliate"` from `core/schema.py:146` and `core/meridian/spec.py:101` (keep the auto-detection default in `core/workbook.py:194`).
11. **Test required to verify the fix**: Unit test verifying that setting `ChannelRole(column="cost_affiliate", role="paid")` with `affiliate_as_control=False` places `cost_affiliate` in paid media.

---

#### `MER-04` — Any Channel Active on `<= 15%` of Days Is Auto-Classified as `print_event` and Collides on Single `"catalog_shift"` Feature Name

1. **Unique issue ID**: `MER-04`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis)
3. **Affected file and exact line number**:
   - `core/schema.py:326` (`is_sparse = active_share <= 0.15` $\to$ `role = "print_event"`)
   - `core/meridian/spec.py:106-118` (`df[ch.column] = catalog_shift(df[ch.column], shift=shift)`)
   - `core/tables.py:164` (`work["catalog_shift"] = catalog_shift(df[c], shift=2)` overwrites the single `"catalog_shift"` column in a loop)
4. **Description of the problem**: Any `cost_*` column with nonzero spend on `<= 15%` of days (such as a seasonal Super Bowl TV flight, Black Friday digital burst, or quarterly OOH campaign) is automatically assigned `role = "print_event"` by `validate_and_summarise` (`core/schema.py:326`). All `print_event` channels receive a 2-day postal in-home lag in `spec.py:106-118`, and in `core/tables.py:164` multiple `print_event` channels overwrite the same `work["catalog_shift"]` column in a loop.
5. **Why it is a bug**: Episodic digital or TV flights do not have a 2-day postal delivery delay, and multiple sparse channels overwrite one another in `core/tables.py:164`.
6. **Steps to reproduce**: Upload a dataset with two sparse channels (`cost_print` and `cost_seasonal_tv`, each active on 10% of days) and inspect `core/tables.py:164`.
7. **Expected behavior**: Auto-assign `print_event` only when the column name matches print/catalog/mail keywords (`print`, `catalog`, `mail`, `insert`, `flyer`) or when explicitly selected by the user, and name shifted columns per channel (`f"{c}_shift"`).
8. **Actual behavior**: Classifies all sparse channels (`active_share <= 0.15`) as `print_event` and overwrites `work["catalog_shift"]`.
9. **Business or statistical impact**: Mis-shifts episodic digital campaigns by 2 days on non-Tchibo datasets.
10. **Recommended solution**: Restrict auto-detection of `print_event` in `core/schema.py:326` to sparse channels matching print/catalog/mail keywords, and use per-channel column names in `core/tables.py:164`.
11. **Test required to verify the fix**: Unit test with two `print_event` channels verifying both appear in the OLS summary table without overwriting each other.

---

#### `DATA-05` — No Validation That Dataset Length Exceeds `holdout_days`; `structural_break_year` and `allow_single_year` Are Dead Config Fields

1. **Unique issue ID**: `DATA-05`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis)
3. **Affected file and exact line number**:
   - `core/schema.py:116-119` (`structural_break_year: int | None = 2020`, `allow_single_year: bool = False`)
   - `core/model_settings.py:34` (`holdout_days: int = Field(default=61, ge=14, le=180)`)
4. **Description of the problem**:
   - Neither `validate_and_summarise` (`core/schema.py`) nor `create_run` (`api/runs.py`) checks that the post-cutoff training dataset has substantially more observations than `settings.holdout_days` (e.g., uploading a 90-day dataset with `holdout_days = 180` or `holdout_days = 85`).
   - `DatasetSchema.structural_break_year` and `DatasetSchema.allow_single_year` (`core/schema.py:116-119`) are persisted in the schema JSON but never read anywhere in `mmm_studio` (`features.py` hardcodes `2020-01-01`).
5. **Why it is a bug**: Allows creating runs where `holdout_days >= n_times` (causing Meridian or OLS slicing to fail mid-pipeline) and misleads developers with dead configuration fields.
6. **Steps to reproduce**: Search the codebase for `structural_break_year` and `allow_single_year`; observe zero usages outside `core/schema.py`.
7. **Expected behavior**: Enforce `n_post_cutoff_rows >= max(90, settings.holdout_days * 2)` before run creation, and wire `structural_break_year` into `features.py` and `eda/sales.py`.
8. **Actual behavior**: Dead schema fields and no minimum post-cutoff row vs. `holdout_days` check.
9. **Business or statistical impact**: Confusing mid-pipeline failures on short custom datasets.
10. **Recommended solution**: Add a post-cutoff row count vs. `holdout_days` check in `api/runs.py:create_run` and wire `schema.structural_break_year` into `features.py`.
11. **Test required to verify the fix**: Unit test asserting HTTP `422` when `holdout_days >= n_rows // 2`.

---

#### `API-07` — Stale Sweeper Ignores Stuck `ForecastScenario` / `OptimisationJob` Rows and `meridian_queue_load` Fails Open on DB Error

1. **Unique issue ID**: `API-07`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis)
3. **Affected file and exact line number**:
   - `jobs/app.py:126-222` (`sweep_stale_runs` only inspects `Run` rows, never `ForecastScenario` or `OptimisationJob` rows)
   - `jobs/app.py:68-69` (`except Exception: return 0, 0` in `meridian_queue_load`)
4. **Description of the problem**:
   - `sweep_stale_runs` (`jobs/app.py:126-222`) recovers `Run` rows when their `procrastinate_jobs` entry is terminal or missing, but never sweeps `ForecastScenario` or `OptimisationJob` rows. If an interactive worker crashes (e.g. OOM or SIGKILL), the scenario or optimisation row remains in `status="running"` or `"queued"` forever and the frontend polls indefinitely.
   - `meridian_queue_load` (`jobs/app.py:68-69`) catches `Exception` and returns `(0, 0)`, which causes `api/runs.py:117-125` to bypass the `max_pending_runs` backpressure check whenever the procrastinate table query fails.
5. **Why it is a bug**: Leaves interactive jobs permanently stuck after a worker crash and fails open on queue overload checks.
6. **Steps to reproduce**: Inspect `jobs/app.py:50-70` and `jobs/app.py:126-222`.
7. **Expected behavior**: Extend `sweep_stale_runs` to also mark orphaned `ForecastScenario` and `OptimisationJob` rows as `failed`, and propagate or log errors in `meridian_queue_load`.
8. **Actual behavior**: Only `Run` rows are swept; `meridian_queue_load` returns `(0, 0)` on error.
9. **Business or statistical impact**: Orphaned spinner states in the Forecast and Optimiser tabs if an interactive worker is killed.
10. **Recommended solution**: Sweep stale `ForecastScenario` and `OptimisationJob` rows (>10 minutes old with no active `procrastinate_jobs` row) in `jobs/app.py`.
11. **Test required to verify the fix**: Unit test creating a `"running"` `OptimisationJob` with a failed procrastinate job and asserting `sweep_stale_runs` transitions it to `"failed"`.

---

#### `UI-02` — Currency Formatters Render `999,950..999,999.9` as `"€1,000.0k"` / `"€1000k"` and `NaN` as `"€nan"`

1. **Unique issue ID**: `UI-02`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis and execution)
3. **Affected file and exact line number**:
   - `report/narrative.py:12-23` (`fmt_eur` checks `abs(val) >= 1_000_000` before rounding `val / 1_000` to `1` or `0` decimals, and does not check `math.isnan(val)`)
   - `frontend/src/components/Forecast/fmt.ts:3-10` (`fmtEur` checks `abs >= 1e6` before `Math.round(val / 1e3)`)
4. **Description of the problem**:
   - For values in `[999_950, 999_999.9]`, `abs(val) < 1_000_000` takes the `k` branch, where rounding `val / 1_000` produces `"€1,000.0k"` (`narrative.py:21`) or `"€1000k"` (`fmt.ts:8`) instead of `"€1.0M"` / `"€1.00M"`.
   - Passing `float("nan")` to `fmt_eur` (`narrative.py:12-23`) returns `"€nan"`.
5. **Why it is a bug**: Inconsistent unit formatting at the 1M boundary and unguarded `NaN` rendering.
6. **Steps to reproduce**: Call `fmt_eur(999_990)` (`"€1,000.0k"`) and `fmt_eur(float("nan"))` (`"€nan"`).
7. **Expected behavior**: Guard `math.isnan(val) or math.isinf(val)` returning `"—"`, and promote rounded `k` values `>= 1000` to `"€1.0M"`.
8. **Actual behavior**: Returns `"€1,000.0k"` and `"€nan"`.
9. **Business or statistical impact**: Cosmetic formatting glitches in narrative callouts and forecast cards.
10. **Recommended solution**: Check `if not math.isfinite(val): return "—"` and switch to the `M` branch when `round(abs(val) / 1_000, decimals) >= 1_000`.
11. **Test required to verify the fix**: Unit test for `fmt_eur(999_990) == "€1.0M"` and `fmt_eur(float("nan")) == "—"`.

---

#### `UI-03` — Missing Case-Study Channel Keys Render `"nan×"` in Narrative Callouts and `"Media share of total sales"` Uses Modelled Denominator (`68.1%` vs. `68.9%`)

1. **Unique issue ID**: `UI-03`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via `audit_core_checks.py` Check 12)
3. **Affected file and exact line number**:
   - `core/eda/spend.py:92-97` (`by_col.get("cost_tv", float("nan"))` formatted as `f"{tv_pct:.1f}%"` $\to$ `"nan%"`)
   - `core/meridian/analysis.py:372-377` (`_roas("TV")`, `_roas("Print")`, `_roas("SEA")` formatted as `f"{tv_roas:.2f}×"` $\to$ `"nan×"`)
   - `core/meridian/optimise.py:323` (`media_share_pct = total_media / total_modelled * 100` labeled `"Media share of total sales"` in `sections.py:567` instead of dividing by `observed_total_sales` or labeling `"Media share of modelled sales"`)
4. **Description of the problem**:
   - `spend.py:92-97` and `analysis.py:372-377` look up hardcoded Tchibo channel names (`"cost_tv"`, `"TV"`, `"Print"`, `"SEA"`), default to `float("nan")` if missing, and interpolate them into markdown callout strings as `"nan%"` or `"nan×"`.
   - `optimise.py:323` computes `media_share_pct = total_media / (total_media + baseline_total) * 100` (`68.1%`), where the denominator is posterior mean modelled sales (`€355.02M`) rather than observed sales (`€351.24M`, which gives `68.9%`), while `sections.py:567` labels the KPI card `"Media share of total sales"`.
5. **Why it is a bug**: Produces literal `"nan×"` / `"nan%"` text on custom datasets without `"TV"`/`"Print"`/`"SEA"`, and slightly mismatches the denominator described by the KPI label.
6. **Steps to reproduce**: Run `build_spend_eda` or `build_meridian_diagnosis` on a dataset without `"cost_tv"`.
7. **Expected behavior**: Dynamically rank the actual channels present in `spend_df` / `roas_df` instead of hardcoding `"TV"`, `"Print"`, and `"SEA"`; either divide by `observed_total_sales` or label the metric `"Media share of modelled sales"`.
8. **Actual behavior**: Renders `"nan%"` / `"nan×"` when those channels are absent.
9. **Business or statistical impact**: Broken narrative prose on non-Tchibo datasets and a 0.8 percentage-point denominator discrepancy (`68.1%` vs. `68.9%`) on the Tchibo run.
10. **Recommended solution**: Replace hardcoded channel lookups in `core/eda/spend.py:92-97` and `core/meridian/analysis.py:372-377` with dynamic top/bottom channel lookups from the dataframe.
11. **Test required to verify the fix**: Unit test running `build_spend_eda` on a dataset with custom channel names and asserting `"nan"` does not appear in any callout body.

---

#### `SEC-03` — `_check_xlsx_archive` Inspects Spoofable `ZipInfo.file_size` Headers Instead of Streaming Decompressed Bytes

1. **Unique issue ID**: `SEC-03`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis)
3. **Affected file and exact line number**: `core/workbook.py:43-52` (`total_uncompressed += info.file_size`)
4. **Description of the problem**: `_check_xlsx_archive` checks for zip bombs by summing `info.file_size` from the ZIP central directory headers before calling `pd.ExcelFile(io.BytesIO(raw), engine="openpyxl")` (`workbook.py:91`). In the ZIP format, central-directory `file_size` is an untrusted header field that a crafted archive can set to `0` or `1` while the DEFLATE stream actually decompresses to gigabytes in memory inside `openpyxl`.
5. **Why it is a bug**: Header-only ZIP size checks can be bypassed by falsified ZIP headers.
6. **Steps to reproduce**: Inspect `core/workbook.py:43-52`.
7. **Expected behavior**: Verify actual decompressed byte counts by reading each member stream in bounded chunks up to `_MAX_UNCOMPRESSED_BYTES` (`100 MB`) before passing the archive to `openpyxl`.
8. **Actual behavior**: Trusts `info.file_size`.
9. **Business or statistical impact**: Potential worker/API memory exhaustion (`OOMKilled`) via crafted `.xlsx` archive.
10. **Recommended solution**: Read each `zf.open(info)` in `64 KiB` chunks while incrementing `actual_bytes` and abort immediately if `actual_bytes > _MAX_UNCOMPRESSED_BYTES`.
11. **Test required to verify the fix**: Unit test with a mocked `ZipFile` stream exceeding `_MAX_UNCOMPRESSED_BYTES` verifying `WorkbookLoadError` is raised.

---

#### `SEC-04` — Raw Internal Exception Messages and File Paths Returned in API Error Payloads and Stored in `run.error`

1. **Unique issue ID**: `SEC-04`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via static analysis)
3. **Affected file and exact line number**:
   - `api/runs.py:120, 146, 224` (`f"Queue inspection failed: {exc}"`, `f"Task enqueue failed: {exc}"`, `f"Selection data failed: {exc}"`)
   - `api/forecasts.py:290-291` (`scenario.error = f"{type(exc).__name__}: {exc}"`)
   - `jobs/tasks.py:282, 412` (`run.error = f"{type(exc).__name__}: {exc}"`)
4. **Description of the problem**: Unhandled exceptions from SQLAlchemy, Google Cloud Storage, NetCDF/HDF5 file loading, or TensorFlow are formatted directly into HTTP `500`/`503` `detail` strings and persisted in `run.error` / `scenario.error`, which are returned verbatim to frontend clients.
5. **Why it is a bug**: Leaks internal database connection details, container filesystem paths (`/tmp/mmm_data/...`), and library internals to external clients.
6. **Steps to reproduce**: Inspect `api/runs.py:120, 146, 224` and `jobs/tasks.py:282`.
7. **Expected behavior**: Log full exception tracebacks server-side via `logger.exception(...)` and return sanitized, user-friendly error messages with a correlation ID to the client.
8. **Actual behavior**: Exposes raw `f"{type(exc).__name__}: {exc}"` strings in API responses.
9. **Business or statistical impact**: Information disclosure of internal paths and database errors.
10. **Recommended solution**: Introduce a sanitizing exception formatter for `run.error`, `scenario.error`, `job.error`, and HTTP `500`/`503` responses.
11. **Test required to verify the fix**: Unit test triggering an internal task error and asserting internal `/tmp/` paths are redacted from `run.error`.

---

#### `SEC-05` — Dependency Advisories (`react-router`), Unpinned `uv:latest` in Dockerfile, DB Password on `gcloud` CLI, and Unescaped CSV Formula Cells

1. **Unique issue ID**: `SEC-05`
2. **Severity**: `P3 — Low` (Confirmed & reproduced via `npm audit` and static inspection)
3. **Affected file and exact line number**:
   - `frontend/package.json:24` (`"react-router-dom": "^6.28.0"` — `npm audit` reports `GHSA-2w69-qvjg-hvjx` High XSS via open redirect and `GHSA-pa3q-j576-2932` Moderate open redirect in `@remix-run/router <= 1.23.1`)
   - `backend/Dockerfile:1` (`COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv` uses unpinned `:latest` tag and runs container as `root`)
   - `deploy/deploy.sh:249-254` (`gcloud sql users set-password postgres ... --password="${DB_PASS}"` passes password on CLI argv, and `MMM_DATABASE_URL` is injected as a plain env var instead of `--set-secrets` in `deploy.sh:287-305`)
   - `frontend/src/components/Sections/SectionRenderer.tsx:261-273` (`tableToCsv` does not prefix cells starting with `=`, `+`, `-`, `@` with a single quote to prevent spreadsheet formula injection)
4. **Description of the problem**: Four supply-chain and deployment hygiene issues across the frontend, Dockerfile, and Cloud Run deployment script.
5. **Why it is a bug**: Exposes the build to unpinned base tooling, known `react-router` advisories, process-table secret leakage during deployment, and CSV formula injection if user-supplied channel names start with `=`.
6. **Steps to reproduce**: Run `cd src/app/frontend && npm audit` (`2 vulnerabilities: 1 moderate, 1 high`).
7. **Expected behavior**: Upgrade `react-router-dom` to the patched release (`>=6.30.1`), pin `uv:0.6.6` and add a non-root `USER appuser` in `backend/Dockerfile`, mount `MMM_DATABASE_URL` via Secret Manager `--set-secrets`, and sanitize leading formula characters in `tableToCsv`.
8. **Actual behavior**: Vulnerable `@remix-run/router`, unpinned `uv:latest` running as root, plaintext env vars in `deploy.sh`, and unescaped CSV export.
9. **Business or statistical impact**: Supply-chain and deployment security hardening gap.
10. **Recommended solution**: Apply the 4 targeted fixes in `frontend/package.json`, `backend/Dockerfile`, `deploy/deploy.sh`, and `SectionRenderer.tsx`.
11. **Test required to verify the fix**: Run `npm audit --audit-level=moderate` (`0 vulnerabilities`) and unit-test `tableToCsv` with a cell starting with `"=SUM(A1:A2)"`.

---

## 2. Potential Bugs Requiring Further Validation

In accordance with Section 8 of the audit instructions, the following items are classified as **Potential Bugs Requiring Further Validation** rather than confirmed bugs because they only manifest under non-default parameter combinations or non-Tchibo data topologies:

1. **`PB-01` — Fourier Seasonality `dayofyear / 365.25` Leap-Year Seam at High Harmonic Orders (`core/features.py:39-50`)**:
   - `fourier_seasonality` computes `t = idx.dayofyear.to_numpy(dtype=float) / 365.25`. On December 31 of a non-leap year (`dayofyear = 365`), `t = 365 / 365.25 = 0.9993155`, whereas on January 1 (`dayofyear = 1`), `t = 1 / 365.25 = 0.0027379` (a step of `1.342 / 365.25` instead of `1.0 / 365.25`). At the default `order = 3` harmonics, the phase jump is negligible (`< 0.006 rad`), but if a user selects `seasonality_harmonics = 8` on multi-decade data, using continuous elapsed days `(idx - idx[0]).days / 365.25` avoids the year-boundary phase seam.
2. **`PB-02` — Multi-Channel `print_event` Lag Interaction with Adstock inside Meridian (`core/meridian/spec.py:106-118`)**:
   - `spec.py:106-118` pre-shifts `print_event` spend columns by `in_home_shift_days` (`2` days) before passing them into `DataFrameInputDataBuilder.with_media(...)`, where Meridian then applies its own adstock transformation (`max_lag = 21`) on top of the pre-shifted series. For Tchibo's single `cost_print` channel this is intentional domain modeling (2-day postal transit before in-home adstock decay begins), but for users uploading already-dated in-home impression columns, pre-shifting spend adds a double lag unless `in_home_shift_days` is explicitly set to `0`.

---

## 3. Architectural Risks

1. **`AR-01` — Dual Codebase Drift Between `src/tchibo_mmm/` and `src/app/backend/mmm_studio/core/`**:
   - The repository maintains two nearly identical copies of the core MMM pipeline (`src/tchibo_mmm/` for the CLI pipeline and `src/app/backend/mmm_studio/core/` for the web application). Fixes or methodological updates applied to one package do not automatically propagate to the other.
2. **`AR-02` — Ephemeral Per-Process `_INTERACTIVE_CACHE` on Serverless Cloud Run Workers (`jobs/tasks.py:37`)**:
   - `_INTERACTIVE_CACHE` is an in-process Python dictionary inside transient Cloud Run Job containers. When multiple interactive worker instances run concurrently (amplified by `PERF-04`), each container maintains a separate cache and re-downloads the `~50–150 MB` NetCDF artifact from GCS on cold start.
3. **`AR-03` — Polling-Only Async Architecture Without SSE / WebSockets**:
   - The frontend polls `GET /api/v1/runs/{id}`, `GET /api/v1/runs/{id}/scenarios`, and `GET /api/v1/runs/{id}/optimisations` every `1.5–4.0` seconds. Under multiple open tabs, this generates steady database read traffic and adds `1.5–2.0s` UI latency even when inline tasks complete in `200ms`.
4. **`AR-04` — Dual Task Execution Paths (`inline_tasks` Thread Pool vs. `Procrastinate` Worker Queue)**:
   - Having two distinct task execution code paths (`jobs/app.py:86-113` via `ThreadPoolExecutor` when `inline_tasks=True` vs. `procrastinate` + Cloud Run Jobs when `inline_tasks=False`) caused `API-02` (`run_scenario_task` failing in inline mode because its signature lacked `**kwargs`).
5. **`AR-05` — Hardcoded Tchibo Case-Study Assumptions Embedded in Core Pipeline Modules**:
   - Column names (`cost_tv`, `cost_print`, `cost_sea`, `cost_affiliate`), structural break dates (`2020-01-01`), COVID dummies (`2020-03-15` to `2020-05-03`), and narrative strings (`"Tchibo has 4.3 years..."`, `"€121.8M media across 6 channels"`) are hardcoded across `features.py`, `eda/sales.py`, `eda/spend.py`, `meridian/spec.py`, `meridian/analysis.py`, and `report/sections.py`.
6. **`AR-06` — Lack of Database Migrations (`Base.metadata.create_all` Instead of Alembic)**:
   - Schema initialization relies on `Base.metadata.create_all(engine)` at startup (`main.py:42`). Any column addition or constraint change in production requires manual DDL intervention on Cloud SQL.
7. **`AR-07` — Unbounded Posterior Artifact Storage Growth**:
   - Every completed run stores a full `meridian_model.nc` artifact (`~50–150 MB`) and optional `selection/` artifacts (`6 × ~15 MB`) in GCS/local disk with no lifecycle retention policy or automatic cleanup when runs or datasets are deleted.

---

## 4. Improvement Recommendations

1. **`IR-01` — Extract a Single Shared Domain Package (`mmm_core`)**:
   - Consolidate `src/tchibo_mmm/` and `src/app/backend/mmm_studio/core/` into a single shared workspace package so the CLI and web platform execute the exact same validated mathematical primitives.
2. **`IR-02` — Persist Pre-Extracted Response Curves and Direct Hill Posterior Draws `(beta_gm, alpha_m, ec_m, slope_m)`**:
   - Persist a compact `<1 MB` JSON/Parquet artifact of the posterior Hill/Adstock parameters alongside `meridian_model.nc`. Interactive forecast scenarios and SLIDE/SLSQP budget optimisations can then evaluate exact posterior draws in `<50 ms` without loading TensorFlow or deserializing a `100 MB` NetCDF file.
3. **`IR-03` — Add Property-Based & Synthetic Ground-Truth Recovery CI Suite**:
   - Integrate the synthetic data-generating process (DGP) tests from `TEST_COVERAGE.md` (`T-01` through `T-45`) into GitHub Actions CI so every PR verifies zero-spend invariance, monotonicity, budget conservation, and posterior interval integrity.
4. **`IR-04` — Parameterize Calendar Regimes & Holidays by Country/Region**:
   - Replace hardcoded German holiday rules and 2020 COVID lock-down dates with configurable calendar presets (e.g., `country="DE"`, `structural_breaks=[...]`, `regime_windows=[...]`) in `ModelSettings`.
5. **`IR-05` — Add Server-Sent Events (SSE) for Real-Time Pipeline Stage Progress**:
   - Stream stage-by-stage progress (`Stage 01` $\to$ `Stage 06`) and MCMC sampling progress over an SSE endpoint (`GET /api/v1/runs/{id}/events`) to eliminate client polling overhead.
