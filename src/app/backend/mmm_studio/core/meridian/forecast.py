"""Future budget plan forecasting, 90% prediction/credible intervals, and what-if scenario evaluation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, line_figure
from mmm_studio.charts.theme import COPPER, INK_MUTED, NAVY, SLATE, TEAL
from mmm_studio.core.meridian.fit import FAST_SAMPLING, fit_meridian_isolated
from mmm_studio.core.meridian.spec import (
    DEFAULT_PAID_CHANNELS,
    MeridianSpecConfig,
    channel_label,
    meridian_roles,
    meridian_table,
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

    target_cols = [slugify_channel(c) for c in paid_channels] if paid_channels else list(PLAN_COLUMNS)
    target_set = set(target_cols)
    col_map: dict[str, str] = {}
    unknown_cols: list[str] = []
    for raw_col in df.columns:
        slug = slugify_channel(str(raw_col))
        base = slug[5:] if slug.startswith("cost_") and slug[5:] in target_set else slug
        if base in target_set:
            if base in col_map:
                raise PlanValidationError(f"Columns '{col_map[base]}' and '{raw_col}' both describe channel '{base}'.")
            col_map[base] = str(raw_col)
        else:
            unknown_cols.append(str(raw_col))

    if unknown_cols:
        raise PlanValidationError(
            f"Unknown or misspelled channel column(s) {unknown_cols} in budget plan. Expected: {target_cols}."
        )

    # Every channel needs a column: a missing one would silently be forecast at €0 (write explicit zeros instead)
    missing_cols = [c for c in target_cols if c not in col_map]
    if missing_cols:
        raise PlanValidationError(
            f"Missing channel column(s) {missing_cols} in budget plan; add them (0 for no spend). Expected: {target_cols}."
        )

    out = pd.DataFrame(0.0, index=df.index, columns=target_cols)
    for col in target_cols:
        if col in col_map:
            vals = pd.to_numeric(df[col_map[col]], errors="coerce")
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


def _weekday_profile(history: pd.DataFrame, dates: pd.DatetimeIndex, cost_col: str) -> np.ndarray:
    """Daily cost for `dates` from history alone: last year's same-window weekday means × the year-on-year growth."""
    last_yr_start = dates[0] - pd.DateOffset(years=1)
    last_yr_end = dates[-1] - pd.DateOffset(years=1)
    last_year = history.loc[last_yr_start:last_yr_end]
    if len(last_year) < 7:
        last_year = history.iloc[-min(365, len(history)) :]
    recent = history.iloc[-min(365, len(history)) :]
    before = history.iloc[-min(730, len(history)) : -min(365, len(history))]
    if len(before) < 30:
        before = recent
    by_weekday = last_year[cost_col].groupby(last_year.index.dayofweek).mean()
    denom = float(before[cost_col].sum())
    growth = float(recent[cost_col].sum()) / denom if denom > 0 else 1.0
    return by_weekday.reindex(dates.dayofweek).fillna(0.0).to_numpy() * growth


def build_example_plan(
    history: pd.DataFrame,
    start: str | None = None,
    days: int = 42,
    send_outs: list[str] | None = None,
    catalog_cost: float | None = None,
    paid_channels: list[str] | None = None,
    print_slugs: tuple[str, ...] = ("catalog",),
) -> pd.DataFrame:
    """Build a realistic future daily budget plan from historical weekday spend and YoY growth."""
    hist_end = pd.Timestamp(history.index[-1])
    start_ts = pd.Timestamp(start) if start else (hist_end + pd.Timedelta(days=1))
    dates = pd.date_range(start_ts, periods=days, freq="D", name="date")
    recent = history.iloc[-min(365, len(history)) :]

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
        if col in print_slugs:
            sends = recent[cost_col][recent[cost_col] > 0]
            avg_send_cost = catalog_cost or (float(sends.mean()) if len(sends) > 0 else 0.0)
            valid_so = [pd.Timestamp(s) for s in send_outs if pd.Timestamp(s) in plan.index]
            if valid_so:
                plan.loc[pd.DatetimeIndex(valid_so), col] = avg_send_cost
            continue
        plan[col] = _weekday_profile(history, dates, cost_col)

    return plan


