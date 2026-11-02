"""Forecast daily total sales for a future budget plan with Meridian.

Meridian predicts only inside the time window it is fitted on. The plan's days are
therefore appended to the history as hold-out rows: their sales are unknown, so they
are excluded from the likelihood, and the model is refit. Hold-out sales still enter
Meridian's KPI scaling (mean / std), so they are filled with the training mean, which
leaves that mean unchanged.

Plan file (CSV): one row per future day, consecutive, starting the day after the
history ends, with the column ``date`` plus one daily cost column (€) per paid channel:
``sea, catalog, display, affiliate, display_network, social_media, psm``. Catalog cost
goes on the send-out day, exactly as in the history.

Usage:
    uv run tchibo-forecast --plan data/input/budget_plan_2025-01.csv --out outputs/forecast.csv
"""

import argparse
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
from meridian.analysis import analyzer
from meridian.analysis.tensors import DataTensors

from tchibo_mmm import data, features
from tchibo_mmm import mmm as M

PLAN_COLUMNS = [data.slug(c) for c in data.PAID_CHANNELS]
FULL_SAMPLING = {"n_chains": 4, "n_adapt": 1000, "n_burnin": 500, "n_keep": 1000, "n_prior": 500}
FAST_SAMPLING = {"n_chains": 2, "n_adapt": 300, "n_burnin": 200, "n_keep": 200, "n_prior": 200}


def load_plan(path: Path | str) -> pd.DataFrame:
    plan = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    missing = set(PLAN_COLUMNS) - set(plan.columns)
    if missing:
        raise ValueError(f"plan is missing cost columns: {sorted(missing)}")
    return plan[PLAN_COLUMNS].astype(float)


def validate_plan(plan: pd.DataFrame, history_end: pd.Timestamp) -> None:
    expected = pd.date_range(history_end + pd.Timedelta(days=1), periods=len(plan), freq="D")
    if not plan.index.equals(expected):
        raise ValueError(
            f"plan must cover consecutive days starting {expected[0].date()} (day after the history)"
        )
    if plan.isna().any().any() or (plan < 0).any().any():
        raise ValueError("plan costs must be non-negative numbers (use 0 for no spend)")


def example_plan(
    history: pd.DataFrame,
    start: str,
    days: int,
    send_outs: list[str],
    catalog_cost: float | None = None,
) -> pd.DataFrame:
    """Illustrative budget plan built from history (replace with marketing's real plan).

    Each non-catalog channel gets, per day, the average spend of the same weekday in the
    same window one year earlier, scaled by the channel's spend growth over the last
    365 days vs the 365 days before. Catalog cost (default: average cost per send-out in
    the last 365 days) is booked on the given send-out days.
    """
    dates = pd.date_range(start, periods=days, freq="D", name="date")
    last_year = history.loc[dates[0] - pd.DateOffset(years=1) : dates[-1] - pd.DateOffset(years=1)]
    recent, before = history.iloc[-365:], history.iloc[-730:-365]
    plan = pd.DataFrame(0.0, index=dates, columns=PLAN_COLUMNS)
    for col in PLAN_COLUMNS:
        cost = f"cost_{col}"
        if col == "catalog":
            sends = recent[cost][recent[cost] > 0]
            plan.loc[pd.DatetimeIndex(send_outs), col] = catalog_cost or sends.mean()
            continue
        by_weekday = last_year[cost].groupby(last_year.index.dayofweek).mean()
        growth = recent[cost].sum() / before[cost].sum()
        plan[col] = by_weekday.reindex(dates.dayofweek).to_numpy() * growth
    return plan


def extend_history(wide: pd.DataFrame, plan: pd.DataFrame) -> pd.DataFrame:
    """History plus plan rows in the wide-table format (unknown sales = training mean)."""
    future = pd.DataFrame(0.0, index=plan.index, columns=wide.columns)
    for col in PLAN_COLUMNS:
        future[f"cost_{col}"] = plan[col]
    future["total_sales"] = wide["total_sales"].mean()
    future.index.name = wide.index.name
    return pd.concat([wide, future])


@dataclass
class ForecastResult:
    daily: pd.DataFrame  # date-indexed: forecast, credible + prediction intervals, planned spend
    model: object  # fitted Meridian model (history + plan window)
    history: pd.DataFrame  # wide history table the model was fitted on
    cfg: M.MMMConfig


