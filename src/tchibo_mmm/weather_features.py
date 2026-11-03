"""Weather features for Germany from Open-Meteo, and their link to total sales.

Tchibo sells nationwide, so the weather is a national index: one point per federal state (its
capital), weighted by the state's population. Daily values come from Open-Meteo's historical
archive (https://open-meteo.com/en/docs/historical-weather-api; ERA5 reanalysis, about 5 days
behind) and, for days the archive does not have yet, from its forecast API (up to 16 days ahead).
Archive days are cached in ``data/external`` so the analysis runs offline and gives the same
result every time. Fetching needs the ``weather`` dependency group (openmeteo-requests,
requests-cache, retry-requests); the cached analysis does not.

    python -m tchibo_mmm.weather_features            # use the cache if present
    python -m tchibo_mmm.weather_features --refresh  # fetch from Open-Meteo again
"""

import argparse
import time

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.multitest import multipletests

from tchibo_mmm.data import (
    PAID_CHANNELS,
    PROJECT_ROOT,
    build_wide_table,
    catalog_send_out_dates,
    load_raw,
    slug,
)
from tchibo_mmm.features import calendar_controls, days_since_send_out, seasonality_controls
from tchibo_mmm.plotting import (
    AXIS,
    INK_2,
    MUTED,
    SLOTS,
    SURFACE,
    TOTAL_COLOR,
    save,
    thousands,
    use_style,
)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEZONE = "Europe/Berlin"  # daily sums over German calendar days, like the sales
CACHE_PATH = PROJECT_ROOT / "data" / "external" / "open_meteo_de_daily.csv"
HTTP_CACHE = PROJECT_ROOT / ".cache" / "open_meteo"  # requests-cache SQLite (git-ignored)
FEATURES_PATH = PROJECT_ROOT / "data" / "processed" / "weather_features.csv"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

HOLDOUT_START = pd.Timestamp("2024-10-01")  # same Q4-2024 hold-out as the MMM
ARCHIVE_DELAY = pd.Timedelta(days=6)  # the archive is about 5 days behind
FORECAST_DAYS = 16
COLOR = SLOTS[0]

# State -> (capital, latitude, longitude, population). Population from statistikportal.de,
# "Fläche und Bevölkerung nach Ländern" (Zensus-2022 basis), retrieved November 2026.
STATES = {
    "Baden-Württemberg": ("Stuttgart", 48.7758, 9.1829, 11_238_761),
    "Bavaria": ("Munich", 48.1351, 11.5820, 13_245_503),
    "Berlin": ("Berlin", 52.5200, 13.4050, 3_700_577),
    "Brandenburg": ("Potsdam", 52.3906, 13.0645, 2_551_889),
    "Bremen": ("Bremen", 53.0793, 8.8017, 706_915),
    "Hamburg": ("Hamburg", 53.5511, 9.9937, 1_869_473),
    "Hesse": ("Wiesbaden", 50.0782, 8.2398, 6_278_594),
    "Lower Saxony": ("Hanover", 52.3759, 9.7320, 8_000_826),
    "Mecklenburg-Western Pomerania": ("Schwerin", 53.6355, 11.4012, 1_573_685),
    "North Rhine-Westphalia": ("Düsseldorf", 51.2277, 6.7735, 17_986_214),
    "Rhineland-Palatinate": ("Mainz", 49.9929, 8.2473, 4_123_022),
    "Saarland": ("Saarbrücken", 49.2402, 6.9969, 1_007_342),
    "Saxony": ("Dresden", 51.0504, 13.7373, 4_026_599),
    "Saxony-Anhalt": ("Magdeburg", 52.1205, 11.6276, 2_120_252),
    "Schleswig-Holstein": ("Kiel", 54.3233, 10.1228, 2_958_519),
    "Thuringia": ("Erfurt", 50.9848, 11.0299, 2_078_946),
}

# Requested daily variables. Up to 10 keep a request at the base call weight; the order fixes
# how the response's variables are read.
DAILY_VARIABLES = [
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "precipitation_hours",
    "sunshine_duration",
    "wind_speed_10m_max",
    "shortwave_radiation_sum",
]