def _seasonal_run_rate(history: pd.DataFrame, dates: pd.DatetimeIndex, cost_col: str, recent_days: int = 56) -> np.ndarray:
    """Daily cost for `dates` as a planner would estimate it: the last `recent_days` days' weekday means (the current
    run-rate, so a recent rate change is included) × last year's seasonal ratio (same window vs the same recent days)."""
    recent = history.iloc[-recent_days:]
    base = recent[cost_col].groupby(recent.index.dayofweek).mean().reindex(dates.dayofweek).fillna(0.0).to_numpy()
    last_window = history.loc[dates[0] - pd.DateOffset(years=1) : dates[-1] - pd.DateOffset(years=1), cost_col]
    last_recent = history.loc[recent.index[0] - pd.DateOffset(years=1) : recent.index[-1] - pd.DateOffset(years=1), cost_col]
    ratio = 1.0
    if len(last_window) >= 7 and len(last_recent) >= 7 and float(last_recent.mean()) > 0:
        ratio = float(last_window.mean()) / float(last_recent.mean())
    return base * ratio


def plan_time_controls(history: pd.DataFrame, plan: pd.DataFrame, cfg: MeridianSpecConfig) -> pd.DataFrame:
    """`plan` with each cost control (e.g. a sales commission) replaced by what was knowable before the plan window.

    A realised commission is paid on realised sales, so feeding it into a back-test leaks the outcome. The plan-time
    value is the recent run-rate × last year's seasonal ratio, from `history` only.
    """
    out = plan.copy()
    dates = pd.DatetimeIndex(plan.index)
    for slug in cfg.as_controls:
        if slug in out.columns and f"cost_{slug}" in history.columns:
            out[slug] = _seasonal_run_rate(history, dates, f"cost_{slug}")
    return out


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


def total_interval(predictive: np.ndarray, index: pd.DatetimeIndex, days: pd.DatetimeIndex) -> tuple[float, float]:
    """90 % prediction interval of the *total* over `days`: quantiles of each draw's sum (not a sum of daily quantiles)."""
    mask = pd.DatetimeIndex(index).isin(days)
    totals = predictive[:, mask].sum(axis=1)
    return float(np.quantile(totals, 0.05)), float(np.quantile(totals, 0.95))


@dataclass
class PlanForecast:
    """A refit forecast of a budget plan: the daily table, the fitted model, its spec and the plan total's interval."""

    daily: pd.DataFrame
    mmm: Any
    cfg: MeridianSpecConfig
    total_lo: float
    total_hi: float


def forecast_budget_plan(
    history: pd.DataFrame,
    plan: pd.DataFrame,
    cfg: MeridianSpecConfig,
    sampling: dict[str, int] | None = None,
    seed: int = 0,
    *,
    work_path: Path | None = None,
    spawn_child: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    on_heartbeat: Callable[[], None] | None = None,
    timeout_seconds: int | None = None,
) -> PlanForecast:
    """Fit Meridian on `history + plan` (with plan dates masked as holdout) and return the daily forecast.

    The fit goes through `fit_meridian_isolated`, so it can run in a spawned child with cancellation and a timeout.
    """
    import tempfile

    start = plan.index[0].strftime("%Y-%m-%d")
    end = plan.index[-1].strftime("%Y-%m-%d")
    ext = extend_history(history, plan)
    fc_cfg = replace(cfg, holdout=(start, end)).with_training_priors(ext)
    with tempfile.TemporaryDirectory(prefix="mmm_fc_") as tmp:
        mmm = fit_meridian_isolated(
            ext,
            fc_cfg,
            work_path or Path(tmp) / "meridian_forecast.binpb",
            sampling=sampling or FAST_SAMPLING,
            seed=seed,
            spawn_child=spawn_child,
            should_cancel=should_cancel,
            on_heartbeat=on_heartbeat,
            **({"timeout_seconds": timeout_seconds} if timeout_seconds else {}),
        )
    expected, predictive = _prediction_draws(mmm, seed=seed)
    index = pd.DatetimeIndex(pd.to_datetime(meridian_table(ext, fc_cfg)["time"]))
    daily = _summarise_draws(expected, predictive, index).loc[start:end].copy()
    daily["planned_spend"] = plan.sum(axis=1).to_numpy()
    lo, hi = total_interval(predictive, index, pd.DatetimeIndex(plan.index))
    return PlanForecast(daily=daily, mmm=mmm, cfg=fc_cfg, total_lo=lo, total_hi=hi)


