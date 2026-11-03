# Remediation status

Date: 2026-11-03. Every finding of `BUG_REPORT.md` is fixed on top of commit `ace6bb0` (uncommitted working tree).
"Test" names the regression test (`backend/tests/...`, `frontend/src/...`) or the check that verified the fix.

## Two findings in the Gemini report that were not real

| Claim | Reality |
|---|---|
| `extend_history` re-computes `trend = np.arange(len)/len`, rescaling the historical trend | The trend is `(index - index[0]).days / 365.25` (`core/features.py:62`); it does not depend on the panel length, and `extend_history` does not recompute features |
| Scenario prediction draws are shifted by a constant mean (`forecast.py:321-323`) | Scenario draws were already draw-aligned (`_prediction_draws` with `new_data`); that code does not exist |

## P0 / P1

| ID | Fix | Test |
|---|---|---|
| SEC-01 | Every route except `/health` and `/auth/*` needs the session cookie (HttpOnly, SameSite=Strict) or `X-Access-Code`; SPA sign-in screen; production refuses to start without a ≥ 16-character code; `deploy.sh` always configures one | `test_api.py::test_access_code_guard_rate_limiter_and_meridian_queue_cap` |
| STAT-01 | Executive summary verdict and OLS comparison computed from both hold-outs and the plan-time back-test | `test_audit_remediations.py::test_executive_summary_follows_the_numbers` |
| STAT-02 | `holdout_range()` derives the hold-out from the data and raises below 365 training days; no Q4-2024 fallback; no training metrics as hold-out metrics; API validates before starting | `test_holdout_always_comes_from_the_data_and_never_falls_back`, `test_run_config_errors_explain_what_cannot_run` |
| STAT-03 | Optimiser raises `OptimisationInfeasible` / `OptimisationError` (infeasible budget, bounds beyond the curves, solver failure, bound violations); fixed channels stay fixed; API returns 422 with the feasible range | `test_infeasible_budgets_raise_instead_of_relaxing_bounds`, `test_fixed_catalog_stays_fixed_and_every_allocation_respects_its_bounds`, `test_optimiser_requests_are_validated` |
| STAT-04 | Plan-time back-test: cost controls (commission) replaced by a history-only estimate (last 56 days' weekday run-rate × last year's seasonal ratio), evaluated on the back-test model; the verdict counts it as a window that must meet the 15 % target; validation text corrected | `test_plan_time_controls_replace_the_commission_with_history_only_values`, end-to-end run |
| STAT-05 | Demand-following channels detected (r(cost, clicks) ≥ 0.9: SEA, PSM) and editable; optimiser headline holds them fixed (+1.3 % instead of +11.1 % on the quick run), unconstrained lift and its upper-bound share shown, lift decomposition table | `test_lift_share_attributes_the_lift_to_channels`, Meridian check on run `run_1da14ea751` |
| STAT-06 | ROI table adds P(ROI < 1), prior interval and an "identified by data" flag (posterior/prior 90 % interval width on the log scale ≥ 0.7 = mostly prior); callout lists prior-dominated channels | Meridian check on run `run_1da14ea751` (PSM, Display Network, Display flagged; SEA and Catalog data-driven) |
| DATA-01 | Conflicting (date, channel) duplicates → 422; exact duplicates dropped with a warning | `test_conflicting_duplicates_are_rejected_and_exact_duplicates_removed` |
| DATA-02 | Plans need every paid-channel column and no unknown/duplicate ones | `test_plans_with_unknown_or_missing_channel_columns_are_rejected`, `test_api.py::test_plans_with_wrong_columns_are_rejected` |
| API-01 | `PATCH` changes settings of drafts only (409 otherwise); `start` takes the full settings and validates/checks capacity before changing anything; one config source for every job | `test_api.py::test_finished_runs_keep_their_settings_and_reruns_are_atomic` |

## P2

