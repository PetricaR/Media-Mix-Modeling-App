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
from mmm_studio.core.as_of import extend_history
from mmm_studio.core.as_of import seasonal_run_rate as _seasonal_run_rate
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
# The residual row of the forecast decomposition: the intercept plus every control at its reference level (0/1 flags
# off; trend, seasonality and cost controls at their historical average, as Meridian centres them)
BASELINE_LABEL = "Baseline demand (controls at reference)"


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


def plan_growth_days(n_history: int) -> int:
    """Days over which the example plan measures each channel's year-on-year spend growth: the last year when the
    history has two, else the longest recent window whose days one year earlier are in the history (0: none)."""
    return max(0, min(365, int(n_history) - 365))


def _weekday_profile(history: pd.DataFrame, dates: pd.DatetimeIndex, cost_col: str) -> np.ndarray:
    """Daily cost for `dates` from history alone: last year's same-window weekday means × the year-on-year growth.

    The growth compares the last `plan_growth_days` days with the same days one year earlier, so a short history (13
    months) still carries the current run-rate: a channel paused in the last weeks is planned at 0, one that doubled
    is planned at twice last year's level. A channel without spend in the year-earlier days (launched since) is planned
    at its recent weekday means.
    """
    last_yr_start = dates[0] - pd.DateOffset(years=1)
    last_yr_end = dates[-1] - pd.DateOffset(years=1)
    last_year = history.loc[last_yr_start:last_yr_end]
    if len(last_year) < 7:
        last_year = history.iloc[-min(365, len(history)) :]
    by_weekday = last_year[cost_col].groupby(last_year.index.dayofweek).mean()
    days = plan_growth_days(len(history))
    if days < 7:
        return by_weekday.reindex(dates.dayofweek).fillna(0.0).to_numpy()
    recent = history[cost_col].iloc[-days:]
    before = history[cost_col].iloc[-(days + 365) : -365]
    denom = float(before.sum())
    if denom <= 0:
        return recent.groupby(recent.index.dayofweek).mean().reindex(dates.dayofweek).fillna(0.0).to_numpy()
    return by_weekday.reindex(dates.dayofweek).fillna(0.0).to_numpy() * (float(recent.sum()) / denom)


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


# Fewer leak-free out-of-sample windows than this and the level uncertainty of a forecast is not estimated
MIN_LEVEL_WINDOWS = 3


def predictive_from_expected(
    expected: np.ndarray,
    noise_sd: np.ndarray,
    seed: int = 0,
    level_sd: float = 0.0,
    ar1: float = 0.0,
) -> np.ndarray:
    """Predictive draws `(n_draws, n_time)` from posterior draws of the expected outcome.

    Each draw gets Meridian's observation noise (`noise_sd`, one value per draw), correlated from day to day as an
    AR(1) with lag-1 correlation `ar1` (the same daily spread; 0 = independent days), and one multiplicative level
    offset ~ Normal(0, `level_sd`) shared by every day of the draw. The offset carries the out-of-sample drift of the
    baseline that independent daily noise cannot: it barely widens a day's band but dominates the interval of a total.
    Neither term moves the mean forecast.
    """
    rng = np.random.default_rng(seed)
    eps = rng.normal(size=expected.shape)
    if ar1:
        phi = float(np.clip(ar1, -0.99, 0.99))
        scale = np.sqrt(1.0 - phi**2)
        for t in range(1, eps.shape[1]):
            eps[:, t] = phi * eps[:, t - 1] + scale * eps[:, t]
    level = rng.normal(size=(expected.shape[0], 1)) * float(level_sd) if level_sd else 0.0
    return expected * (1.0 + level) + eps * noise_sd


def residual_ar1(actual: np.ndarray, expected_mean: np.ndarray) -> float:
    """Lag-1 autocorrelation of the training residuals (actual − posterior-mean expected outcome); 0 when undefined."""
    r = np.asarray(actual, dtype=float) - np.asarray(expected_mean, dtype=float)
    if len(r) < 3 or float(np.std(r[:-1])) == 0.0 or float(np.std(r[1:])) == 0.0:
        return 0.0
    return float(np.corrcoef(r[:-1], r[1:])[0, 1])


def level_uncertainty(windows: list[dict[str, Any]], before: pd.Timestamp | None = None) -> dict[str, Any] | None:
    """The level uncertainty of a forecast: the RMS of the total errors ((actual − forecast) / forecast) of leak-free
    out-of-sample windows, using only windows that end before `before` (a back-test must not learn from its own window).

    Returns None with fewer than `MIN_LEVEL_WINDOWS` windows: the interval of a total is then not calibrated.
    """
    use = [w for w in windows if before is None or pd.Timestamp(w["end"]) < pd.Timestamp(before)]
    if len(use) < MIN_LEVEL_WINDOWS:
        return None
    errors = np.array([float(w["error"]) for w in use])
    return {
        "level_sd": float(np.sqrt(np.mean(errors**2))),
        "n_windows": len(use),
        "sources": sorted({str(w["source"]) for w in use}),
        "windows": use,
    }


def forecast_level_sd(summary: dict[str, Any] | None) -> float:
    """The level uncertainty a run's forecasts use (stored in `data/forecast_summary.json`); 0 for older runs."""
    lvl = (summary or {}).get("level_uncertainty") or {}
    return float(lvl.get("level_sd") or 0.0)


def window_level_error(actual: pd.Series, forecast: pd.Series) -> float:
    """(actual − forecast) / forecast of a window's total."""
    f = float(np.sum(forecast))
    return (float(np.sum(actual)) - f) / f if f else float("nan")


