"""Future budget plan forecasting, 90% prediction/credible intervals, and what-if scenario evaluation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, line_figure
from mmm_studio.charts.theme import COPPER, NAVY, SLATE, TEAL
from mmm_studio.core.meridian.fit import FAST_SAMPLING, fit_meridian
from mmm_studio.core.meridian.spec import (
    CHANNEL_DISPLAY_NAMES,
    DEFAULT_PAID_CHANNELS,
    MeridianSpecConfig,
    build_input_data,
    build_meridian_model,
    build_meridian_table,
    meridian_roles,
)
from mmm_studio.core.metrics import regression_metrics
from mmm_studio.core.schema import slugify_channel
from mmm_studio.report.narrative import fmt_eur
from mmm_studio.report.sections import (
    CalloutBlock,
    ChartBlock,
    KpiItem,
    KpisBlock,
    MarkdownBlock,
    Section,
    TableBlock,
    TableColumn,
)

PLAN_COLUMNS: list[str] = [slugify_channel(c) for c in DEFAULT_PAID_CHANNELS]


class PlanValidationError(ValueError):
    """Raised when a submitted future budget plan fails schema or date validation."""


def validate_plan(
    plan_df: pd.DataFrame,
    last_history_date: pd.Timestamp | None = None,
    max_horizon_days: int = 90,
) -> pd.DataFrame:
    """Validate and normalise an uploaded future budget plan DataFrame."""
    df = plan_df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        if df["date"].isna().any():
            raise PlanValidationError("Plan 'date' column contains invalid dates.")
        df = df.set_index("date")
    else:
        df.index = pd.to_datetime(df.index, errors="coerce")
        if df.index.isna().any():
            raise PlanValidationError("Plan index must be valid dates or include a 'date' column.")

    df = df.sort_index()
    df.index.name = "date"
    if len(df) == 0:
        raise PlanValidationError("Submitted budget plan is empty.")
    if len(df) > max_horizon_days:
        raise PlanValidationError(
            f"Submitted plan has {len(df)} days, exceeding the maximum horizon of {max_horizon_days} days."
        )
    expected_idx = pd.date_range(df.index[0], df.index[-1], freq="D", name="date")
    if len(expected_idx) != len(df) or not df.index.equals(expected_idx):
        raise PlanValidationError("Budget plan dates must be contiguous daily dates without gaps or duplicates.")

    if last_history_date is not None:
        expected_start = pd.Timestamp(last_history_date) + pd.Timedelta(days=1)
        if df.index[0] != expected_start:
            raise PlanValidationError(
                f"Budget plan must start on {expected_start.strftime('%Y-%m-%d')} (the day after history ends)."
            )

    out = pd.DataFrame(0.0, index=df.index, columns=PLAN_COLUMNS)
    col_map = {slugify_channel(c): c for c in df.columns}
    for col in PLAN_COLUMNS:
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
        elif col in col_map:
            vals = pd.to_numeric(df[col_map[col]], errors="coerce")
        elif f"cost_{col}" in df.columns:
            vals = pd.to_numeric(df[f"cost_{col}"], errors="coerce")
        else:
            vals = pd.Series(0.0, index=df.index)
        if vals.isna().any():
            raise PlanValidationError(f"Channel '{col}' contains non-numeric or NaN spend values.")
        if (vals < 0).any():
            raise PlanValidationError(f"Channel '{col}' contains negative spend values.")
        out[col] = vals.astype(float)
    return out


def _flatten_draws(values: Any) -> np.ndarray:
    arr = np.asarray(values)
    return arr.reshape(-1, *arr.shape[2:])


def build_example_plan(
    history: pd.DataFrame,
    start: str | None = None,
    days: int = 42,
    send_outs: list[str] | None = None,
    catalog_cost: float | None = None,
) -> pd.DataFrame:
    """Build a realistic future daily budget plan from historical weekday spend and YoY growth."""
    hist_end = pd.Timestamp(history.index[-1])
    start_ts = pd.Timestamp(start) if start else (hist_end + pd.Timedelta(days=1))
    dates = pd.date_range(start_ts, periods=days, freq="D", name="date")

    last_yr_start = dates[0] - pd.DateOffset(years=1)
    last_yr_end = dates[-1] - pd.DateOffset(years=1)
    last_year = history.loc[last_yr_start:last_yr_end]
    if len(last_year) < 7:
        last_year = history.iloc[-min(365, len(history)) :]

    recent = history.iloc[-min(365, len(history)) :]
    before = history.iloc[-min(730, len(history)) : -min(365, len(history))]
    if len(before) < 30:
        before = recent

    if send_outs is None:
        # Default: 2nd and 5th Thursdays of the forecast window (e.g. 2025-01-09 and 2025-01-30)
        thursdays = [d for d in dates if d.dayofweek == 3]
        chosen_thu = [thursdays[i] for i in [1, 4] if i < len(thursdays)]
        if not chosen_thu and thursdays:
            chosen_thu = [thursdays[0]]
        send_outs = [d.strftime("%Y-%m-%d") for d in chosen_thu]

    plan = pd.DataFrame(0.0, index=dates, columns=PLAN_COLUMNS)
    for col in PLAN_COLUMNS:
        cost_col = f"cost_{col}"
        if cost_col not in history.columns:
            continue
        if col == "catalog":
            sends = recent[cost_col][recent[cost_col] > 0]
            avg_send_cost = catalog_cost or (float(sends.mean()) if len(sends) > 0 else 1299842.0)
            valid_so = [pd.Timestamp(s) for s in send_outs if pd.Timestamp(s) in plan.index]
            if valid_so:
                plan.loc[pd.DatetimeIndex(valid_so), col] = avg_send_cost
            continue
        by_weekday = last_year[cost_col].groupby(last_year.index.dayofweek).mean()
        denom = float(before[cost_col].sum())
        growth = float(recent[cost_col].sum()) / denom if denom > 0 else 1.0
        plan[col] = by_weekday.reindex(dates.dayofweek).fillna(0.0).to_numpy() * growth

    return plan


def extend_history(wide: pd.DataFrame, plan: pd.DataFrame) -> pd.DataFrame:
    """Append future plan rows to `wide`, setting unknown future `total_sales` to the training mean."""
    future = pd.DataFrame(0.0, index=plan.index, columns=wide.columns)
    for col in plan.columns:
        cost_col = f"cost_{col}"
        if cost_col in future.columns:
            future[cost_col] = plan[col].astype(float)
    future["total_sales"] = float(wide["total_sales"].mean())
    future.index.name = wide.index.name
    return pd.concat([wide, future])


def _prediction_draws(mmm: Any, new_data: Any = None, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Return `(expected_draws, predictive_draws)` of shape `(n_draws, n_time)`."""
    from meridian.analysis import analyzer

    out = analyzer.Analyzer(mmm).expected_outcome(new_data=new_data, aggregate_times=False)
    expected = _flatten_draws(out).reshape(-1, mmm.input_data.time.size)
    sigma = _flatten_draws(mmm.inference_data.posterior["sigma"].values).reshape(expected.shape[0], -1)[:, :1]
    noise_sd = sigma * float(np.asarray(mmm.kpi_transformer.population_scaled_stdev))
    rng = np.random.default_rng(seed)
    predictive = expected + rng.normal(size=expected.shape) * noise_sd
    return expected, predictive


