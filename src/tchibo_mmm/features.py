"""Feature engineering: catalog timing, calendar and seasonality controls, Meridian input table."""

import holidays
import numpy as np
import pandas as pd

from tchibo_mmm.data import PAID_CHANNELS, slug

DEFAULT_CATALOG_SHIFT = 2  # days from send-out to "in-home"; tuned on the hold-out


# --- catalog timing -------------------------------------------------------------------------


def days_since_send_out(index: pd.DatetimeIndex, send_outs: pd.DatetimeIndex) -> pd.Series:
    """Days since the most recent catalog send-out (NaN before the first one)."""
    pos = send_outs.searchsorted(index, side="right") - 1
    last = pd.Series(send_outs[np.clip(pos, 0, None)], index=index)
    out = (index - pd.DatetimeIndex(last)).days.astype(float)
    return pd.Series(np.where(pos >= 0, out, np.nan), index=index, name="days_since_send_out")


def shift_spend(spend: pd.Series, days: int) -> pd.Series:
    """Move spend `days` forward in time (send-out date -> in-home date), zero-filled."""
    return spend.shift(days, fill_value=0.0)


# --- adstock shapes (mirror meridian.model.adstock_hill, for illustration) ------------------


def geometric_decay_weights(alpha: float, max_lag: int) -> np.ndarray:
    return alpha ** np.arange(max_lag + 1)


def binomial_decay_weights(alpha: float, max_lag: int) -> np.ndarray:
    lags = np.arange(max_lag + 1)
    return (1 - lags / (max_lag + 1)) ** (1 / alpha - 1)


# --- calendar & seasonality controls ---------------------------------------------------------


def black_friday(year: int) -> pd.Timestamp:
    """Day after the 4th Thursday of November."""
    nov1 = pd.Timestamp(year=year, month=11, day=1)
    first_thu = nov1 + pd.Timedelta(days=(3 - nov1.dayofweek) % 7)
    return first_thu + pd.Timedelta(days=22)


def calendar_controls(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Weekday dummies (Monday = reference), German holidays, Christmas window, half-day holidays
    (24 / 31 Dec), Black Week."""
    out = pd.DataFrame(index=index)
    for d, name in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]):
        if d:
            out[f"dow_{name}"] = (index.dayofweek == d).astype(float)
    de_holidays = holidays.Germany(years=sorted(set(index.year)))
    out["holiday"] = index.map(lambda d: d in de_holidays).astype(float)
    out["christmas"] = ((index.month == 12) & (index.day >= 20)).astype(float)
    # Christmas Eve and New Year's Eve: the half-day holidays of Google's holiday calendar and the
    # deepest dips in the data (see holiday_features.py).
    out["half_day"] = ((index.month == 12) & index.day.isin([24, 31])).astype(float)
    bw = np.zeros(len(index), dtype=bool)
    for year in sorted(set(index.year)):
        bf = black_friday(year)
        bw |= (index >= bf) & (index <= bf + pd.Timedelta(days=3))  # Black Friday .. Cyber Monday
    out["black_week"] = bw.astype(float)
    return out


def seasonality_controls(index: pd.DatetimeIndex, n_fourier: int = 2) -> pd.DataFrame:
    """Yearly Fourier terms plus a linear trend (in years since the first day)."""
    out = pd.DataFrame(index=index)
    t = index.dayofyear / 365.25
    for k in range(1, n_fourier + 1):
        out[f"sin_{k}"] = np.sin(2 * np.pi * k * t)
        out[f"cos_{k}"] = np.cos(2 * np.pi * k * t)
    out["trend"] = (index - index[0]).days / 365.25
    return out


# --- model tables ----------------------------------------------------------------------------


def spend_columns() -> list[str]:
    return [f"{slug(c)}_spend" for c in PAID_CHANNELS]


def media_channels() -> list[str]:
    return [slug(c) for c in PAID_CHANNELS]


def build_meridian_table(
    wide: pd.DataFrame,
    catalog_shift: int = DEFAULT_CATALOG_SHIFT,
    n_fourier: int = 2,
) -> pd.DataFrame:
    """One row per day: `time`, KPI `total_sales`, spend per paid channel, controls.

    Spend is the media variable (the forecast has to run on budget). Catalog spend
    is moved to its in-home date because Meridian's adstock curves peak at lag 0.
    """
    idx = wide.index
    table = pd.DataFrame({"time": idx.strftime("%Y-%m-%d"), "total_sales": wide["total_sales"]})
    for ch in PAID_CHANNELS:
        spend = wide[f"cost_{slug(ch)}"]
        if ch == "Catalog":
            spend = shift_spend(spend, catalog_shift)
        table[f"{slug(ch)}_spend"] = spend
    table = table.join(calendar_controls(idx)).join(seasonality_controls(idx, n_fourier))
    return table.reset_index(drop=True)


def control_columns(table: pd.DataFrame) -> list[str]:
    fixed = {"time", "total_sales", *spend_columns()}
    return [c for c in table.columns if c not in fixed]