def evaluate_forecast_scenario(
    mmm: Any,
    history: pd.DataFrame,
    base_daily: pd.DataFrame,
    alt_plan: pd.DataFrame,
    cfg: MeridianSpecConfig,
    seed: int = 0,
    *,
    with_controls: bool = False,
    horizon_days: int | None = None,
) -> tuple[pd.DataFrame, tuple[float, float]]:
    """Evaluate an alternative budget plan over the same forecast window without refitting.

    Returns `(daily, (total_lo, total_hi))`, the total's 90 % prediction interval over the first `horizon_days` days.
    With `with_controls` the plan's control columns (e.g. a commission) are passed to the model too; otherwise only
    the media change and the fitted model's controls are kept.
    """
    from meridian.analysis.tensors import DataTensors

    alt_table = meridian_table(extend_history(history, alt_plan), cfg)
    spend_cols, _, control_cols = meridian_roles(alt_table, cfg)
    media = alt_table[spend_cols].to_numpy(dtype=float)[np.newaxis, :, :]
    tensors = {"media": media}
    if with_controls and control_cols:
        tensors["controls"] = alt_table[control_cols].to_numpy(dtype=float)[np.newaxis, :, :]
    expected, predictive = _prediction_draws(mmm, new_data=DataTensors(**tensors), seed=seed)
    index = pd.DatetimeIndex(pd.to_datetime(alt_table["time"]))
    daily = _summarise_draws(expected, predictive, index).loc[base_daily.index].copy()
    daily["planned_spend"] = alt_plan.sum(axis=1).to_numpy()
    n = min(horizon_days or len(base_daily), len(base_daily))
    return daily, total_interval(predictive, index, pd.DatetimeIndex(base_daily.index[:n]))


def channel_effect_draws(
    mmm: Any,
    history: pd.DataFrame,
    plan: pd.DataFrame,
    cfg: MeridianSpecConfig,
) -> tuple[np.ndarray, list[str]]:
    """Posterior draws of each media channel's incremental sales on the plan's days for `plan`.

    Returns `(draws, channels)` with draws of shape `(n_draws, n_plan_days, n_media_channels)`. Meridian's media
    effects are additive, so the difference of these draws between two plans is each channel's own share of the
    change in forecast sales, and their sum over channels equals the change in the expected outcome.
    """
    from meridian.analysis import analyzer
    from meridian.analysis.tensors import DataTensors

    table = meridian_table(extend_history(history, plan), cfg)
    spend_cols, media_channels, _ = meridian_roles(table, cfg)
    media = table[spend_cols].to_numpy(dtype=float)[np.newaxis, :, :]
    inc = analyzer.Analyzer(mmm).incremental_outcome(
        new_data=DataTensors(media=media),
        selected_times=[d.strftime("%Y-%m-%d") for d in plan.index],
        aggregate_times=False,
        include_non_paid_channels=False,
    )
    return _flatten_draws(inc), media_channels


