"""Feature helpers shared by preparation, the OLS benchmark and the Meridian input table.

They build the calendar controls and catalog transforms (weekday dummies with Monday as
reference, German holidays, 20–31 Dec Christmas window with the 24 / 31 Dec half-day holidays, Black Friday → Cyber
Monday, yearly Fourier terms, a linear trend in years, and the catalog in-home shift).
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from mmm_studio.core.model_settings import (
    DEFAULT_HOLDOUT_DAYS,
    MIN_HOLDOUT_DAYS,
    MIN_SPLIT_WINDOW_DAYS,
    MIN_TRAINING_DAYS,
    SPLIT_SNAP_DAYS,
    SPLIT_WINDOW_SHARE,
)

DEFAULT_CATALOG_SHIFT = 2
CATALOG_MAX_LAG = 21
CATALOG_INHOME_DECAY = 0.8


def black_friday(year: int) -> pd.Timestamp:
    """Day after the 4th Thursday of November."""
    nov1 = pd.Timestamp(year=year, month=11, day=1)
    first_thu = nov1 + pd.Timedelta(days=(3 - nov1.dayofweek) % 7)
    return first_thu + pd.Timedelta(days=22)


def calendar_controls(
    index: pd.DatetimeIndex,
    groups: Iterable[str] = ("weekdays", "holidays", "christmas", "black_week"),
    country: str = "DE",
) -> pd.DataFrame:
    """Weekday dummies (Monday = reference), the public holidays of `country` from Google Calendar (German in the case
    study), Christmas window and Black Week; `groups` picks which.

    The Christmas group also flags Christmas Eve and New Year's Eve (`half_day`), the half-day holidays of Google's
    German holiday calendar and the deepest dips in the data.
    """
    groups = set(groups)
    out = pd.DataFrame(index=index)
    if "weekdays" in groups:
        for d, name in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]):
            if d:
                out[f"dow_{name}"] = (index.dayofweek == d).astype(float)
    if "holidays" in groups:
        from mmm_studio.core.holidays import public_holiday_flags

        out["holiday"] = public_holiday_flags(index, country).to_numpy()
    if "christmas" in groups:
        out["christmas"] = ((index.month == 12) & (index.day >= 20)).astype(float)
        out["half_day"] = ((index.month == 12) & index.day.isin([24, 31])).astype(float)
    if "black_week" in groups:
        bw = np.zeros(len(index), dtype=bool)
        for year in sorted(set(index.year)):
            bf = black_friday(year)
            bw |= (index >= bf) & (index <= bf + pd.Timedelta(days=3))
        out["black_week"] = bw.astype(float)
    return out


def seasonality_controls(index: pd.DatetimeIndex, n_fourier: int = 2, trend: bool = True) -> pd.DataFrame:
    """Yearly Fourier terms plus (optionally) a linear trend in years since the first day."""
    out = pd.DataFrame(index=index)
    t = index.dayofyear / 365.25
    for k in range(1, n_fourier + 1):
        out[f"sin_{k}"] = np.sin(2 * np.pi * k * t)
        out[f"cos_{k}"] = np.cos(2 * np.pi * k * t)
    if trend:
        out["trend"] = (index - index[0]).days / 365.25
    return out


def shift_spend(spend: pd.Series, days: int) -> pd.Series:
    """Move spend `days` forward in time (send-out date → in-home date), zero-filled."""
    return spend.shift(days, fill_value=0.0)


def geometric_decay_weights(alpha: float, max_lag: int) -> np.ndarray:
    return alpha ** np.arange(max_lag + 1)


def binomial_decay_weights(alpha: float, max_lag: int) -> np.ndarray:
    lags = np.arange(max_lag + 1)
    return (1 - lags / (max_lag + 1)) ** (1 / alpha - 1)


def convolve_weights(x: pd.Series | np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Causal (un-normalised) convolution, as used for the adstock scans of the data preparation."""
    arr = np.asarray(x, dtype=float)
    return np.convolve(arr, weights)[: len(arr)]


def catalog_inhome_adstock(
    cost_catalog: pd.Series,
    shift: int = DEFAULT_CATALOG_SHIFT,
    alpha: float = CATALOG_INHOME_DECAY,
    max_lag: int = CATALOG_MAX_LAG,
) -> pd.Series:
    """Catalog spend shifted to its in-home date and geometrically adstocked."""
    values = convolve_weights(shift_spend(cost_catalog, shift), geometric_decay_weights(alpha, max_lag))
    return pd.Series(values, index=cost_catalog.index, name="catalog_inhome_adstock")


def days_since_send_out(index: pd.DatetimeIndex, send_outs: pd.DatetimeIndex) -> pd.Series:
    """Days since the most recent catalog send-out (NaN before the first one)."""
    pos = send_outs.searchsorted(index, side="right") - 1
    last = pd.Series(send_outs[np.clip(pos, 0, None)], index=index)
    out = (index - pd.DatetimeIndex(last)).days.astype(float)
    return pd.Series(np.where(pos >= 0, out, np.nan), index=index, name="days_since_send_out")


