# Statistical & Mathematical Validation of MMM Calculations

Audit date: 2026-11-03. Audited commit: `ace6bb0` (`main` branch), `google-meridian[schema]==2.1.0`.

This document separates two fundamental statistical questions:
1. **Are the numbers computed correctly from the fitted Meridian posterior?**
   **Yes, with high fidelity.** Every algebraic and probabilistic identity checked against `meridian.analysis.analyzer.Analyzer` on the stored 4-chain × 1,000-draw posterior holds to floating-point precision.
2. **Do the numbers mean what the application claims they mean?**
   **Not always.** Hold-out forecasting accuracy, the native optimiser's headline lift, the interactive optimiser's constrained allocations, out-of-sample prediction interval coverage, and the ROI of weakly identified channels are presented with stronger claims than the statistical design warrants, and several edge cases (duplicates, missing channel-days, short panels, appended forecast trend) silently alter the underlying estimands.

All reproducible verification scripts are in `src/app/audit/scripts/` (`audit_meridian_posterior.py`, `audit_meridian_fits.py`, `audit_core_checks.py`, `audit_race_and_holdout.py`), with raw JSON/text outputs saved in `src/app/audit/scripts/results/`.

---

## 1. Empirical Evidence Base

| Source | Description |
|---|---|
| **Stored Production Run (`run_f1e8df83da`)** | Full-profile run (`seed=0`, 4 chains × 1,000 draws after 1,000 adapt + 500 burn-in, fitted 2026-11-02): inspected `.binpb` model binaries, `drivers_roi.json`, `drivers_roi_curves.parquet`, `meridian_model.json`, `optimiser.json`, `forecast.json`. |
| **Synthetic Ground-Truth Dataset (`audit_meridian_fits.py`)** | 730-day national daily panel with 3 paid channels (`sea`, `display`, `psm`) generated from Meridian's exact structural family (normalised geometric adstock + Hill saturation + Fourier seasonality + Gaussian noise) with known true ROIs: $\text{ROI}_{\text{sea}} = 3.00$, $\text{ROI}_{\text{display}} = 0.00$ (zero effect), $\text{ROI}_{\text{psm}} = 1.50$. Fitted via `fit_meridian_isolated` (FAST profile: 2 chains × 200 draws). |
| **Controlled Counterfactual Refits (`audit_meridian_fits.py`)** | Case-study workbook (`tests/fixtures/case_study.xlsx`) re-fitted under FAST sampling (`seed=0`) with and without the endogenous Affiliate commission control (`cost_affiliate`), plus repeated-seed (`seed=7` vs. `seed=7` vs. `seed=8`) reproducibility runs. |
| **Short-Dataset Hold-Out Stress Test (`audit_race_and_holdout.py`)** | 390-day slice (`2022-01-01` .. `2023-01-25`) fitted with `holdout_days=200` through `build_meridian_model_section`. |

---

## 2. Posterior Identities & Mathematical Verification (`audit_meridian_posterior.py`)

Independently computed from `models/meridian_Holdout.binpb` using `meridian.analysis.analyzer.Analyzer(mmm)` and `mmm.inference_data`:

| Mathematical Identity / Check | Formula Verified | Empirical Result | Status |
|---|---|---|---|
| **Daily Additive Decomposition** | $\mathbb{E}[Y_t \mid X] = \mathbb{E}[Y_t \mid X_{\text{media}}=0] + \sum_{m=1}^M \Delta Y_{t,m} \quad \forall t \in \{1,\dots,732\}$ | Max absolute error: **$7.22 \times 10^{-9}\text{ €}$** (relative error **$9.07 \times 10^{-15}$**) | PASS |
| **Posterior ROI Draw Ratio** | $\text{ROI}_m^{(s)} = \frac{\sum_t \Delta Y_{t,m}^{(s)}}{\sum_t \text{spend}_{t,m}}$; mean and $(q_{0.05}, q_{0.95})$ across $S=4{,}000$ draws vs. `extract_roi_summary` | Matches `app_roi`, `app_lo`, `app_hi` to **4 decimal places** across all 6 paid channels | PASS |
| **Zero-Spend Boundary Condition** | Setting a paid channel's media execution tensor to `0` (`new_data=DataTensors(media=...)`) yields $\sum_t \Delta Y_{t,m} = 0$ | Display incremental outcome at zero spend = **`0.0 €`** | PASS |
| **Response Curve Monotonicity** | $\bar{f}_m(\lambda_k) \le \bar{f}_m(\lambda_{k+1})$ for $\lambda \in \{0.00, 0.05, \dots, 2.00\}$ | Strictly non-decreasing across all 41 grid points for all 6 channels | PASS |
| **Response Curve Anchor at $1.0\times$** | $\bar{f}_m(1.0) / \sum_t \overline{\Delta Y}_{t,m} = 1$ | Ratio = **`1.0000`** for all 6 channels (`sea`, `catalog`, `display`, `display_network`, `social_media`, `psm`) | PASS |
| **Marginal ROI vs. Curve Derivative** | $\text{mROI}_m \approx \frac{\bar{f}_m(1.05) - \bar{f}_m(0.95)}{0.10 \cdot \sum_t \text{spend}_{t,m}}$ | Within **`0.5%`** across all 6 channels (e.g., SEA: curve slope `4.608` vs. app mROI `4.586`; Catalog: `0.781` vs. `0.780`; Display: `0.235` vs. `0.234`) | PASS |
| **MCMC Convergence ($\hat{R}$)** | Split-chain Gelman-Rubin $\hat{R} < 1.10$ across all structural parameters | $\max \hat{R}$: `roi_m` **`1.0009`**, `alpha_m` **`1.0021`**, `ec_m` **`1.0040`**, `sigma` **`1.0007`** | PASS |
| **Effective Sample Size (ESS)** | Bulk ESS $> 400$ across 4,000 posterior draws | Min bulk ESS: `knot_values` **`2,177.4`**, `roi_m` **`2,307.5`**, `ec_m` **`2,589.0`**, `alpha_m` **`2,651.6`**, `beta_m` **`2,810.7`**, `sigma` **`8,098.5`** | PASS |
| **Deterministic Seed Reproducibility** | Two independent fits with `seed=7` produce identical posterior summaries; `seed=8` differs | $\max |\text{ROI}_{\text{seed}=7, \text{run } 1} - \text{ROI}_{\text{seed}=7, \text{run } 2}| = \mathbf{0.0}$; `seed=8` differs as expected | PASS |
| **Native Meridian BudgetOptimizer Constraints** | $\sum_m x_m^* = B_0$ and $0.70 x_{m,0} \le x_m^* \le 1.30 x_{m,0}$ | Total budget `€121.8M` $\to$ `€121.8M`; channel multipliers in `[0.701, 1.300]` | PASS |

### Formula Verification in `core/metrics.py` and `core/features.py`

| Formula | Implementation | Mathematical Audit |
|---|---|---|
| **Primary KPI (`mae_over_mean`)** | `float(np.mean(abs_err) / mean_actual)` (`metrics.py:34`) | Equivalent to $\text{wMAPE} = \frac{\sum_t |\hat{y}_t - y_t|}{\sum_t y_t}$ for $y_t > 0$. Matches the case-study brief's $\le 15\%$ criterion. |
| **Out-of-Sample $R^2$** | `1.0 - ss_res / ss_tot` where `ss_tot = np.sum((y - mean_actual) ** 2)` (`metrics.py:31-32`) | Uses the hold-out window's own sample mean $\bar{y}_{\text{test}}$. Mathematically standard for within-window variance explained, though lower than training $R^2$ when the seasonal dynamic range inside a single quarter is narrower than the full 2-year range. |
| **Geometric & Binomial Adstock** | `geometric_adstock(x, decay, max_lag=21, normalise=True)` and `binomial_adstock(x, alpha, max_lag=21, normalise=True)` (`features.py:66-83`) | Normalises weights so $\sum_{l=0}^L w_l = 1$, matching Meridian's internal `adstock_hill.Adstock` normalisation. |
| **Fourier Seasonality (`features.py:54-56`)** | `t = index.dayofyear / 365.25`; $\sin(2\pi k t), \cos(2\pi k t)$ | Uses `dayofyear / 365.25` rather than continuous elapsed days. At the transition from leap year 2024 (`dayofyear = 366`) to 2025 (`dayofyear = 1`), $t$ jumps slightly differently than continuous time, though negligible at $K \le 6$. |
| **Linear Trend (`features.py:58`)** | `out["trend"] = np.arange(len(index)) / max(len(index), 1)` | Normalises `trend` onto $[0, 1)$ by dividing by `len(index)`. Unlike `src/tchibo_mmm/features.py:74` (`(index - index[0]).days / 365.25`), dividing by `len(index)` causes `extend_history` (`forecast.py:167`) to rescale historical trend values when $H$ future plan days are appended (see `STAT-12`). |

---

## 3. Synthetic Ground-Truth Recovery & Prior Dominance (`STAT-06`)