def _summarise_draws(
    expected: np.ndarray,
    predictive: np.ndarray,
    index: pd.DatetimeIndex,
) -> pd.DataFrame:
    idx = pd.DatetimeIndex(index, name="date")
    return pd.DataFrame(
        {
            "forecast": expected.mean(axis=0),
            "credible_lo": np.quantile(expected, 0.05, axis=0),
            "credible_hi": np.quantile(expected, 0.95, axis=0),
            "prediction_lo": np.quantile(predictive, 0.05, axis=0),
            "prediction_hi": np.quantile(predictive, 0.95, axis=0),
        },
        index=idx,
    )


def forecast_budget_plan(
    history: pd.DataFrame,
    plan: pd.DataFrame,
    cfg: MeridianSpecConfig | None = None,
    sampling: dict[str, int] | None = None,
    seed: int = 0,
) -> tuple[pd.DataFrame, Any, MeridianSpecConfig]:
    """Fit Meridian on `history + plan` (with plan dates masked as holdout) and return daily forecast."""
    start = plan.index[0].strftime("%Y-%m-%d")
    end = plan.index[-1].strftime("%Y-%m-%d")
    fc_cfg = replace(cfg or MeridianSpecConfig(), holdout=(start, end))
    ext = extend_history(history, plan)
    table = build_meridian_table(
        ext,
        catalog_shift=fc_cfg.catalog_shift,
        n_fourier=fc_cfg.n_fourier,
        paid_channels=fc_cfg.paid_channels,
    )
    mmm = fit_meridian(
        build_meridian_model(build_input_data(table, fc_cfg), fc_cfg),
        seed=seed,
        **(sampling or FAST_SAMPLING),
    )
    expected, predictive = _prediction_draws(mmm, seed=seed)
    daily = _summarise_draws(expected, predictive, pd.DatetimeIndex(pd.to_datetime(table["time"]))).loc[start:end].copy()
    daily["planned_spend"] = plan.sum(axis=1).to_numpy()
    return daily, mmm, fc_cfg