| ID | Fix | Test |
|---|---|---|
| STAT-07 | Selection grid and sensitivity fits trained before and scored on the validation window (quarter before the hold-out) | end-to-end run (selection subtitle) |
| STAT-08 | Attributed-ROAS priors recomputed from each fit's training days (`with_training_priors`) | `test_attributed_roas_priors_use_training_days_only` |
| STAT-09 | No PCHIP extrapolation; bounds beyond 2× rejected (API max 2.0); `res.success` checked | `test_bounds_beyond_the_response_curves_are_refused` |
| STAT-11 | Forecast band labelled with its back-test coverage; text no longer recommends it as a 90 % band without that caveat | `format.test.ts`, forecast section |
| TXT-02 | Draft brief says "pending (Meridian fit)"; channel importance from the HAC OLS (p < 0.05) | `test_draft_brief_has_no_invented_roi_and_computed_channel_importance` |
| DATA-03 | `missing_row_controls` (default on): a 0/1 control per channel with missing rows in Meridian and OLS-2 (only when it flags a training day); off = notebook parity | `test_missing_row_controls_only_for_channels_with_gaps_in_training`, `test_model_settings.py` |
| DATA-04 | Negative costs → 422; negative sales → warning | `test_negative_costs_are_rejected_and_negative_sales_warned` |
| API-02 | Re-run deletes forecasts, scenarios, optimisations and their files | `test_finished_runs_keep_their_settings_and_reruns_are_atomic` |
| API-03 | Typed `(min, max)` multiplier bounds ≤ 2, slug/display-name normalisation, media channels only | `test_optimiser_requests_are_validated` |
| API-04 | Leftover queued job → 409 before anything changes; defer failure → run failed + 503 | `test_finished_runs_keep_their_settings_and_reruns_are_atomic` |
| API-05 | Meridian job deferred by the analysis job when it finishes (works inside the worker); `s11` never overwrites final summaries | end-to-end run (chained job) |
| UI-01 | Budget typed in € million, comma or dot, preview of the parsed amount, anything else rejected | `budget.test.ts` |
| PERF-01 | Upload and plan handlers are sync (thread pool), bounded reads | `test_api.py` |
| PERF-02 | Queue queries through the pooled engine, batched queue positions, sweep at most every 30 s | `test_api.py`, `test_jobs.py` |
| PERF-03 | Every fit through `fit_meridian_isolated` (child, cancel, timeout); temp dirs cleaned | end-to-end run |
| PERF-04 | Wake a worker only when no execution of its job is starting or running; sweep wakes queues with waiting jobs | code review (needs Cloud Run) |
| SEC-02 | Failed attempts limited per client (trusted-proxy-aware) and globally, on every guarded route; bounded memory | `test_access_code_guard_rate_limiter_and_meridian_queue_cap` |

## P3

| ID | Fix |
|---|---|
| MER-01 | Custom ROI priors for non-media channels → 422 / `ValueError` |
| MER-02 | Per-run channel names in a context variable (`set_channel_names` / `channel_label`), never merged across runs |
| MER-03 | The commission role is honoured as chosen; choosing "paid" for a commission channel says why its ROI is biased |
| MER-04 | Print events inferred by name only; sparse spend gets a note instead |
| STAT-10 | OLS-2 uses the run's in-home shift and missing-row controls |
| STAT-12 | Totals' intervals from the draws (`prediction_total_lo/hi`); sums of daily bounds renamed; KPI-scaling text corrected |
| STAT-13 | Selection grid flips only commission channels |
| TXT-01 | Benchmark takeaways conditional ("x points less accurate than OLS") |
| DATA-05 | Minimum 379 days at upload, 365 training days at start; dead config fields removed |
| API-06 | GET routes never write (example forecast registered by the Meridian job; `mmm-studio migrate` backfills) |
| API-07 | Stuck scenarios/optimisations swept; queue counts fail closed (503) |
| UI-02 | `fmt_eur` / `eurCompact` boundaries and `—` for non-finite values; no "nan×" KPIs |
| UI-03 | "Media share of modelled sales"; optimiser notes Meridian's spend rounding |
| SEC-03 | Zip members streamed and counted (Gemini's fix kept) |
| SEC-04 | `public_error()` strips paths from every stored or returned error |
| SEC-05 | `uv` image pinned, react-router 7.18, Vite 7, vitest 4 (0 npm advisories), DB password sent on stdin, CSV formula escaping |

## Architectural risks

| ID | Status |
|---|---|
| AR-01 | Regime breaks detected from the data; channel-specific narratives only when the channel exists; year labels from the data |
| AR-02 | Narratives derive verdicts from numbers (see STAT-01, TXT-01, TXT-02) |
| AR-03 | Settings frozen after start; jobs read the database config |
| AR-04 | One fitting path (`fit_meridian_isolated`) |
| AR-05 | Alembic with a baseline revision; drift test `tests/test_migrations.py` |
| AR-06 | Shared access code with sessions; per-user identity left to Identity-Aware Proxy (documented) |
| AR-07 | Frontend unit tests (vitest) and nightly slow Meridian tests in CI |
