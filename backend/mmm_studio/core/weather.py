"""National daily weather from Open-Meteo for the run's country, and the weather controls built from it.

A national business sells everywhere, so the weather is a national index: a few points per country (Germany: one per
federal state capital; elsewhere the largest metropolitan areas),
weighted by population. Daily values come from Open-Meteo's historical archive (ERA5 reanalysis, about five days behind;
https://open-meteo.com/en/docs/historical-weather-api) and, for the last few days the archive does not have yet, from its
forecast API. Fetched days are cached in the external-data cache directory.

The controls are weather *surprises*: each value minus its seasonal normal (a yearly Fourier fit), so a warm spell in
March counts, not March itself, and the yearly cycle stays with the model's Fourier terms. With leak-free validation the
normal is fitted on the days before the test window only (`normal_until`), so the weather of a scored window never
shapes the controls the model learns from. A forecast assumes normal weather: the surprises are 0 on future days.
"""

from __future__ import annotations

import json
import threading
from urllib.error import URLError
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from mmm_studio.core.holidays import cache_dir
from mmm_studio.core.model_settings import WEATHER_CONTROLS
from mmm_studio.core.net import error_detail, open_url

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_VARIABLES: tuple[str, ...] = ("temperature_2m_mean", "sunshine_duration", "precipitation_sum")
__all__ = ["WEATHER_CONTROLS", "WeatherError", "national_weather", "seasonal_normal", "weather_controls"]

