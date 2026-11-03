"""Holiday & observance features from Google Calendar's public "Holidays in Germany" calendar.

The events come from the Calendar API v3: `events.list` on the public holiday calendar
(https://developers.google.com/workspace/calendar/api/concepts/events-calendars). Reading a
public calendar needs an API key (``GOOGLE_API_KEY``). Without one, the calendar's public iCal
export is read instead; it carries the same events with the same ids. Fetched events are cached
in ``data/external`` so the analysis runs offline and gives the same result every time.

    python -m tchibo_mmm.holiday_features            # use the cache if present
    python -m tchibo_mmm.holiday_features --refresh  # fetch from Google again
"""

import argparse
import json
import os
import re
from urllib.parse import quote, urlencode
from urllib.request import urlopen

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

from tchibo_mmm.data import PROJECT_ROOT, build_wide_table, catalog_send_out_dates, load_raw
from tchibo_mmm.features import calendar_controls, days_since_send_out, seasonality_controls
from tchibo_mmm.plotting import AXIS, INK_2, SLOTS, save, use_style

CALENDAR_ID = "en.german#holiday@group.v.calendar.google.com"
API_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar}/events"
ICAL_URL = "https://calendar.google.com/calendar/ical/{calendar}/public/basic.ics"
CACHE_PATH = PROJECT_ROOT / "data" / "external" / "google_holidays_de.csv"
FEATURES_PATH = PROJECT_ROOT / "data" / "processed" / "holiday_features.csv"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

HOLDOUT_START = pd.Timestamp("2024-10-01")  # same Q4-2024 hold-out as the MMM
POSITIVE, NEGATIVE = SLOTS[0], SLOTS[7]

# Population per state in millions (approx. 2023). Weights a regional holiday by the share of
# Germany that has the day off.
STATE_POPULATION = {
    "Baden-Württemberg": 11.2,
    "Bavaria": 13.4,
    "Berlin": 3.8,
    "Brandenburg": 2.6,
    "Bremen": 0.7,
    "Hamburg": 1.9,
    "Hesse": 6.4,
    "Lower Saxony": 8.1,
    "Mecklenburg-Western Pomerania": 1.6,
    "North Rhine-Westphalia": 18.1,
    "Rhineland-Palatinate": 4.2,
    "Saarland": 1.0,
    "Saxony": 4.1,
    "Saxony-Anhalt": 2.2,
    "Schleswig-Holstein": 2.9,
    "Thuringia": 2.1,
}
# Google lists all 16 states for this day. By law it is a day off only in Berlin and, since
# 2023, in Mecklenburg-Western Pomerania.
STATE_OVERRIDES = {"International Women's Day": ["Berlin", "Mecklenburg-Western Pomerania"]}

# The compact feature set used in the analysis (per-event dummies are extra columns).
GROUP_FEATURES = [
    "gcal_public_holiday",
    "gcal_half_day",
    "gcal_regional_share",
    "gcal_observance",
    "gcal_eve",
    "gcal_after",
    "gcal_bridge_day",
    "gcal_advent",
]


# --- fetching ---------------------------------------------------------------------------------


def _event_record(event_id: str, day, summary: str, description: str, source: str) -> dict:
    """Normalise one event. The description's first line names the kind and the states."""
    first, _, rest = description.partition("\n")
    name = re.sub(r" \((regional holiday|half-day)\)$", "", summary)
    if first.startswith("Public holiday in "):
        kind, states = "regional", first.removeprefix("Public holiday in ").split(", ")
    elif first == "Public holiday":
        kind, states = ("half_day" if "half-day" in rest else "public"), list(STATE_POPULATION)
    else:  # "Observance" or "Observance in <states>": no day off
        kind = "observance"
        listed = first.removeprefix("Observance").removeprefix(" in ")
        states = listed.split(", ") if listed else list(STATE_POPULATION)
    states = STATE_OVERRIDES.get(name, states)
    share = sum(STATE_POPULATION[s] for s in states) / sum(STATE_POPULATION.values())
    return {
        "date": pd.Timestamp(str(day)[:10]),
        "event_id": event_id,
        "name": name,
        "summary": summary,
        "kind": kind,
        "states": "; ".join(states),
        "pop_share": round(share, 4),
        "source": source,
    }


