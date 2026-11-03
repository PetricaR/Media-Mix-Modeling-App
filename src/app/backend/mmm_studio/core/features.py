"""Notebook-faithful feature helpers shared by preparation, the OLS benchmark and the Meridian input table.

These reproduce the controls and catalog transforms of notebooks 02–05 exactly (weekday dummies with Monday as
reference, German holidays, 20–31 Dec Christmas window with the 24 / 31 Dec half-day holidays, Black Friday → Cyber
Monday, yearly Fourier terms, a linear trend in years, and the catalog in-home shift).
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

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
) -> pd.DataFrame:
    """Weekday dummies (Monday = reference), German holidays, Christmas window and Black Week; `groups` picks which.

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
        import holidays

        de_holidays = holidays.Germany(years=sorted(set(index.year)))
        out["holiday"] = index.map(lambda d: d in de_holidays).astype(float)
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
    """Causal (un-normalised) convolution, as used for the adstock scans in notebook 02."""
    arr = np.asarray(x, dtype=float)
    return np.convolve(arr, weights)[: len(arr)]


def catalog_inhome_adstock(
    cost_catalog: pd.Series,
    shift: int = DEFAULT_CATALOG_SHIFT,
    alpha: float = CATALOG_INHOME_DECAY,
    max_lag: int = CATALOG_MAX_LAG,
) -> pd.Series:
    """Catalog spend shifted to its in-home date and geometrically adstocked (notebooks 02 and 04)."""
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


def quarter_label(start: pd.Timestamp | str, end: pd.Timestamp | str) -> str:
    """'Q4 2024' when a window starts on a quarter start, else an explicit date range."""
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    if s == s.to_period("Q").start_time:
        return f"Q{s.quarter} {s.year}"
    return f"{s:%Y-%m-%d} – {e:%Y-%m-%d}"


def holdout_windows(
    index: pd.DatetimeIndex,
    holdout_days: int,
) -> dict[str, tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Return the two forward hold-outs of notebooks 03/04 as `{name: (train_end, test_start, test_end)}`.

    `primary` is the last `holdout_days` days (Q4 2024 → 1 Jan 2025 for the case study); `forward` is the calendar
    quarter before it, with training data ending the day before (Q3 2024, trained to June).
    """
    idx = pd.DatetimeIndex(index)
    end = idx.max()
    h = max(int(holdout_days), 14)
    test_start = end - pd.Timedelta(days=h - 1)
    windows = {"primary": (test_start - pd.Timedelta(days=1), test_start, end)}
    fwd_end = test_start - pd.Timedelta(days=1)
    fwd_start = fwd_end.to_period("Q").start_time
    if (fwd_end - fwd_start).days < 27:
        fwd_start = fwd_end - pd.Timedelta(days=91)
    if fwd_start - pd.Timedelta(days=1) >= idx.min() + pd.Timedelta(days=180):
        windows["forward"] = (fwd_start - pd.Timedelta(days=1), fwd_start, fwd_end)
    return windows
