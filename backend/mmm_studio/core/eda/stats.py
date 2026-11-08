"""Statistical helpers behind the EDA tables: de-seasonalising, correlation robustness, VIF, STL, stationarity and
event studies."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.core.schema import DataRoleConfig

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def calendar_design(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Month and weekday dummies plus a linear trend, used to de-seasonalise series."""
    frame = pd.DataFrame({"month": index.month.astype(str), "dow": index.dayofweek.astype(str)}, index=index)
    x = pd.get_dummies(frame, drop_first=True).astype(float)
    x.insert(0, "trend", np.arange(len(index)) / len(index))
    x.insert(0, "const", 1.0)
    return x


def residualize(s: pd.Series, x: pd.DataFrame) -> pd.Series:
    beta, *_ = np.linalg.lstsq(x.to_numpy(), s.to_numpy(dtype=float), rcond=None)
    return s - x.to_numpy() @ beta


def paid_cost_frame(panel: pd.DataFrame, config: DataRoleConfig) -> pd.DataFrame:
    slug_map = config.slug_map()
    return pd.DataFrame({ch: panel[f"cost_{slug_map[ch]}"] for ch in config.paid_channels if f"cost_{slug_map[ch]}" in panel})


def complete_weeks(index: pd.DatetimeIndex) -> pd.Index:
    counts = pd.Series(1, index=index).resample("W-SUN").sum()
    return counts.index[counts == 7]


def correlation_robustness(panel: pd.DataFrame, config: DataRoleConfig) -> pd.DataFrame:
    """Same-day, weekly-total and de-seasonalised correlation of each channel's cost with total sales."""
    y = panel["total_sales"]
    cost = paid_cost_frame(panel, config)
    corr_input = cost.join(y.rename("Total sales"))
    x_cal = calendar_design(pd.DatetimeIndex(panel.index))
    weekly = corr_input.resample("W-SUN").sum().loc[complete_weeks(pd.DatetimeIndex(panel.index))]
    resid = corr_input.apply(lambda s: residualize(s, x_cal))
    robust = pd.DataFrame(
        {
            "daily": corr_input.corr()["Total sales"].drop("Total sales"),
            "weekly": weekly.corr()["Total sales"].drop("Total sales"),
            "deseasonalised": resid.corr()["Total sales"].drop("Total sales"),
        }
    )
    prints = [c for c in config.print_event_channels if c in cost]
    if prints:
        lag3 = cost[prints[0]].shift(3)
        robust.loc[f"{prints[0]} (lag 3 days)"] = [
            y.corr(lag3),
            np.nan,
            pd.Series(residualize(y, x_cal)).iloc[3:].corr(pd.Series(residualize(lag3.fillna(0), x_cal)).iloc[3:]),
        ]
    return robust.sort_values("daily", ascending=False)


def vif_variants(panel: pd.DataFrame, config: DataRoleConfig) -> pd.DataFrame:
    """VIF of log1p(spend): daily, weekly, and daily with trend & calendar terms."""
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    cost = paid_cost_frame(panel, config)

    def vif_table(frame: pd.DataFrame) -> pd.Series:
        x = (frame - frame.mean()) / frame.std()
        x.insert(0, "const", 1.0)
        return pd.Series([variance_inflation_factor(x.values, i) for i in range(1, x.shape[1])], index=frame.columns)

    idx = pd.DatetimeIndex(panel.index)
    weekly_cost = cost.resample("W-SUN").sum().loc[complete_weeks(idx)]
    x_cal = calendar_design(idx)
    with_cal = pd.concat([np.log1p(cost), x_cal.drop(columns="const")], axis=1)
    with_cal = ((with_cal - with_cal.mean()) / with_cal.std()).fillna(0).assign(const=1.0)
    return pd.DataFrame(
        {
            "daily": vif_table(np.log1p(cost)),
            "weekly": vif_table(np.log1p(weekly_cost)),
            "with_calendar": [variance_inflation_factor(with_cal.values, i) for i in range(cost.shape[1])],
        }
    )


def readiness_diagnostics(panel: pd.DataFrame, config: DataRoleConfig) -> pd.DataFrame:
    """Per paid channel: activity, variability, seasonal share of spend, collinearity and lag."""
    y = panel["total_sales"]
    cost = paid_cost_frame(panel, config)
    x_cal = calendar_design(pd.DatetimeIndex(panel.index))
    rows = {}
    for ch in cost:
        s = cost[ch]
        log_s = np.log1p(s)
        fitted = log_s - residualize(log_s, x_cal)
        lag_r = [y.corr(s.shift(k)) for k in range(22)]
        rows[ch] = {
            "active_days": float((s > 0).mean()),
            "cv": float(s.std() / s.mean()) if s.mean() else float("nan"),
            "seasonal_r2": float(1 - np.var(log_s - fitted) / np.var(log_s)) if np.var(log_s) else float("nan"),
            "max_r_other": float(cost.drop(columns=ch).corrwith(s).abs().max()) if cost.shape[1] > 1 else float("nan"),
            "r_same_day": float(s.corr(y)),
            "r_best_lag": float(np.nanmax(lag_r)),
            "best_lag": int(np.nanargmax(lag_r)),
        }
    return pd.DataFrame(rows).T