def plan_arrays(plan: pd.DataFrame) -> dict[str, list[float]]:
    """Daily spend per plan column, rounded to whole euros, for the planner chart."""
    return {str(col): [round(float(v), 0) for v in plan[col]] for col in plan.columns}


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
    effect_draws: np.ndarray | None = None,
    effect_channels: list[str] | None = None,
    total_pi: tuple[float, float] | None = None,
    coverage: float | None = None,
) -> dict[str, Any]:
    """Result payload shared by forecasts and scenarios (totals, deltas, per-channel summary, daily rows, figure).

    `total_pi` is the 90 % prediction interval of the total over the shown days, from the draws.
    `coverage` is the share of back-test days inside their daily 90 % interval, shown next to the band.

    `effect_draws` holds each media channel's change in daily sales, shape `(n_draws, n_days, n_channels)` in the
    order of `effect_channels`. With more than one draw the payload carries 90 % credible intervals; a single draw
    (the marginal-ROI approximation) gives point estimates only.
    """
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

    effects: dict[str, np.ndarray] = {}
    total_ci: tuple[float, float] | None = None
    if effect_draws is not None and effect_channels:
        draws = np.asarray(effect_draws, dtype=float)[:, :n_days, :]
        effects = {str(ch): draws[:, :, i] for i, ch in enumerate(effect_channels)}
        if draws.shape[0] > 1:
            totals = draws.sum(axis=(1, 2))
            total_ci = (float(np.quantile(totals, 0.05)), float(np.quantile(totals, 0.95)))

    channel_summary = []
    for col in alt_plan.columns:
        b_sp = float(base_plan[col].sum())
        a_sp = float(alt_plan[col].sum())
        row: dict[str, Any] = {
            "channel": channel_label(col),
            "slug": col,
            "multiplier": round(a_sp / b_sp, 3) if b_sp > 0 else float((multipliers or {}).get(col, 1.0)),
            "base_spend": round(b_sp, 0),
            "scenario_spend": round(a_sp, 0),
            "delta_spend": round(a_sp - b_sp, 0),
        }
        if effects:
            ch_draws = effects.get(col)
            if ch_draws is None:
                # Not a media channel of the model (a control such as Affiliate): it has no sales effect of its own
                row.update(delta_sales=None, delta_sales_lo=None, delta_sales_hi=None, delta_roas=None)
            else:
                ch_totals = ch_draws.sum(axis=1)
                ch_delta = float(ch_totals.mean())
                multi = ch_totals.size > 1
                row.update(
                    delta_sales=round(ch_delta, 0),
                    delta_sales_lo=round(float(np.quantile(ch_totals, 0.05)), 0) if multi else None,
                    delta_sales_hi=round(float(np.quantile(ch_totals, 0.95)), 0) if multi else None,
                    delta_roas=round(ch_delta / (a_sp - b_sp), 2) if abs(a_sp - b_sp) > 1.0 else None,
                )
        channel_summary.append(row)

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
        "delta_sales_lo": round(total_ci[0], 0) if total_ci else None,
        "delta_sales_hi": round(total_ci[1], 0) if total_ci else None,
        # 90 % prediction interval of the total (quantiles of each draw's sum); None when no draws were available
        "prediction_total_lo": round(total_pi[0], 0) if total_pi else None,
        "prediction_total_hi": round(total_pi[1], 0) if total_pi else None,
        # Sums of the daily 5 % / 95 % bounds: much wider than an interval of the total, kept for weekly tables
        "sum_daily_prediction_lo": round(float(alt_daily["prediction_lo"].sum()), 0),
        "sum_daily_prediction_hi": round(float(alt_daily["prediction_hi"].sum()), 0),
        "backtest_coverage": round(coverage, 3) if coverage is not None else None,
        "channel_summary": channel_summary,
        "plan_daily": plan_arrays(base_plan),
        "scenario_plan_daily": plan_arrays(alt_plan),
        "channel_daily_delta": {ch: [round(float(v), 0) for v in d.mean(axis=0)] for ch, d in effects.items()},
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
    base_effects: tuple[np.ndarray, list[str]] | None = None,
    coverage: float | None = None,
) -> dict[str, Any]:
    """Evaluate a multiplier scenario with the fitted forecast model (no refit), as in notebook 05 §4.

    Each channel's own effect is the difference of its incremental-sales draws between the scenario and the plan;
    pass `base_effects` (`channel_effect_draws` of `base_plan`) to reuse the plan's draws across scenarios.
    """
    alt_plan = apply_multipliers(base_plan, multipliers, locked=tuple(cfg.as_controls))
    n = min(horizon_days or len(base_daily), len(base_daily))
    alt_daily, totals = evaluate_forecast_scenario(mmm, history, base_daily, alt_plan, cfg, seed=seed, horizon_days=n)
    base_draws, channels = base_effects or channel_effect_draws(mmm, history, base_plan, cfg)
    alt_draws, _ = channel_effect_draws(mmm, history, alt_plan, cfg)
    return scenario_payload(
        base_daily.iloc[:n],
        base_plan.iloc[:n],
        alt_daily.iloc[:n],
        alt_plan.iloc[:n],
        "Meridian posterior (fitted model, no refit)",
        multipliers,
        effect_draws=alt_draws - base_draws,
        effect_channels=channels,
        total_pi=totals,
        coverage=coverage,
    )