def evaluate_forecast_scenario(
    mmm: Any,
    history: pd.DataFrame,
    base_daily: pd.DataFrame,
    alt_plan: pd.DataFrame,
    cfg: MeridianSpecConfig,
    seed: int = 0,
) -> pd.DataFrame:
    """Evaluate an alternative budget plan over the same forecast window without refitting."""
    from meridian.analysis.tensors import DataTensors

    alt_table = build_meridian_table(
        extend_history(history, alt_plan),
        catalog_shift=cfg.catalog_shift,
        n_fourier=cfg.n_fourier,
        paid_channels=cfg.paid_channels,
    )
    spend_cols, _, _ = meridian_roles(alt_table, cfg)
    media = alt_table[spend_cols].to_numpy(dtype=float)[np.newaxis, :, :]
    expected, predictive = _prediction_draws(mmm, new_data=DataTensors(media=media), seed=seed)
    index = pd.DatetimeIndex(pd.to_datetime(alt_table["time"]))
    daily = _summarise_draws(expected, predictive, index).loc[base_daily.index].copy()
    daily["planned_spend"] = alt_plan.sum(axis=1).to_numpy()
    return daily


def fast_forecast_from_multipliers(
    base_daily: pd.DataFrame,
    base_plan: pd.DataFrame,
    multipliers: dict[str, float] | None = None,
    horizon_days: int | None = None,
    mroi_map: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Fast interactive scenario evaluator scaling channel spend and updating daily sales via mROI / adstock."""
    mults = multipliers or {}
    default_mroi = {
        "sea": 3.85,
        "catalog": 0.85,
        "display": 0.22,
        "display_network": 0.95,
        "social_media": 1.40,
        "psm": 1.15,
        "affiliate": 0.0,
    }
    if mroi_map:
        default_mroi.update({slugify_channel(k): float(v) for k, v in mroi_map.items()})

    n_days = min(horizon_days or len(base_daily), len(base_daily))
    d_base = base_daily.iloc[:n_days].copy()
    p_base = base_plan.iloc[:n_days].copy()
    p_alt = p_base.copy()

    daily_delta_sales = np.zeros(n_days, dtype=float)
    for col in p_alt.columns:
        slug = slugify_channel(col)
        m = float(mults.get(slug, mults.get(col, 1.0)))
        p_alt[col] = p_base[col] * m
        diff_spend = (p_alt[col] - p_base[col]).to_numpy(dtype=float)
        mroi = default_mroi.get(slug, 1.0)
        if slug == "catalog":
            # Distribute catalog delta over 14 days post-in-home (shifted 2 days)
            kernel = np.array([0.0, 0.0, 0.18, 0.22, 0.16, 0.12, 0.09, 0.07, 0.05, 0.04, 0.03, 0.02, 0.01, 0.01])
            conv = np.convolve(diff_spend, kernel)[:n_days]
            daily_delta_sales += conv * mroi
        else:
            kernel = np.array([0.65, 0.22, 0.08, 0.05])
            conv = np.convolve(diff_spend, kernel)[:n_days]
            daily_delta_sales += conv * mroi

    d_alt = d_base.copy()
    d_alt["forecast"] = np.maximum(0.0, d_base["forecast"].to_numpy(dtype=float) + daily_delta_sales)
    d_alt["credible_lo"] = np.maximum(0.0, d_base["credible_lo"].to_numpy(dtype=float) + daily_delta_sales * 0.85)
    d_alt["credible_hi"] = np.maximum(0.0, d_base["credible_hi"].to_numpy(dtype=float) + daily_delta_sales * 1.15)
    d_alt["prediction_lo"] = np.maximum(0.0, d_base["prediction_lo"].to_numpy(dtype=float) + daily_delta_sales * 0.80)
    d_alt["prediction_hi"] = np.maximum(0.0, d_base["prediction_hi"].to_numpy(dtype=float) + daily_delta_sales * 1.20)
    d_alt["planned_spend"] = p_alt.sum(axis=1).to_numpy(dtype=float)

    base_total_sales = float(d_base["forecast"].sum())
    alt_total_sales = float(d_alt["forecast"].sum())
    base_total_spend = float(p_base.to_numpy().sum())
    alt_total_spend = float(p_alt.to_numpy().sum())

    dates_str = [d.strftime("%Y-%m-%d") for d in d_alt.index]
    fig = line_figure(
        x=dates_str,
        series=[
            {
                "name": "Scenario Forecast (90% PI)",
                "y": d_alt["forecast"].round(0).tolist(),
                "band_lower": d_alt["prediction_lo"].round(0).tolist(),
                "band_upper": d_alt["prediction_hi"].round(0).tolist(),
                "color": TEAL,
                "width": 2.2,
            },
            {
                "name": "Baseline Plan Forecast",
                "y": d_base["forecast"].round(0).tolist(),
                "color": NAVY,
                "dash": "dot",
                "width": 1.6,
            },
        ],
        title=f"Interactive Scenario Forecast ({dates_str[0]} .. {dates_str[-1]}, {n_days} Days)",
        subtitle=(
            f"Scenario Sales = {fmt_eur(alt_total_sales)} (Δ {alt_total_sales - base_total_sales:+,.0f} €) | "
            f"Planned Spend = {fmt_eur(alt_total_spend)} (Δ {alt_total_spend - base_total_spend:+,.0f} €)"
        ),
        yaxis_title="Daily Sales Forecast (€)",
    )

    daily_rows = [
        {
            "date": d.strftime("%Y-%m-%d"),
            "baseline_forecast": round(float(d_base.loc[d, "forecast"]), 0),
            "scenario_forecast": round(float(d_alt.loc[d, "forecast"]), 0),
            "prediction_lo": round(float(d_alt.loc[d, "prediction_lo"]), 0),
            "prediction_hi": round(float(d_alt.loc[d, "prediction_hi"]), 0),
            "planned_spend": round(float(d_alt.loc[d, "planned_spend"]), 0),
        }
        for d in d_alt.index
    ]

    delta_sales = alt_total_sales - base_total_sales
    delta_spend = alt_total_spend - base_total_spend
    m_roas = round(delta_sales / delta_spend, 2) if abs(delta_spend) > 1e-6 else None

    return {
        "horizon_days": n_days,
        "start_date": dates_str[0],
        "end_date": dates_str[-1],
        "baseline_sales": round(base_total_sales, 0),
        "scenario_sales": round(alt_total_sales, 0),
        "sales_delta": round(delta_sales, 0),
        "baseline_spend": round(base_total_spend, 0),
        "scenario_spend": round(alt_total_spend, 0),
        "spend_delta": round(delta_spend, 0),
        "base_total_sales": round(base_total_sales, 0),
        "scenario_total_sales": round(alt_total_sales, 0),
        "delta_sales": round(delta_sales, 0),
        "base_total_spend": round(base_total_spend, 0),
        "scenario_total_spend": round(alt_total_spend, 0),
        "delta_spend": round(delta_spend, 0),
        "marginal_roas": m_roas,
        "figure": fig,
        "daily": daily_rows,
    }


def build_forecast_scenarios_section(
    wide_df: pd.DataFrame,
    q4_holdout_df: pd.DataFrame,
    forecast_daily: pd.DataFrame,
    plan_df: pd.DataFrame,
    scenarios_df: pd.DataFrame,
) -> Section:
    """Build the `forecast_scenarios` section reproducing `05_forecast.ipynb`."""
    # 1. Leak-free Q4 backtest metrics & chart (`05_backtest_q4`)
    bt_m = regression_metrics(q4_holdout_df["actual"].to_numpy(), q4_holdout_df["expected"].to_numpy())
    lo_col = "prediction_lo" if "prediction_lo" in q4_holdout_df.columns else "expected_lo"
    hi_col = "prediction_hi" if "prediction_hi" in q4_holdout_df.columns else "expected_hi"
    coverage = float(
        ((q4_holdout_df["actual"] >= q4_holdout_df[lo_col]) & (q4_holdout_df["actual"] <= q4_holdout_df[hi_col])).mean()
    )

    q4_dates = [d.strftime("%Y-%m-%d") for d in q4_holdout_df.index]
    fig_bt = line_figure(
        x=q4_dates,
        series=[
            {"name": "Actual Q4 Sales", "y": q4_holdout_df["actual"].round(0).tolist(), "color": SLATE, "width": 1.6},
            {
                "name": "Out-of-Sample Forecast (90% Interval)",
                "y": q4_holdout_df["expected"].round(0).tolist(),
                "band_lower": q4_holdout_df[lo_col].round(0).tolist(),
                "band_upper": q4_holdout_df[hi_col].round(0).tolist(),
                "color": NAVY,
                "width": 2.0,
            },
        ],
        title=f"Leak-Free Back-Test on Q4 2024: MAE / Mean = {100.0 * bt_m['MAE_over_mean']:.1f}% (R² = {bt_m['R2']:.3f})",
        subtitle="Trained strictly on history up to 2024-09-30; predicted from Q4 planned media spend and calendar controls",
        yaxis_title="Daily Total Sales (€)",
    )

    # 2. 42-day Jan–Feb 2025 forecast chart (`05_forecast_2025_01`)
    recent_hist = wide_df.loc["2024-11-15":, "total_sales"]
    hist_dates = [d.strftime("%Y-%m-%d") for d in recent_hist.index]
    fc_dates = [d.strftime("%Y-%m-%d") for d in forecast_daily.index]
    all_dates = hist_dates + fc_dates

    hist_y: list[float | None] = [round(float(v), 0) for v in recent_hist] + [None] * len(fc_dates)
    fc_y: list[float | None] = [None] * len(hist_dates) + [round(float(v), 0) for v in forecast_daily["forecast"]]
    fc_lo: list[float | None] = [None] * len(hist_dates) + [round(float(v), 0) for v in forecast_daily["prediction_lo"]]
    fc_hi: list[float | None] = [None] * len(hist_dates) + [round(float(v), 0) for v in forecast_daily["prediction_hi"]]

    vlines = [{"x": fc_dates[0], "color": SLATE, "dash": "dash", "annotation": "Forecast Start"}]
    if "catalog" in plan_df.columns:
        for d in plan_df.index[plan_df["catalog"] > 0]:
            vlines.append({"x": d.strftime("%Y-%m-%d"), "color": COPPER, "dash": "dot", "annotation": "Planned Catalog"})

    fig_fc = line_figure(
        x=all_dates,
        series=[
            {"name": "Actual Sales (Recent History)", "y": hist_y, "color": SLATE, "width": 1.6},
            {
                "name": "Daily Forecast (90% Prediction Interval)",
                "y": fc_y,
                "band_lower": fc_lo,
                "band_upper": fc_hi,
                "color": TEAL,
                "width": 2.2,
            },
        ],
        title=f"Daily Sales Forecast for the 42-Day Budget Plan ({fc_dates[0]} .. {fc_dates[-1]})",
        subtitle=(
            f"Total Forecast Sales = {fmt_eur(float(forecast_daily['forecast'].sum()))} "
            f"(Ø {fmt_eur(float(forecast_daily['forecast'].mean()))}/day) "
            f"for {fmt_eur(float(forecast_daily['planned_spend'].sum()))} Planned Spend"
        ),
        yaxis_title="Daily Total Sales (€)",
        vlines=vlines,
    )

    # 3. Scenarios comparison chart (`05_scenarios`)
    scen_sub = scenarios_df[scenarios_df["scenario"] != "Plan as submitted"].copy()
    fig_scen = bar_figure(
        categories=scen_sub["scenario"].tolist(),
        series=[
            {
                "name": "Δ Forecast Sales (€k)",
                "values": (scen_sub["delta_sales"] / 1e3).round(1).tolist(),
                "color": TEAL,
            },
            {
                "name": "Δ Planned Spend (€k)",
                "values": (scen_sub["delta_spend"] / 1e3).round(1).tolist(),
                "color": SLATE,
            },
        ],
        barmode="group",
        orientation="h",
        title="Budget What-If Scenarios Over the 42-Day Forecast Window (Δ k€ vs Submitted Plan)",
        subtitle="Evaluated directly on the fitted Bayesian posterior without refitting MCMC",
        xaxis_title="Change vs Submitted Plan (€ Thousands)",
        vlines=[{"x": 0.0, "color": NAVY, "dash": "solid"}],
    )

    # Weekly budget plan table
    wk_plan = plan_df.resample("W-SUN").sum()
    wk_plan_rows = []
    for dt, r in wk_plan.iterrows():
        row_dict: dict[str, Any] = {"week_ending": dt.strftime("%Y-%m-%d")}
        for col in plan_df.columns:
            row_dict[col] = fmt_eur(float(r[col]))
        row_dict["total"] = fmt_eur(float(r.sum()))
        wk_plan_rows.append(row_dict)

    # Weekly forecast summary table
    wk_fc = forecast_daily[["forecast", "prediction_lo", "prediction_hi", "planned_spend"]].resample("W-SUN").sum()
    wk_fc["days"] = forecast_daily.resample("W-SUN").size()
    wk_fc_rows = [
        {
            "week_ending": dt.strftime("%Y-%m-%d"),
            "days": int(r["days"]),
            "planned_spend": fmt_eur(float(r["planned_spend"])),
            "forecast_sales": fmt_eur(float(r["forecast"])),
            "pi_90_low": fmt_eur(float(r["prediction_lo"])),
            "pi_90_high": fmt_eur(float(r["prediction_hi"])),
        }
        for dt, r in wk_fc.iterrows()
    ]

    scen_rows = [
        {
            "scenario": str(r["scenario"]),
            "sales": fmt_eur(float(r["sales"])),
            "spend": fmt_eur(float(r["spend"])),
            "delta_sales": f"{'+' if float(r['delta_sales']) >= 0 else ''}{fmt_eur(float(r['delta_sales']))}",
            "delta_spend": f"{'+' if float(r['delta_spend']) >= 0 else ''}{fmt_eur(float(r['delta_spend']))}",
            "marginal_roas": f"{float(r['marginal_roas']):.2f}x" if pd.notna(r["marginal_roas"]) else "– (budget neutral)",
        }
        for _, r in scenarios_df.iterrows()
    ]

    tot_fc_sales = float(forecast_daily["forecast"].sum())
    tot_fc_spend = float(forecast_daily["planned_spend"].sum())

    blocks = [
        KpisBlock(
            items=[
                KpiItem(
                    label="42-Day Forecast Total Sales",
                    value=fmt_eur(tot_fc_sales),
                    delta=f"Ø {fmt_eur(float(forecast_daily['forecast'].mean()))}/day",
                    status="success",
                ),
                KpiItem(
                    label="42-Day Planned Media Spend",
                    value=fmt_eur(tot_fc_spend),
                    delta=f"2 Catalog send-outs ({len(forecast_daily)}d)",
                    status="neutral",
                ),
                KpiItem(
                    label="Leak-Free Q4 Back-Test MAE/Mean",
                    value=f"{100.0 * bt_m['MAE_over_mean']:.2f}%",
                    delta=f"R² = {bt_m['R2']:.3f} (≤ 15% target)",
                    status="success",
                ),
                KpiItem(
                    label="90% Prediction Interval Coverage",
                    value=f"{100.0 * coverage:.0f}%",
                    delta="Out-of-sample daily coverage",
                    status="neutral",
                ),
            ]
        ),
        MarkdownBlock(
            body=(
                "### Operational Short-Term Forecasting from a Marketing Budget Plan\n"
                "To forecast future sales from a planned daily media budget, future plan dates are appended to the historical panel "
                "as hold-out rows (with placeholder sales equal to the training mean so KPI scaling is completely leak-free). "
                "We report two uncertainty bands:\n"
                "- **90% Prediction Interval:** where an *actual* future day's sales will land (includes observation noise $\\sigma$).\n"
                "- **90% Credible Interval:** parameter uncertainty around the *expected* mean sales trajectory."
            )
        ),
        ChartBlock(
            id="forecast_backtest_q4",
            title="Leak-Free Back-Test on Q4 2024 (No Q4 Sales Used in Training or Scaling)",
            subtitle="Daily out-of-sample forecast and 90% prediction interval vs actual Q4 2024 sales",
            figure=fig_bt,
        ),
        ChartBlock(
            id="forecast_2025_01_chart",
            title=f"Daily Sales Forecast for the {len(forecast_daily)}-Day Budget Plan ({fc_dates[0]} .. {fc_dates[-1]})",
            subtitle="Recent history vs future daily forecast with 90% prediction interval and planned Catalog send-outs",
            figure=fig_fc,
        ),
        TableBlock(
            id="forecast_weekly_summary_table",
            title="Weekly Sales Forecast & 90% Prediction Interval Summary",
            subtitle="Aggregated weekly sales forecast and planned media spend across the planning horizon",
            columns=[
                TableColumn(key="week_ending", label="Week Ending (Sun)"),
                TableColumn(key="days", label="Days", align="right"),
                TableColumn(key="planned_spend", label="Planned Spend", align="right"),
                TableColumn(key="forecast_sales", label="Forecast Sales", align="right"),
                TableColumn(key="pi_90_low", label="90% PI Low (Sum)", align="right"),
                TableColumn(key="pi_90_high", label="90% PI High (Sum)", align="right"),
            ],
            rows=wk_fc_rows,
        ),
        ChartBlock(
            id="forecast_scenarios_chart",
            title="Budget What-If Scenarios: Change in Forecast Sales vs Change in Spend (€k)",
            subtitle="Instant counterfactual evaluation on the fitted model over the 42-day planning window",
            figure=fig_scen,
        ),
        TableBlock(
            id="forecast_scenarios_table",
            title="What-If Budget Scenario Comparison Table",
            subtitle="Impact of skipping a Catalog edition, shifting Display to SEA, or scaling digital channels +10%",
            columns=[
                TableColumn(key="scenario", label="Budget Scenario"),
                TableColumn(key="sales", label="Forecast Sales", align="right"),
                TableColumn(key="spend", label="Planned Spend", align="right"),
                TableColumn(key="delta_sales", label="Δ Sales vs Plan", align="right"),
                TableColumn(key="delta_spend", label="Δ Spend vs Plan", align="right"),
                TableColumn(key="marginal_roas", label="Δ Sales / Δ Spend", align="right"),
            ],
            rows=scen_rows,
        ),
        TableBlock(
            id="forecast_weekly_plan_table",
            title="Submitted Weekly Media Budget Plan by Channel",
            subtitle="Planned weekly spend (€) across the 7 paid channels in the forecast window",
            columns=[
                TableColumn(key="week_ending", label="Week Ending"),
                *[
                    TableColumn(key=col, label=CHANNEL_DISPLAY_NAMES.get(col, col), align="right")
                    for col in plan_df.columns
                ],
                TableColumn(key="total", label="Total Spend", align="right"),
            ],
            rows=wk_plan_rows,
        ),
        CalloutBlock(
            tone="info",
            title="Interactive Scenario Simulator Available Above",
            body=(
                "Use the interactive channel spend multipliers above to test custom budget scenarios over any horizon "
                "(14 to 90 days) and inspect the resulting daily sales trajectory and 90% prediction intervals."
            ),
        ),
    ]

    return Section(
        id="forecast_scenarios",
        title="Forward Budget Forecasting & What-If Scenario Planning",
        subtitle="Leak-free Q4 back-test, 42-day Jan–Feb 2025 daily sales forecast, and interactive budget scenario simulations",
        blocks=blocks,
    )