def elasticities(panel: pd.DataFrame, config: DataRoleConfig) -> pd.DataFrame:
    """Raw and de-seasonalised log-log slopes of total sales on each digital channel's spend."""
    y = panel["total_sales"]
    cost = paid_cost_frame(panel, config)
    x_cal = calendar_design(pd.DatetimeIndex(panel.index))
    rows = {}
    for ch in cost:
        if ch in config.print_event_channels or (cost[ch] <= 0).any():
            continue
        lx, ly = np.log(cost[ch]), np.log(y)
        rows[ch] = {
            "raw": float(np.polyfit(lx, ly, 1)[0]),
            "deseasonalised": float(np.polyfit(residualize(lx, x_cal), residualize(ly, x_cal), 1)[0]),
        }
    return pd.DataFrame(rows).T


def stl_stats(y: pd.Series, days_since: pd.Series | None) -> dict[str, Any]:
    """STL with weekly period, 53-day seasonal and 91-day trend windows, plus variance shares."""
    from statsmodels.tsa.seasonal import STL

    stl = STL(y, period=7, seasonal=53, trend=91, robust=True).fit()
    out: dict[str, Any] = {
        "stl": stl,
        "strength_trend": max(0.0, 1 - np.var(stl.resid) / np.var(stl.trend + stl.resid)),
        "strength_weekly": max(0.0, 1 - np.var(stl.resid) / np.var(stl.seasonal + stl.resid)),
        "var_trend": float(stl.trend.var() / y.var()),
        "var_weekly": float(stl.seasonal.var() / y.var()),
        "var_resid": float(stl.resid.var() / y.var()),
    }
    if days_since is not None:
        top = stl.resid.nlargest(15).index
        out["top_resid_after_catalog"] = float((days_since.reindex(top) <= 6).mean())
    return out


def stationarity_table(y: pd.Series) -> list[dict[str, Any]]:
    """ADF and KPSS on sales, log sales and the weekly log-difference."""
    import warnings

    from statsmodels.tools.sm_exceptions import InterpolationWarning
    from statsmodels.tsa.stattools import adfuller, kpss

    rows = []
    log_y = np.log(np.maximum(y, 1.0))
    for name, s in (("total sales", y), ("log total sales", log_y), ("weekly difference of log sales", log_y.diff(7))):
        s = s.dropna()
        adf_stat, adf_p, *_ = adfuller(s, autolag="AIC")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InterpolationWarning)
            kpss_stat, kpss_p, *_ = kpss(s, regression="c", nlags="auto")
        rows.append(
            {
                "series": name,
                "adf_stat": float(adf_stat),
                "adf_p": float(adf_p),
                "kpss_stat": float(kpss_stat),
                "kpss_p": float(kpss_p),
                "verdict": "stationary" if adf_p < 0.05 and kpss_p > 0.05 else "non-stationary / ambiguous",
            }
        )
    return rows


def calendar_events(
    index: pd.DatetimeIndex,
    country: str = "DE",
    *,
    include_holidays: bool = True,
) -> dict[pd.Timestamp, str]:
    """Public holidays of `country` from Google Calendar plus retail events, restricted to
    the index."""
    from mmm_studio.core.features import black_friday

    years = sorted(set(index.year))
    events: dict[pd.Timestamp, str] = {}
    if include_holidays:
        from mmm_studio.core.holidays import HolidayCalendarError, holiday_events

        try:
            public = holiday_events(country, index)
            events = {pd.Timestamp(d): str(name) for d, name in zip(public["date"], public["name"], strict=False)}
        except HolidayCalendarError:
            events = {}
    for yr in years:
        bf = black_friday(yr)
        events[bf] = "Black Friday"
        events[bf + pd.Timedelta(days=3)] = "Cyber Monday"
        events[pd.Timestamp(yr, 12, 24)] = "Christmas Eve"
        events[pd.Timestamp(yr, 12, 31)] = "New Year's Eve"
    return dict(sorted((d, n) for d, n in events.items() if d in index))