def fast_forecast_from_multipliers(
    base_daily: pd.DataFrame,
    base_plan: pd.DataFrame,
    multipliers: dict[str, float] | None = None,
    horizon_days: int | None = None,
    mroi_map: dict[str, float] | None = None,
    catalog_shift: int = 2,
    print_slugs: tuple[str, ...] = ("catalog",),
) -> dict[str, Any]:
    """Approximate a scenario from the run's marginal ROIs when the fitted forecast model is not available.

    Each channel's spend change is spread over a short carry-over kernel (print events: after the in-home date) and
    multiplied by that channel's posterior marginal ROI. Channels without an mROI (e.g. controls) do not move sales.
    """
    mults = {slugify_channel(k): float(v) for k, v in (multipliers or {}).items()}
    mroi = {slugify_channel(k): float(v) for k, v in (mroi_map or {}).items()}
    n_days = min(horizon_days or len(base_daily), len(base_daily))
    d_base = base_daily.iloc[:n_days].copy()
    p_base = base_plan.iloc[:n_days].copy()
    p_alt = p_base.copy()
    delta = np.zeros(n_days, dtype=float)
    channels: list[str] = []
    per_channel: list[np.ndarray] = []
    for col in p_alt.columns:
        slug = slugify_channel(col)
        if slug not in mroi:
            continue
        p_alt[col] = p_base[col] * mults.get(slug, 1.0)
        diff = (p_alt[col] - p_base[col]).to_numpy(dtype=float)
        if slug in print_slugs:
            kernel = np.r_[np.zeros(catalog_shift), 0.8 ** np.arange(14)]
        else:
            kernel = 0.5 ** np.arange(4)
        kernel = kernel / kernel.sum()
        ch_delta = np.convolve(diff, kernel)[:n_days] * mroi[slug]
        delta += ch_delta
        channels.append(str(col))
        per_channel.append(ch_delta)
    d_alt = d_base.copy()
    for col in ("forecast", "credible_lo", "credible_hi", "prediction_lo", "prediction_hi"):
        d_alt[col] = np.maximum(0.0, d_base[col].to_numpy(dtype=float) + delta)
    d_alt["planned_spend"] = p_alt.sum(axis=1).to_numpy(dtype=float)
    effect_draws = np.stack(per_channel, axis=-1)[np.newaxis] if per_channel else None
    return scenario_payload(
        d_base,
        p_base,
        d_alt,
        p_alt,
        "Marginal-ROI approximation (fitted model not available)",
        multipliers,
        effect_draws=effect_draws,
        effect_channels=channels,
    )


def interval_coverage(df: pd.DataFrame) -> float:
    """Share of days whose actual sales fall inside the daily prediction interval."""
    inside = (df["actual"] >= df["prediction_lo"]) & (df["actual"] <= df["prediction_hi"])
    return float(inside.mean())