Executed via `src/app/audit/scripts/audit_meridian_fits.py` on a 730-day synthetic panel with known true ROIs under Meridian's default `LogNormal(0.2, 0.9)` ROI prior (prior median $= e^{0.2} \approx 1.22$, prior mean $= e^{0.2 + 0.9^2/2} \approx 1.83$, prior 90% interval $[0.28, 5.38]$):

| Channel | True ROI | Posterior Mean ROI | Posterior 90% CI | Covered? | Diagnosis |
|---|---:|---:|---|---|---|
| `sea` | `3.00` | `2.67` | `[2.11, 3.61]` | **Yes** | Strong signal; well-recovered |
| `psm` | `1.50` | `1.24` | `[0.26, 2.86]` | **Yes** | Moderate spend; covered |
| `display` (Zero True Effect) | **`0.00`** | **`0.51`** | **`[0.12, 1.34]`** | **No** | **False positive**: `LogNormal` prior has zero density at $\le 0$ |

On the stored full-profile Tchibo run (`run_f1e8df83da`), comparing the posterior standard deviation against the prior standard deviation for each channel (`audit_meridian_posterior.py`) reveals:

| Channel | Historical Spend | Prior Mean ROI | Posterior Mean ROI | Posterior 90% CI | $\mathbb{P}(\text{ROI} < 1)$ | $\text{SD}_{\text{post}} / \text{SD}_{\text{prior}}$ | Identification Status |
|---|---:|---:|---:|---|---:|---:|---|
| `sea` | `€38.5M` | `1.81` | `8.40` | `[6.61, 10.87]` | `0.00` | `0.79` | Data-informed (endogenous upper bound) |
| `catalog` | `€38.6M` | `1.74` | `0.97` | `[0.87, 1.07]` | `0.72` | `0.03` | Tightly identified by pulsing send-outs |
| `display` | `€29.8M` | `1.65` | `0.66` | `[0.17, 1.49]` | `0.84` | `0.27` | Data-informed ($\mathbb{P}(\text{ROI}<1) = 84\%$) |
| `social_media` | `€8.8M` | `1.85` | `0.72` | `[0.19, 1.64]` | `0.79` | `0.25` | Data-informed ($\mathbb{P}(\text{ROI}<1) = 79\%$) |
| `display_network` | `€4.1M` | `1.66` | `1.23` | `[0.24, 3.26]` | `0.54` | **`0.64`** | Weakly identified |
| `psm` | `€2.0M` | `1.75` | `2.52` | `[0.31, 8.44]` | `0.35` | **`1.77`** | **Prior-dominated** ($\text{SD}_{\text{post}} > \text{SD}_{\text{prior}}$) |

Because `LogNormal` has heavier right tails as its mean shifts upward, `psm`'s posterior standard deviation is `1.77×` wider than its prior standard deviation (`0.16%` of total spend cannot identify a national daily effect), yet the ROI table presents `psm` (`ROI = 2.52`, `mROI = 1.27`) without a prior-dominance warning or $\mathbb{P}(\text{ROI} < 1)$ column.

---

## 4. Hold-Out Accuracy & Future Information Leakage (`STAT-02`, `STAT-04`, `STAT-07`, `STAT-08`)

### 4.1 Realised Demand-Following Costs in the Test Window (`STAT-04`)

In the Tchibo panel:
- $\text{corr}(\text{cost\_affiliate}, \text{sales\_affiliate}) = \mathbf{0.797}$ (Affiliate cost is a commission paid on realised Affiliate orders).
- $\text{corr}(\text{cost\_sea}, \text{clicks\_sea}) = \mathbf{0.959}$ (SEA cost scales with realised consumer search volume).

Both the Q4 2024 hold-out (`8.5%` MAE/mean) and the `"leak-free"` back-test (`9.2%` MAE/mean) pass the **realised** daily `cost_affiliate` and `cost_sea` of Q4 2024 into the model while `analysis.py:515-516` states: *"These are genuine forecasts from the budget and the calendar, not retrospective fits."*

Counterfactual refits (`audit_meridian_fits.py` and `audit_core_checks.py`) quantify the exact impact of removing realised demand-following costs from the test window:

| Model | Test Window | With Realised Affiliate Commission | Without Affiliate Commission | Without Affiliate Commission & SEA Spend |
|---|---|---:|---:|---:|
| **Meridian MMM** (FAST refit, `seed=0`) | Q4 2024 Hold-out (`93d`) | **`8.56%`** | **`11.30%`** (`+2.74 pp`) | — |
| **OLS-2** (Meridian inputs) | Q4 2024 Hold-out (`93d`) | `9.6%` | `12.2%` (`+2.6 pp`) | `13.8%` (`+4.2 pp`) |
| **OLS-2** (Meridian inputs) | Q3 2024 Forward Hold-out (`92d`) | `14.0%` | `15.6%` (exceeds `15%` target) | `24.9%` |