def fetch_events_api(
    start: pd.Timestamp, end: pd.Timestamp, api_key: str, calendar_id: str = CALENDAR_ID
) -> list[dict]:
    """`events.list` on the public calendar: all-day events in [start, end], oldest first."""
    url = API_URL.format(calendar=quote(calendar_id, safe=""))
    params = {
        "key": api_key,
        "timeMin": f"{start.date()}T00:00:00Z",
        "timeMax": f"{(end + pd.Timedelta(days=1)).date()}T00:00:00Z",
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 2500,
    }
    items, token = [], None
    while True:
        query = urlencode(params | ({"pageToken": token} if token else {}))
        with urlopen(f"{url}?{query}", timeout=30) as resp:
            page = json.load(resp)
        items += page.get("items", [])
        token = page.get("nextPageToken")
        if not token:
            break
    return [
        _event_record(
            it["id"], it["start"]["date"], it["summary"], it.get("description", ""), "api"
        )
        for it in items
        if it.get("status") != "cancelled"
    ]


def fetch_events_ical(
    start: pd.Timestamp, end: pd.Timestamp, calendar_id: str = CALENDAR_ID
) -> list[dict]:
    """The same calendar's public iCal export (no key needed). Its UIDs are the API event ids."""
    url = ICAL_URL.format(calendar=quote(calendar_id, safe=""))
    with urlopen(url, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    text = text.replace("\r\n", "\n").replace("\n ", "")  # unfold long lines (RFC 5545, 3.1)
    records = []
    for block in re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", text, flags=re.DOTALL):
        fields = {}
        for line in block.split("\n"):
            key, _, value = line.partition(":")
            fields[key.split(";")[0]] = value
        day = pd.Timestamp(fields["DTSTART"][:8])
        if not start <= day <= end or fields.get("STATUS") == "CANCELLED":
            continue
        description = fields.get("DESCRIPTION", "")
        for escaped, char in [("\\n", "\n"), ("\\,", ","), ("\\;", ";"), ("\\\\", "\\")]:
            description = description.replace(escaped, char)
        uid = fields["UID"].split("@")[0]
        records.append(_event_record(uid, day, fields["SUMMARY"], description, "ical"))
    return records


def load_google_holidays(
    start: str = "2023-01-01",
    end: str = "2025-12-31",
    refresh: bool = False,
    api_key: str | None = None,
) -> pd.DataFrame:
    """One row per (date, event) in [start, end].

    Read from the cache when it covers every requested year. Otherwise, or with `refresh`, fetch
    from Google: the API when a key is given (or ``GOOGLE_API_KEY`` is set), else the iCal export.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    years = set(range(start.year, end.year + 1))
    events = pd.read_csv(CACHE_PATH, parse_dates=["date"]) if CACHE_PATH.exists() else None
    if refresh or events is None or not years <= set(events["date"].dt.year):
        api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        records = (
            fetch_events_api(start, end, api_key) if api_key else fetch_events_ical(start, end)
        )
        events = pd.DataFrame(records).sort_values(["date", "name"]).reset_index(drop=True)
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        events.to_csv(CACHE_PATH, index=False)
    return events[events["date"].between(start, end)].reset_index(drop=True)


# --- features ---------------------------------------------------------------------------------


def _event_slug(name: str) -> str:
    """'St. Martin's Day' -> 'st_martins_day'."""
    return re.sub(r"[^a-z0-9]+", "_", re.sub(r"['’]", "", name.lower())).strip("_")


def holiday_features(index: pd.DatetimeIndex, events: pd.DataFrame) -> pd.DataFrame:
    """Daily features (prefix ``gcal_``), one row per day of `index`.

    - public_holiday / half_day: nationwide day off (full / half: Christmas Eve, New Year's Eve)
    - regional_share: population share with a regional day off (0 when none)
    - observance: a non-holiday observance (Valentine's, Mother's Day, Advent Sundays, ...)
    - eve / after: the day before / after a nationwide day off, unless it is a holiday itself
    - bridge_day: Friday after a Thursday holiday, Monday before a Tuesday holiday
    - advent: First Advent Sunday up to 23 December
    - evt_<name>: one dummy per event
    """
    out = pd.DataFrame(index=index)
    dates = {k: pd.DatetimeIndex(g["date"].unique()) for k, g in events.groupby("kind")}
    public = dates.get("public", pd.DatetimeIndex([]))
    off = public.union(dates.get("half_day", pd.DatetimeIndex([])))
    day = pd.Timedelta(days=1)

    out["gcal_public_holiday"] = index.isin(public).astype(float)
    out["gcal_half_day"] = index.isin(dates.get("half_day", [])).astype(float)
    regional = events[events["kind"] == "regional"].groupby("date")["pop_share"].max()
    out["gcal_regional_share"] = regional.reindex(index, fill_value=0.0).to_numpy()
    out["gcal_observance"] = index.isin(dates.get("observance", [])).astype(float)
    holiday = index.isin(off) | index.isin(regional.index)
    out["gcal_eve"] = (index.isin(off - day) & ~holiday).astype(float)
    out["gcal_after"] = (index.isin(off + day) & ~holiday).astype(float)
    bridge = (public[public.dayofweek == 3] + day).union(public[public.dayofweek == 1] - day)
    out["gcal_bridge_day"] = (index.isin(bridge) & ~holiday).astype(float)
    advent = np.zeros(len(index), dtype=bool)
    for first in events.loc[events["name"] == "First Advent Sunday", "date"]:
        advent |= (index >= first) & (index < pd.Timestamp(year=first.year, month=12, day=24))
    out["gcal_advent"] = advent.astype(float)
    for name, g in events.groupby("name"):
        out[f"gcal_evt_{_event_slug(name)}"] = index.isin(g["date"]).astype(float)
    return out


# --- link to sales ----------------------------------------------------------------------------


def baseline_controls(
    index: pd.DatetimeIndex, send_outs: pd.DatetimeIndex, catalog_days: int = 21
) -> pd.DataFrame:
    """Everything but holidays: weekday, Black Week, yearly Fourier + trend, catalog timing.

    The catalog is the main driver of demand peaks, so each of the first `catalog_days` days
    after a send-out gets a dummy; otherwise a holiday next to a catalog peak looks like a lift.
    """
    out = calendar_controls(index).drop(columns=["holiday", "christmas", "half_day"])
    out = out.join(seasonality_controls(index))
    since = days_since_send_out(index, send_outs)
    for d in range(catalog_days + 1):
        out[f"catalog_day_{d}"] = (since == d).astype(float)
    return out


def _fit_hac(log_sales: pd.Series, X: pd.DataFrame):
    X = sm.add_constant(X, has_constant="add")
    return sm.OLS(log_sales, X).fit(cov_type="HAC", cov_kwds={"maxlags": 7})


def correlation_table(
    sales: pd.Series, feats: pd.DataFrame, controls: pd.DataFrame
) -> pd.DataFrame:
    """Raw and adjusted link of each grouped feature with total sales.

    raw_r: Pearson r with sales. partial_r: r with the residual of log sales after the baseline
    controls. lift: exp(beta) - 1 when the feature alone is added to the baseline (log-OLS, HAC
    errors with 7 lags). For regional_share the lift is for 100 % of the population off.
    """
    log_sales = np.log(sales)
    resid = _fit_hac(log_sales, controls).resid
    rows = []
    for col in GROUP_FEATURES:
        x = feats[col]
        fit = _fit_hac(log_sales, controls.join(x))
        lo, hi = fit.conf_int().loc[col]
        on = x > 0
        rows.append(
            {
                "feature": col,
                "days": int(on.sum()),
                "mean_sales_on": sales[on].mean(),
                "mean_sales_off": sales[~on].mean(),
                "raw_r": x.corr(sales),
                "partial_r": x.corr(resid),
                "lift": np.expm1(fit.params[col]),
                "lift_lo": np.expm1(lo),
                "lift_hi": np.expm1(hi),
                "p_value": fit.pvalues[col],
            }
        )
    return pd.DataFrame(rows).set_index("feature")


def expected_sales(sales: pd.Series, events: pd.DataFrame, controls: pd.DataFrame) -> pd.Series:
    """Counterfactual: the baseline fitted on days without any Google event, predicted for all."""
    X = sm.add_constant(controls, has_constant="add")
    normal = ~sales.index.isin(events["date"])
    fit = sm.OLS(np.log(sales[normal]), X[normal]).fit()
    return np.exp(fit.predict(X))


def same_weekday_baseline(sales: pd.Series, day: pd.Timestamp, exclude, weeks: int = 4) -> float:
    """Median sales of the same weekday in the surrounding ±`weeks` weeks, event days excluded."""
    refs = [day + pd.Timedelta(weeks=k) for k in range(-weeks, weeks + 1) if k]
    return sales.loc[[r for r in refs if r in sales.index and r not in exclude]].median()


def event_lift_table(
    sales: pd.Series, events: pd.DataFrame, expected: pd.Series, send_outs: pd.DatetimeIndex
) -> pd.DataFrame:
    """One row per event occurrence: lift vs the counterfactual and vs the same weekday."""
    since = days_since_send_out(sales.index, send_outs)
    exclude = set(events["date"])
    rows = []
    for ev in events[events["date"].isin(sales.index)].itertuples():
        d = ev.date
        rows.append(
            {
                "date": d,
                "event": ev.name,
                "kind": ev.kind,
                "weekday": d.strftime("%a"),
                "sales": sales[d],
                "expected": expected[d],
                "lift_vs_expected": sales[d] / expected[d] - 1,
                "lift_vs_same_weekday": sales[d] / same_weekday_baseline(sales, d, exclude) - 1,
                "days_since_catalog": since[d],
            }
        )
    return pd.DataFrame(rows).set_index("date")


def event_study(
    sales: pd.Series, events: pd.DataFrame, expected: pd.Series, window: int = 3
) -> pd.Series:
    """Mean lift vs the counterfactual by day offset around nationwide public holidays.

    The Christmas / New Year period is left out: its days overlap and would dominate.
    """
    days = events.loc[events["kind"] == "public", "date"]
    days = days[~days.dt.month.isin([1, 12]) & days.isin(sales.index)]
    ratio = np.log(sales / expected)
    lifts = {}
    for k in range(-window, window + 1):
        shifted = days + pd.Timedelta(days=k)
        lifts[k] = np.expm1(ratio.reindex(shifted).mean())
    return pd.Series(lifts, name="lift_vs_expected").rename_axis("offset_days")


def model_check(sales: pd.Series, feats: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    """Do the Google features help a log-OLS baseline? In-sample fit and Q4-2024 hold-out error."""
    current = calendar_controls(sales.index)[["holiday", "christmas", "half_day"]]
    google = feats[GROUP_FEATURES]
    specs = {
        "baseline (no holidays)": controls,
        "+ holiday, christmas (previous controls)": controls.join(
            current[["holiday", "christmas"]]
        ),
        "+ holiday, christmas, half_day (current controls)": controls.join(current),
        "+ Google Calendar features": controls.join(google),
        "+ Google features + christmas window": controls.join(google).join(current["christmas"]),
    }
    log_sales = np.log(sales)
    train = sales.index < HOLDOUT_START
    test = (sales.index >= HOLDOUT_START) & (sales.index.year == 2024)
    rows = []
    for name, X in specs.items():
        X = sm.add_constant(X, has_constant="add")
        full = sm.OLS(log_sales, X).fit()
        pred = np.exp(sm.OLS(log_sales[train], X[train]).fit().predict(X[test]))
        mae = (sales[test] - pred).abs().mean()
        rows.append(
            {
                "spec": name,
                "columns": X.shape[1],
                "adj_r2": full.rsquared_adj,
                "aic": full.aic,
                "holdout_mae_over_mean": mae / sales[test].mean(),
            }
        )
    return pd.DataFrame(rows).set_index("spec")


# --- figures ----------------------------------------------------------------------------------


def _signed_barh(ax, values: pd.Series) -> None:
    ax.barh(
        values.index,
        values.to_numpy(),
        color=[POSITIVE if v >= 0 else NEGATIVE for v in values],
        height=0.62,
    )
    for i, v in enumerate(values):
        ha = "left" if v >= 0 else "right"
        ax.text(v, i, f" {v:+.0%} ", va="center", ha=ha, fontsize=7.5, color=INK_2)
    ax.axvline(0, color=AXIS, lw=0.8)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:+.0%}"))
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.margins(x=0.14, y=0.01)


def plot_event_lift(lift: pd.DataFrame) -> plt.Figure:
    """Mean lift vs the counterfactual per event, averaged over its occurrences.

    † marks events with an occurrence 0-6 days after a catalog send-out: the baseline controls
    the catalog's timing, not its size, so those lifts may still be catalog.
    """
    suffix = {"public": "", "half_day": " (half-day)", "regional": " (regional)"}
    per_event = lift.groupby("event").agg(
        lift=("lift_vs_expected", "mean"),
        kind=("kind", "first"),
        catalog=("days_since_catalog", lambda s: s.between(0, 6).any()),
    )
    labels = [
        f"{e}{suffix.get(k, ' (observance)')}{' †' if c else ''}"
        for e, k, c in zip(per_event.index, per_event["kind"], per_event["catalog"])
    ]
    values = pd.Series(per_event["lift"].to_numpy(), index=labels).sort_values()
    fig, ax = plt.subplots(figsize=(9, 0.24 * len(values) + 1.2), layout="constrained")
    _signed_barh(ax, values)
    ax.set_xlabel(
        "Sales vs a normal day with the same weekday, season, trend and catalog timing\n"
        "(mean of 2023 and 2024;  † = 0-6 days after a catalog send-out)"
    )
    ax.set_title("Google Calendar events: the Christmas holidays cut demand most")
    return fig


def plot_event_study(study: pd.Series) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(7, 3.2), layout="constrained")
    ax.bar(
        study.index,
        study.to_numpy(),
        color=[POSITIVE if v >= 0 else NEGATIVE for v in study],
        width=0.62,
    )
    for k, v in study.items():
        ax.annotate(
            f"{v:+.0%}",
            (k, v),
            xytext=(0, 3 if v >= 0 else -3),
            textcoords="offset points",
            ha="center",
            va="bottom" if v >= 0 else "top",
            fontsize=8,
            color=INK_2,
        )
    ax.axhline(0, color=AXIS, lw=0.8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:+.0%}"))
    ax.margins(y=0.15)
    ax.set_xticks(study.index)
    ax.set_xlabel(
        "Days from a nationwide public holiday (Christmas / New Year excluded); "
        "sales vs the counterfactual"
    )
    ax.set_title("Demand dips most on the day before a public holiday")
    return fig


