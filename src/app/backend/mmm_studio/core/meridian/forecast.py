"""Future budget plan forecasting, 90% prediction/credible intervals, and what-if scenario evaluation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, line_figure
from mmm_studio.charts.theme import COPPER, INK_MUTED, NAVY, SLATE, TEAL
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
from mmm_studio.report.narrative import fmt_eur, fmt_eur_signed
from mmm_studio.report.sections import (
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
    paid_channels: list[str] | None = None,
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

    target_cols = [slugify_channel(c) for c in paid_channels] if paid_channels else PLAN_COLUMNS
    out = pd.DataFrame(0.0, index=df.index, columns=target_cols)
    col_map = {slugify_channel(c): c for c in df.columns}
    for col in target_cols:
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
    paid_channels: list[str] | None = None,
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

    target_cols = [slugify_channel(c) for c in paid_channels] if paid_channels else PLAN_COLUMNS
    plan = pd.DataFrame(0.0, index=dates, columns=target_cols)
    for col in target_cols:
        cost_col = f"cost_{col}"
        if cost_col not in history.columns:
            continue
        if col == "catalog":
            sends = recent[cost_col][recent[cost_col] > 0]
            avg_send_cost = catalog_cost or (float(sends.mean()) if len(sends) > 0 else 0.0)
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


def apply_multipliers(
    plan: pd.DataFrame,
    multipliers: dict[str, float] | None,
    locked: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Scale each plan column by its multiplier; `locked` columns (controls such as Affiliate) stay as planned."""
    alt = plan.copy()
    for col in alt.columns:
        if col in locked:
            continue
        alt[col] = alt[col] * float((multipliers or {}).get(col, 1.0))
    return alt


