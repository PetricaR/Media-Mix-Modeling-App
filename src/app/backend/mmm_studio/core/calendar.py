"""Adstock, saturation, Fourier seasonality, and German retail calendar features."""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_DIGITAL_ALPHAS: dict[str, float] = {
    "sea": 0.15,
    "affiliate": 0.20,
    "psm": 0.20,
    "display": 0.40,
    "display_network": 0.45,
    "social_media": 0.50,
}


def geometric_adstock(x: np.ndarray | pd.Series, alpha: float) -> np.ndarray:
    """Recursive geometric adstock: A_t = x_t + alpha * A_{t-1}."""
    if not 0.0 <= alpha < 1.0:
        raise ValueError(f"alpha must be in [0, 1), got {alpha}")
    arr = np.asarray(x, dtype=float)
    out = np.zeros_like(arr)
    carry = 0.0
    for i, val in enumerate(arr):
        carry = val + alpha * carry
        out[i] = carry
    return out


def delayed_binomial_adstock(
    x: np.ndarray | pd.Series,
    peak_lag: float = 3.0,
    max_lag: int = 21,
    decay: float = 0.88,
) -> tuple[np.ndarray, np.ndarray]:
    """Delayed adstock kernel peaking at `peak_lag` days and decaying over `max_lag` days."""
    lags = np.arange(0, max_lag + 1, dtype=float)
    weights = np.power(lags + 1.0, peak_lag) * np.power(decay, lags)
    weights = weights / weights.sum()
    arr = np.asarray(x, dtype=float)
    convolved = np.convolve(arr, weights, mode="full")[: len(arr)]
    return convolved, weights


def hill_saturation(
    x: np.ndarray | pd.Series,
    half_saturation: float,
    slope: float = 1.0,
) -> np.ndarray:
    """Hill saturation curve x^s / (k^s + x^s) mapped to [0, 1]."""
    arr = np.maximum(np.asarray(x, dtype=float), 0.0)
    if half_saturation <= 0:
        return np.zeros_like(arr)
    xs = np.power(arr, slope)
    ks = float(half_saturation) ** slope
    return xs / (ks + xs)


def fourier_terms(
    day_of_year: np.ndarray | pd.Series,
    period: float = 365.25,
    order: int = 3,
) -> pd.DataFrame:
    """Generate annual sine and cosine Fourier harmonics."""
    t = np.asarray(day_of_year, dtype=float)
    cols: dict[str, np.ndarray] = {}
    for k in range(1, order + 1):
        angle = 2.0 * np.pi * k * t / period
        cols[f"sin_{k}"] = np.sin(angle)
        cols[f"cos_{k}"] = np.cos(angle)
    return pd.DataFrame(cols)


def german_holiday_flags(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Build German federal holiday and major retail calendar indicators."""
    import holidays

    years = sorted(set(dates.year.tolist()))
    de_holidays = holidays.country_holidays("DE", years=years)
    is_holiday = np.array([d.date() in de_holidays for d in dates], dtype=int)

    month = dates.month
    day = dates.day
    christmas_build = ((month == 12) & (day >= 1) & (day <= 23)).astype(int)
    post_christmas = ((month == 12) & (day >= 24)).astype(int)

    black_week = np.zeros(len(dates), dtype=int)
    cyber_week = np.zeros(len(dates), dtype=int)
    for yr in years:
        nov = pd.date_range(f"{yr}-11-01", f"{yr}-11-30", freq="D")
        fridays = nov[nov.dayofweek == 4]
        if len(fridays) >= 4:
            bf = fridays[3]
            bw_start = bf - pd.Timedelta(days=4)
            bw_end = bf + pd.Timedelta(days=3)
            cw_end = bf + pd.Timedelta(days=10)
            black_week |= ((dates >= bw_start) & (dates <= bw_end)).astype(int)
            cyber_week |= ((dates > bw_end) & (dates <= cw_end)).astype(int)

    easter_sundays = [pd.Timestamp(yr, 4, 9) if yr == 2023 else pd.Timestamp(yr, 3, 31) for yr in years]
    pre_easter = np.zeros(len(dates), dtype=int)
    for es in easter_sundays:
        pre_easter |= ((dates >= es - pd.Timedelta(days=10)) & (dates < es)).astype(int)

    return pd.DataFrame(
        {
            "is_de_holiday": is_holiday,
            "is_christmas_window": christmas_build,
            "is_post_christmas": post_christmas,
            "is_black_week": black_week,
            "is_cyber_week": cyber_week,
            "is_pre_easter": pre_easter,
        },
        index=dates,
    )


def add_calendar_and_adstock_features(
    wide: pd.DataFrame,
    config: object | None = None,
    catalog_alpha: float = 0.88,
    catalog_peak_lag: float = 3.0,
    catalog_max_lag: int = 21,
    digital_alphas: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Enrich the daily wide table with calendar, holiday, regime, and adstock/saturation features."""
    df = wide.copy()
    idx = pd.DatetimeIndex(df.index)

    df["t"] = np.arange(len(df), dtype=float)
    df["t_norm"] = df["t"] / max(len(df) - 1, 1)
    df["year"] = idx.year
    df["month"] = idx.month
    df["dayofweek"] = idx.dayofweek
    df["dayofyear"] = idx.dayofyear
    df["is_weekend"] = (idx.dayofweek >= 5).astype(int)

    for dow, name in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]):
        df[f"dow_{name}"] = (idx.dayofweek == dow).astype(int)

    f_df = fourier_terms(idx.dayofyear, period=365.25, order=3)
    for col in f_df.columns:
        df[col] = f_df[col].to_numpy()

    h_df = german_holiday_flags(idx)
    for col in h_df.columns:
        df[col] = h_df[col].to_numpy()

    df["social_post_aug2024"] = (idx >= pd.Timestamp("2024-08-01")).astype(int)
    df["affiliate_post_feb2024"] = (idx >= pd.Timestamp("2024-02-01")).astype(int)

    if "cost_catalog" in df.columns:
        df["cost_catalog_adstock_geo"] = geometric_adstock(df["cost_catalog"], alpha=catalog_alpha)
        delayed, _ = delayed_binomial_adstock(
            df["cost_catalog"],
            peak_lag=catalog_peak_lag,
            max_lag=catalog_max_lag,
            decay=catalog_alpha,
        )
        df["cost_catalog_adstock"] = delayed
        df["catalog_in_home_7d"] = df["cost_catalog"].rolling(7, min_periods=1).sum()
        df["catalog_in_home_14d"] = df["cost_catalog"].rolling(14, min_periods=1).sum()

    alphas = digital_alphas or DEFAULT_DIGITAL_ALPHAS
    for slug, alpha in alphas.items():
        col = f"cost_{slug}"
        if col in df.columns:
            ad = geometric_adstock(df[col], alpha=alpha)
            df[f"{col}_adstock"] = ad
            pos = ad[ad > 0]
            half_sat = float(np.median(pos)) if len(pos) > 0 else 1.0
            df[f"{col}_sat"] = hill_saturation(ad, half_saturation=half_sat, slope=1.0)
            df[f"{col}_roll7"] = df[col].rolling(7, min_periods=1).mean()

    df["digital_spend_share"] = np.where(
        df["total_cost"] > 0, df["digital_cost"] / df["total_cost"], 0.0
    )
    df["log_total_sales"] = np.log(np.maximum(df["total_sales"], 1.0))
    return df