def build_forecast_scenarios_section(
    wide_df: pd.DataFrame,
    backtest_df: pd.DataFrame,
    forecast_daily: pd.DataFrame,
    plan_df: pd.DataFrame,
    scenarios_df: pd.DataFrame,
    backtest_label: str = "hold-out",
    backtest_train_end: str | None = None,
    sampling_label: str | None = None,
    plan_time_df: pd.DataFrame | None = None,
    control_names: list[str] | None = None,
    forecast_total_pi: tuple[float, float] | None = None,
    print_slugs: tuple[str, ...] = ("catalog",),
) -> Section:
    """Build the `forecast_scenarios` section reproducing `05_forecast.ipynb`.

    `backtest_df` holds `actual`, `forecast` and the 90 % prediction interval of a leak-free back-test: a model fitted
    only on history up to `backtest_train_end` and given the hold-out's actual spend as its plan. `plan_time_df` is the
    same back-test with the cost controls (`control_names`, e.g. a sales commission) replaced by values knowable before
    the window; it is the headline accuracy for budget planning when present.
    """
    bt_m = regression_metrics(backtest_df["actual"].to_numpy(), backtest_df["forecast"].to_numpy())
    head_df = plan_time_df if plan_time_df is not None else backtest_df
    head_m = regression_metrics(head_df["actual"].to_numpy(), head_df["forecast"].to_numpy())
    coverage = interval_coverage(head_df)
    controls_txt = ", ".join(control_names or [])
    bt_dates = [d.strftime("%Y-%m-%d") for d in backtest_df.index]
    bt_series = [
        {
            "name": "Forecast from a plan (90% prediction interval)" if plan_time_df is not None else "Forecast (90% prediction interval)",
            "y": head_df["forecast"].round(0).tolist(),
            "band_lower": head_df["prediction_lo"].round(0).tolist(),
            "band_upper": head_df["prediction_hi"].round(0).tolist(),
            "color": TEAL,
            "width": 2.0,
        },
        {"name": "Actual", "y": backtest_df["actual"].round(0).tolist(), "color": NAVY, "width": 1.4, "mode": "lines+markers", "marker": {"size": 4}},
    ]
    if plan_time_df is not None:
        bt_series.insert(1, {"name": f"With realised {controls_txt}", "y": backtest_df["forecast"].round(0).tolist(), "color": INK_MUTED, "dash": "dot", "width": 1.4})
    fig_bt = line_figure(x=bt_dates, series=bt_series, yaxis_title="Daily total sales (€)")

    recent_hist = wide_df["total_sales"].iloc[-min(48, len(wide_df)) :]
    hist_dates = [d.strftime("%Y-%m-%d") for d in recent_hist.index]
    fc_dates = [d.strftime("%Y-%m-%d") for d in forecast_daily.index]
    all_dates = hist_dates + fc_dates
    pad = [None] * len(hist_dates)
    vlines = [{"x": fc_dates[0], "color": SLATE, "dash": "dash", "label": "history | forecast"}]
    for col in [c for c in plan_df.columns if c in print_slugs]:
        for d in plan_df.index[plan_df[col] > 0]:
            vlines.append({"x": d.strftime("%Y-%m-%d"), "color": COPPER, "dash": "dot", "label": channel_label(col)})
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

    scen_notes = []
    skip = scenarios_df[(scenarios_df["delta_spend"] < 0) & scenarios_df["scenario"].str.startswith("Skip the ")]
    if len(skip):
        r = skip.iloc[0]
        scen_notes.append(
            f"Skipping a send-out saves {fmt_eur(-float(r['delta_spend']))} and loses {fmt_eur(-float(r['delta_sales']))} in sales "
            f"(ratio {float(r['delta_sales']) / float(r['delta_spend']):.2f})."
        )
    if scenarios_df["scenario"].str.contains("upper-bound", case=False).any():
        scen_notes.append("Moving budget into an upper-bound channel rides on its upward-biased ROI; test it before acting on it.")

    tot_fc_sales = float(forecast_daily["forecast"].sum())
    tot_fc_spend = float(forecast_daily["planned_spend"].sum())
    print_cols = [c for c in plan_df.columns if c in print_slugs]
    n_sends = int((plan_df[print_cols] > 0).to_numpy().sum()) if print_cols else 0
    head_label = "from a plan" if plan_time_df is not None else "with realised costs"
    plan_kpis = [
        KpiItem(
            label=f"Forecast sales, {len(forecast_daily)} days",
            value=fmt_eur(tot_fc_sales),
            delta=(
                f"90 % interval {fmt_eur(forecast_total_pi[0])}–{fmt_eur(forecast_total_pi[1])}"
                if forecast_total_pi
                else f"Ø {fmt_eur(float(forecast_daily['forecast'].mean()))}/day"
            ),
            status="neutral",
        ),
        KpiItem(
            label="Planned spend",
            value=fmt_eur(tot_fc_spend),
            delta=(f"{n_sends} send-out{'s' if n_sends != 1 else ''} · " if print_cols else "") + f"{fc_dates[0]} → {fc_dates[-1]}",
        ),
    ]
    accuracy_kpis = [
        KpiItem(
            label=f"Back-test {backtest_label} {head_label} (MAE / mean)",
            value=f"{100.0 * head_m['MAE_over_mean']:.1f}%",
            delta=(
                f"{100.0 * bt_m['MAE_over_mean']:.1f}% with realised {controls_txt} · bias {fmt_eur_signed(head_m['bias'])}/day"
                if plan_time_df is not None
                else f"R² {bt_m['R2']:.3f} · bias {fmt_eur_signed(bt_m['bias'])}/day"
            ),
            status="success" if head_m["MAE_over_mean"] <= 0.15 else "warn",
        ),
        KpiItem(
            label="Days inside the 90% prediction interval",
            value=f"{100.0 * coverage:.0f}%",
            delta="Measured on the back-test; nominal 90 %",
            status="success" if coverage >= 0.85 else "warn",
        ),
    ]
    blocks: list[Any] = [
        KpisBlock(id="forecast_accuracy_kpis", items=accuracy_kpis),
        KpisBlock(id="forecast_plan_kpis", items=plan_kpis),
        MarkdownBlock(
            title="How the forecast works",
            body=(
                "Meridian only predicts inside the window it is fitted on, so the plan's days are appended to the history as "
                "**hold-out rows** (their sales are unknown) and the model is refit"
                + (f" ({sampling_label})" if sampling_label else "")
                + ". The rows' sales placeholder is the training mean, so no future sales are used; it keeps Meridian's KPI "
                "mean and shrinks its scale slightly (a few percent), which does not change the forecast's level.\n\n"
                f"- **Prediction interval:** where an *actual* day's sales are expected to land. Nominally 90 %; on the back-test "
                f"it held {100.0 * coverage:.0f} % of the days, so plan with that coverage in mind.\n"
                "- **Credible interval:** uncertainty about the *expected* level only (much narrower).\n"
                "- **Totals:** the interval of a total comes from the draws' sums, not from adding daily bounds."
                + (
                    f"\n- **Cost controls ({controls_txt}):** a plan only knows planned amounts. The back-test therefore uses "
                    "their recent run-rate × last year's seasonal pattern instead of their realised cost; the gap between the "
                    "two back-tests shows how much the forecast depends on knowing them."
                    if plan_time_df is not None
                    else ""
                )
            ),
        ),
        ChartBlock(
            id="forecast_backtest_q4",
            title=f"Leak-free back-test, {backtest_label}: MAE / mean {head_label} = {100.0 * head_m['MAE_over_mean']:.1f}%",
            subtitle=(
                f"Trained only on history up to {backtest_train_end or 'the day before the window'}; the media plan is the actual "
                "spend of the window"
                + (f" and {controls_txt} is estimated from history" if plan_time_df is not None else "")
                + "; no sales from the window are used, not even in the KPI scaling."
            ),
            figure=fig_bt,
            caption=(
                f"Coverage below the nominal 90 % comes from the level offset (bias {fmt_eur_signed(head_m['bias'])}/day); "
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
                f"{fmt_eur(tot_fc_spend)} planned spend" + ("; dotted lines are planned send-outs." if print_cols else ".")
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
            subtitle=(
                "Evaluated with the fitted model (no refit); only media channels change"
                + (f", {controls_txt} stays as planned." if controls_txt else ".")
            ),
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
            caption=" ".join(scen_notes) or None,
        ),
        TableBlock(
            id="forecast_weekly_plan_table",
            title="Budget plan by week and channel",
            subtitle="Example plan built from history: same-weekday spend one year earlier × each channel's spend growth.",
            columns=[
                TableColumn(key="week_ending", label="Week ending"),
                *[TableColumn(key=col, label=channel_label(col), align="right") for col in plan_df.columns],
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
