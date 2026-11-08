"""Public holidays from Google Calendar's public holiday calendars, for the run's country.

Every country has a public Google calendar, e.g. `en.german#holiday@group.v.calendar.google.com`. Its events are read
with the Calendar API v3 (`events.list`) when an API key is configured (`MMM_GOOGLE_API_KEY` / `GOOGLE_API_KEY`), and
otherwise from the same calendar's public iCal export, which carries the same events with the same ids. Each event's description says what it is: "Public holiday" (a nationwide
day off), "Public holiday in <regions>" (regional), or "Observance" (no day off); the German calendar also marks
Christmas Eve and New Year's Eve as half-days.

Fetched events are cached as CSV in the external-data cache directory (`MMM_EXTERNAL_CACHE_DIR`, default
`<local data dir>/external`), so a calendar is fetched once and every model on the same machine sees the same days.
Years outside Google Calendar's rolling window (2021–2031 in November 2026) are supplemented with the nationwide public
holidays of `python-holidays` (`holidays.country_holidays`), and `HolidayCalendarError` is raised when neither source
can cover the requested dates.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urlencode

import pandas as pd

from mmm_studio.core.net import error_detail, open_url

API_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar}/events"
ICAL_URL = "https://calendar.google.com/calendar/ical/{calendar}/public/basic.ics"
# ISO country → the name in Google's public holiday calendar id (`en.<name>#holiday@group.v.calendar.google.com`);
# every id was checked against the iCal export on 2026-11-07
GOOGLE_CALENDAR_NAMES: dict[str, str] = {
    "DE": "german",
    "AT": "austrian",
    "CH": "ch",
    "NL": "dutch",
    "BE": "be",
    "LU": "lu",
    "FR": "french",
    "IT": "italian",
    "ES": "spain",
    "PT": "portuguese",
    "GB": "uk",
    "IE": "irish",
    "DK": "danish",
    "SE": "swedish",
    "NO": "norwegian",
    "FI": "finnish",
    "PL": "polish",
    "CZ": "czech",
    "SK": "slovak",
    "HU": "hungarian",
    "RO": "romanian",
    "BG": "bulgarian",
    "GR": "greek",
    "HR": "croatian",
    "SI": "slovenian",
    "TR": "turkish",
    "US": "usa",
    "CA": "canadian",
    "MX": "mexican",
    "BR": "brazilian",
    "AU": "australian",
    "NZ": "new_zealand",
    "IN": "indian",
    "JP": "japanese",
    "ZA": "sa",
}
# The holiday control flags nationwide public holidays; half-days (24 and 31 Dec) have their own control in the
# Christmas group
PUBLIC_KINDS: tuple[str, ...] = ("public",)
EVENT_COLUMNS: list[str] = ["date", "event_id", "name", "kind", "regions", "source"]

_LOCK = threading.RLock()
_MEMORY: dict[str, pd.DataFrame] = {}


class HolidayCalendarError(RuntimeError):
    """Google's holiday calendar could not be read, or does not cover the dates asked for (message shown to users)."""


def calendar_id(country: str) -> str:
    name = GOOGLE_CALENDAR_NAMES.get(country.upper())
    if name is None:
        raise HolidayCalendarError(f"There is no Google holiday calendar configured for '{country}'.")
    return f"en.{name}#holiday@group.v.calendar.google.com"


def cache_dir() -> Path:
    """Where fetched external data (holidays, weather) is cached (`MMM_EXTERNAL_CACHE_DIR`)."""
    env = os.environ.get("MMM_EXTERNAL_CACHE_DIR")
    if env:
        return Path(env)
    from mmm_studio.settings import get_settings

    settings = get_settings()
    return Path(settings.external_cache_dir or Path(settings.storage_local_dir) / "external")