def _prediction_draws(mmm, new_data: DataTensors | None = None, seed: int = 0):
    """Posterior draws (draws, time) of expected sales and of a new observation (expected + noise)."""
    out = analyzer.Analyzer(mmm).expected_outcome(new_data=new_data, aggregate_times=False)
    expected = M.draws(out).reshape(-1, mmm.input_data.time.size)
    sigma = M.draws(mmm.inference_data.posterior["sigma"].values).reshape(expected.shape[0], -1)[
        :, :1
    ]
    noise_sd = sigma * float(np.asarray(mmm.kpi_transformer.population_scaled_stdev))
    rng = np.random.default_rng(seed)
    return expected, expected + rng.normal(size=expected.shape) * noise_sd


def _summarise(
    expected: np.ndarray, predictive: np.ndarray, index: pd.DatetimeIndex
) -> pd.DataFrame:
    index = pd.DatetimeIndex(index, name="date")
    return pd.DataFrame(
        {
            "forecast": expected.mean(axis=0),
            "credible_lo": np.quantile(expected, 0.05, axis=0),
            "credible_hi": np.quantile(expected, 0.95, axis=0),
            "prediction_lo": np.quantile(predictive, 0.05, axis=0),
            "prediction_hi": np.quantile(predictive, 0.95, axis=0),
        },
        index=index,
    )


def forecast(
    plan: pd.DataFrame,
    history: pd.DataFrame | None = None,
    cfg: M.MMMConfig | None = None,
    sampling: dict | None = None,
    seed: int = 0,
) -> ForecastResult:
    """Fit Meridian on history + plan window and forecast daily sales for the plan days.

    `history` is a wide table as built by `data.build_wide_table` (defaults to the full
    case-study data). Intervals are 90 %: `credible_*` for the expected sales,
    `prediction_*` for an actual day (adds the model's residual noise).
    """
    history = data.build_wide_table(data.load_raw()) if history is None else history
    validate_plan(plan, history.index[-1])
    start, end = plan.index[0].strftime("%Y-%m-%d"), plan.index[-1].strftime("%Y-%m-%d")
    cfg = replace(cfg or M.MMMConfig(), holdout=(start, end))
    table = features.build_meridian_table(
        extend_history(history, plan), catalog_shift=cfg.catalog_shift
    )
    mmm = M.fit(
        M.build_model(M.build_input_data(table, cfg), cfg), seed=seed, **(sampling or FULL_SAMPLING)
    )

    expected, predictive = _prediction_draws(mmm, seed=seed)
    daily = _summarise(expected, predictive, pd.DatetimeIndex(pd.to_datetime(table["time"]))).loc[
        start:end
    ]
    daily["planned_spend"] = plan.sum(axis=1)
    return ForecastResult(daily=daily, model=mmm, history=history, cfg=cfg)


def scenario(result: ForecastResult, alt_plan: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """Forecast for an alternative plan over the same window, reusing the fitted model.

    Only media channels can differ from the fitted plan (controls such as Affiliate are
    taken from the fitted plan).
    """
    if not alt_plan.index.equals(result.daily.index):
        raise ValueError("alternative plan must cover exactly the forecast window")
    alt_table = features.build_meridian_table(
        extend_history(result.history, alt_plan), catalog_shift=result.cfg.catalog_shift
    )
    spend_cols, _, _ = M.roles(alt_table, result.cfg)
    media = alt_table[spend_cols].to_numpy(dtype=float)[np.newaxis, :, :]
    expected, predictive = _prediction_draws(result.model, DataTensors(media=media), seed)
    index = pd.DatetimeIndex(pd.to_datetime(alt_table["time"]))
    daily = _summarise(expected, predictive, index).loc[result.daily.index]
    daily["planned_spend"] = alt_plan.sum(axis=1)
    return daily


def main() -> None:
    parser = argparse.ArgumentParser(description="Forecast daily sales for a future budget plan.")
    parser.add_argument(
        "--plan", required=True, type=Path, help="CSV with date + daily cost per paid channel"
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="where to write the daily forecast CSV"
    )
    parser.add_argument("--fast", action="store_true", help="short sampling (for quick checks)")
    args = parser.parse_args()

    plan = load_plan(args.plan)
    result = forecast(plan, sampling=FAST_SAMPLING if args.fast else FULL_SAMPLING)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.daily.round(0).to_csv(args.out)
    total = result.daily["forecast"].sum()
    print(
        f"{plan.index[0].date()} .. {plan.index[-1].date()}: forecast {total:,.0f} € sales "
        f"for {plan.to_numpy().sum():,.0f} € planned spend -> {args.out}"
    )


if __name__ == "__main__":
    main()