# Country → (place, latitude, longitude, population in millions). Germany: the 16 state capitals weighted by their state's
# population (Zensus 2022); elsewhere the largest metropolitan areas (approximate populations).
LOCATIONS: dict[str, list[tuple[str, float, float, float]]] = {
    "DE": [
        ("Stuttgart", 48.7758, 9.1829, 11.239), ("Munich", 48.1351, 11.5820, 13.246), ("Berlin", 52.5200, 13.4050, 3.701),
        ("Potsdam", 52.3906, 13.0645, 2.552), ("Bremen", 53.0793, 8.8017, 0.707), ("Hamburg", 53.5511, 9.9937, 1.869),
        ("Wiesbaden", 50.0782, 8.2398, 6.279), ("Hanover", 52.3759, 9.7320, 8.001), ("Schwerin", 53.6355, 11.4012, 1.574),
        ("Düsseldorf", 51.2277, 6.7735, 17.986), ("Mainz", 49.9929, 8.2473, 4.123), ("Saarbrücken", 49.2402, 6.9969, 1.007),
        ("Dresden", 51.0504, 13.7373, 4.027), ("Magdeburg", 52.1205, 11.6276, 2.120), ("Kiel", 54.3233, 10.1228, 2.959),
        ("Erfurt", 50.9848, 11.0299, 2.079),
    ],
    "AT": [("Vienna", 48.2082, 16.3738, 1.98), ("Graz", 47.0707, 15.4395, 0.30), ("Linz", 48.3069, 14.2858, 0.21), ("Salzburg", 47.8095, 13.0550, 0.16), ("Innsbruck", 47.2692, 11.4041, 0.13)],
    "CH": [("Zurich", 47.3769, 8.5417, 1.40), ("Geneva", 46.2044, 6.1432, 0.60), ("Basel", 47.5596, 7.5886, 0.55), ("Bern", 46.9480, 7.4474, 0.42), ("Lausanne", 46.5197, 6.6323, 0.42)],
    "NL": [("Amsterdam", 52.3676, 4.9041, 1.15), ("Rotterdam", 51.9244, 4.4777, 1.00), ("The Hague", 52.0705, 4.3007, 0.80), ("Utrecht", 52.0907, 5.1214, 0.65), ("Eindhoven", 51.4416, 5.4697, 0.75), ("Groningen", 53.2194, 6.5665, 0.35)],
    "BE": [("Brussels", 50.8503, 4.3517, 1.20), ("Antwerp", 51.2194, 4.4025, 1.05), ("Ghent", 51.0543, 3.7174, 0.55), ("Liège", 50.6326, 5.5797, 0.75), ("Charleroi", 50.4108, 4.4446, 0.42)],
    "LU": [("Luxembourg", 49.6116, 6.1319, 0.13), ("Esch-sur-Alzette", 49.4958, 5.9806, 0.04)],
    "FR": [("Paris", 48.8566, 2.3522, 12.3), ("Lyon", 45.7640, 4.8357, 2.3), ("Marseille", 43.2965, 5.3698, 1.9), ("Toulouse", 43.6047, 1.4442, 1.4), ("Lille", 50.6292, 3.0573, 1.5), ("Bordeaux", 44.8378, -0.5792, 1.3), ("Nantes", 47.2184, -1.5536, 1.0), ("Strasbourg", 48.5734, 7.7521, 0.8)],
    "IT": [("Milan", 45.4642, 9.1900, 4.3), ("Rome", 41.9028, 12.4964, 4.3), ("Naples", 40.8518, 14.2681, 3.1), ("Turin", 45.0703, 7.6869, 1.7), ("Palermo", 38.1157, 13.3615, 1.2), ("Bologna", 44.4949, 11.3426, 1.0), ("Florence", 43.7696, 11.2558, 1.0), ("Bari", 41.1171, 16.8719, 1.2)],
    "ES": [("Madrid", 40.4168, -3.7038, 6.7), ("Barcelona", 41.3874, 2.1686, 5.6), ("Valencia", 39.4699, -0.3763, 1.6), ("Seville", 37.3891, -5.9845, 1.5), ("Bilbao", 43.2630, -2.9350, 1.0), ("Málaga", 36.7213, -4.4214, 1.0), ("Zaragoza", 41.6488, -0.8891, 0.8)],
    "PT": [("Lisbon", 38.7223, -9.1393, 2.9), ("Porto", 41.1579, -8.6291, 1.7), ("Braga", 41.5454, -8.4265, 0.2), ("Coimbra", 40.2033, -8.4103, 0.15), ("Faro", 37.0194, -7.9322, 0.12)],
    "GB": [("London", 51.5072, -0.1276, 9.5), ("Manchester", 53.4808, -2.2426, 2.8), ("Birmingham", 52.4862, -1.8904, 2.6), ("Leeds", 53.8008, -1.5491, 1.9), ("Glasgow", 55.8642, -4.2518, 1.7), ("Newcastle", 54.9783, -1.6178, 1.1), ("Bristol", 51.4545, -2.5879, 0.7), ("Cardiff", 51.4816, -3.1791, 0.5), ("Belfast", 54.5973, -5.9301, 0.6)],
    "IE": [("Dublin", 53.3498, -6.2603, 1.45), ("Cork", 51.8985, -8.4756, 0.30), ("Limerick", 52.6638, -8.6267, 0.10), ("Galway", 53.2707, -9.0568, 0.09)],
    "DK": [("Copenhagen", 55.6761, 12.5683, 1.40), ("Aarhus", 56.1629, 10.2039, 0.36), ("Odense", 55.4038, 10.4024, 0.20), ("Aalborg", 57.0488, 9.9217, 0.14)],
    "SE": [("Stockholm", 59.3293, 18.0686, 2.40), ("Gothenburg", 57.7089, 11.9746, 1.10), ("Malmö", 55.6050, 13.0038, 0.75), ("Uppsala", 59.8586, 17.6389, 0.24), ("Umeå", 63.8258, 20.2630, 0.13)],
    "NO": [("Oslo", 59.9139, 10.7522, 1.10), ("Bergen", 60.3913, 5.3221, 0.29), ("Trondheim", 63.4305, 10.3951, 0.21), ("Stavanger", 58.9700, 5.7331, 0.24), ("Tromsø", 69.6492, 18.9553, 0.08)],
    "FI": [("Helsinki", 60.1699, 24.9384, 1.50), ("Tampere", 61.4978, 23.7610, 0.40), ("Turku", 60.4518, 22.2666, 0.33), ("Oulu", 65.0121, 25.4651, 0.21)],
    "PL": [("Warsaw", 52.2297, 21.0122, 3.1), ("Kraków", 50.0647, 19.9450, 1.5), ("Katowice", 50.2649, 19.0238, 2.2), ("Łódź", 51.7592, 19.4560, 1.0), ("Wrocław", 51.1079, 17.0385, 1.2), ("Poznań", 52.4064, 16.9252, 1.0), ("Gdańsk", 54.3520, 18.6466, 1.1)],
    "CZ": [("Prague", 50.0755, 14.4378, 2.2), ("Brno", 49.1951, 16.6068, 0.7), ("Ostrava", 49.8209, 18.2625, 0.5), ("Plzeň", 49.7384, 13.3736, 0.3)],
    "SK": [("Bratislava", 48.1486, 17.1077, 0.70), ("Košice", 48.7164, 21.2611, 0.37), ("Žilina", 49.2231, 18.7394, 0.16), ("Banská Bystrica", 48.7395, 19.1535, 0.11)],
    "HU": [("Budapest", 47.4979, 19.0402, 3.0), ("Debrecen", 47.5316, 21.6273, 0.20), ("Szeged", 46.2530, 20.1414, 0.16), ("Miskolc", 48.1035, 20.7784, 0.15), ("Pécs", 46.0727, 18.2323, 0.14), ("Győr", 47.6875, 17.6504, 0.13)],
    "RO": [("Bucharest", 44.4268, 26.1025, 2.3), ("Cluj-Napoca", 46.7712, 23.6236, 0.42), ("Iași", 47.1585, 27.6014, 0.38), ("Timișoara", 45.7489, 21.2087, 0.32), ("Constanța", 44.1598, 28.6348, 0.30), ("Craiova", 44.3302, 23.7949, 0.27), ("Brașov", 45.6427, 25.5887, 0.25)],
    "BG": [("Sofia", 42.6977, 23.3219, 1.5), ("Plovdiv", 42.1354, 24.7453, 0.35), ("Varna", 43.2141, 27.9147, 0.34), ("Burgas", 42.5048, 27.4626, 0.20)],
    "GR": [("Athens", 37.9838, 23.7275, 3.8), ("Thessaloniki", 40.6401, 22.9444, 1.1), ("Patras", 38.2466, 21.7346, 0.2), ("Heraklion", 35.3387, 25.1442, 0.2)],
    "HR": [("Zagreb", 45.8150, 15.9819, 0.80), ("Split", 43.5081, 16.4402, 0.25), ("Rijeka", 45.3271, 14.4422, 0.20), ("Osijek", 45.5550, 18.6955, 0.10)],
    "SI": [("Ljubljana", 46.0569, 14.5058, 0.29), ("Maribor", 46.5547, 15.6459, 0.11), ("Celje", 46.2397, 15.2677, 0.05), ("Koper", 45.5481, 13.7302, 0.03)],
    "TR": [("Istanbul", 41.0082, 28.9784, 15.5), ("Ankara", 39.9334, 32.8597, 5.7), ("Izmir", 38.4237, 27.1428, 4.4), ("Bursa", 40.1885, 29.0610, 3.1), ("Antalya", 36.8969, 30.7133, 2.6), ("Adana", 37.0000, 35.3213, 2.3)],
    "US": [("New York", 40.7128, -74.0060, 19.6), ("Los Angeles", 34.0522, -118.2437, 13.0), ("Chicago", 41.8781, -87.6298, 9.4), ("Dallas", 32.7767, -96.7970, 7.6), ("Houston", 29.7604, -95.3698, 7.1), ("Atlanta", 33.7490, -84.3880, 6.1), ("Washington", 38.9072, -77.0369, 6.3), ("Miami", 25.7617, -80.1918, 6.1), ("Philadelphia", 39.9526, -75.1652, 6.2), ("Phoenix", 33.4484, -112.0740, 4.9), ("Boston", 42.3601, -71.0589, 4.9), ("Seattle", 47.6062, -122.3321, 4.0), ("Denver", 39.7392, -104.9903, 2.9), ("Minneapolis", 44.9778, -93.2650, 3.7)],
    "CA": [("Toronto", 43.6532, -79.3832, 6.2), ("Montreal", 45.5017, -73.5673, 4.3), ("Vancouver", 49.2827, -123.1207, 2.6), ("Calgary", 51.0447, -114.0719, 1.5), ("Edmonton", 53.5461, -113.4938, 1.4), ("Ottawa", 45.4215, -75.6972, 1.4), ("Winnipeg", 49.8951, -97.1384, 0.8), ("Halifax", 44.6488, -63.5752, 0.5)],
    "MX": [("Mexico City", 19.4326, -99.1332, 21.8), ("Guadalajara", 20.6597, -103.3496, 5.3), ("Monterrey", 25.6866, -100.3161, 5.3), ("Puebla", 19.0414, -98.2063, 3.2), ("Tijuana", 32.5149, -117.0382, 2.2), ("Mérida", 20.9674, -89.5926, 1.3)],
    "BR": [("São Paulo", -23.5505, -46.6333, 22.0), ("Rio de Janeiro", -22.9068, -43.1729, 13.6), ("Belo Horizonte", -19.9167, -43.9345, 6.0), ("Brasília", -15.7939, -47.8828, 4.8), ("Porto Alegre", -30.0346, -51.2177, 4.3), ("Recife", -8.0476, -34.8770, 4.1), ("Fortaleza", -3.7319, -38.5267, 4.1), ("Salvador", -12.9777, -38.5016, 3.9), ("Curitiba", -25.4284, -49.2733, 3.7), ("Manaus", -3.1190, -60.0217, 2.3)],
    "AU": [("Sydney", -33.8688, 151.2093, 5.3), ("Melbourne", -37.8136, 144.9631, 5.2), ("Brisbane", -27.4698, 153.0251, 2.6), ("Perth", -31.9505, 115.8605, 2.2), ("Adelaide", -34.9285, 138.6007, 1.4)],
    "NZ": [("Auckland", -36.8485, 174.7633, 1.70), ("Wellington", -41.2865, 174.7762, 0.43), ("Christchurch", -43.5321, 172.6362, 0.39), ("Hamilton", -37.7870, 175.2793, 0.18)],
    "IN": [("Delhi", 28.7041, 77.1025, 32.0), ("Mumbai", 19.0760, 72.8777, 21.0), ("Kolkata", 22.5726, 88.3639, 15.0), ("Bengaluru", 12.9716, 77.5946, 13.0), ("Chennai", 13.0827, 80.2707, 11.5), ("Hyderabad", 17.3850, 78.4867, 10.5), ("Ahmedabad", 23.0225, 72.5714, 8.5), ("Pune", 18.5204, 73.8567, 7.0)],
    "JP": [("Tokyo", 35.6762, 139.6503, 37.0), ("Osaka", 34.6937, 135.5023, 19.0), ("Nagoya", 35.1815, 136.9066, 9.5), ("Fukuoka", 33.5904, 130.4017, 5.5), ("Sapporo", 43.0618, 141.3545, 2.6), ("Sendai", 38.2682, 140.8694, 2.3)],
    "ZA": [("Johannesburg", -26.2041, 28.0473, 6.2), ("Cape Town", -33.9249, 18.4241, 4.8), ("Durban", -29.8587, 31.0218, 3.9), ("Pretoria", -25.7479, 28.2293, 2.8), ("Gqeberha", -33.9608, 25.6022, 1.2)],
}