def _api_key() -> str:
    key = os.environ.get("MMM_GOOGLE_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key is None:
        from mmm_studio.settings import get_settings

        key = get_settings().google_api_key
    return (key or "").strip()


def event_record(event_id: str, day: str, summary: str, description: str, source: str) -> dict[str, object]:
    """One event normalised; the description's first line says the kind (and the regions of a regional holiday)."""
    first, _, rest = description.strip().partition("\n")
    name = re.sub(r" \((regional holiday|half-day|tentative)\)$", "", summary.strip())
    if first.startswith("Public holiday in "):
        kind, regions = "regional", first.removeprefix("Public holiday in ")
    elif first.startswith("Public holiday"):
        kind, regions = ("half_day" if "half-day" in (rest + summary).lower() else "public"), ""
    else:  # "Observance" or "Observance in <regions>": no day off
        kind, regions = "observance", first.removeprefix("Observance").removeprefix(" in ")
    return {
        "date": pd.Timestamp(str(day)[:10]),
        "event_id": event_id,
        "name": name,
        "kind": kind,
        "regions": regions,
        "source": source,
    }


def parse_ical(text: str) -> list[dict[str, object]]:
    """Events of an iCal export (RFC 5545 lines unfolded, escapes undone); its UIDs are the API event ids."""
    text = text.replace("\r\n", "\n").replace("\n ", "")
    records = []
    for block in re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", text, flags=re.DOTALL):
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            key, _, value = line.partition(":")
            fields[key.split(";")[0]] = value
        if fields.get("STATUS") == "CANCELLED" or "DTSTART" not in fields:
            continue
        description = fields.get("DESCRIPTION", "")
        for escaped, char in (("\\n", "\n"), ("\\,", ","), ("\\;", ";"), ("\\\\", "\\")):
            description = description.replace(escaped, char)
        uid = fields.get("UID", "").split("@")[0]
        records.append(event_record(uid, fields["DTSTART"][:8], fields.get("SUMMARY", ""), description, "ical"))
    return records


def _fetch_api(calendar: str, api_key: str) -> list[dict[str, object]]:
    url = API_URL.format(calendar=quote(calendar, safe=""))
    params = {
        "key": api_key,
        "timeMin": "2000-01-01T00:00:00Z",
        "timeMax": "2041-01-01T00:00:00Z",
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 2500,
    }
    items: list[dict] = []
    token: str | None = None
    while True:
        query = urlencode(params | ({"pageToken": token} if token else {}))
        with open_url(f"{url}?{query}", timeout=30) as resp:
            page = json.load(resp)
        items += page.get("items", [])
        token = page.get("nextPageToken")
        if not token:
            break
    return [
        event_record(it["id"], it["start"].get("date") or it["start"].get("dateTime", ""), it.get("summary", ""), it.get("description", ""), "api")
        for it in items
        if it.get("status") != "cancelled"
    ]


def _fetch_ical(calendar: str) -> list[dict[str, object]]:
    with open_url(ICAL_URL.format(calendar=quote(calendar, safe="")), timeout=30) as resp:
        return parse_ical(resp.read().decode("utf-8"))


def fetch_google_holidays(country: str) -> pd.DataFrame:
    """Every event of the country's Google holiday calendar (the API with a key, else the public iCal export)."""
    calendar = calendar_id(country)
    key = _api_key()
    try:
        records = _fetch_api(calendar, key) if key else _fetch_ical(calendar)
    except (URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        if key:
            how, hint = "Calendar API", (
                "Check MMM_GOOGLE_API_KEY (the Calendar API must be enabled in the key's project) or unset it to read "
                "the public calendar export"
            )
        else:
            how, hint = "public calendar export", "Try again"
        raise HolidayCalendarError(
            f"Google's holiday calendar for {country} could not be read ({how}: {error_detail(exc)}). {hint}, or "
            "switch the holiday control off in the run settings."
        ) from exc
    if not records:
        raise HolidayCalendarError(f"Google's holiday calendar for {country} returned no events.")
    return pd.DataFrame(records, columns=EVENT_COLUMNS).sort_values(["date", "name"]).reset_index(drop=True)


def _cache_path(country: str) -> Path:
    return cache_dir() / f"google_holidays_{country.lower()}.csv"


def google_holidays(country: str, refresh: bool = False) -> pd.DataFrame:
    """The cached events of the country's Google holiday calendar (fetched once per cache directory)."""
    country = country.upper()
    with _LOCK:
        if not refresh and country in _MEMORY:
            return _MEMORY[country]
        path = _cache_path(country)
        events: pd.DataFrame | None = None
        if path.exists() and not refresh:
            events = pd.read_csv(path, parse_dates=["date"], keep_default_na=False)
        if events is None:
            events = fetch_google_holidays(country)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                events.to_csv(path, index=False)
            except OSError:  # a read-only cache only costs a refetch next time
                pass
        _MEMORY[country] = events
        return events


def covered_years(events: pd.DataFrame) -> tuple[int, int]:
    years = pd.DatetimeIndex(events["date"]).year
    return int(years.min()), int(years.max())


def _fallback_holiday_events(country: str, years: list[int]) -> pd.DataFrame:
    """Nationwide public holidays for `years` outside Google Calendar's rolling window, via `python-holidays`."""
    code = country.upper()
    if code not in GOOGLE_CALENDAR_NAMES:
        raise HolidayCalendarError(f"There is no Google holiday calendar configured for '{country}'.")
    try:
        import holidays as pyholidays

        try:
            cal = pyholidays.country_holidays(code, years=years, language="en_US")
        except Exception:
            cal = pyholidays.country_holidays(code, years=years)
    except Exception as exc:
        raise HolidayCalendarError(
            f"Holiday calendar fallback for {code} ({min(years)}–{max(years)}) failed: {exc}."
        ) from exc
    rows = [
        {
            "date": pd.Timestamp(d),
            "event_id": f"{d:%Y%m%d}_{code.lower()}_fallback",
            "name": str(name),
            "kind": "public",
            "regions": "",
            "source": "python-holidays",
        }
        for d, name in sorted(cal.items())
    ]
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def holiday_events(country: str, index: pd.DatetimeIndex, kinds: tuple[str, ...] = PUBLIC_KINDS) -> pd.DataFrame:
    """Events of `kinds` on the days of `index`; supplements out-of-window years via `python-holidays`."""
    idx = pd.DatetimeIndex(index)
    if len(idx) == 0:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    events = google_holidays(country)
    first, last = covered_years(events)
    if idx.max().year > last:
        try:
            events = google_holidays(country, refresh=True)  # the cache may predate newly published years
            first, last = covered_years(events)
        except HolidayCalendarError:
            pass
    missing_years = sorted(yr for yr in set(idx.year.tolist()) if yr < first or yr > last)
    if missing_years:
        extra = _fallback_holiday_events(country, missing_years)
        if not extra.empty:
            events = pd.concat([events, extra], ignore_index=True).sort_values(["date", "name"]).reset_index(drop=True)
            first, last = covered_years(events)
    if idx.min().year < first or idx.max().year > last:
        raise HolidayCalendarError(
            f"Google's holiday calendar for {country} covers {first}–{last}, but the dates run "
            f"{idx.min():%Y-%m-%d} … {idx.max():%Y-%m-%d}. Switch the holiday control off in the run settings or use "
            f"data from {first} to {last}."
        )
    sel = events[events["kind"].isin(kinds)]
    return sel[pd.DatetimeIndex(sel["date"]).isin(idx.normalize())].reset_index(drop=True)


def public_holiday_flags(index: pd.DatetimeIndex, country: str) -> pd.Series:
    """1.0 on the nationwide public holidays of `country` (Google Calendar), else 0.0."""
    idx = pd.DatetimeIndex(index)
    days = pd.DatetimeIndex(holiday_events(country, idx)["date"])
    return pd.Series(idx.normalize().isin(days).astype(float), index=idx, name="holiday")