Removing `cost_affiliate` also shifts Meridian's posterior ROI estimates substantially (`display`: `0.66` $\to$ `2.94`; `sea`: `8.43` $\to$ `9.71`; `All Channels`: `3.27` $\to$ `4.24`), demonstrating that omitted-variable bias is absorbed by Display and SEA when Affiliate commission is dropped.

### 4.2 Selection on the Test Window (`STAT-07`) & Full-Panel ROAS Priors (`STAT-08`)

- `run_specification_selection` (`core/meridian/selection.py:101-116`) evaluates the grid of `catalog_shift ∈ {0, 2, 5}` × `affiliate_role ∈ {"control", "media"}` on the **same Q4 2024 hold-out** whose MAE/mean is reported as the headline out-of-sample test error.
- `attributed_roas_priors` (`core/meridian/spec.py:143-155`, `selection.py:121`) computes `wide[sales_col].sum() / wide[cost_col].sum()` across the **entire** `wide` dataframe, including the hold-out period's attributed sales.

### 4.3 Short-Dataset Hold-Out Collapse (`STAT-02`)

When `len(index) <= 2 * holdout_days`, `MeridianSpecConfig.from_role_config` (`spec.py:119-127`) falls back to `HOLDOUT_Q4_2024 = ("2024-10-01", "2025-01-01")`. On a 390-day panel (`2022-01-01` .. `2023-01-25`) with `holdout_days=200` (`audit_race_and_holdout.py`):
- `build_meridian_model` (`spec.py:258-269`) finds `0` dates overlapping `2024-10-01..2025-01-01` and sets `holdout_spec = None` (training on all 390 days).
- `build_meridian_model_section` (`analysis.py:297-302`) executes `m_test = ... if len(test_df) else m_train`, reporting `holdout_mae_over_mean = 0.0809` (`8.1%`, identical to `train_mae_over_mean = 0.0809`) with `reported_holdout_days = 0` and badge `"Target ≤ 15 % · met"`.

---

## 5. Budget Optimisation Audit (`STAT-03`, `STAT-05`, `STAT-09`)

### 5.1 Native Meridian Optimiser (`STAT-05`)

On `run_f1e8df83da`, Meridian's `BudgetOptimizer` (`optimise.py:391-417`) reallocates the historical `€121.8M` budget within $\pm 30\%$ per channel and reports **`+€44.05M` (`+11.1%`)** incremental sales lift:

| Channel | Historical Spend | Optimal Spend | Spend Change | $\Delta$ Incremental Sales | Share of Net Lift |
|---|---:|---:|---:|---:|---:|
| `sea` | `€38.53M` | `€50.07M` | `+€11.54M` (`+29.9%`) | **`+€47.01M`** | **`+106.7%`** |
| `psm` | `€2.02M` | `€2.63M` | `+€0.61M` (`+30.0%`) | `+€0.68M` | `+1.5%` |
| `catalog` | `€38.56M` | `€39.17M` | `+€0.61M` (`+1.6%`) | `+€0.47M` | `+1.1%` |
| `display` | `€29.80M` | `€20.89M` | `-€8.91M` (`-29.9%`) | `-€2.52M` | `-5.7%` |
| `social_media` | `€8.82M` | `€6.18M` | `-€2.64M` (`-29.9%`) | `-€0.85M` | `-1.9%` |
| `display_network` | `€4.06M` | `€2.85M` | `-€1.21M` (`-29.8%`) | `-€0.74M` | `-1.7%` |
| **Total** | **`€121.80M`** | **`€121.80M`** | **`€0.00M`** | **`+€44.05M`** | **`100.0%`** |

Every non-SEA reallocation combined nets `-€2.97M`; `106.7%` of the headline lift comes from expanding SEA spend by `+29.9%`. While the section includes a caveat callout noting that SEA is an endogenous upper bound, `stages.py:414-420` surfaces `+11.1%` as a green `"success"` KPI on the Executive Overview.

### 5.2 Interactive Constrained Optimiser (`STAT-03`, `STAT-09`)