def event_lifts(
    y: pd.Series,
    days_since: pd.Series | None,
    country: str = "DE",
    *,
    include_holidays: bool = True,
) -> pd.DataFrame:
    """Sales on each event vs the median of the same weekday in the surrounding ±4 weeks."""
    events = calendar_events(pd.DatetimeIndex(y.index), country, include_holidays=include_holidays)
    rows = []
    for d, name in events.items():
        refs = [d + pd.Timedelta(weeks=k) for k in range(-4, 5) if k != 0]
        refs = [r for r in refs if r in y.index and r not in events]
        base = float(y.loc[refs].median()) if refs else float("nan")
        dss = float(days_since.get(d, np.nan)) if days_since is not None else float("nan")
        rows.append({"date": d, "event": name, "sales": float(y[d]), "baseline": base, "lift": float(y[d]) / base - 1, "days_since_catalog": dss})
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def catalog_event_study(panel: pd.DataFrame) -> pd.DataFrame:
    """Mean total sales and weekday-adjusted lift by days since send-out."""
    y = panel["total_sales"]
    ds = panel["days_since_catalog"]
    idx = pd.DatetimeIndex(panel.index)
    base_mask = ds >= 14
    base = y[base_mask].groupby([idx[base_mask].year, idx[base_mask].dayofweek]).mean()
    keys = pd.MultiIndex.from_arrays([idx.year, idx.dayofweek])
    lift = y / base.reindex(keys).to_numpy() - 1
    window = ds.between(0, 27)
    ev = pd.DataFrame({"sales": y[window], "lift": lift[window], "k": ds[window].astype(int)})
    agg = ev.groupby("k").agg(mean=("sales", "mean"), sd=("sales", "std"), n=("sales", "size"), lift=("lift", "mean"))
    agg["ci"] = 1.96 * agg["sd"] / np.sqrt(agg["n"])
    agg.attrs["baseline_level"] = float(y[base_mask].mean())
    return agg


def catalog_halo_0_6(panel: pd.DataFrame, config: DataRoleConfig) -> tuple[pd.Series, float, float]:
    """Channel uplift in days 0–6 after a send-out vs ≥ 14 days after."""
    slug_map = config.slug_map()
    ds = panel["days_since_catalog"]
    in_window, base = ds.between(0, 6), ds >= 14
    sales = pd.DataFrame({ch: panel[f"sales_{slug_map[ch]}"] for ch in config.active_channels if f"sales_{slug_map[ch]}" in panel})
    uplift = sales[in_window].mean() / sales[base].mean() - 1
    total = float(panel.loc[in_window, "total_sales"].mean() / panel.loc[base, "total_sales"].mean() - 1)
    prints = [c for c in config.print_event_channels if c in uplift]
    cat = float(uplift[prints[0]]) if prints else float("nan")
    return uplift.drop(index=prints, errors="ignore"), total, cat


def catalog_decay(panel: pd.DataFrame, cat_sales_col: str) -> dict[str, Any]:
    """Catalog-attributed sales by day since send-out; days by which 50 % / 90 % of the excess is reached."""
    ds = panel["days_since_catalog"]
    curve = panel[cat_sales_col].groupby(ds).mean().loc[0:27]
    floor = float(panel.loc[ds >= 21, cat_sales_col].mean())
    excess = (curve - floor).clip(lower=0)
    cum = excess.cumsum() / excess.sum()
    return {
        "curve": curve,
        "floor": floor,
        "cum_share": cum,
        "half_day": int(cum[cum >= 0.5].index[0]),
        "p90_day": int(cum[cum >= 0.9].index[0]),
    }


def per_sendout_lifts(panel: pd.DataFrame, cost_col: str, impr_col: str | None) -> pd.DataFrame:
    """7-day lift after each send-out vs the 7 days before, with edition cost and circulation."""
    y = panel["total_sales"]
    sends = pd.DatetimeIndex(panel.index[panel[cost_col] > 0])
    rows = []
    for d in sends:
        before = y.loc[d - pd.Timedelta(days=7) : d - pd.Timedelta(days=1)].mean()
        after = y.loc[d : d + pd.Timedelta(days=6)].mean()
        rows.append(
            {
                "date": d,
                "cost": float(panel.loc[d, cost_col]),
                "circulation": float(panel.loc[d, impr_col]) if impr_col and impr_col in panel else float("nan"),
                "lift": float(after / before - 1) if before else float("nan"),
            }
        )
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def social_regime(panel: pd.DataFrame, break_date: pd.Timestamp) -> pd.DataFrame:
    """Daily means of Social Media spend, clicks, CTR and attributed sales before/after the break, within its year."""
    yr = break_date.year
    soc = pd.DataFrame(
        {
            "Spend": panel["cost_social_media"],
            "Clicks": panel["clicks_social_media"],
            "CTR": panel["clicks_social_media"] / panel["impr_social_media"].replace(0, np.nan),
            "Attributed sales": panel["sales_social_media"],
        }
    ).loc[f"{yr}-01-01" : f"{yr}-12-31"]
    before = soc.loc[: break_date - pd.Timedelta(days=1)].mean()
    after = soc.loc[break_date:].mean()
    return pd.DataFrame({"before": before, "after": after, "ratio": after / before})