def _prediction_draws(
    mmm: Any,
    new_data: Any = None,
    seed: int = 0,
    level_sd: float = 0.0,
    forecast_start: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return `(expected_draws, predictive_draws)` of shape `(n_draws, n_time)`.

    With `forecast_start` (the first forecast day's position) the daily noise is AR(1) with the lag-1 correlation of the
    residuals before it, and `level_sd` adds a level offset per draw (`predictive_from_expected`); without it the
    noise is independent from day to day, as Meridian's likelihood assumes.
    """
    from meridian.analysis import analyzer

    out = analyzer.Analyzer(mmm).expected_outcome(new_data=new_data, aggregate_times=False)
    expected = _flatten_draws(out).reshape(-1, mmm.input_data.time.size)
    sigma = _flatten_draws(mmm.inference_data.posterior["sigma"].values).reshape(expected.shape[0], -1)[:, :1]
    noise_sd = sigma * float(np.asarray(mmm.kpi_transformer.population_scaled_stdev))
    ar1 = 0.0
    if forecast_start:
        actual = np.asarray(mmm.kpi, dtype=float).reshape(-1)[:forecast_start]
        ar1 = residual_ar1(actual, expected[:, :forecast_start].mean(axis=0))
    return expected, predictive_from_expected(expected, noise_sd, seed=seed, level_sd=level_sd, ar1=ar1)


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
    # Lag-1 correlation of the daily noise (from the training residuals) and the level uncertainty the draws carry
    ar1: float = 0.0
    level_sd: float = 0.0


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
    level_sd: float = 0.0,
) -> PlanForecast:
    """Fit Meridian on `history + plan` (with plan dates masked as holdout) and return the daily forecast.

    The fit goes through `fit_meridian_isolated`, so it can run in a spawned child with cancellation and a timeout.
    `level_sd` is the forecast's level uncertainty (`level_uncertainty`); 0 keeps independent daily noise only.
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
    expected, predictive = _prediction_draws(mmm, seed=seed, level_sd=level_sd, forecast_start=len(history))
    index = pd.DatetimeIndex(pd.to_datetime(meridian_table(ext, fc_cfg)["time"]))
    daily = _summarise_draws(expected, predictive, index).loc[start:end].copy()
    daily["planned_spend"] = plan.sum(axis=1).to_numpy()
    lo, hi = total_interval(predictive, index, pd.DatetimeIndex(plan.index))
    n_hist = len(history)
    ar1 = residual_ar1(np.asarray(mmm.kpi, dtype=float).reshape(-1)[:n_hist], expected[:, :n_hist].mean(axis=0))
    return PlanForecast(daily=daily, mmm=mmm, cfg=fc_cfg, total_lo=lo, total_hi=hi, ar1=ar1, level_sd=level_sd)


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
    level_sd: float = 0.0,
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
    expected, predictive = _prediction_draws(
        mmm, new_data=DataTensors(**tensors), seed=seed, level_sd=level_sd, forecast_start=len(history)
    )
    index = pd.DatetimeIndex(pd.to_datetime(alt_table["time"]))
    daily = _summarise_draws(expected, predictive, index).loc[base_daily.index].copy()
    daily["planned_spend"] = alt_plan.loc[base_daily.index].sum(axis=1).to_numpy()
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


def control_effect_draws(
    mmm: Any,
    history: pd.DataFrame,
    plan: pd.DataFrame,
    cfg: MeridianSpecConfig,
) -> dict[str, dict[str, Any]]:
    """Posterior draws `(n_draws, n_plan_days)` of each control variable group's contribution on `plan.index`.

    In Meridian, each control $k$ adds $\\gamma_k (x_{k,t}^{\\text{scaled}} - x_{k,0}^{\\text{scaled}}) \\sigma_y$ to the
    expected KPI on day $t$, where $x_{k,0}^{\\text{scaled}}$ is the scaled value of a zero control input.
    """
    if mmm is None:
        return {}
    table = meridian_table(extend_history(history, plan), cfg)
    if hasattr(mmm, "input_data") and hasattr(mmm.input_data, "control_variable"):
        control_cols = [str(c) for c in mmm.input_data.control_variable.values]
    else:
        _, _, control_cols = meridian_roles(table, cfg)
    if not control_cols or not hasattr(mmm, "inference_data") or "gamma_gc" not in mmm.inference_data.posterior:
        return {}
    try:
        for col in control_cols:
            if col not in table.columns:
                table[col] = 0.0
        c_raw = table[control_cols].to_numpy(dtype=float)[np.newaxis, :, :]
        c_scaled = np.asarray(mmm.controls_transformer.forward(c_raw), dtype=float)[0, -len(plan) :, :]
        c_zero = np.asarray(mmm.controls_transformer.forward(np.zeros_like(c_raw)), dtype=float)[0, -len(plan) :, :]
        gamma = np.asarray(mmm.inference_data.posterior["gamma_gc"].values, dtype=float)
        gamma = gamma.reshape(-1, gamma.shape[-1])
        kpi_sd = float(np.asarray(mmm.kpi_transformer.population_scaled_stdev))
    except Exception:
        return {}

    binary_prefixes = ("dow_", "missing_")
    binary_exact = {"holiday", "christmas", "half_day", "black_week", "promo"}

    groups: dict[str, dict[str, Any]] = {}
    for j, col in enumerate(control_cols):
        is_binary = col.startswith(binary_prefixes) or col in binary_exact
        if col.startswith(("ctrl_", "wx_")) and col in table.columns:
            is_binary = bool(set(np.unique(table[col].to_numpy(dtype=float))).issubset({0.0, 1.0}))
        c_effect_j = (c_scaled[:, j] - c_zero[:, j]) if is_binary else c_scaled[:, j]
        draws_j = gamma[:, j, np.newaxis] * c_effect_j[np.newaxis, :] * kpi_sd
        if col.endswith("_spend"):
            slug = col[:-6]
            key = slug
            label = f"{channel_label(slug)} (control, vs its average)"
            category = "control_channel"
            spend_col: str | None = slug if slug in plan.columns else None
        elif col.startswith(("sin_", "cos_")):
            key = "seasonality"
            label = "Annual Seasonality (Fourier)"
            category = "seasonality"
            spend_col = None
        elif col.startswith("dow_"):
            key = "weekdays"
            label = "Weekday Profile"
            category = "calendar"
            spend_col = None
        elif col in {"holiday", "christmas", "half_day", "black_week", "promo"}:
            key = "holidays"
            label = "Holidays & Events"
            category = "calendar"
            spend_col = None
        elif col.startswith(("ctrl_", "wx_")):
            key = col
            name = cfg.control_names.get(col, col.removeprefix("ctrl_").replace("_", " "))
            label = name if is_binary else f"{name} (vs its average)"
            category = "calendar"
            spend_col = None
        elif col == "trend":
            key = "trend"
            label = "Long-Term Trend (vs its average)"
            category = "trend"
            spend_col = None
        else:
            key = "other_controls"
            label = "Other Controls"
            category = "calendar"
            spend_col = None

        if key not in groups:
            groups[key] = {
                "key": key,
                "label": label,
                "category": category,
                "spend_col": spend_col,
                "draws": np.zeros_like(draws_j),
            }
        groups[key]["draws"] = groups[key]["draws"] + draws_j

    if "other_controls" in groups and np.allclose(groups["other_controls"]["draws"], 0.0, atol=1e-2):
        groups.pop("other_controls", None)
    return groups


def plan_arrays(plan: pd.DataFrame) -> dict[str, list[float]]:
    """Daily spend per plan column, rounded to whole euros, for the planner chart."""
    return {str(col): [round(float(v), 0) for v in plan[col]] for col in plan.columns}


def apply_multipliers(
    plan: pd.DataFrame,
    multipliers: dict[str, float] | None,
    locked: tuple[str, ...] = (),
    *,
    spend_overrides: dict[str, float] | None = None,
    horizon_days: int | None = None,
    print_slugs: tuple[str, ...] = ("catalog",),
    print_shifts: dict[str, int] | None = None,
    catalog_shift: int = 2,
) -> pd.DataFrame:
    """Scale each plan column by its multiplier or override its period spend; `locked` columns stay as planned."""
    alt = plan.copy()
    for col in alt.columns:
        if col in locked:
            continue
        alt[col] = alt[col] * float((multipliers or {}).get(col, 1.0))

    if spend_overrides:
        n = min(horizon_days or len(alt), len(alt))
        shifts = print_shifts or {}
        window_idx = pd.DatetimeIndex(alt.index[:n])
        for col, raw_target in spend_overrides.items():
            if col in locked or col not in alt.columns or n <= 0:
                continue
            target = max(0.0, float(raw_target))
            col_loc = alt.columns.get_loc(col)
            window_sum = float(plan[col].iloc[:n].sum())
            if window_sum > 0:
                alt[col] = plan[col] * (target / window_sum)
            else:
                alt.iloc[:n, col_loc] = 0.0
                if target > 0:
                    if col in print_slugs:
                        shift = int(shifts.get(col, catalog_shift))
                        last_day = window_idx[-1]
                        thursdays = [
                            d
                            for d in window_idx
                            if d.dayofweek == 3 and (d + pd.Timedelta(days=shift)) <= last_day
                        ]
                        send_dates = thursdays if thursdays else [window_idx[0]]
                        per_send = target / len(send_dates)
                        for d in send_dates:
                            alt.loc[d, col] = per_send
                    else:
                        alt.iloc[:n, col_loc] = target / n
    return alt


def _build_variable_contributions(
    base_daily: pd.DataFrame,
    base_plan: pd.DataFrame,
    alt_daily: pd.DataFrame,
    alt_plan: pd.DataFrame,
    *,
    effect_channels: list[str] | None = None,
    effects: dict[str, np.ndarray] | None = None,
    base_media_draws: np.ndarray | None = None,
    alt_media_draws: np.ndarray | None = None,
    base_control_draws: dict[str, dict[str, Any]] | None = None,
    alt_control_draws: dict[str, dict[str, Any]] | None = None,
    roi_map: dict[str, float] | None = None,
    multipliers: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Build exact additive (Shapley-style) variable attributions summing to 100 % of forecast sales.

    Without posterior media draws (the marginal-ROI approximation), a media channel's contribution is its planned spend
    × the model's average ROI (`roi_map`, the Drivers table); a channel without an ROI gets no contribution of its own
    and the baseline row says so. Nothing falls back to an assumed ROI.
    """
    n_days = len(alt_daily)
    base_sales = float(base_daily["forecast"].sum())
    alt_sales = float(alt_daily["forecast"].sum())
    eff_map = effects or {}
    roi = {slugify_channel(k): float(v) for k, v in (roi_map or {}).items()}

    media_lookup: dict[str, int] = {}
    if effect_channels:
        media_lookup = {str(ch): idx for idx, ch in enumerate(effect_channels)}

    b_med_arr = np.asarray(base_media_draws, dtype=float)[:, :n_days, :] if base_media_draws is not None else None
    a_med_arr = np.asarray(alt_media_draws, dtype=float)[:, :n_days, :] if alt_media_draws is not None else None
    if b_med_arr is not None and a_med_arr is None:
        if effect_channels and eff_map:
            diff_stack = np.stack([eff_map.get(ch, np.zeros((b_med_arr.shape[0], n_days))) for ch in effect_channels], axis=-1)
            a_med_arr = b_med_arr + diff_stack
        else:
            a_med_arr = b_med_arr

    # Determine media vs control columns in the plan
    media_cols: list[str] = []
    control_plan_cols: list[str] = []
    for col in alt_plan.columns:
        if b_med_arr is not None:
            (media_cols if col in media_lookup else control_plan_cols).append(col)
        elif slugify_channel(col) in roi or col in eff_map:
            media_cols.append(col)
        else:
            control_plan_cols.append(col)

    # Without posterior draws: planned spend × the model's average ROI (never the marginal ROI, never an assumed one)
    fallback_daily_base: dict[str, np.ndarray] = {}
    unattributed: list[str] = []
    if b_med_arr is None:
        for col in media_cols:
            slug = slugify_channel(col)
            if slug in roi:
                fallback_daily_base[col] = base_plan[col].to_numpy(dtype=float) * roi[slug]
            else:
                unattributed.append(channel_label(col))

    media_rows: list[dict[str, Any]] = []
    sum_other_daily_base = np.zeros(n_days, dtype=float)
    sum_other_daily_alt = np.zeros(n_days, dtype=float)

    for col in media_cols:
        b_sp = float(base_plan[col].sum())
        a_sp = float(alt_plan[col].sum())
        d_sp = a_sp - b_sp
        mult = (
            round(a_sp / b_sp, 3)
            if b_sp > 0
            else float((multipliers or {}).get(col, 2.0 if a_sp > 0 else 1.0))
        )

        if b_med_arr is not None and a_med_arr is not None and col in media_lookup:
            idx = media_lookup[col]
            b_d = b_med_arr[:, :, idx]
            a_d = a_med_arr[:, :, idx]
            d_d = a_d - b_d
            b_tots = b_d.sum(axis=1)
            a_tots = a_d.sum(axis=1)
            d_tots = d_d.sum(axis=1)
            multi = b_tots.size > 1
            b_val = float(b_tots.mean())
            a_val = float(a_tots.mean())
            d_val = float(d_tots.mean())
            b_lo = float(np.quantile(b_tots, 0.05)) if multi else None
            b_hi = float(np.quantile(b_tots, 0.95)) if multi else None
            a_lo = float(np.quantile(a_tots, 0.05)) if multi else None
            a_hi = float(np.quantile(a_tots, 0.95)) if multi else None
            d_lo = float(np.quantile(d_tots, 0.05)) if multi else None
            d_hi = float(np.quantile(d_tots, 0.95)) if multi else None
            day_b = b_d.mean(axis=0)
            day_a = a_d.mean(axis=0)
            day_d = d_d.mean(axis=0)
        else:
            day_b = fallback_daily_base.get(col, np.zeros(n_days, dtype=float))
            ch_eff = eff_map.get(col)
            day_d = ch_eff.mean(axis=0) if ch_eff is not None else np.zeros(n_days, dtype=float)
            day_a = day_b + day_d
            b_val = float(day_b.sum())
            a_val = float(day_a.sum())
            d_val = float(day_d.sum())
            b_lo = b_hi = a_lo = a_hi = d_lo = d_hi = None

        sum_other_daily_base += day_b
        sum_other_daily_alt += day_a
        media_rows.append(
            {
                "key": col,
                "label": channel_label(col),
                "category": "media",
                "editable": True,
                "multiplier": mult,
                "base_spend": round(b_sp, 0),
                "scenario_spend": round(a_sp, 0),
                "delta_spend": round(d_sp, 0),
                "base_contribution": round(b_val, 0),
                "base_ci_lo": round(b_lo, 0) if b_lo is not None else None,
                "base_ci_hi": round(b_hi, 0) if b_hi is not None else None,
                "scenario_contribution": round(a_val, 0),
                "scenario_ci_lo": round(a_lo, 0) if a_lo is not None else None,
                "scenario_ci_hi": round(a_hi, 0) if a_hi is not None else None,
                "delta_contribution": round(d_val, 0),
                "delta_ci_lo": round(d_lo, 0) if d_lo is not None else None,
                "delta_ci_hi": round(d_hi, 0) if d_hi is not None else None,
                "base_share_pct": round(100.0 * b_val / base_sales, 2) if base_sales else 0.0,
                "scenario_share_pct": round(100.0 * a_val / alt_sales, 2) if alt_sales else 0.0,
                "base_roi": round(b_val / b_sp, 2) if b_sp > 1.0 else None,
                "roi": round(a_val / a_sp, 2) if a_sp > 1.0 else None,
                "mroi": round(d_val / d_sp, 2) if abs(d_sp) > 1.0 else None,
                "daily_base": [round(float(v), 0) for v in day_b],
                "daily_scenario": [round(float(v), 0) for v in day_a],
                "daily_delta": [round(float(v), 0) for v in day_d],
            }
        )

    media_rows.sort(key=lambda r: float(r["base_contribution"]), reverse=True)

    # Control groups (from posterior gamma_gc draws when available, or fallback for control_plan_cols)
    control_rows: list[dict[str, Any]] = []
    b_ctrl = base_control_draws or {}
    a_ctrl = alt_control_draws or b_ctrl
    ordered_ctrl_keys = [
        k for k in ("trend", "seasonality", "weekdays", "holidays", *control_plan_cols, "other_controls") if k in b_ctrl
    ]
    for k in b_ctrl:
        if k not in ordered_ctrl_keys:
            ordered_ctrl_keys.append(k)

    if ordered_ctrl_keys:
        for key in ordered_ctrl_keys:
            g_b = b_ctrl[key]
            g_a = a_ctrl.get(key, g_b)
            b_d = np.asarray(g_b["draws"], dtype=float)[:, :n_days]
            a_d = np.asarray(g_a["draws"], dtype=float)[:, :n_days]
            d_d = a_d - b_d
            b_tots = b_d.sum(axis=1)
            a_tots = a_d.sum(axis=1)
            d_tots = d_d.sum(axis=1)
            multi = b_tots.size > 1
            b_val = float(b_tots.mean())
            a_val = float(a_tots.mean())
            d_val = float(d_tots.mean())
            day_b = b_d.mean(axis=0)
            day_a = a_d.mean(axis=0)
            day_d = d_d.mean(axis=0)
            sum_other_daily_base += day_b
            sum_other_daily_alt += day_a

            sp_col = g_b.get("spend_col")
            if sp_col and sp_col in alt_plan.columns:
                b_sp_val: float | None = float(base_plan[sp_col].sum())
                a_sp_val: float | None = float(alt_plan[sp_col].sum())
                d_sp_val: float | None = a_sp_val - b_sp_val
                mult_val = round(a_sp_val / b_sp_val, 3) if b_sp_val > 0 else 1.0
            else:
                b_sp_val = a_sp_val = d_sp_val = None
                mult_val = 1.0

            control_rows.append(
                {
                    "key": key,
                    "label": str(g_b["label"]),
                    "category": str(g_b["category"]),
                    "editable": False,
                    "multiplier": mult_val,
                    "base_spend": round(b_sp_val, 0) if b_sp_val is not None else None,
                    "scenario_spend": round(a_sp_val, 0) if a_sp_val is not None else None,
                    "delta_spend": round(d_sp_val, 0) if d_sp_val is not None else None,
                    "base_contribution": round(b_val, 0),
                    "base_ci_lo": round(float(np.quantile(b_tots, 0.05)), 0) if multi else None,
                    "base_ci_hi": round(float(np.quantile(b_tots, 0.95)), 0) if multi else None,
                    "scenario_contribution": round(a_val, 0),
                    "scenario_ci_lo": round(float(np.quantile(a_tots, 0.05)), 0) if multi else None,
                    "scenario_ci_hi": round(float(np.quantile(a_tots, 0.95)), 0) if multi else None,
                    "delta_contribution": round(d_val, 0),
                    "delta_ci_lo": round(float(np.quantile(d_tots, 0.05)), 0) if multi else None,
                    "delta_ci_hi": round(float(np.quantile(d_tots, 0.95)), 0) if multi else None,
                    "base_share_pct": round(100.0 * b_val / base_sales, 2) if base_sales else 0.0,
                    "scenario_share_pct": round(100.0 * a_val / alt_sales, 2) if alt_sales else 0.0,
                    "base_roi": None,
                    "roi": None,
                    "mroi": None,
                    "daily_base": [round(float(v), 0) for v in day_b],
                    "daily_scenario": [round(float(v), 0) for v in day_a],
                    "daily_delta": [round(float(v), 0) for v in day_d],
                }
            )
    else:
        for col in control_plan_cols:
            b_sp_c = float(base_plan[col].sum())
            a_sp_c = float(alt_plan[col].sum())
            d_sp_c = a_sp_c - b_sp_c
            control_rows.append(
                {
                    "key": col,
                    "label": f"{channel_label(col)} (control, no estimate)",
                    "category": "control_channel",
                    "editable": False,
                    "multiplier": round(a_sp_c / b_sp_c, 3) if b_sp_c > 0 else 1.0,
                    "base_spend": round(b_sp_c, 0),
                    "scenario_spend": round(a_sp_c, 0),
                    "delta_spend": round(d_sp_c, 0),
                    "base_contribution": 0.0,
                    "base_ci_lo": None,
                    "base_ci_hi": None,
                    "scenario_contribution": 0.0,
                    "scenario_ci_lo": None,
                    "scenario_ci_hi": None,
                    "delta_contribution": 0.0,
                    "delta_ci_lo": None,
                    "delta_ci_hi": None,
                    "base_share_pct": 0.0,
                    "scenario_share_pct": 0.0,
                    "base_roi": None,
                    "roi": None,
                    "mroi": None,
                    "daily_base": [0.0] * n_days,
                    "daily_scenario": [0.0] * n_days,
                    "daily_delta": [0.0] * n_days,
                }
            )

    # Baseline demand: exact residual so all daily and total attributions sum to 100 % of forecast
    base_fc_arr = base_daily["forecast"].to_numpy(dtype=float)
    alt_fc_arr = alt_daily["forecast"].to_numpy(dtype=float)
    base_intercept_daily = base_fc_arr - sum_other_daily_base
    alt_intercept_daily = alt_fc_arr - sum_other_daily_alt
    delta_intercept_daily = alt_intercept_daily - base_intercept_daily
    b_int_val = float(base_intercept_daily.sum())
    a_int_val = float(alt_intercept_daily.sum())
    d_int_val = float(delta_intercept_daily.sum())

    baseline_row: dict[str, Any] = {
        "key": "baseline",
        "label": BASELINE_LABEL + (f" + {', '.join(unattributed)} (no ROI)" if unattributed else ""),
        "category": "baseline",
        "editable": False,
        "multiplier": 1.0,
        "base_spend": None,
        "scenario_spend": None,
        "delta_spend": None,
        "base_contribution": round(b_int_val, 0),
        "base_ci_lo": None,
        "base_ci_hi": None,
        "scenario_contribution": round(a_int_val, 0),
        "scenario_ci_lo": None,
        "scenario_ci_hi": None,
        "delta_contribution": round(d_int_val, 0),
        "delta_ci_lo": None,
        "delta_ci_hi": None,
        "base_share_pct": round(100.0 * b_int_val / base_sales, 2) if base_sales else 0.0,
        "scenario_share_pct": round(100.0 * a_int_val / alt_sales, 2) if alt_sales else 0.0,
        "base_roi": None,
        "roi": None,
        "mroi": None,
        "daily_base": [round(float(v), 0) for v in base_intercept_daily],
        "daily_scenario": [round(float(v), 0) for v in alt_intercept_daily],
        "daily_delta": [round(float(v), 0) for v in delta_intercept_daily],
    }

    return [baseline_row, *control_rows, *media_rows]


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
    *,
    base_media_draws: np.ndarray | None = None,
    alt_media_draws: np.ndarray | None = None,
    base_control_draws: dict[str, dict[str, Any]] | None = None,
    alt_control_draws: dict[str, dict[str, Any]] | None = None,
    roi_map: dict[str, float] | None = None,
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

    variable_contributions = _build_variable_contributions(
        base_daily,
        base_plan,
        alt_daily,
        alt_plan,
        effect_channels=effect_channels,
        effects=effects,
        base_media_draws=base_media_draws,
        alt_media_draws=alt_media_draws,
        base_control_draws=base_control_draws,
        alt_control_draws=alt_control_draws,
        roi_map=roi_map,
        multipliers=multipliers,
    )
    var_by_key = {str(v["key"]): v for v in variable_contributions}

    channel_summary = []
    for col in alt_plan.columns:
        b_sp = float(base_plan[col].sum())
        a_sp = float(alt_plan[col].sum())
        v_row = var_by_key.get(col)
        row: dict[str, Any] = {
            "channel": channel_label(col),
            "slug": col,
            "role": "control" if (v_row and v_row["category"] != "media") else "media",
            "multiplier": (
                round(a_sp / b_sp, 3)
                if b_sp > 0
                else float((multipliers or {}).get(col, 2.0 if a_sp > 0 else 1.0))
            ),
            "base_spend": round(b_sp, 0),
            "scenario_spend": round(a_sp, 0),
            "delta_spend": round(a_sp - b_sp, 0),
            "base_contribution": v_row["base_contribution"] if v_row else None,
            "scenario_contribution": v_row["scenario_contribution"] if v_row else None,
            "contribution_lo": v_row["scenario_ci_lo"] if v_row else None,
            "contribution_hi": v_row["scenario_ci_hi"] if v_row else None,
            "share_of_forecast_pct": v_row["scenario_share_pct"] if v_row else None,
            "base_roi": v_row["base_roi"] if v_row else None,
            "scenario_roi": v_row["roi"] if v_row else None,
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
        elif v_row and v_row["category"] == "media":
            row.update(
                delta_sales=v_row["delta_contribution"],
                delta_sales_lo=v_row["delta_ci_lo"],
                delta_sales_hi=v_row["delta_ci_hi"],
                delta_roas=v_row["mroi"],
            )
        channel_summary.append(row)

    media_vars = [v for v in variable_contributions if v["category"] == "media"]
    media_base_sales = sum(float(v["base_contribution"]) for v in media_vars)
    media_scenario_sales = sum(float(v["scenario_contribution"]) for v in media_vars)
    media_base_spend = sum(float(v["base_spend"] or 0.0) for v in media_vars)
    media_scenario_spend = sum(float(v["scenario_spend"] or 0.0) for v in media_vars)
    baseline_and_controls_sales = alt_sales - media_scenario_sales

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
        "decomposition_version": 2,
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
        "media_base_sales": round(media_base_sales, 0),
        "media_scenario_sales": round(media_scenario_sales, 0),
        "media_share_pct": round(100.0 * media_scenario_sales / alt_sales, 2) if alt_sales else 0.0,
        "baseline_and_controls_sales": round(baseline_and_controls_sales, 0),
        "baseline_and_controls_share_pct": round(100.0 * baseline_and_controls_sales / alt_sales, 2) if alt_sales else 0.0,
        "media_base_spend": round(media_base_spend, 0),
        "media_scenario_spend": round(media_scenario_spend, 0),
        "base_media_roi": round(media_base_sales / media_base_spend, 2) if media_base_spend > 1.0 else None,
        "media_roi": round(media_scenario_sales / media_scenario_spend, 2) if media_scenario_spend > 1.0 else None,
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
        "variable_contributions": variable_contributions,
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
    base_controls: dict[str, dict[str, Any]] | None = None,
    coverage: float | None = None,
    unlock_controls: bool = False,
    level_sd: float = 0.0,
    spend_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate a multiplier scenario with the fitted forecast model (no refit).

    Each channel's own effect is the difference of its incremental-sales draws between the scenario and the plan;
    pass `base_effects` (`channel_effect_draws` of `base_plan`) to reuse the plan's draws across scenarios.
    """
    locked = () if unlock_controls else tuple(cfg.as_controls)
    n = min(horizon_days or len(base_daily), len(base_daily))
    alt_plan = apply_multipliers(
        base_plan,
        multipliers,
        locked=locked,
        spend_overrides=spend_overrides,
        horizon_days=n,
        print_slugs=cfg.print_slugs,
        print_shifts=cfg.print_shifts,
        catalog_shift=cfg.catalog_shift,
    )
    alt_daily, totals = evaluate_forecast_scenario(
        mmm,
        history,
        base_daily,
        alt_plan,
        cfg,
        seed=seed,
        with_controls=True,
        horizon_days=n,
        level_sd=level_sd,
    )
    base_draws, channels = base_effects or channel_effect_draws(mmm, history, base_plan, cfg)
    alt_draws, _ = channel_effect_draws(mmm, history, alt_plan, cfg)
    b_ctrl = base_controls if base_controls is not None else control_effect_draws(mmm, history, base_plan, cfg)
    a_ctrl = control_effect_draws(mmm, history, alt_plan, cfg) if unlock_controls else b_ctrl
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
        base_media_draws=base_draws,
        alt_media_draws=alt_draws,
        base_control_draws=b_ctrl,
        alt_control_draws=a_ctrl,
    )


def fast_forecast_from_multipliers(
    base_daily: pd.DataFrame,
    base_plan: pd.DataFrame,
    multipliers: dict[str, float] | None = None,
    horizon_days: int | None = None,
    mroi_map: dict[str, float] | None = None,
    catalog_shift: int = 2,
    print_slugs: tuple[str, ...] = ("catalog",),
    roi_map: dict[str, float] | None = None,
    spend_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Approximate a scenario from the run's marginal ROIs when the fitted forecast model is not available.

    Each channel's spend change is spread over a short carry-over kernel (print events: after the in-home date) and
    multiplied by that channel's posterior marginal ROI. Channels without an mROI (e.g. controls) do not move sales.
    The plan's own channel contributions use the average ROIs (`roi_map`).
    """
    mults = {slugify_channel(k): float(v) for k, v in (multipliers or {}).items()}
    overrides = {slugify_channel(k): float(v) for k, v in (spend_overrides or {}).items()}
    mroi = {slugify_channel(k): float(v) for k, v in (mroi_map or {}).items()}
    n_days = min(horizon_days or len(base_daily), len(base_daily))
    d_base = base_daily.iloc[:n_days].copy()
    p_base = base_plan.iloc[:n_days].copy()
    locked_cols = tuple(col for col in p_base.columns if slugify_channel(col) not in mroi)
    p_alt = apply_multipliers(
        p_base,
        mults,
        locked=locked_cols,
        spend_overrides=overrides,
        horizon_days=n_days,
        print_slugs=print_slugs,
        catalog_shift=catalog_shift,
    )
    delta = np.zeros(n_days, dtype=float)
    channels: list[str] = []
    per_channel: list[np.ndarray] = []
    for col in p_alt.columns:
        slug = slugify_channel(col)
        if slug not in mroi:
            continue
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
        roi_map=roi_map,
    )


def interval_coverage(df: pd.DataFrame, shift: float = 0.0) -> float:
    """Share of days whose actual sales fall inside the daily prediction interval (moved by `shift` euros)."""
    inside = (df["actual"] >= df["prediction_lo"] + shift) & (df["actual"] <= df["prediction_hi"] + shift)
    return float(inside.mean())


def total_check(df: pd.DataFrame, total_pi: tuple[float, float] | None) -> dict[str, Any] | None:
    """How a back-test's total compares with the 90 % interval of its total: the error and half-width in % of the
    forecast total, and whether the actual total landed inside."""
    if total_pi is None or total_pi[0] is None or total_pi[1] is None:
        return None
    actual, forecast = float(df["actual"].sum()), float(df["forecast"].sum())
    if forecast <= 0:
        return None
    return {
        "days": int(len(df)),
        "error_pct": 100.0 * (actual - forecast) / forecast,
        "half_width_pct": 100.0 * (float(total_pi[1]) - float(total_pi[0])) / 2.0 / forecast,
        "inside": bool(float(total_pi[0]) <= actual <= float(total_pi[1])),
    }


def _plan_method_text(n_history: int) -> str:
    """How `build_example_plan` sets each channel's daily spend for a history of `n_history` days."""
    days = plan_growth_days(n_history)
    if days < 7:
        return "Example plan built from history: same-weekday spend one year earlier (too little history to measure growth)."
    window = "the last year vs the year before" if days >= 365 else f"the last {days} days vs the same days a year earlier"
    return f"Example plan built from history: same-weekday spend one year earlier × each channel's spend growth ({window})."


def spend_beyond_history(history: pd.DataFrame, plan: pd.DataFrame) -> list[tuple[str, int]]:
    """`(channel slug, days)` for each plan channel that spends more on some of the plan's days than on any day of
    `history`: on those days the model's response curve is extrapolated beyond the data it was fitted on."""
    out: list[tuple[str, int]] = []
    for col in plan.columns:
        cost = f"cost_{col}"
        if cost not in history.columns or not len(history):
            continue
        days = int((plan[col].to_numpy(dtype=float) > float(history[cost].max()) + 1e-9).sum())
        if days:
            out.append((str(col), days))
    return out


def _extrapolation_text(beyond: list[tuple[str, int]], n_days: int, history_label: str) -> str | None:
    if not beyond:
        return None
    names = ", ".join(f"{channel_label(c)} ({d} of {n_days} days)" for c, d in sorted(beyond, key=lambda x: -x[1]))
    return (
        f"Spend above anything in {history_label}: {names}. On those days the response curves are extrapolated beyond "
        "the data, so the forecast there rests on the fitted saturation shape rather than on observed spend levels."
    )


def _total_check_text(check: dict[str, Any], label: str) -> str:
    return (
        f"{label}: actual {check['error_pct']:+.1f} % vs the forecast, interval ±{check['half_width_pct']:.1f} % "
        f"({'inside' if check['inside'] else 'outside'})"
    )


def _totals_method_text(
    level_info: dict[str, Any] | None, backtest_level_info: dict[str, Any] | None, noise_ar1: float | None
) -> str:
    """How the interval of a total is built, with the evidence behind its width."""
    ar = f" (AR(1), lag-1 r = {noise_ar1:.2f} from the model's training residuals)" if noise_ar1 else ""
    if not level_info:
        return (
            f"It only has the day-to-day noise{ar}: this run has fewer than {MIN_LEVEL_WINDOWS} out-of-sample windows to "
            "measure how far a forecast's level drifts, so the interval of a total is not calibrated."
        )
    text = (
        f"Besides the day-to-day noise{ar}, each draw carries one level offset for the whole window with a standard "
        f"deviation of {100.0 * level_info['level_sd']:.1f} %: the RMS total error of {level_info['n_windows']} leak-free "
        f"out-of-sample windows ({', '.join(level_info['sources'])}). That is how far a forecast's level drifts over a few "
        "weeks; it widens the interval of a total far more than a day's band, and it cancels when two scenarios of the "
        "same plan are compared."
    )
    if backtest_level_info:
        text += (
            f" The back-test uses only the {backtest_level_info['n_windows']} windows that end before it "
            f"(σ {100.0 * backtest_level_info['level_sd']:.1f} %)."
        )
    return text


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
    backtest_total_pi: tuple[float, float] | None = None,
    plan_time_total_pi: tuple[float, float] | None = None,
    level_info: dict[str, Any] | None = None,
    backtest_level_info: dict[str, Any] | None = None,
    noise_ar1: float | None = None,
    estimated_costs: list[str] | None = None,
) -> Section:
    """Build the `forecast_scenarios` section.

    `backtest_df` holds `actual`, `forecast` and the 90 % prediction interval of a leak-free back-test: a model fitted
    only on history up to `backtest_train_end` and given the hold-out's actual spend as its plan. `plan_time_df` is the
    same back-test with the cost controls (`control_names`, e.g. a sales commission) replaced by values knowable before
    the window; it is the headline accuracy for budget planning when present. `estimated_costs` names the channels whose
    cost a leak-free back-test (`core/as_of.py`) already planned from history: `backtest_df` is then itself the forecast
    from a plan, and no realised-cost variant exists.
    """
    estimated_txt = ", ".join(estimated_costs or [])
    from_plan = plan_time_df is not None or bool(estimated_costs)
    bt_m = regression_metrics(backtest_df["actual"].to_numpy(), backtest_df["forecast"].to_numpy())
    head_df = plan_time_df if plan_time_df is not None else backtest_df
    head_m = regression_metrics(head_df["actual"].to_numpy(), head_df["forecast"].to_numpy())
    coverage = interval_coverage(head_df)
    # Coverage once the level offset is removed: tells whether the offset or a too-narrow band misses the days
    coverage_unbiased = interval_coverage(head_df, shift=-float(head_m["bias"]))
    controls_txt = ", ".join(control_names or [])
    bt_total = total_check(backtest_df, backtest_total_pi)
    pt_total = total_check(plan_time_df, plan_time_total_pi) if plan_time_df is not None else None
    head_total = pt_total if plan_time_df is not None else bt_total
    total_checks = [
        _total_check_text(c, lbl)
        for c, lbl in (
            (pt_total, "from a plan"),
            (bt_total, f"with realised {controls_txt}" if plan_time_df is not None else ("from a plan" if from_plan else "back-test")),
        )
        if c is not None
    ]
    if coverage >= 0.85:
        coverage_caption = None
    elif coverage_unbiased >= 0.85:
        coverage_caption = (
            f"Coverage below the nominal 90 % comes from the level offset (bias {fmt_eur_signed(head_m['bias'])}/day): without it "
            f"{100.0 * coverage_unbiased:.0f} % of the days would be inside. Refitting weekly re-anchors the level."
        )
    else:
        coverage_caption = (
            f"Even without the level offset (bias {fmt_eur_signed(head_m['bias'])}/day) only {100.0 * coverage_unbiased:.0f} % of "
            "the days would be inside: the day-to-day spread in this window is wider than the model's noise estimate, so the "
            "daily band is too narrow."
        )
    bt_index = pd.DatetimeIndex(backtest_df.index)
    bt_history = wide_df.loc[: pd.Timestamp(backtest_train_end)] if backtest_train_end else wide_df.loc[wide_df.index < bt_index[0]]
    bt_spend = pd.DataFrame({c: wide_df.loc[bt_index, f"cost_{c}"].to_numpy() for c in plan_df.columns if f"cost_{c}" in wide_df})
    bt_extrapolation = _extrapolation_text(spend_beyond_history(bt_history, bt_spend), len(bt_index), "the training days")
    fc_extrapolation = _extrapolation_text(spend_beyond_history(wide_df, plan_df), len(plan_df), "the history")
    bt_caption = " ".join(t for t in (coverage_caption, bt_extrapolation) if t) or None
    bt_dates = [d.strftime("%Y-%m-%d") for d in backtest_df.index]
    bt_series = [
        {
            "name": "Forecast from a plan (90% prediction interval)" if from_plan else "Forecast (90% prediction interval)",
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
    head_label = "from a plan" if from_plan else "with realised costs"
    plan_kpis = [
        KpiItem(
            label=f"Forecast sales, {len(forecast_daily)} days",
            value=fmt_eur(tot_fc_sales),
            delta=(
                f"90 % interval {fmt_eur(forecast_total_pi[0])}–{fmt_eur(forecast_total_pi[1])}"
                + (f" · incl. level drift (σ {100.0 * level_info['level_sd']:.1f} %)" if level_info else "")
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
    if head_total is not None:
        accuracy_kpis.append(
            KpiItem(
                label=f"Back-test {head_total['days']}-day total vs its 90% interval",
                value=f"{head_total['error_pct']:+.1f}%",
                delta=f"actual vs forecast; interval ±{head_total['half_width_pct']:.1f}% · {'inside' if head_total['inside'] else 'outside'}",
                status="success" if head_total["inside"] else "warn",
            )
        )
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
                "- **Totals:** the interval of a total comes from the draws' sums, not from adding daily bounds. "
                + _totals_method_text(level_info, backtest_level_info, noise_ar1)
                + (
                    " On the back-test the total landed "
                    + "; ".join(total_checks)
                    + (
                        ", so read the interval of a total as too narrow."
                        if any(c is not None and not c["inside"] for c in (pt_total, bt_total))
                        else "."
                    )
                    if total_checks
                    else ""
                )
                + (
                    f"\n- **Cost controls ({controls_txt}):** a plan only knows planned amounts. The back-test therefore uses "
                    "their recent run-rate × last year's seasonal pattern instead of their realised cost; the gap between the "
                    "two back-tests shows how much the forecast depends on knowing them."
                    if plan_time_df is not None
                    else (
                        f"\n- **Costs that follow the outcome ({estimated_txt}):** a plan only knows planned amounts, and their "
                        "realised cost carries the window's sales (a commission on sales, click-billed spend that tracks "
                        "demand). The back-test therefore uses their recent run-rate × last year's seasonal pattern."
                        if estimated_costs
                        else ""
                    )
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
                + (f", except {estimated_txt}, estimated from history" if estimated_costs and plan_time_df is None else "")
                + "; no sales from the window are used, not even in the KPI scaling."
            ),
            figure=fig_bt,
            caption=bt_caption,
        ),
        ChartBlock(
            id="forecast_2025_01_chart",
            title=f"Daily sales forecast for the budget plan ({fc_dates[0]} → {fc_dates[-1]})",
            subtitle=(
                f"{fmt_eur(tot_fc_sales)} forecast sales (Ø {fmt_eur(float(forecast_daily['forecast'].mean()))}/day) for "
                f"{fmt_eur(tot_fc_spend)} planned spend" + ("; dotted lines are planned send-outs." if print_cols else ".")
            ),
            figure=fig_fc,
            caption=fc_extrapolation,
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
            subtitle=_plan_method_text(len(wide_df)),
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