`optimise_hill_response_curves` (`core/meridian/optimise.py:51-236`) contains four mathematical flaws confirmed by `audit_core_checks.py` and `audit_api_checks.py`:
1. **Silent Feasibility Rewriting (`optimise.py:123-131`)**: When `budget < lo.sum()` or `budget > hi.sum()`, the function scales `lo = lo * (budget / lo.sum()) * 0.95` or `hi = hi * (budget / hi.sum()) * 1.05` instead of returning an infeasibility error. Requesting `total_budget = €50M` with bounds `[0.9×, 1.1×]` on `€121.8M` historical spend silently allocates **`0.411×`** to every channel and returns `status = "succeeded"`.
2. **Unlocking Fixed Catalog (`optimise.py:117, 128-131, 253-254`)**: When `include_catalog=False`, Catalog bounds are set to `(c_sp, c_sp)`. If `budget > hi.sum()`, `hi` is scaled upward while `lo` stays at `c_sp`, unlocking Catalog (`€20.0M` $\to$ `€26.6M` in `OPT-1`) even though the response payload reports `"include_catalog": false`.
3. **Post-SLSQP Bound Violation & Unchecked Convergence (`optimise.py:149-160`)**: `scipy.optimize.minimize(..., method="SLSQP")` is called without checking `res.success`. Line 157 clips `x_opt` to `[lo, hi]`, and line 159 executes `x_opt = x_opt * (budget / x_opt.sum())` **without re-clipping**, pushing channels past their upper or lower bounds whenever other channels are pinned at a bound.
4. **PCHIP Extrapolation Beyond $2\times$ & Mislabelled Totals (`optimise.py:106-108, 223-224`)**: Response curves are evaluated only on $\lambda \in [0, 2.0]$ (`analysis.py:130`), but `api/optimiser.py:24-25` accepts `max_multiplier` up to `5.0`, extrapolating cubic splines (`PchipInterpolator(..., extrapolate=True)`) into unmodelled spend territory. Meanwhile, `optimise.py:223-224` stores `cur_inc_total` and `opt_inc_total` (media incremental outcome only, omitting ~73% baseline sales) under the keys `historical_total_sales` and `optimal_total_sales`.

---

## 6. Prediction Intervals, Quantile Summing & Forecast Scaling (`STAT-11`, `STAT-12`)

1. **Out-of-Sample Prediction Interval Calibration (`STAT-11`)**:
   - `_prediction_draws` (`forecast.py:170-180`) adds a single Gaussian draw $\epsilon_{s,t} \sim \mathcal{N}(0, \sigma_s \cdot s_{\text{KPI}})$ per posterior draw $s$ and day $t$.
   - On `run_f1e8df83da`, empirical coverage of the nominal 90% prediction interval (`[prediction_lo, prediction_hi]`) is `91.7%` in-sample (`639` training days), **`79.6%`** on the Q4 2024 hold-out (`93` days, mean bias `+€68.9k/day`), and **`80.6%`** on the leak-free back-test (`93` days).
   - Furthermore, in `evaluate_forecast_scenario` (`forecast.py:321-323`), scenario prediction intervals are computed by shifting the baseline `pred_draws` by the **posterior mean difference** `shift = (alt_expected - base_expected).mean(axis=0)` rather than adding `alt_expected - base_expected` draw-by-draw, discarding parameter uncertainty in the scenario delta.
2. **Summing Daily Quantiles for Weekly and Horizon Totals (`STAT-12a`)**:
   - `evaluate_forecast_scenario` (`forecast.py:399-402`), `build_forecast_section` (`forecast.py:583-595`), and `execute_forecast_job` (`jobs/tasks.py:537-538`) compute horizon and weekly prediction bounds by summing daily 5th and 95th percentiles (`alt_daily["prediction_lo"].sum()`, `resample("W-SUN").sum()`).
   - Because the sum of quantiles equals the quantile of the sum only under perfect comonotonicity ($\rho = 1$), summing daily intervals inflates the independent observation-noise component over $T = 42$ days by $\sqrt{42} \approx 6.48\times$.
3. **KPI Standard Deviation & Linear Trend Shift in `extend_history` (`STAT-12b,c`)**:
   - `extend_history` (`forecast.py:165`) fills $H$ appended plan rows with `wide["total_sales"].mean()`. Because Meridian's `KpiTransformer` standardises over all $N + H$ rows (before `HoldoutSpec` masks the likelihood), adding $H$ zero-deviation rows leaves the sample mean unchanged but shrinks the KPI standard deviation by $\sqrt{\frac{N-1}{N+H-1}}$ (`-2.75%` for $H=42$, `-5.63%` for $H=90$), contradicting `forecast.py:656-657` (*"Meridian's KPI scaling is unchanged"*).
   - Line 167 then calls `add_calendar_and_adstock_features(combined)`, which recomputes `trend = np.arange(len(index)) / len(index)` (`core/features.py:58`) over $N + H$ days instead of $N$ days—compressing historical `trend` values by $\frac{N}{N+H}$ whenever a forecast plan is appended.