def complete_years(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    """Drop a trailing isolated partial year (≤ 7 days, e.g. 2025-01-01) for yearly and monthly views."""
    idx = pd.DatetimeIndex(df.index)
    if len(idx) == 0:
        return df
    max_year = int(idx.max().year)
    if len(set(idx.year)) > 1 and int((idx.year == max_year).sum()) <= 7:
        return df[idx.year < max_year]
    return df


def full_years(df: pd.DataFrame | pd.Series) -> list[int]:
    """Calendar years with every one of their days in the index: the only years a year-over-year comparison may use
    (a year with only its first weeks in the data would compare 28 days with 365)."""
    import calendar

    idx = pd.DatetimeIndex(df.index)
    counts = pd.Series(idx.year).value_counts()
    return sorted(int(yr) for yr, n in counts.items() if int(n) >= (366 if calendar.isleap(int(yr)) else 365))


def quarter_label(start: pd.Timestamp | str, end: pd.Timestamp | str) -> str:
    """'Q4 2024' when a window covers a whole calendar quarter from its first day, plus at most a week (e.g. a
    hold-out that also includes 1 Jan 2025), else an explicit date range: 28 days of January are not "Q1", nor is a year
    that starts on 1 October "Q4"."""
    s, e = pd.Timestamp(start), pd.Timestamp(end).normalize()
    quarter = s.to_period("Q")
    q_end = quarter.end_time.normalize()
    if s == quarter.start_time and q_end <= e <= q_end + pd.Timedelta(days=SPLIT_SNAP_DAYS):
        return f"Q{s.quarter} {s.year}"
    return f"{s:%Y-%m-%d} – {e:%Y-%m-%d}"


def _snap_to_month_start(start: pd.Timestamp, earliest: pd.Timestamp, latest: pd.Timestamp) -> pd.Timestamp:
    """The first of a month nearest to `start`, within `SPLIT_SNAP_DAYS` days and inside [`earliest`, `latest`];
    `start` itself when there is none. Windows then read as months or quarters (Q3 2024, not 30 Jun – 30 Sep)."""
    month = start.to_period("M")
    candidates = [month.start_time, (month + 1).start_time]
    fits = [c for c in candidates if abs((c - start).days) <= SPLIT_SNAP_DAYS and earliest <= c <= latest]
    return min(fits, key=lambda c: abs((c - start).days)) if fits else start


def default_holdout_days(index: pd.DatetimeIndex) -> int:
    """The test window (`holdout_days`) a new run starts with, from the length of its data.

    The split is chronological: training, then validation and test of equal length at the end (see
    `holdout_windows`). Each window takes about an eighth of the days (`SPLIT_WINDOW_SHARE`: 75 / 12.5 / 12.5), at most
    about a quarter (`DEFAULT_HOLDOUT_DAYS`) and at least four weeks, and a full year of training days
    (`MIN_TRAINING_DAYS`) stays before the validation window. When the data is too short for two four-week windows
    after a training year, the test takes every day beyond that year (at least 14) and there is no validation window.
    The first test day moves to the nearest first of a month within a week. A 732-day 2023–2024 panel gives 93
    (Q4 2024 + 1 Jan 2025) and a Q3 2024 validation window.
    """
    idx = pd.DatetimeIndex(index)
    n, end = len(idx), idx.max()
    room = n - MIN_TRAINING_DAYS  # days beyond the training year, for validation and test together
    target = min(round(n * SPLIT_WINDOW_SHARE), DEFAULT_HOLDOUT_DAYS)
    if room >= 2 * MIN_SPLIT_WINDOW_DAYS:
        shortest, longest = MIN_SPLIT_WINDOW_DAYS, room // 2
    else:
        shortest, longest = MIN_HOLDOUT_DAYS, max(MIN_HOLDOUT_DAYS, room)
    h = max(shortest, min(target, longest))
    start = _snap_to_month_start(
        end - pd.Timedelta(days=h - 1),
        earliest=end - pd.Timedelta(days=longest - 1),
        latest=end - pd.Timedelta(days=shortest - 1),
    )
    return int((end - start).days) + 1


def holdout_windows(
    index: pd.DatetimeIndex,
    holdout_days: int,
) -> dict[str, tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """The chronological test and validation windows as `{name: (train_end, test_start, test_end)}`.

    `primary` (the test) is the last `holdout_days` days (e.g. Q4 2024 → 1 Jan 2025 on the sample workbook). `forward` (the
    validation window) is as long as the test and ends the day before it, its first day moved to the nearest first of a
    month within a week (e.g. Q3 2024, trained to June). Every fit scored on it is trained on the days
    before it only, so it exists only when a full year of training days (`MIN_TRAINING_DAYS`) remains there, the rule
    the final model's hold-out follows.
    """
    idx = pd.DatetimeIndex(index)
    first, end = idx.min(), idx.max()
    h = max(int(holdout_days), MIN_HOLDOUT_DAYS)
    test_start = end - pd.Timedelta(days=h - 1)
    windows = {"primary": (test_start - pd.Timedelta(days=1), test_start, end)}
    val_end = test_start - pd.Timedelta(days=1)
    earliest = first + pd.Timedelta(days=MIN_TRAINING_DAYS)
    val_start = _snap_to_month_start(
        val_end - pd.Timedelta(days=h - 1), earliest=earliest, latest=val_end - pd.Timedelta(days=MIN_HOLDOUT_DAYS - 1)
    )
    if val_start >= earliest:
        windows["forward"] = (val_start - pd.Timedelta(days=1), val_start, val_end)
    return windows