_LOCK = threading.RLock()


class WeatherError(RuntimeError):
    """Open-Meteo could not be read, or has no values for some days (message shown to users)."""


def _get(url: str, params: dict[str, object]) -> object:
    with open_url(f"{url}?{urlencode(params)}", timeout=60) as resp:
        return json.load(resp)


def _fetch(url: str, country: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Daily values per place, as a long table (date, place, variables…)."""
    places = LOCATIONS[country]
    payload = _get(
        url,
        {
            "latitude": ",".join(f"{lat:.4f}" for _, lat, _, _ in places),
            "longitude": ",".join(f"{lon:.4f}" for _, _, lon, _ in places),
            "start_date": f"{start:%Y-%m-%d}",
            "end_date": f"{end:%Y-%m-%d}",
            "daily": ",".join(DAILY_VARIABLES),
            "timezone": "auto",
        },
    )
    blocks = payload if isinstance(payload, list) else [payload]
    frames = []
    for (name, _, _, _), block in zip(places, blocks, strict=True):
        daily = block["daily"]
        frame = pd.DataFrame({v: daily.get(v) for v in DAILY_VARIABLES})
        frame.insert(0, "place", name)
        frame.insert(0, "date", pd.to_datetime(daily["time"]))
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def national_weather(country: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Population-weighted national daily temperature (°C), sunshine (h) and precipitation (mm) for [start, end]."""
    country = country.upper()
    if country not in LOCATIONS:
        raise WeatherError(f"There are no weather locations configured for '{country}'.")
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    path = cache_dir() / f"open_meteo_{country.lower()}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    with _LOCK:
        if path.exists():
            return pd.read_csv(path, parse_dates=["date"]).set_index("date")
        try:
            raw = _fetch(ARCHIVE_URL, country, start, end)
            missing = raw.groupby("date")[list(DAILY_VARIABLES)].apply(lambda g: g.isna().any().any())
            recent = missing[missing].index
            if len(recent):
                # The archive is about five days behind; the forecast API has the recent past
                fresh = _fetch(FORECAST_URL, country, recent.min(), min(recent.max(), end))
                raw = raw.set_index(["date", "place"])
                raw.update(fresh.set_index(["date", "place"]))
                raw = raw.reset_index()
        except (URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            raise WeatherError(
                f"Open-Meteo's weather for {country} could not be read ({error_detail(exc)}). Try again, or switch "
                "the weather controls off in the run settings."
            ) from exc
        weights = {name: pop for name, _, _, pop in LOCATIONS[country]}
        raw["w"] = raw["place"].map(weights)
        values = raw[list(DAILY_VARIABLES)].mul(raw["w"], axis=0)
        values["date"] = raw["date"]
        total_w = raw.groupby("date")["w"].sum()
        national = values.groupby("date")[list(DAILY_VARIABLES)].sum().div(total_w, axis=0)
        national = national.reindex(pd.date_range(start, end, freq="D", name="date"))
        if national.isna().any().any():
            bad = national.index[national.isna().any(axis=1)]
            raise WeatherError(
                f"Open-Meteo has no weather for {len(bad)} day(s) (e.g. {', '.join(f'{d:%Y-%m-%d}' for d in bad[:3])}); "
                "switch the weather controls off for this run."
            )
        national = national.rename(
            columns={"temperature_2m_mean": "temp_mean", "sunshine_duration": "sunshine_h", "precipitation_sum": "precip_mm"}
        )
        national["sunshine_h"] = national["sunshine_h"] / 3600.0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            national.reset_index().to_csv(path, index=False)
        except OSError:
            pass
        return national


def seasonal_normal(values: pd.Series, n_fourier: int = 3, fit_until: pd.Timestamp | None = None) -> pd.Series:
    """The value expected for the time of year: a constant plus yearly Fourier terms, fitted over the days given up to
    `fit_until` (all of them by default) and evaluated on every day."""
    idx = pd.DatetimeIndex(values.index)
    t = idx.dayofyear.to_numpy() / 365.25
    cols = [np.ones(len(idx))]
    for k in range(1, n_fourier + 1):
        cols += [np.sin(2 * np.pi * k * t), np.cos(2 * np.pi * k * t)]
    x = np.column_stack(cols)
    fit = np.ones(len(idx), dtype=bool) if fit_until is None else np.asarray(idx <= pd.Timestamp(fit_until))
    beta, *_ = np.linalg.lstsq(x[fit], values.to_numpy(dtype=float)[fit], rcond=None)
    return pd.Series(x @ beta, index=idx)


def weather_controls(country: str, index: pd.DatetimeIndex, normal_until: pd.Timestamp | None = None) -> pd.DataFrame:
    """The weather controls on `index`: temperature, sunshine and precipitation minus their seasonal normal, fitted on
    the days up to `normal_until` (the day before the test window under leak-free validation; every day otherwise)."""
    idx = pd.DatetimeIndex(index)
    nw = national_weather(country, idx.min(), idx.max()).reindex(idx)
    return pd.DataFrame(
        {
            "wx_temp_anomaly": nw["temp_mean"] - seasonal_normal(nw["temp_mean"], fit_until=normal_until),
            "wx_sunshine_anomaly": nw["sunshine_h"] - seasonal_normal(nw["sunshine_h"], fit_until=normal_until),
            "wx_precip_anomaly": nw["precip_mm"] - seasonal_normal(nw["precip_mm"], fit_until=normal_until),
        },
        index=idx,
    ).round(4)
