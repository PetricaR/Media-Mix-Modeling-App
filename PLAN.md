# Tchibo Case Study: Marketing Sales Prediction with Meridian MMM (Project Plan)

## 1. Objective and approach

| Item | Definition |
| --- | --- |
| **Business question** | Can daily total demand be forecast from the marketing budget (plus other drivers) for short-term business steering? |
| **Target (y)** | `total_sales` = sum of `sales_by_channel` over **all 12 channels**, per day |
| **Inputs** | Daily cost per paid channel (7 channels), plus controls: calendar, holidays, seasonality, trend |
| **Model** | **Google Meridian marketing mix model (MMM)**: a Bayesian **regression** of daily sales on transformed channel spend plus controls. It runs as a national model on daily data |
| **Acceptance KPI** | `MAE / mean(y_test) ≤ 15 %` on a time-based hold-out. This is exactly Meridian's built-in `wMAPE` (Σ\|actual − predicted\| / Σ actual, verified in the source code) |
| **Second metric** | RMSE (penalises large misses on peak days) and R². Meridian also reports MAPE |
| **Deliverables** | 1) Presentation (approach and visual results), 2) reproducible PoC code in notebooks and `src/`, 3) Bonus: Google Cloud ML lifecycle and services |

Sources: [docx/Case Study Marketing Data Science.pdf](docx/Case%20Study%20Marketing%20Data%20Science.pdf), [data/DataScienceMarketingUseCase.xlsx](data/DataScienceMarketingUseCase.xlsx), and the [Meridian getting-started notebook](https://developers.google.com/meridian/notebook/meridian-getting-started).

### 1.1 Why Meridian counts as the brief's "regression analysis"

Meridian is a linear regression on transformed media variables, estimated with Bayesian methods:

```text
sales_t = μ_t                                   baseline: intercept + time effects (knots)
        + Σ_k γ_k · control_k,t                 calendar, holidays, seasonality, trend
        + Σ_c β_c · Hill_c( Adstock_c(spend_c) )  one term per paid channel
        + ε_t
```

This structure answers several of the brief's questions directly:

| Brief question | How Meridian answers it |
| --- | --- |
| What should be considered about the costs? | **Adstock** models carry-over (spend keeps working on later days). The **Hill** function models saturation (diminishing returns). Both are estimated per channel |
| Which channels matter most for sales? | Each channel's **contribution**, **ROI** and **marginal ROI**, all with credible intervals |
| What is the challenge of using costs as inputs? | **Priors** regularise the effects of correlated channels. Credible intervals show honestly when an effect cannot be identified |
| Forecast from the budget | `Analyzer.expected_outcome(new_data=...)` predicts sales for a *planned* budget |
| Choose and explain the approach | A regression approach, as the brief prefers, built for marketing data and backed by Google |

**Benchmark:** a classical OLS regression with the same inputs is kept as a transparent sanity check. It is fast, and the prototype numbers are in section 2.6. Meridian remains the headline model.

### 1.2 What the brief asks for, and where the plan answers it

| Brief section | Requirement | Answered in |
| --- | --- | --- |
| Challenge | Forecast daily demand from the marketing budget; MAE / mean ≤ 15 % | Phase 3 |
| 1. Data Understanding | EDA | Phase 1 |
| | Correlation matrix of channels to sales | Phase 1 |
| | Conclusions from channel interactions | Phase 1, answer 1 |
| | Obstacles for further processing | Phase 1, answer 2 |
| | Influencing factors that cannot be used | Phase 1, answer 3 |
| 2. Data Preparation | Wide table: all channels as cost columns, daily total sales as the dependent variable | Phase 2 |
| | What to consider about costs | Phase 2 |
| | Which channels matter most for sales | Phase 2 and Phase 3 |
| | At least 2 new features | Phase 2 |
| | Why channel costs are hard to use as influencing factors | Phase 2 |
| 3. Modeling | Choose and explain the approach (regression preferred) and the feature set | Section 1.1 and Phase 3 |
| | Train and validate | Phase 3 |
| | MAE plus one other metric | Phase 3 |
| 4. Bonus | ML lifecycle in Google's reference architecture; other GCP ML services | Phase 4 |
| Hints | Short presentation with visuals | Section 6 |
| | Model performance is a bonus, so a clear approach matters more than the last percentage point | Applies throughout |

---

## 2. Initial data findings (already verified)

The brief mentions a `.csv`, but the file we received is an `.xlsx` with two sheets:

- **`data`**: 8,740 rows in long format (`date × channel`), with the columns `date, channel, sales_by_channel, impressions, clicks, costs`.
- **`info`**: a column dictionary. It contains two notes that matter later:
  - For Catalog, *impressions* means *circulation*.
  - *Costs* are booked on the **day of causation**. For example, the full catalog cost lands on the day the catalog is sent out.

### 2.1 Structure and quality

- The data covers **2023-01-01 → 2025-01-01** (732 days). No calendar days are missing overall.
- **The rows are shuffled**, so we must sort them. There are no duplicate `(date, channel)` pairs.
- **Missing channel-days:** TV has 38 (scattered between Feb and Sep 2024), Catalog has 4 and Owned has 2. TV also has 8 days with sales below 1.
- **2025 contains a single day** (Jan 1). It needs care in any weekly or yearly aggregation.

### 2.2 Channel overview

| Channel | Costs available | Sales share | Cost share | Attributed ROAS | Note |
| --- | --- | ---: | ---: | ---: | --- |
| SEA | daily | 26.3 % | 30.4 % | 3.9 | Billed per click: cost and clicks correlate at r = 0.96 |
| Direct Type-in | – | 21.0 % | – | – | Organic, no spend |
| SEO | – | 14.0 % | – | – | Organic, no spend |
| **Catalog** | **28 send-out days only** | 13.9 % | 30.5 % | 2.1 | 1.0–1.7 M€ per send-out. *Impressions* = circulation (r = 0.94 with cost). Clicks are always 0 |
| Display | daily | 11.2 % | 23.5 % | 2.2 | |
| Affiliate | daily | 6.9 % | 3.8 % | 8.2 | Commission-based, so cost partly *follows* sales |
| Display Network | daily | 2.5 % | 3.2 % | 3.6 | |
| Social Media | daily | 2.0 % | 6.9 % | 1.3 | Spend spikes up to 160 k€ (2024-10-07). Monday spend is twice the level of other days |
| PSM | daily | 1.1 % | 1.6 % | 3.3 | No impressions |
| TV | – | 0.6 % | – | – | Has sales but no costs. 38 missing days |
| Direct Referred | – | 0.3 % | – | – | |
| Owned | – | 0.2 % | – | – | |

Paid channels (those with costs) account for **64 %** of attributed sales.

### 2.3 Target behaviour

- Average daily sales are **787 k€** (std 282 k€). The minimum is 218 k€ (24 Dec 2023) and the maximum is 2.75 M€ (27 Oct 2024).
- **Trend:** 2024 sales were +20.7 % vs 2023, while total spend rose only +12.4 %.
- **Weekly pattern:** Sunday is the strongest day (Ø 938 k), then Tuesday (Ø 875 k). Saturday is the weakest (Ø 714 k).
- **Yearly pattern:** demand peaks in Q4 (Nov 2024 averaged 1.21 M/day). It dips around Christmas (23–25 Dec are the lowest days), and Black Friday shows up as a peak.

### 2.4 Catalog is the main driver of demand spikes

- **When it is sent out:** almost always on a Thursday (26 of 28 send-outs), every 3–5 weeks.
- **Response pattern:** total demand peaks **3 days after send-out (Sunday)** at about 1.35 M, against a baseline of about 0.7 M. The effect then fades over roughly 2–3 weeks.
  - 20 of the 25 highest-sales days fall within 7 days after a send-out.
- **Why the raw correlation misses it:** catalog cost and sales on the same day correlate at **−0.01**. Shifting cost by 3 days raises the correlation to **0.40**.
- **Natural experiment:** no catalog went out for 42 days (24 Aug → 5 Oct 2023). Sales in September 2023 averaged **473 k/day**, versus **980 k/day** in September 2024, when a catalog went out on 12 Sep.
- **Halo effect:** in the 7 days after a send-out, total demand rises **+38 %**. Sales attributed to *other* channels also rise:

  | Channel | Uplift in the 7 days after send-out |
  | --- | ---: |
  | SEO | +20 % |
  | Social Media | +20 % |
  | Direct Type-in | +17 % |
  | Affiliate | +14 % |
  | SEA | +11 % |

  This means the catalog's channel-level ROAS (2.1) **understates** its real impact. An MMM that models *total* sales captures this halo, which channel attribution cannot.

### 2.5 Correlations: same-day cost vs total sales

| | Affiliate | SEA | Display | PSM | Display Network | Social | Catalog (lag 0 / lag 3) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Pearson | 0.76 | 0.72 | 0.56 | 0.49 | 0.44 | 0.33 | −0.01 / 0.40 |

The digital channels' costs also correlate with each other (0.22–0.58). Their variance inflation factors (VIF) are only 1.4–2.4, so this **multicollinearity** is moderate. Reverse causality is the bigger problem (notebook 02).

### 2.6 Baselines and the OLS benchmark

**Naive baselines** (MAE / mean over the full period):

| Baseline | MAE / mean |
| --- | ---: |
| Constant mean | 26.1 % |
| Weekday mean | 24.9 % |
| Last week's value | 26.0 % |
| Yesterday's value | 19.1 % |

None of them meets the 15 % target.

**OLS benchmark.** A quick ladder of linear regressions, each step adding features to the previous one. Two validations were used:

- **Q4 hold-out:** train until 2024-09-30, test on 2024-10-01 → 2025-01-01 (93 days).
- **Walk-forward cross-validation:** 8 folds of 6 weeks each, 2024-01-15 → 2025-01-01, with an expanding training window.

| Step | Features | Q4 hold-out MAE / mean | Walk-forward mean | Walk-forward worst fold |
| --- | --- | ---: | ---: | ---: |
| Baseline | Weekday mean | – | 20.5 % | 37.6 % |
| OLS 1 | Raw channel costs only | 10.7 % | 15.0 % | 26.9 % |
| OLS 2 | + calendar and trend | 10.3 % | 13.3 % | 20.2 % |
| **OLS 3** | **+ catalog timing** | **9.1 %** | **9.3 %** | **17.4 %** |

What this shows:

- **The target is achievable** with a regression on these inputs.
- **Catalog timing is the decisive feature.** Meridian has to capture it too (see Phase 2).
- **A single Q4 hold-out can mislead.** Raw costs pass it, but average 15 % across walk-forward folds.
- **Benchmark for Meridian:** about 9 % MAE / mean.

### 2.7 Meridian smoke test (works on daily national data)

**Setup:**

- Spend used as the media variable for all 7 paid channels.
- Catalog shifted +2 days (in-home date), with binomial decay. `max_lag = 21`.
- Controls: weekday, holidays, Christmas, Black Week, Fourier terms, trend.
- Hold-out: Q4 2024.
- Fast sampling: 2 chains × 200 draws, **53 s on CPU**.

**Hold-out accuracy** (`Analyzer.predictive_accuracy()`):

| Set | R² | MAPE | **wMAPE = MAE / mean** |
| --- | ---: | ---: | ---: |
| Train | 0.86 | 9.2 % | 8.9 % |
| **Test (Q4 2024)** | **0.87** | **9.4 %** | **9.0 %** |

What this shows:

- **The target is met**, and Meridian matches the OLS benchmark (9.1 %).
- **Train and test errors are almost the same**, so there is no sign of overfitting.

**Posterior mean ROI** (an early, unconverged run):

| SEA | Display | Affiliate | Display Network | Social Media | PSM | Catalog |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 7.2 | 0.9 | **48.7** | 1.4 | 0.8 | 3.8 | **1.0** |

These ROIs are not ready to present. They point to two issues for Phase 3:

- **Affiliate's ROI of 48.7 is implausible.** Affiliate costs are commissions on sales, so the cost follows demand. Options:
  - A tighter, informative ROI prior.
  - Treating Affiliate as a control, i.e. a cost of sales rather than a budget lever.
- **Catalog's ROI of 1.0 looks too low** given the +38 % halo. Options:
  - Tune the shift k, the decay type and `max_lag`.
  - Check whether the trend and seasonality controls absorb catalog effects.
  - Confirm convergence with full sampling.

---

## 3. Environment (uv)

```bash
uv sync --all-groups       # core + dev + Meridian 2.1.0 (TensorFlow 2.21, JAX)
uv run jupyter lab
```

- Python is pinned to **3.12** (`.python-version`). Meridian 2.1.0 requires TensorFlow 2.21, which supports Python up to 3.13.
- Meridian runs on **CPU** locally (verified: `jax.devices() → [CpuDevice]`). A national daily model is small. If final sampling is too slow, switch to Colab with a T4 GPU.
- Dependencies are in `pyproject.toml`, and `uv.lock` should be committed.

---

## 4. Target repository structure

```text
├── data/                         # raw xlsx (read-only)
├── docx/                         # case brief
├── notebooks/
│   ├── 01_data_understanding.ipynb
│   ├── 02_data_preparation.ipynb # wide table + Meridian input table
│   ├── 03_meridian_mmm.ipynb     # main model: fit, validate, interpret, optimise
│   └── 04_benchmark_ols.ipynb    # transparent regression benchmark
├── src/tchibo_mmm/
│   ├── data.py                   # load, sort, pivot, clean → daily wide table
│   ├── features.py               # catalog in-home shift, calendar/holiday/seasonality controls
│   ├── mmm.py                    # build InputData, ModelSpec, fit, save/load
│   └── evaluate.py               # MAE, wMAPE, RMSE, R², hold-out split, plots
├── outputs/                      # saved model (.binpb), Meridian HTML reports
├── reports/figures/              # exported charts for slides
├── presentation/                 # final deck (PDF/PPTX)
├── pyproject.toml, uv.lock, .python-version
└── PLAN.md
```

---

## 5. Work plan, following the brief

### Phase 1: Data Understanding (`01_data_understanding.ipynb`)

- [x] Load both sheets, sort the data, and validate it: data types, uniqueness, calendar completeness, NaN patterns per channel.
- [x] Plot daily sales and costs per channel over time, and check distributions and outliers (Social Media spikes, Christmas, Black Friday).
- [x] Decompose the target into trend, weekly and yearly components (STL), and plot weekday and month profiles.
- [x] **Correlation matrix (required):**
  - Pearson and Spearman heatmaps of channel costs vs total sales.
  - Lagged correlations for days 0–21 (this reveals the catalog's 3-day delay).
  - Channel-sales vs channel-sales heatmap (shows the halo).
- [x] Efficiency KPIs per channel: CPC, CPM, attributed ROAS, cost share vs sales share.
- [x] Catalog event study: average daily sales by days since send-out (0–28), split by channel.

**Draft answers to the brief's questions:**

1. **What can we conclude from the channel interactions?**
   - Digital budgets move together and follow the season, which makes their individual effects hard to separate.
   - The catalog works with a delay and lifts other channels (halo). Its same-day correlation is misleading.
   - Affiliate and SEA costs are partly a *consequence* of demand, because they are billed per sale or per click.
   - Attributed sales are not the same as incremental sales. This is why we model *total* sales with an MMM.
2. **What obstacles exist for further processing?**
   - The raw data is in long format, and its rows are shuffled.
   - Gaps (TV, Catalog, Owned) and near-zero sales values.
   - Catalog costs are lumpy, booked once on the send-out day.
   - Channel costs are on very different scales, from 2.7 k€ per day for PSM to 1.4 M€ per catalog send-out.
   - Multicollinearity and reverse causality between costs and sales.
   - Only two years of history, so there are just two Q4s and two Black Fridays to learn from.
   - 2025 contains a single day.
   - There are no external drivers in the data: promotions, prices, Tchibo's weekly product "worlds", weather, or competitors.
3. **Which influencing factors cannot be used?**
   - **`sales_by_channel` as a feature:** it is target leakage, because the channel sales add up to y.
   - **Clicks and impressions:** they are recorded on the same day as the sales, are outcomes of spend, and are not known when the forecast is made. PSM also has no impressions, and catalog clicks are always 0.
   - **TV, SEO, Direct Type-in, Direct Referred and Owned:** none of them has cost data, so they cannot act as budget levers. Their sales stay part of the target, and Meridian's baseline absorbs them.

### Phase 2: Data Preparation (`02_data_preparation.ipynb`, `src/.../data.py`, `features.py`)

- [x] **Build the wide daily table** exactly as the brief asks: **all 12 channels as cost columns**, plus `total_sales` (the sum over all 12 channels) as the dependent variable.
  - Five channels (TV, SEO, Direct Type-in, Direct Referred, Owned) have no costs at all. Their columns stay in the table to show this, then they are dropped from the model inputs.
  - Missing channel-days count as 0 sales. This affects less than 0.6 % of total sales, and the rows are flagged.
  - A missing catalog cost on a non-send-out day means 0 spend.
- [x] **Turn the wide table into Meridian's input table.** Each column gets a role:

  | Meridian role | Columns | Note |
  | --- | --- | --- |
  | KPI (`kpi_type="revenue"`) | `total_sales` | Sales are monetary, so no `revenue_per_kpi` is needed |
  | Media and media spend | Costs of SEA, Display, Display Network, Social Media, Affiliate, PSM, Catalog | **Spend is used as the media variable.** The forecast must run on the budget, impressions are not known in advance, and PSM has none |
  | Controls | Weekday dummies, German holidays, Christmas window, Black Week, yearly Fourier terms, trend | These give the baseline its shape |
  | Not used | Clicks, impressions, `sales_by_channel`, TV and the organic channels | Reasons are in Phase 1, answer 3 |

  A variant that uses impressions as the media variable (circulation for the Catalog) is run as a sensitivity check only.
- [x] **What to consider about costs, and how Meridian handles it:**
  - **Carry-over:** costs sit on the day of causation, not on the day of effect. Meridian's **adstock** covers this, with `max_lag = 21` days.
  - **Diminishing returns:** handled by Meridian's **Hill** saturation function. In the observed spend range most channels look near-linear; Display Network flattens and Social Media is S-shaped (notebook 02). The Hill curve matters most for budget scenarios beyond today's spend.
  - **The catalog's delayed peak:** Meridian's decay curves (geometric and binomial) always peak on day 0. This was verified in `adstock_hill.py`. They cannot represent a peak on day 3, so we handle it in preprocessing:
    - Shift catalog spend to its *in-home date* (k days after send-out). In notebook 02, k = 1–2 with slow decay fits best; k = 3 misses the ramp-up. The default is k = 2, and notebook 03 tests k ∈ {1, 2, 3} on the hold-out.
    - Use binomial decay for the catalog's longer tail.
    - **Fallback:** add catalog ramp-up indicators (days 0–2 after send-out) as `non_media_treatments`.
  - **Scale:** Meridian scales each media channel internally, so the large catalog amounts are not a problem.
  - **NaN vs 0:** "not applicable" (organic channels) is different from "0 spend" (catalog off-days).
  - **Changing prices:** CPC and CPM drift, so the same budget does not buy the same exposure over time.
  - **Forecasting needs planned budgets:** at forecast time we need the *planned* budget, which for performance channels is only a cap.
- [x] **Feature engineering.** The brief requires at least 2 new features; we plan 3:
  1. **Catalog in-home shift:** spend moved k days forward, with ramp-up indicators as the fallback. In the OLS benchmark, catalog timing was the single biggest improvement.
  2. **Calendar controls:** weekday dummies, German public holidays (`holidays` package), the Christmas window (20–31 Dec), and Black Week / Cyber Monday.
  3. **Seasonality and trend controls:** yearly Fourier terms (k = 1–2) and a linear trend. They are used as controls rather than many knots, because knots that fall inside a time-based hold-out see no training data.
- [x] **Channel importance:**
  - Main view: Meridian's **contribution share, ROI and marginal ROI**, with credible intervals.
  - Cross-checks: the correlation view from Phase 1 and the OLS benchmark coefficients.
  - First answer from OLS with Newey-West standard errors (notebook 02):
    - **Affiliate, SEA and Catalog** are significant (p < 0.001).
    - Display, Display Network, Social Media and PSM cannot be told apart from 0.
    - Affiliate is likely inflated by reverse causality (commission). Catalog is likely understated, because the seasonality controls absorb part of its regular schedule.
- [x] **Why channel costs are hard to use as influencing factors:**
  - **Reverse causality:** budgets are planned around expected demand, and CPC and commission costs scale with demand. This inflates the ROI of SEA and Affiliate.
  - **Multicollinearity:** channel costs rise and fall together. Meridian's priors stabilise the estimates, but wide credible intervals remain.
  - **Delayed effects:** spend shows its effect only later.
  - **Saturation:** extra spend has diminishing returns.
  - **Attribution bias:** sales are assigned to the last-touch channel.
  - **Limited variation:** with little independent change in spend, effects cannot be identified reliably.
  - **Practical consequence:** a good *forecast* is not the same as a *causal* ROI. Lift tests or geo experiments would calibrate the priors.

### Phase 3: Modeling with Meridian (`03_meridian_mmm.ipynb`, `src/.../mmm.py`, `evaluate.py`)

> **Status: done.** Full results are in notebooks 03 and 04; figures `03_*` and `04_*` are in `reports/figures/`.
>
> **Final configuration:**
>
> - Catalog shifted +2 days.
> - Affiliate entered as a **control**, not a media channel.
> - Meridian's default ROI priors.
> - Sampling: 4 chains × 1000 draws, 142 s on CPU. Max R-hat 1.003.
>
> **How the configuration was chosen:**
>
> - Fast fits compared catalog shifts k ∈ {1, 2, 3} with Affiliate as media vs control. Affiliate as a control wins on every k, and removes an implausible Affiliate ROI of about 50.
> - Informative priors (centred on attributed ROAS) leave SEA, Catalog and the hold-out error unchanged. The small channels move toward their priors, so the data identifies them only weakly.
>
> **Accuracy** (MAE / mean on forward hold-outs):
>
> | Model | Q4 2024 | Q3 2024 |
> | --- | ---: | ---: |
> | **Meridian** | **8.5 %** (R² 0.89, RMSE 115 k) | **11.0 %** |
> | OLS on the same inputs | 9.6 % | 14.0 % |
> | OLS on raw costs | 10.7 % | 13.5 % |
> | Naive weekday mean | 34.8 % | 19.0 % |
>
> The Q4 forecast runs about 6.5 % high (a level offset); refitting weekly would fix this.
>
> **ROI** (posterior mean, 90 % credible interval):
>
> | Channel | ROI | Note |
> | --- | --- | --- |
> | SEA | 8.4 [6.6–10.9] | Upper bound: cost follows demand; SEA absorbs about 55 % of sales |
> | PSM | 2.5 [0.3–8.3] | |
> | Display Network | 1.2 [0.2–3.4] | |
> | Catalog | 1.0 [0.9–1.1] | Nearly linear response; skipping one send-out saves 1.50 M€ of cost and loses 1.43 M€ of sales |
> | Social Media | 0.7 [0.2–1.6] | |
> | Display | 0.7 [0.2–1.5] | Saturated (marginal ROI 0.23) |
>
> **Budget optimiser:** +11 % incremental sales at the same budget, by moving money from Display, Social and Display Network into SEA. This needs an SEA experiment before acting on it.
>
> **Production note:** Meridian only predicts inside the training time window. Future days are forecast by appending them, with their planned budget, as hold-out rows and refitting (a few minutes).

**Model specification:**

| Setting | Value | Reason |
| --- | --- | --- |
| Geography | National (single geo) | No geo column in the data. The builder treats this as a national model |
| Time step | Daily, 732 days | The brief asks for daily demand. Meridian accepts any regular interval |
| `max_lag` | 21 | The catalog effect fades over about 3 weeks |
| `adstock_decay_spec` | `{"Catalog": "binomial"}`, geometric for the rest | Gives the catalog a flatter, longer tail |
| Saturation | Hill (default) | Diminishing returns |
| `knots` | 1 (national default), with the trend and seasonality controls | The baseline can then extend into the hold-out. Automatic knot selection is tested only if no knots fall inside the hold-out |
| Prior | `media_prior_type="roi"`, `roi_m = LogNormal(0.2, 0.9)` (notebook default) | Followed by a sensitivity check. Attributed ROAS is not used as a prior, because it is not incremental |
| `holdout` | `HoldoutSpec([DateRange("2024-10-01", "2025-01-01")])` | Time-based hold-out, the same window as the OLS benchmark |

**Code sketch** (API checked against the installed version 2.1.0):

```python
data = (data_frame_input_data_builder.DataFrameInputDataBuilder(
            kpi_type="revenue", default_kpi_column="total_sales")
        .with_kpi(df)
        .with_controls(df, control_cols=CONTROLS)
        .with_media(df, media_cols=SPEND_COLS, media_spend_cols=SPEND_COLS, media_channels=CHANNELS)
        .build())
model_spec = spec.ModelSpec(max_lag=21, adstock_decay_spec={"Catalog": "binomial"},
                            holdout=spec.HoldoutSpec(spec=[spec.DateRange("2024-10-01", "2025-01-01")]))
mmm = model.Meridian(input_data=data, model_spec=model_spec)
mmm.sample_prior(500)
mmm.sample_posterior(n_chains=4, n_adapt=1000, n_burnin=500, n_keep=1000, seed=0)
```

**Iteration loop:**

- Use fast sampling settings while iterating on the catalog shift k, the controls and the priors: 2 chains, 300 adaptation, 200 burn-in and 200 kept draws.
- Use the full settings above for the final model only.

**Validation:**

- **Convergence:**
  - `reviewer.ModelReviewer(mmm).run()` produces the health card.
  - R-hat should be below 1.2 (`visualizer.ModelDiagnostics.plot_rhat_boxplot`).
  - Compare prior and posterior distributions.
- **Hold-out accuracy:**
  - `Analyzer.predictive_accuracy()` gives R², MAPE and **wMAPE (= MAE / mean)** for the train and test sets.
  - MAE and RMSE are computed from `expected_vs_actual_data()`.
- **Robustness:** refit with a second hold-out window (2024-07-01 → 2024-09-30). Each fit takes minutes, so this replaces full walk-forward cross-validation.
- **Fit diagnostics:**
  - `visualizer.ModelFit.plot_model_fit()`.
  - Residuals by weekday and month, and for the days after a catalog send-out.
- **Benchmark:** compare against OLS 3 (9.1 % on the Q4 hold-out). The smoke test already reached 9.0 %. If a later version does clearly worse, check the catalog shift, the controls and the knots.
- **Plausibility of the ROIs:**
  - Fix Affiliate's ROI using a prior or by treating it as a control.
  - Tune the catalog's timing.
  - Run a prior sensitivity check (default vs informative priors).
  - Plausible ROIs matter more for the slides than the last 0.5 % of wMAPE.

**Business outputs** (from the [getting-started notebook](https://developers.google.com/meridian/notebook/meridian-getting-started)):

1. `Summarizer(mmm).output_model_results_summary(...)`: contribution per channel, ROI and marginal ROI with credible intervals, and response curves.
2. Adstock decay curves and Hill saturation curves per channel.
3. `optimizer.BudgetOptimizer(mmm).optimize()`: re-allocates a fixed budget and shows the expected uplift, e.g. "move X € from Social to SEA".
4. **Short-term forecast:** `Analyzer.expected_outcome(new_data=DataTensors(media=planned_spend, ...))` predicts daily sales for a planned budget. This is the brief's use case.
5. `meridian_serde.save_meridian(mmm, "outputs/mmm.binpb")`.

**Fallback:** if the daily model does not converge or sampling is too slow, aggregate to weekly data (104 full weeks) with `max_lag = 4`. Daily forecasts are then produced by splitting each weekly prediction across days using weekday shares.

### Phase 3c: Forecasting new budget plans (`05_forecast.ipynb`, `src/.../forecast.py`)

> **Status: done.**
>
> **Input:** a plan CSV with the daily cost per paid channel. The template is `data/input/budget_plan_template.csv`, and an example plan for 2 Jan – 12 Feb 2025 is in `data/input/budget_plan_2025-01.csv`.
>
> **Command:** `uv run tchibo-forecast --plan … --out …`.
>
> **How it works:** the plan days are appended to the history as Meridian hold-out rows, and the model is refit (about 2–3 min). The output is a daily forecast with 90 % prediction intervals.
>
> **Leak-free back-test (Q4 2024):** MAE / mean 8.5 %. The prediction interval covers 80 % of days; it falls short of 90 % because of the level offset.
>
> **Example plan:** €8.0 M of spend gives a forecast of €42.6 M in sales.
>
> **Scenarios** (evaluated in seconds, without a refit):
>
> | Scenario | Effect on sales | Effect on cost |
> | --- | ---: | ---: |
> | Skip the 30 Jan catalog | −€1.24 M | −€1.30 M |
> | Move 20 % of Display to SEA | +€1.27 M | none |
> | +10 % on all digital channels | +€1.18 M | +€0.50 M |

### Phase 4: Bonus, Google Cloud ML lifecycle (1–2 slides)

**Reference architecture (Vertex AI MLOps):**

- The flow runs from data to features to training to evaluation to registry to serving to monitoring, and back to retraining.
- Three automation levels:
  - **Level 0:** manual, notebook-based.
  - **Level 1:** an automated, continuously-training pipeline.
  - **Level 2:** CI/CD for the pipelines themselves.

**Mapping to this use case:**

| Lifecycle step | Google Cloud service |
| --- | --- |
| Data | BigQuery (sales and costs), Cloud Storage, Dataflow / Dataform for transformations |
| Features | Vertex AI Feature Store (features in BigQuery) |
| Experimentation | Vertex AI Workbench or Colab Enterprise (Meridian runs there on a GPU); Vertex AI Experiments |
| Training pipeline | Vertex AI Pipelines (Kubeflow / TFX), Custom Training running the Meridian fit |
| Model management | Vertex AI Model Registry (the saved Meridian model) and Model Evaluation |
| Serving | Batch Prediction for daily forecasts; Endpoints for what-if budget scenarios |
| Monitoring | Vertex AI Model Monitoring (drift, and wMAPE tracked against the 15 % threshold), Cloud Monitoring and Logging |
| Orchestration and CI/CD | Cloud Composer (Airflow), Cloud Scheduler, Cloud Build, Artifact Registry |
| Consumption | Meridian Scenario Planner, Looker / Looker Studio dashboards for marketing |

**Other ML services to mention:**

- **BigQuery ML:** `LINEAR_REG`, `ARIMA_PLUS_XREG` and `BOOSTED_TREE`, plus `AI.FORECAST` with the TimesFM foundation model.
- **Vertex AI Forecasting (AutoML):** tabular forecasting.
- **Gemini on Vertex AI:** for generating insight narratives.
- **Vertex AI Vizier:** hyper-parameter tuning.
- **Dataproc:** Spark processing.

---

## 6. Presentation outline (about 12–15 slides)

1. Title and the question being answered.
2. Summary: the Meridian MMM, the result vs the 15 % target, and three takeaways.
3. The data at a glance: channels, cost coverage, quality issues.
4. EDA: trend, weekly and yearly patterns, holidays.
5. Correlation matrix and lagged correlations, showing the catalog's delayed effect and the halo.
6. Answers to the Phase 1 questions: obstacles and unusable factors.
7. Data preparation: the wide table and the Meridian input roles (KPI, media, controls).
8. Cost handling and features: adstock, Hill saturation, catalog in-home shift, calendar controls.
9. The model: Meridian as a regression, with the equation, priors and validation design.
10. Results: wMAPE (MAE / mean), RMSE and R² on the hold-out vs the 15 % target, actual-vs-forecast for Q4 2024, and the OLS benchmark.
11. Channel importance: contribution, ROI and marginal ROI with credible intervals.
12. Response curves and budget optimisation: what to do with the next euro.
13. Limitations: reverse causality, daily noise, two years of data, missing promotion and price data.
14. Bonus: Google Cloud MLOps reference architecture and services.
15. Next steps: geo-level data, lift tests to calibrate priors, promotion and price controls, a production pipeline.

Charts are exported to `reports/figures/` at 1920 px width with a consistent palette.

---

## 7. Order of work

| Step | Output | Effort |
| --- | --- | --- |
| 1 ✅ | `src/data.py` and notebook 01: EDA and correlation answers | 0.5 day |
| 2 ✅ | `src/features.py` and notebook 02: wide table and Meridian input table | 0.5 day |
| 3 ✅ | `src/mmm.py`, `evaluate.py` and notebook 03: Meridian fit, catalog-shift iterations, hold-out metrics, diagnostics, ROI, optimiser | 1–1.5 days |
| 4 ✅ | Notebook 04: OLS benchmark | 0.25 day |
| 5 ✅ | Figures and slide deck, including the Google Cloud bonus (20 slides: https://claude.ai/artifact/4A4sQ9gDQDfZeGfdfquYFC) | 0.5–1 day |

---

## 8. Risks and open questions

- **Daily MMM is unusual.** Most MMMs run on weekly data. Daily data is noisier, and sampling takes longer. The fallback is a weekly model with weekday shares, as described in Phase 3.
- **The catalog's delayed peak is not native to Meridian's adstock.** It is handled by the in-home shift, which must be validated on the hold-out.
- **Identification:** a national model with about two years of data has weakly identified channel effects. Priors and sensitivity checks matter, and lift tests would help in practice.
- **Knots and a time-based hold-out:** knots inside the hold-out see no training data. This is why trend and seasonality are modelled as controls.
- **Reverse causality:** SEA and Affiliate costs follow demand, so their ROI is likely overstated. This must be called out on the slides.
- **Forecast horizon:** is "short-term assessment" meant as next-day, next-week or month-ahead? The model needs planned budgets for that horizon, especially for SEA and Affiliate, whose costs depend on performance.
- **Attribution model:** how is `sales_by_channel` attributed (last-click?). This affects how the per-channel ROAS should be read.
- **Missing drivers:** promotions and pricing, Tchibo's weekly product "worlds", store traffic, and TV spend are not in the data. They are the most likely sources of the remaining error.