def scenario_payload(
    base_daily: pd.DataFrame,
    base_plan: pd.DataFrame,
    alt_daily: pd.DataFrame,
    alt_plan: pd.DataFrame,
    method: str,
    multipliers: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Result payload shared by forecasts and scenarios (totals, deltas, per-channel summary, daily rows, figure)."""
    n_days = len(alt_daily)
    base_daily = base_daily.iloc[:n_days]
    base_plan = base_plan.iloc[:n_days]
    alt_plan = alt_plan.iloc[:n_days]
    base_sales = float(base_daily["forecast"].sum())
    alt_sales = float(alt_daily["forecast"].sum())
    base_spend = float(base_plan.to_numpy().sum())
    alt_spend = float(alt_plan.to_numpy().sum())
    delta_sales = alt_sales - base_sales
    delta_spend = alt_spend - base_spend
    m_roas = round(delta_sales / delta_spend, 2) if abs(delta_spend) > 1.0 else None
    dates = [d.strftime("%Y-%m-%d") for d in alt_daily.index]

    channel_summary = []
    for col in alt_plan.columns:
        b_sp = float(base_plan[col].sum())
        a_sp = float(alt_plan[col].sum())
        channel_summary.append(
            {
                "channel": CHANNEL_DISPLAY_NAMES.get(col, col),
                "slug": col,
                "multiplier": round(a_sp / b_sp, 3) if b_sp > 0 else float((multipliers or {}).get(col, 1.0)),
                "base_spend": round(b_sp, 0),
                "scenario_spend": round(a_sp, 0),
                "delta_spend": round(a_sp - b_sp, 0),
            }
        )

    same = alt_daily["forecast"].round(0).equals(base_daily["forecast"].round(0))
    series = [
        {
            "name": "Forecast (90% prediction interval)" if same else "Scenario (90% prediction interval)",
            "y": alt_daily["forecast"].round(0).tolist(),
            "band_lower": alt_daily["prediction_lo"].round(0).tolist(),
            "band_upper": alt_daily["prediction_hi"].round(0).tolist(),
            "color": TEAL,
            "width": 2.2,
        }
    ]
    if not same:
        series.append({"name": "Plan as submitted", "y": base_daily["forecast"].round(0).tolist(), "color": NAVY, "dash": "dot", "width": 1.6})
    fig = line_figure(x=dates, series=series, yaxis_title="Daily sales (€)")

    daily_rows = [
        {
            "date": d.strftime("%Y-%m-%d"),
            "baseline_forecast": round(float(base_daily.loc[d, "forecast"]), 0),
            "scenario_forecast": round(float(alt_daily.loc[d, "forecast"]), 0),
            "prediction_lo": round(float(alt_daily.loc[d, "prediction_lo"]), 0),
            "prediction_hi": round(float(alt_daily.loc[d, "prediction_hi"]), 0),
            "planned_spend": round(float(alt_plan.loc[d].sum()), 0),
        }
        for d in alt_daily.index
    ]
    return {
        "method": method,
        "horizon_days": n_days,
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "base_total_sales": round(base_sales, 0),
        "scenario_total_sales": round(alt_sales, 0),
        "delta_sales": round(delta_sales, 0),
        "delta_sales_pct": round(100.0 * delta_sales / base_sales, 2) if base_sales else 0.0,
        "base_total_spend": round(base_spend, 0),
        "scenario_total_spend": round(alt_spend, 0),
        "delta_spend": round(delta_spend, 0),
        "marginal_roas": m_roas,
        "prediction_lo_total": round(float(alt_daily["prediction_lo"].sum()), 0),
        "prediction_hi_total": round(float(alt_daily["prediction_hi"].sum()), 0),
        "channel_summary": channel_summary,
        "figure": fig,
        "daily": daily_rows,
    }


def model_scenario_from_multipliers(
    mmm: Any,
    history: pd.DataFrame,
    base_daily: pd.DataFrame,
    base_plan: pd.DataFrame,
    cfg: MeridianSpecConfig,
    multipliers: dict[str, float] | None = None,
    horizon_days: int | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    """Evaluate a multiplier scenario with the fitted forecast model (no refit), as in notebook 05 §4."""
    alt_plan = apply_multipliers(base_plan, multipliers, locked=tuple(cfg.as_controls))
    alt_daily = evaluate_forecast_scenario(mmm, history, base_daily, alt_plan, cfg, seed=seed)
    n = min(horizon_days or len(base_daily), len(base_daily))
    return scenario_payload(
        base_daily.iloc[:n], base_plan.iloc[:n], alt_daily.iloc[:n], alt_plan.iloc[:n], "Meridian posterior (fitted model, no refit)", multipliers
    )


def fast_forecast_from_multipliers(
    base_daily: pd.DataFrame,
    base_plan: pd.DataFrame,
    multipliers: dict[str, float] | None = None,
    horizon_days: int | None = None,
    mroi_map: dict[str, float] | None = None,
    catalog_shift: int = 2,
) -> dict[str, Any]:
    """Approximate a scenario from the run's marginal ROIs when the fitted forecast model is not available.

    Each channel's spend change is spread over a short carry-over kernel (catalog: after its in-home date) and
    multiplied by that channel's posterior marginal ROI. Channels without an mROI (e.g. controls) do not move sales.
    """
    mults = {slugify_channel(k): float(v) for k, v in (multipliers or {}).items()}
    mroi = {slugify_channel(k): float(v) for k, v in (mroi_map or {}).items()}
    n_days = min(horizon_days or len(base_daily), len(base_daily))
    d_base = base_daily.iloc[:n_days].copy()
    p_base = base_plan.iloc[:n_days].copy()
    p_alt = p_base.copy()
    delta = np.zeros(n_days, dtype=float)
    for col in p_alt.columns:
        slug = slugify_channel(col)
        if slug not in mroi:
            continue
        p_alt[col] = p_base[col] * mults.get(slug, 1.0)
        diff = (p_alt[col] - p_base[col]).to_numpy(dtype=float)
        if slug == "catalog":
            kernel = np.r_[np.zeros(catalog_shift), 0.8 ** np.arange(14)]
        else:
            kernel = 0.5 ** np.arange(4)
        kernel = kernel / kernel.sum()
        delta += np.convolve(diff, kernel)[:n_days] * mroi[slug]
    d_alt = d_base.copy()
    for col in ("forecast", "credible_lo", "credible_hi", "prediction_lo", "prediction_hi"):
        d_alt[col] = np.maximum(0.0, d_base[col].to_numpy(dtype=float) + delta)
    d_alt["planned_spend"] = p_alt.sum(axis=1).to_numpy(dtype=float)
    return scenario_payload(d_base, p_base, d_alt, p_alt, "Marginal-ROI approximation (fitted model not available)", multipliers)


def build_forecast_scenarios_section(
    wide_df: pd.DataFrame,
    backtest_df: pd.DataFrame,
    forecast_daily: pd.DataFrame,
    plan_df: pd.DataFrame,
    scenarios_df: pd.DataFrame,
    backtest_label: str = "hold-out",
    backtest_train_end: str | None = None,
    sampling_label: str | None = None,
) -> Section:
    """Build the `forecast_scenarios` section reproducing `05_forecast.ipynb`.

    `backtest_df` holds `actual`, `forecast` and the 90 % prediction interval of a leak-free back-test: a model fitted
    only on history up to `backtest_train_end` and given the hold-out's actual spend as its plan.
    """
    bt_m = regression_metrics(backtest_df["actual"].to_numpy(), backtest_df["forecast"].to_numpy())
    coverage = float(
        ((backtest_df["actual"] >= backtest_df["prediction_lo"]) & (backtest_df["actual"] <= backtest_df["prediction_hi"])).mean()
    )
    bt_dates = [d.strftime("%Y-%m-%d") for d in backtest_df.index]
    fig_bt = line_figure(
        x=bt_dates,
        series=[
            {
                "name": "Forecast (90% prediction interval)",
                "y": backtest_df["forecast"].round(0).tolist(),
                "band_lower": backtest_df["prediction_lo"].round(0).tolist(),
                "band_upper": backtest_df["prediction_hi"].round(0).tolist(),
                "color": TEAL,
                "width": 2.0,
            },
            {"name": "Actual", "y": backtest_df["actual"].round(0).tolist(), "color": NAVY, "width": 1.4, "mode": "lines+markers", "marker": {"size": 4}},
        ],
        yaxis_title="Daily total sales (€)",
    )

    recent_hist = wide_df["total_sales"].iloc[-min(48, len(wide_df)) :]
    hist_dates = [d.strftime("%Y-%m-%d") for d in recent_hist.index]
    fc_dates = [d.strftime("%Y-%m-%d") for d in forecast_daily.index]
    all_dates = hist_dates + fc_dates
    pad = [None] * len(hist_dates)
    vlines = [{"x": fc_dates[0], "color": SLATE, "dash": "dash", "label": "history | forecast"}]
    if "catalog" in plan_df.columns:
        for d in plan_df.index[plan_df["catalog"] > 0]:
            vlines.append({"x": d.strftime("%Y-%m-%d"), "color": COPPER, "dash": "dot", "label": "Catalog"})
    fig_fc = line_figure(
        x=all_dates,
        series=[
            {"name": "Actual (history)", "y": [round(float(v), 0) for v in recent_hist] + [None] * len(fc_dates), "color": NAVY, "width": 1.6},
            {
                "name": "Forecast (90% prediction interval)",
                "y": pad + [round(float(v), 0) for v in forecast_daily["forecast"]],
                "band_lower": pad + [round(float(v), 0) for v in forecast_daily["prediction_lo"]],
                "band_upper": pad + [round(float(v), 0) for v in forecast_daily["prediction_hi"]],
                "color": TEAL,
                "width": 2.2,
            },
        ],
        yaxis_title="Daily total sales (€)",
        vlines=vlines,
    )

    scen_sub = scenarios_df.iloc[1:].copy()
    fig_scen = bar_figure(
        categories=scen_sub["scenario"].tolist(),
        series=[
            {"name": "Δ forecast sales (k€)", "values": (scen_sub["delta_sales"] / 1e3).round(1).tolist(), "color": TEAL},
            {"name": "Δ planned spend (k€)", "values": (scen_sub["delta_spend"] / 1e3).round(1).tolist(), "color": INK_MUTED},
        ],
        barmode="group",
        orientation="h",
        xaxis_title="Change vs plan as submitted (k€)",
        vlines=[{"x": 0.0, "color": NAVY, "dash": "solid"}],
        height=320,
    )

    wk_plan = plan_df.resample("W-SUN").sum()
    wk_plan_rows = []
    for dt, r in wk_plan.iterrows():
        row_dict: dict[str, Any] = {"week_ending": dt.strftime("%Y-%m-%d")}
        for col in plan_df.columns:
            row_dict[col] = fmt_eur(float(r[col]))
        row_dict["total"] = fmt_eur(float(r.sum()))
        wk_plan_rows.append(row_dict)

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
            "delta_sales": fmt_eur_signed(float(r["delta_sales"])),
            "delta_spend": fmt_eur_signed(float(r["delta_spend"])),
            "marginal_roas": f"{float(r['marginal_roas']):.2f}" if pd.notna(r["marginal_roas"]) else "–",
        }
        for _, r in scenarios_df.iterrows()
    ]

    tot_fc_sales = float(forecast_daily["forecast"].sum())
    tot_fc_spend = float(forecast_daily["planned_spend"].sum())
    n_sends = int((plan_df["catalog"] > 0).sum()) if "catalog" in plan_df.columns else 0
    blocks: list[Any] = [
        KpisBlock(
            items=[
                KpiItem(
                    label=f"Forecast sales, {len(forecast_daily)} days",
                    value=fmt_eur(tot_fc_sales),
                    delta=f"Ø {fmt_eur(float(forecast_daily['forecast'].mean()))}/day",
                    status="success",
                ),
                KpiItem(
                    label="Planned spend",
                    value=fmt_eur(tot_fc_spend),
                    delta=f"{n_sends} catalog send-out{'s' if n_sends != 1 else ''} · {fc_dates[0]} → {fc_dates[-1]}",
                ),
                KpiItem(
                    label=f"Back-test {backtest_label} MAE / mean",
                    value=f"{100.0 * bt_m['MAE_over_mean']:.1f}%",
                    delta=f"R² {bt_m['R2']:.3f} · bias {fmt_eur_signed(bt_m['bias'])}/day",
                    status="success" if bt_m["MAE_over_mean"] <= 0.15 else "warn",
                ),
                KpiItem(
                    label="Days inside 90% prediction interval",
                    value=f"{100.0 * coverage:.0f}%",
                    delta="Nominal 90 %",
                    status="success" if coverage >= 0.85 else "warn",
                ),
            ]
        ),
        MarkdownBlock(
            title="How the forecast works",
            body=(
                "Meridian only predicts inside the window it is fitted on, so the plan's days are appended to the history as "
                "**hold-out rows** (their sales are unknown) and the model is refit"
                + (f" ({sampling_label})" if sampling_label else "")
                + ". The rows' sales placeholder is the training mean, so Meridian's KPI scaling is unchanged and no future "
                "sales are used.\n\n"
                "- **90 % prediction interval:** where an *actual* day's sales are expected to land — use this for planning.\n"
                "- **90 % credible interval:** uncertainty about the *expected* level only (much narrower)."
            ),
        ),
        ChartBlock(
            id="forecast_backtest_q4",
            title=f"Leak-free back-test, {backtest_label}: MAE / mean = {100.0 * bt_m['MAE_over_mean']:.1f}%",
            subtitle=(
                f"Trained only on history up to {backtest_train_end or 'the day before the window'}; the plan is the actual spend of the "
                "window; no sales from the window are used, not even in the KPI scaling."
            ),
            figure=fig_bt,
            caption=(
                f"Coverage below the nominal 90 % comes from the level offset (bias {fmt_eur_signed(bt_m['bias'])}/day); "
                "refitting weekly re-anchors the level."
                if coverage < 0.85
                else None
            ),
        ),
        ChartBlock(
            id="forecast_2025_01_chart",
            title=f"Daily sales forecast for the budget plan ({fc_dates[0]} → {fc_dates[-1]})",
            subtitle=(
                f"{fmt_eur(tot_fc_sales)} forecast sales (Ø {fmt_eur(float(forecast_daily['forecast'].mean()))}/day) for "
                f"{fmt_eur(tot_fc_spend)} planned spend; dotted lines are planned catalog send-outs."
            ),
            figure=fig_fc,
        ),
        TableBlock(
            id="forecast_weekly_summary_table",
            title="Weekly forecast",
            subtitle="Low/high add up the daily 5 % / 95 % bounds, so they are deliberately conservative.",
            columns=[
                TableColumn(key="week_ending", label="Week ending (Sun)"),
                TableColumn(key="days", label="Days", align="right"),
                TableColumn(key="planned_spend", label="Planned spend", align="right"),
                TableColumn(key="forecast_sales", label="Forecast sales", align="right"),
                TableColumn(key="pi_90_low", label="Low (sum of daily 5 %)", align="right"),
                TableColumn(key="pi_90_high", label="High (sum of daily 95 %)", align="right"),
            ],
            rows=wk_fc_rows,
        ),
        ChartBlock(
            id="forecast_scenarios_chart",
            title="Budget scenarios on the same forecast window",
            subtitle="Evaluated with the fitted model (no refit); only media channels change, Affiliate stays as planned.",
            figure=fig_scen,
        ),
        TableBlock(
            id="forecast_scenarios_table",
            title="Scenario comparison",
            columns=[
                TableColumn(key="scenario", label="Scenario"),
                TableColumn(key="sales", label="Forecast sales", align="right"),
                TableColumn(key="spend", label="Planned spend", align="right"),
                TableColumn(key="delta_sales", label="Δ sales", align="right"),
                TableColumn(key="delta_spend", label="Δ spend", align="right"),
                TableColumn(key="marginal_roas", label="Δ sales / Δ spend", align="right"),
            ],
            rows=scen_rows,
            caption=(
                "Skipping a catalog saves its cost but loses roughly the same in sales (catalog ROI ≈ 1). Moving Display "
                "budget to SEA rides on SEA's upward-biased ROI and should be tested before acting on it."
            ),
        ),
        TableBlock(
            id="forecast_weekly_plan_table",
            title="Budget plan by week and channel",
            subtitle="Example plan built from history: same-weekday spend one year earlier × each channel's spend growth.",
            columns=[
                TableColumn(key="week_ending", label="Week ending"),
                *[TableColumn(key=col, label=CHANNEL_DISPLAY_NAMES.get(col, col), align="right") for col in plan_df.columns],
                TableColumn(key="total", label="Total", align="right"),
            ],
            rows=wk_plan_rows,
        ),
    ]
    return Section(
        id="forecast_scenarios",
        title="Forecast & scenarios",
        subtitle="Leak-free back-test, the daily forecast for a budget plan, and budget what-if scenarios.",
        blocks=blocks,
    )