# The feature set used in the analysis, with plot labels (unit = what "per unit" means).
GROUP_FEATURES = {
    "wx_temp_mean": "Mean temperature (°C)",
    "wx_temp_anomaly": "Temperature vs normal (°C)",
    "wx_temp_max": "Max temperature (°C)",
    "wx_hot_share": "Share with a summer day (max ≥ 25 °C)",
    "wx_frost_share": "Share with a frost day (min < 0 °C)",
    "wx_precip_mm": "Precipitation (mm)",
    "wx_precip_hours": "Hours with precipitation",
    "wx_rainy_share": "Share with a wet day (≥ 1 mm)",
    "wx_snow_cm": "Snowfall (cm)",
    "wx_sunshine_h": "Sunshine (h)",
    "wx_sunshine_anomaly": "Sunshine vs normal (h)",
    "wx_wind_max": "Max wind speed (km/h)",
}
# The anomaly block tested in the model check: weather surprises, not the seasonal cycle.
MODEL_FEATURES = [
    "wx_temp_anomaly",
    "wx_sunshine_anomaly",
    "wx_precip_mm",
    "wx_snow_cm",
    "wx_hot_share",
]
SEASONS = {
    "winter (DJF)": [12, 1, 2],
    "spring (MAM)": [3, 4, 5],
    "summer (JJA)": [6, 7, 8],
    "autumn (SON)": [9, 10, 11],
}


# --- fetching ---------------------------------------------------------------------------------


def _client(expire_after: int):
    """Open-Meteo client with an HTTP cache and retries, as in Open-Meteo's own examples."""
    import openmeteo_requests
    import requests_cache
    from retry_requests import retry

    HTTP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    session = requests_cache.CachedSession(str(HTTP_CACHE), expire_after=expire_after)
    return openmeteo_requests.Client(session=retry(session, retries=5, backoff_factor=0.2))


def _daily_frame(response) -> pd.DataFrame:
    """One location's daily block, indexed by German calendar day."""
    daily = response.Daily()
    stamps = pd.date_range(
        start=pd.to_datetime(daily.Time(), unit="s", utc=True),
        end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=daily.Interval()),
        inclusive="left",
    )
    # A fixed 24 h grid lands on 01:00 local time in summer; the calendar day is what counts.
    index = stamps.tz_convert(TIMEZONE).tz_localize(None).normalize().rename("date")
    values = {v: daily.Variables(i).ValuesAsNumpy() for i, v in enumerate(DAILY_VARIABLES)}
    return pd.DataFrame(values, index=index)


def fetch_states(url: str, start: pd.Timestamp, end: pd.Timestamp, client) -> pd.DataFrame:
    """Daily values per state in [start, end], one row per (date, state).

    One request per state: three years for one point already weigh ~80 of the free tier's 600
    calls a minute. When the minute's quota is used up, wait a minute and carry on.
    """
    from openmeteo_requests.Client import OpenMeteoRequestsError

    frames = []
    for state, (_, lat, lon, _) in STATES.items():
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": f"{start.date()}",
            "end_date": f"{end.date()}",
            "daily": DAILY_VARIABLES,
            "timezone": TIMEZONE,
        }
        for attempt in range(4):
            try:
                response = client.weather_api(url, params=params)[0]
                break
            except OpenMeteoRequestsError as err:
                if "limit exceeded" not in str(err) or attempt == 3:
                    raise
                time.sleep(61)
        frames.append(_daily_frame(response).assign(state=state))
    out = pd.concat(frames).reset_index()
    return out[["date", "state", *DAILY_VARIABLES]]