# --- CLI --------------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google Calendar holiday features and their link to total sales."
    )
    parser.add_argument("--refresh", action="store_true", help="fetch the events from Google again")
    parser.add_argument(
        "--api-key", help="Calendar API key (default $GOOGLE_API_KEY; none -> public iCal export)"
    )
    args = parser.parse_args()

    raw = load_raw()
    sales = build_wide_table(raw)["total_sales"]
    send_outs = catalog_send_out_dates(raw)
    events = load_google_holidays(refresh=args.refresh, api_key=args.api_key)

    horizon = pd.date_range(sales.index[0], events["date"].max(), freq="D", name="date")
    holiday_features(horizon, events).to_csv(FEATURES_PATH)
    feats = holiday_features(sales.index, events)
    controls = baseline_controls(sales.index, send_outs)
    in_sample = events[events["date"].isin(sales.index)]
    expected = expected_sales(sales, in_sample, controls)

    corr = correlation_table(sales, feats, controls)
    lift = event_lift_table(sales, in_sample, expected, send_outs)
    study = event_study(sales, in_sample, expected)
    check = model_check(sales, feats, controls)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    corr.to_csv(OUTPUT_DIR / "holiday_correlation.csv")
    lift.to_csv(OUTPUT_DIR / "holiday_event_lift.csv")
    study.to_csv(OUTPUT_DIR / "holiday_event_study.csv")
    check.to_csv(OUTPUT_DIR / "holiday_model_check.csv")

    use_style()
    for name, fig in [
        ("holidays_event_lift", plot_event_lift(lift)),
        ("holidays_event_study", plot_event_study(study)),
    ]:
        save(fig, name)
        plt.close(fig)

    source = events["source"].iloc[0]
    print(
        f"{len(events)} events from Google Calendar ({source}), {len(in_sample)} in the sales history"
    )
    with pd.option_context("display.width", 160, "display.float_format", "{:,.3f}".format):
        print(
            "\nCorrelation with total sales\n",
            corr.drop(columns=["mean_sales_on", "mean_sales_off"]),
        )
        print("\nModel check (log-OLS)\n", check)
        print("\nLift around public holidays\n", study)


if __name__ == "__main__":
    main()
