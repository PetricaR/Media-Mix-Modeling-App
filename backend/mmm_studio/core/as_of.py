"""What a forecast made the day before a window could know: the modelling panel "as of" that day.

A scored window (the test, the validation window, an OLS walk-forward fold, a selection-grid fit) is a fair test of a
budget-plan forecast only when the model sees nothing a planner could not have known the day before it:

- Its sales are unknown. The window's rows carry the training mean as a placeholder (`extend_history`), so no scaling
  reads them: not Meridian's KPI mean and standard deviation, not its automatic knot selection.
- The planner sets most costs, so their actual spend in the window is the plan. A cost that follows the outcome (a
  commission paid on sales, a click-billed channel whose spend tracks demand) is replaced by its recent run-rate × last
  year's seasonal pattern (`seasonal_run_rate`), because its realised value carries the window's sales.
- The user's controls keep their last observed value, the weather surprises are 0 (normal weather) and the missing-row
  flags are 0, exactly as for a future plan.

Pure pandas, so the OLS benchmark and the Meridian refits build their windows the same way. `leak_free_validation` in
the run settings switches this on (the default); off, conventional hold-outs are scored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from mmm_studio.core.schema import DataRoleConfig

# Days whose weekday means give a cost's current run-rate in `seasonal_run_rate`
RUN_RATE_DAYS = 56

Window = tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]


def leak_free(config: DataRoleConfig | None) -> bool:
    """Whether a run scores its windows leak-free (`ModelSettings.leak_free_validation`; the default without a config)."""
    return config is None or bool(config.model_settings.leak_free_validation)


def seasonal_run_rate(
    history: pd.DataFrame, dates: pd.DatetimeIndex, cost_col: str, recent_days: int = RUN_RATE_DAYS
) -> np.ndarray:
    """Daily cost for `dates` as a planner would estimate it from `history` alone: the last `recent_days` days' weekday
    means (the current run-rate, so a recent rate change is included) × last year's seasonal contour or ratio."""
    recent = history.iloc[-recent_days:]
    base = recent[cost_col].groupby(recent.index.dayofweek).mean().reindex(dates.dayofweek).fillna(0.0).to_numpy()
    last_window = history.loc[dates[0] - pd.DateOffset(years=1) : dates[-1] - pd.DateOffset(years=1), cost_col]
    if len(dates) > 28 and len(last_window) == len(dates) and float(last_window.mean()) > 0:
        contour = (last_window.rolling(7, min_periods=1, center=True).mean() / float(last_window.mean())).to_numpy()
        return base * contour
    last_recent = history.loc[recent.index[0] - pd.DateOffset(years=1) : recent.index[-1] - pd.DateOffset(years=1), cost_col]
    ratio = 1.0
    if len(last_window) >= 7 and len(last_recent) >= 7 and float(last_recent.mean()) > 0:
        ratio = float(last_window.mean()) / float(last_recent.mean())
    return base * ratio


def planned_costs(history: pd.DataFrame, plan: pd.DataFrame, slugs: tuple[str, ...] | list[str]) -> pd.DataFrame:
    """`plan` with the cost of each channel in `slugs` replaced by what was knowable before the plan's first day."""
    out = plan.copy()
    dates = pd.DatetimeIndex(plan.index)
    for slug in slugs:
        if slug in out.columns and f"cost_{slug}" in history.columns:
            out[slug] = seasonal_run_rate(history, dates, f"cost_{slug}")
    return out


def extend_history(wide: pd.DataFrame, plan: pd.DataFrame) -> pd.DataFrame:
    """Append future plan rows to `wide`, setting unknown future `total_sales` to the training mean.

    The user's controls (`ctrl_*`) keep their last observed value: a flag that ended (Covid) stays off and a level
    (a price index) stays where it is. Every other non-cost column is 0 in the future, so the weather surprises
    (`wx_*`) assume normal weather and the missing-row flags (`missing_*`) are off.
    """
    future = pd.DataFrame(0.0, index=plan.index, columns=wide.columns)
    for col in wide.columns:
        if str(col).startswith("ctrl_") and len(wide):
            future[col] = float(wide[col].iloc[-1])
    for col in plan.columns:
        cost_col = f"cost_{col}"
        if cost_col in future.columns:
            future[cost_col] = plan[col].astype(float)
    future["total_sales"] = float(wide["total_sales"].mean())
    future.index.name = wide.index.name
    return pd.concat([wide, future])


def window_plan(
    wide: pd.DataFrame,
    window: Window,
    plan_cols: list[str],
    outcome_driven: tuple[str, ...] | list[str] = (),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`(history, plan)` for scoring `window = (train_end, start, end)` as a plan made on `train_end`.

    The plan is the window's actual spend of `plan_cols`, except the channels in `outcome_driven`, which are planned
    from history (`planned_costs`).
    """
    tr_end, start, end = window
    history = wide.loc[:tr_end]
    plan = pd.DataFrame({c: wide.loc[start:end, f"cost_{c}"] for c in plan_cols if f"cost_{c}" in wide.columns})
    plan.index = pd.DatetimeIndex(wide.loc[start:end].index, name="date")
    return history, planned_costs(history, plan, outcome_driven)


def as_of_panel(
    wide: pd.DataFrame,
    window: Window,
    plan_cols: list[str],
    outcome_driven: tuple[str, ...] | list[str] = (),
) -> pd.DataFrame:
    """The panel a model scored on `window` may see: every day up to `train_end`, then the window as a plan made that
    day (`window_plan` + `extend_history`). Nothing after the window is included."""
    history, plan = window_plan(wide, window, plan_cols, outcome_driven)
    return extend_history(history, plan)


def outcome_driven_slugs(role_config: DataRoleConfig, wide: pd.DataFrame | None = None) -> tuple[str, ...]:
    """Channels whose cost follows the outcome, so their realised cost in a window leaks its sales: commission
    channels (a share of their own sales) and demand-following media (click-billed spend that tracks demand)."""
    from mmm_studio.core.meridian.spec import demand_following_slugs

    commission = [ch.slug for ch in role_config.channels if ch.enabled and ch.role == "commission"]
    return tuple(dict.fromkeys([*commission, *demand_following_slugs(role_config, wide)]))


def scoring_cutoff(index: pd.DatetimeIndex, holdout_days: int) -> pd.Timestamp:
    """The last day before the test window: choices made from the data (shift scans, feature ladders, the weather's
    seasonal normal) use only the days up to here, so the test window never informs the model it scores."""
    from mmm_studio.core.features import holdout_windows

    return holdout_windows(pd.DatetimeIndex(index), holdout_days)["primary"][0]