def load_weather(
    start: str = "2023-01-01", end: str = "2025-12-31", refresh: bool = False
) -> pd.DataFrame:
    """Daily values per state in [start, end], with a `source` column (archive / forecast).

    Archive days are read from the cache when it covers them; otherwise, or with `refresh`, they
    are fetched and cached. Days the archive does not have yet come from the forecast API (at
    most 16 days ahead) and are never cached.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    today = pd.Timestamp.today().normalize()
    archive_end = min(end, today - ARCHIVE_DELAY)
    parts = []
    if start <= archive_end:
        cached = pd.read_csv(CACHE_PATH, parse_dates=["date"]) if CACHE_PATH.exists() else None
        stale = cached is None or not (
            cached["date"].min() <= start and cached["date"].max() >= archive_end
        )
        if refresh or stale:
            cached = fetch_states(ARCHIVE_URL, start, archive_end, _client(expire_after=-1))
            cached["source"] = "archive"
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            cached.to_csv(CACHE_PATH, index=False)
        parts.append(cached[cached["date"].between(start, archive_end)])
    forecast_start = max(start, archive_end + pd.Timedelta(days=1))
    forecast_end = min(end, today + pd.Timedelta(days=FORECAST_DAYS - 1))
    if forecast_start <= forecast_end:
        fc = fetch_states(FORECAST_URL, forecast_start, forecast_end, _client(expire_after=3600))
        parts.append(fc.assign(source="forecast"))
    return pd.concat(parts, ignore_index=True)


# --- features ---------------------------------------------------------------------------------


def national_weather(states: pd.DataFrame) -> pd.DataFrame:
    """Population-weighted national daily values, plus the population share with a summer day
    (max >= 25 °C), a frost day (min < 0 °C), a wet day (>= 1 mm) or snowfall."""
    pop = states["state"].map({s: v[3] for s, v in STATES.items()})
    weight = pop / pop.groupby(states["date"]).transform("sum")
    flags = pd.DataFrame(
        {
            "hot_share": states["temperature_2m_max"] >= 25.0,
            "frost_share": states["temperature_2m_min"] < 0.0,
            "rainy_share": states["precipitation_sum"] >= 1.0,
            "snow_share": states["snowfall_sum"] > 0.0,
        }
    ).astype(float)
    values = states[DAILY_VARIABLES].join(flags).mul(weight, axis=0)
    national = values.groupby(states["date"]).sum()
    national["source"] = states.groupby("date")["source"].first()
    return national


def seasonal_normal(values: pd.Series, n_fourier: int = 3) -> pd.Series:
    """The value expected for the time of year: yearly Fourier fit over all days given."""
    X = sm.add_constant(seasonality_controls(values.index, n_fourier).drop(columns="trend"))
    return sm.OLS(values, X).fit().fittedvalues


def weather_features(index: pd.DatetimeIndex, national: pd.DataFrame) -> pd.DataFrame:
    """Daily features (prefix ``wx_``), one row per day of `index` (NaN where no weather).

    - temp_mean / temp_max / temp_min (°C), precip_mm, rain_mm, snow_cm, precip_hours,
      sunshine_h, wind_max (km/h), radiation (MJ/m²): population-weighted national values
    - hot_share / frost_share / rainy_share / snow_share: population share with that kind of day
    - temp_anomaly / sunshine_anomaly: the value minus its seasonal normal, so a warm spell in
      March counts, not March itself
    """
    nw = national
    out = pd.DataFrame(
        {
            "wx_temp_mean": nw["temperature_2m_mean"],
            "wx_temp_max": nw["temperature_2m_max"],
            "wx_temp_min": nw["temperature_2m_min"],
            "wx_precip_mm": nw["precipitation_sum"],
            "wx_rain_mm": nw["rain_sum"],
            "wx_snow_cm": nw["snowfall_sum"],
            "wx_precip_hours": nw["precipitation_hours"],
            "wx_sunshine_h": nw["sunshine_duration"] / 3600,
            "wx_wind_max": nw["wind_speed_10m_max"],
            "wx_radiation": nw["shortwave_radiation_sum"],
            "wx_hot_share": nw["hot_share"],
            "wx_frost_share": nw["frost_share"],
            "wx_rainy_share": nw["rainy_share"],
            "wx_snow_share": nw["snow_share"],
        }
    )
    out["wx_temp_anomaly"] = out["wx_temp_mean"] - seasonal_normal(out["wx_temp_mean"])
    out["wx_sunshine_anomaly"] = out["wx_sunshine_h"] - seasonal_normal(out["wx_sunshine_h"])
    return out.reindex(index).rename_axis("date")


# --- link to sales ----------------------------------------------------------------------------


def baseline_controls(
    index: pd.DatetimeIndex, send_outs: pd.DatetimeIndex, catalog_days: int = 21
) -> pd.DataFrame:
    """What drives sales besides the weather: weekday, holidays, Christmas, Black Week, yearly
    Fourier + trend, and one dummy per day 0..`catalog_days` after a catalog send-out.

    Media spend is left out on purpose: the weather cannot be caused by spend, and demand-led
    spend (SEA bids) would absorb part of a real weather effect.
    """
    out = calendar_controls(index).join(seasonality_controls(index))
    since = days_since_send_out(index, send_outs)
    for d in range(catalog_days + 1):
        out[f"catalog_day_{d}"] = (since == d).astype(float)
    return out


def _fit_hac(y: pd.Series, X: pd.DataFrame):
    X = sm.add_constant(X, has_constant="add")
    return sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 7})


def _residual(y: pd.Series, controls: pd.DataFrame) -> pd.Series:
    return sm.OLS(y, sm.add_constant(controls, has_constant="add")).fit().resid


def correlation_table(
    sales: pd.Series, feats: pd.DataFrame, controls: pd.DataFrame
) -> pd.DataFrame:
    """Raw and adjusted link of each weather feature with total sales.

    raw_r / raw_spearman: correlation with daily sales as they are (season included).
    partial_r: correlation of what the baseline controls do not explain, in both the feature and
    log sales (the weather surprise vs the sales surprise). partial_r_weekly: the same on weekly
    means of both residuals. partial_r_next_day: the feature one day before the sales.
    effect_per_sd: exp(beta * sd) - 1 when the feature alone is added to the baseline (log-OLS,
    HAC errors with 7 lags), for a one-SD weather surprise; effect_per_unit for one unit.
    q_value: Benjamini-Hochberg over the features tested.
    """
    log_sales = np.log(sales)
    resid_y = _residual(log_sales, controls)
    rows = []
    for col, label in GROUP_FEATURES.items():
        x = feats[col]
        resid_x = _residual(x, controls)
        weekly = pd.DataFrame({"x": resid_x, "y": resid_y}).resample("W-SUN").mean()
        fit = _fit_hac(log_sales, controls.join(x))
        beta, (lo, hi), sd = fit.params[col], fit.conf_int().loc[col], resid_x.std()
        rows.append(
            {
                "feature": col,
                "label": label,
                "raw_r": x.corr(sales),
                "raw_spearman": x.corr(sales, method="spearman"),
                "partial_r": resid_x.corr(resid_y),
                "partial_r_weekly": weekly["x"].corr(weekly["y"]),
                "partial_r_next_day": resid_x.shift(1).corr(resid_y),
                "surprise_sd": sd,
                "effect_per_unit": np.expm1(beta),
                "effect_per_sd": np.expm1(beta * sd),
                "effect_per_sd_lo": np.expm1(lo * sd),
                "effect_per_sd_hi": np.expm1(hi * sd),
                "p_value": fit.pvalues[col],
            }
        )
    table = pd.DataFrame(rows).set_index("feature")
    table["q_value"] = multipletests(table["p_value"], method="fdr_bh")[1]
    return table


def season_table(
    sales: pd.Series,
    feats: pd.DataFrame,
    controls: pd.DataFrame,
    columns: tuple[str, ...] = ("wx_temp_anomaly", "wx_sunshine_anomaly", "wx_precip_mm"),
) -> pd.DataFrame:
    """Partial correlation within each meteorological season (residuals from the full-year
    baseline), with a HAC p-value; a warm day may help sales in winter and hurt them in summer."""
    resid_y = _residual(np.log(sales), controls)
    rows = []
    for col in columns:
        resid_x = _residual(feats[col], controls)
        for season, months in SEASONS.items():
            on = sales.index.month.isin(months)
            fit = _fit_hac(resid_y[on], resid_x[on].rename(col))
            rows.append(
                {
                    "feature": col,
                    "season": season,
                    "days": int(on.sum()),
                    "partial_r": resid_x[on].corr(resid_y[on]),
                    "effect_per_sd": np.expm1(fit.params[col] * resid_x.std()),
                    "p_value": fit.pvalues[col],
                }
            )
    return pd.DataFrame(rows).set_index(["feature", "season"])


def robustness_table(
    sales: pd.Series,
    feats: pd.DataFrame,
    controls: pd.DataFrame,
    spend: pd.DataFrame,
    columns: tuple[str, ...] = (
        "wx_temp_anomaly",
        "wx_sunshine_anomaly",
        "wx_precip_mm",
        "wx_wind_max",
    ),
) -> pd.DataFrame:
    """Is the same-day link real?

    partial_r: as in `correlation_table`. placebo_7d / placebo_28d: the weather 7 / 28 days
    later, which cannot drive today's sales and should be ~0 (temperature spells last weeks, so
    its placebo is weaker evidence). with_spend: partial r when log paid spend joins the
    controls; a drop means part of the effect runs through demand-led spend such as SEA.
    """
    log_sales = np.log(sales)
    resid_y = _residual(log_sales, controls)
    resid_y_spend = _residual(log_sales, controls.join(spend))
    rows = []
    for col in columns:
        resid_x = _residual(feats[col], controls)
        rows.append(
            {
                "feature": col,
                "partial_r": resid_x.corr(resid_y),
                "placebo_7d": resid_x.shift(-7).corr(resid_y),
                "placebo_28d": resid_x.shift(-28).corr(resid_y),
                "with_spend": _residual(feats[col], controls.join(spend)).corr(resid_y_spend),
            }
        )
    return pd.DataFrame(rows).set_index("feature")


def model_check(sales: pd.Series, feats: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    """Does the weather help a log-OLS baseline? In-sample fit, a joint HAC Wald test of the
    weather block, and the Q4-2024 hold-out error (with the weather as it actually was)."""
    specs = {
        "baseline (calendar, season, catalog timing)": [],
        "+ weather surprises": MODEL_FEATURES,
    }
    log_sales = np.log(sales)
    train = sales.index < HOLDOUT_START
    test = (sales.index >= HOLDOUT_START) & (sales.index.year == 2024)
    rows = []
    for name, cols in specs.items():
        X = controls.join(feats[cols])
        full = _fit_hac(log_sales, X)
        Xc = sm.add_constant(X, has_constant="add")
        pred = np.exp(sm.OLS(log_sales[train], Xc[train]).fit().predict(Xc[test]))
        mae = (sales[test] - pred).abs().mean()
        wald = full.wald_test(", ".join(f"{c} = 0" for c in cols), scalar=True) if cols else None
        rows.append(
            {
                "spec": name,
                "columns": Xc.shape[1],
                "adj_r2": full.rsquared_adj,
                "aic": full.aic,
                "weather_wald_p": wald.pvalue if wald else np.nan,
                "holdout_mae_over_mean": mae / sales[test].mean(),
            }
        )
    return pd.DataFrame(rows).set_index("spec")


# --- figures ----------------------------------------------------------------------------------


def plot_correlation(corr: pd.DataFrame) -> plt.Figure:
    """Raw vs adjusted correlation per feature: how much of the link is just the season."""
    corr = corr.sort_values("partial_r")
    y = np.arange(len(corr))
    fig, ax = plt.subplots(figsize=(9, 0.36 * len(corr) + 1.4), layout="constrained")
    ax.hlines(y, corr["raw_r"], corr["partial_r"], color=AXIS, lw=1.2, zorder=1)
    ax.scatter(corr["raw_r"], y, s=46, color=MUTED, edgecolor=SURFACE, lw=1.5, zorder=2,
               label="raw (season included)")
    sig = (corr["q_value"] < 0.05).to_numpy()
    ax.scatter(corr["partial_r"][sig], y[sig], s=56, color=COLOR, edgecolor=SURFACE, lw=1.5,
               zorder=3, label="after calendar, season and catalog controls (q < 0.05)")
    ax.scatter(corr["partial_r"][~sig], y[~sig], s=56, facecolor=SURFACE, edgecolor=COLOR,
               lw=1.5, zorder=3, label="after controls, not significant")
    for i, v in enumerate(corr["partial_r"]):
        ha = "left" if v >= corr["raw_r"].iloc[i] else "right"
        ax.annotate(f"{v:+.2f}", (v, i), xytext=(7 if ha == "left" else -7, 0),
                    textcoords="offset points", ha=ha, va="center", fontsize=8, color=INK_2)
    ax.axvline(0, color=AXIS, lw=0.8)
    ax.set_yticks(y, corr["label"])
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.margins(x=0.12)
    ax.set_xlabel("Pearson r with daily total sales (adjusted: weather surprise vs log-sales "
                  "surprise)")
    ax.legend(loc="lower right", fontsize=8)
    raw_max, part_max = corr["raw_r"].abs().max(), corr["partial_r"].abs().max()
    ax.set_title(f"Weather vs sales: |r| up to {raw_max:.2f} raw, up to {part_max:.2f} once the "
                 "season is removed")
    return fig


def plot_temperature(sales: pd.Series, feats: pd.DataFrame, controls: pd.DataFrame) -> plt.Figure:
    """Left: sales vs temperature as they are. Right: sales vs normal against the temperature
    anomaly, as binned means (deciles) with 95 % intervals."""
    resid_y = _residual(np.log(sales), controls)
    anomaly = _residual(feats["wx_temp_anomaly"], controls)
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4), layout="constrained")

    left.scatter(feats["wx_temp_mean"], sales, s=9, color=TOTAL_COLOR, alpha=0.35, lw=0)
    thousands(left, unit="M", scale=1e6)
    left.set_xlabel("National mean temperature (°C, population-weighted)")
    left.set_ylabel("Daily total sales (€)")
    left.set_title(f"Raw: r = {feats['wx_temp_mean'].corr(sales):+.2f}")

    bins = pd.qcut(anomaly, 10)
    grouped = pd.DataFrame({"x": anomaly, "y": np.expm1(resid_y)}).groupby(bins, observed=True)
    mean, sem, x = grouped["y"].mean(), grouped["y"].sem(), grouped["x"].mean()
    right.axhline(0, color=AXIS, lw=0.8)
    right.errorbar(x, mean, yerr=1.96 * sem, color=COLOR, lw=2, marker="o", ms=6, mec=SURFACE,
                   mew=1.5, capsize=0, elinewidth=1.2)
    right.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:+.0%}"))
    right.set_xlabel("Temperature surprise: °C vs normal for the date, after controls (deciles)")
    right.set_ylabel("Sales vs expected")
    right.set_title(f"Adjusted: partial r = {anomaly.corr(resid_y):+.2f}")
    return fig


# --- CLI --------------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Open-Meteo weather features for Germany and their link to total sales."
    )
    parser.add_argument("--refresh", action="store_true", help="fetch from Open-Meteo again")
    parser.add_argument("--start", default="2023-01-01", help="first day of weather features")
    parser.add_argument("--end", default="2025-12-31", help="last day of weather features")
    args = parser.parse_args()

    raw = load_raw()
    wide = build_wide_table(raw)
    sales = wide["total_sales"]
    spend = np.log1p(wide[[f"cost_{slug(c)}" for c in PAID_CHANNELS]])
    send_outs = catalog_send_out_dates(raw)
    states = load_weather(args.start, args.end, refresh=args.refresh)
    national = national_weather(states)

    horizon = pd.date_range(national.index[0], national.index[-1], freq="D", name="date")
    weather_features(horizon, national).join(national["source"]).to_csv(FEATURES_PATH)
    feats = weather_features(sales.index, national)
    if feats[list(GROUP_FEATURES)].isna().any().any():
        missing = feats.index[feats.isna().any(axis=1)]
        raise ValueError(f"No weather for {len(missing)} sales days, e.g. {missing[0].date()}")
    controls = baseline_controls(sales.index, send_outs)

    corr = correlation_table(sales, feats, controls)
    seasons = season_table(sales, feats, controls)
    robust = robustness_table(sales, feats, controls, spend)
    check = model_check(sales, feats, controls)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    corr.to_csv(OUTPUT_DIR / "weather_correlation.csv")
    seasons.to_csv(OUTPUT_DIR / "weather_by_season.csv")
    robust.to_csv(OUTPUT_DIR / "weather_robustness.csv")
    check.to_csv(OUTPUT_DIR / "weather_model_check.csv")

    use_style()
    for name, fig in [
        ("weather_correlation", plot_correlation(corr)),
        ("weather_temperature", plot_temperature(sales, feats, controls)),
    ]:
        save(fig, name)
        plt.close(fig)

    print(
        f"{len(states)} state-days from Open-Meteo ({states['date'].min().date()} to "
        f"{states['date'].max().date()}), {len(STATES)} states, {len(sales)} sales days"
    )
    with pd.option_context("display.width", 200, "display.float_format", "{:,.3f}".format):
        print("\nCorrelation with total sales\n", corr.drop(columns=["label", "surprise_sd"]))
        print("\nBy season (partial r)\n", seasons)
        print("\nRobustness (placebo: future weather; with paid spend controlled)\n", robust)
        print("\nModel check (log-OLS)\n", check)


if __name__ == "__main__":
    main()
