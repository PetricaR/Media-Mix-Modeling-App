"""Map any uploaded table onto the app's data contract, and describe its columns for the upload wizard.

The pipeline reads one canonical long table: `date`, `channel`, `sales_by_channel`, `costs_by_channel`,
`impressions_by_channel`, `clicks_by_channel`, plus per-date columns: `kpi_total` (the target, when the upload has one
column for it instead of sales per channel) and `ctrl_{slug}` (the user's control variables). A `ColumnMapping` says
where each of these comes from, in either layout:

- **long**: one row per date and channel (e.g. the sample workbook). The target is the daily sum of channel sales.
- **wide**: one row per date, the target in its own column and one column per channel's spend (most MMM exports).

`suggest_mapping` guesses a mapping from column names and values; `mapping_errors` lists, in plain words, what makes a
mapping unusable; `canonicalise` applies a valid one. Nothing is repaired silently: a gap in the date index, a column
with more than 20 % blank values, an unsupported date cadence or a column used twice is an error, and every automatic
conversion (blank spend cells read as zero, small gaps in the target or controls interpolated, categorical controls
one-hot encoded into 0/1 flags, and regular weekly tables disaggregated to daily rows) is reported as a warning.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.core.schema import (
    ColumnMapping,
    ControlColumn,
    WideChannel,
    detect_column_mapping,
    slugify_channel,
)

TARGET_COLUMN = "kpi_total"
CANONICAL_COLUMNS: tuple[str, ...] = (
    "date",
    "channel",
    "sales_by_channel",
    "costs_by_channel",
    "impressions_by_channel",
    "clicks_by_channel",
)

# Name hints for the heuristic mapping (lower-case words or word stems; whole-name matches rank first)
TARGET_WORDS: tuple[str, ...] = (
    "revenue",
    "sales",
    "total_sales",
    "total_revenue",
    "net_sales",
    "umsatz",
    "turnover",
    "gmv",
    "kpi",
    "target",
    "orders",
    "conversions",
    "bookings",
    "income",
)
CONTROL_WORDS: tuple[str, ...] = (
    "covid",
    "corona",
    "lockdown",
    "pandemic",
    "holiday",
    "feiertag",
    "promo",
    "promotion",
    "discount",
    "sale_event",
    "price",
    "preis",
    "temperature",
    "weather",
    "rain",
    "competitor",
    "competition",
    "event",
    "dummy",
    "flag",
    "cpi",
    "inflation",
    "unemployment",
    "strike",
    "outage",
    "launch",
)
IMPRESSION_WORDS: tuple[str, ...] = ("impressions", "impression", "impr", "views", "reach", "grp", "grps")
CLICK_WORDS: tuple[str, ...] = ("clicks", "click")
# Outcomes measured after the media worked: never inputs (they would leak the target into the model)
OUTCOME_WORDS: tuple[str, ...] = ("sessions", "visits", "traffic", "users", "pageviews", "transactions", "roas", "cpc", "cpm", "ctr")
SPEND_WORDS: tuple[str, ...] = ("spend", "spends", "cost", "costs", "kosten", "budget", "investment", "eur", "usd", "gbp", "media")

MAX_SAMPLE_VALUES = 4
MAX_BLANK_SHARE = 0.20
MAX_CONTROL_CATEGORIES = 12
BASELINE_CATEGORIES: tuple[str, ...] = (
    "normal",
    "none",
    "no",
    "baseline",
    "regular",
    "standard",
    "off",
    "0",
    "false",
)


class WorkbookValidationError(ValueError):
    """Raised when an uploaded file fails safety, schema or mapping validation (the message is shown to the user)."""


def control_categories(values: pd.Series) -> tuple[str | None, list[str]]:
    """`(baseline_category, dummy_categories)` when a text column holds 2..12 repeatable categories, else `(None, [])`."""
    present = values.dropna().astype("string").str.strip()
    present = present[present != ""]
    if present.empty:
        return None, []
    counts = present.value_counts(sort=True)
    if len(counts) < 2 or len(counts) > MAX_CONTROL_CATEGORIES or len(counts) >= len(present):
        return None, []
    cats_all = [str(k) for k in counts.index]
    baseline = next((c for c in cats_all if c.lower() in BASELINE_CATEGORIES), cats_all[0])
    dummies = [c for c in cats_all if c != baseline]
    return baseline, dummies


# --------------------------------------------------------------------------------------------------------------------
# Column profiles
# --------------------------------------------------------------------------------------------------------------------


def _words(name: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", str(name).lower()) if w]


def _norm(name: str) -> str:
    return "_".join(_words(name))


def _has_word(name: str, words: tuple[str, ...]) -> bool:
    tokens = _words(name)
    joined = _norm(name)
    return any(w in tokens or (("_" in w) and w in joined) for w in words)


def parse_dates(values: pd.Series) -> pd.Series:
    """Dates of a column as day-normalised timestamps (NaT where a value is not a date); see `parse_timestamps`."""
    return parse_timestamps(values).dt.normalize()


def parse_timestamps(values: pd.Series) -> pd.Series:
    """Timestamps of a column, time of day kept (NaT where a value is not a date).

    Datetime columns are used as they are. Text is read as ISO dates first; when that fails for many values, day-first
    (`31.01.2024`, `31/01/2024`) and month-first readings are tried and the one that reads more values wins. Numbers are
    never read as dates (a revenue of 5,796 is not a timestamp).
    """
    if pd.api.types.is_datetime64_any_dtype(values):
        return pd.to_datetime(values, errors="coerce").dt.tz_localize(None)
    if pd.api.types.is_numeric_dtype(values) or pd.api.types.is_bool_dtype(values):
        return pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    text = values.astype("string").str.strip()
    best = pd.to_datetime(text, errors="coerce", format="ISO8601")
    if best.isna().sum() > text.isna().sum():
        for dayfirst in (True, False):
            try:
                cand = pd.to_datetime(text, errors="coerce", dayfirst=dayfirst, format="mixed")
            except (ValueError, TypeError):
                continue
            if cand.notna().sum() > best.notna().sum():
                best = cand
    if getattr(best.dt, "tz", None) is not None:
        best = best.dt.tz_localize(None)
    return best


def _numeric(values: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(values):
        return values.astype(float)
    return pd.to_numeric(values, errors="coerce")


def column_kind(values: pd.Series) -> str:
    """`date`, `numeric`, `text` or `empty` for one column (≥ 95 % of its non-blank values decide)."""
    present = values.dropna()
    if present.empty:
        return "empty"
    if pd.api.types.is_datetime64_any_dtype(values):
        return "date"
    n = len(present)
    if _numeric(present).notna().sum() >= 0.95 * n:
        return "numeric"
    if parse_dates(present).notna().sum() >= 0.95 * n:
        return "date"
    return "text"


def _json_value(v: Any) -> Any:
    """A JSON-safe scalar (timestamps as ISO dates, NaN as null)."""
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.strftime("%Y-%m-%d") if v == v.normalize() else v.isoformat()
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else round(v, 6)
    if isinstance(v, (int, bool, str)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def json_records(df: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    """The first `limit` rows as JSON-safe records keyed by column name."""
    head = df.head(limit)
    return [{str(c): _json_value(v) for c, v in row.items()} for _, row in head.iterrows()]


def profile_columns(df: pd.DataFrame) -> list[dict[str, Any]]:
    """What the wizard and Gemini see of each column: kind, blanks, distinct values, range, zero share and samples."""
    out: list[dict[str, Any]] = []
    n = len(df)
    for col in df.columns:
        s = df[col]
        kind = column_kind(s)
        info: dict[str, Any] = {
            "name": str(col),
            "kind": kind,
            "non_null": int(s.notna().sum()),
            "blank": int(n - s.notna().sum()),
            "distinct": int(s.nunique(dropna=True)),
        }
        if kind == "numeric":
            num = _numeric(s)
            present = num.dropna()
            info.update(
                min=_json_value(float(present.min())) if len(present) else None,
                max=_json_value(float(present.max())) if len(present) else None,
                mean=_json_value(float(present.mean())) if len(present) else None,
                sum=_json_value(float(present.sum())) if len(present) else None,
                zero_share=_json_value(float((present == 0).mean())) if len(present) else None,
                negative=int((present < 0).sum()),
                binary=bool(len(present) and set(present.unique()).issubset({0, 1})),
            )
        elif kind == "date":
            dates = parse_dates(s).dropna()
            unique_dates = pd.DatetimeIndex(dates.unique()).sort_values()
            step_days = int(pd.Series(unique_dates).diff().dt.days.dropna().median()) if len(unique_dates) >= 2 else None
            info.update(
                min=dates.min().strftime("%Y-%m-%d") if len(dates) else None,
                max=dates.max().strftime("%Y-%m-%d") if len(dates) else None,
                distinct_dates=int(dates.nunique()),
                step_days=step_days,
            )
        elif kind == "text":
            baseline, cats = control_categories(s)
            if cats:
                info.update(categories=cats, baseline=baseline)
        samples = [_json_value(v) for v in s.dropna().drop_duplicates().head(MAX_SAMPLE_VALUES).tolist()]
        info["samples"] = samples
        out.append(info)
    return out


# --------------------------------------------------------------------------------------------------------------------
# Heuristic mapping
# --------------------------------------------------------------------------------------------------------------------


def _date_columns(df: pd.DataFrame) -> list[str]:
    cols = [str(c) for c in df.columns if column_kind(df[c]) == "date"]
    # A column named like a date ranks first
    return sorted(cols, key=lambda c: (0 if _has_word(c, ("date", "day", "datum", "tag", "time", "dt", "week")) else 1))


def detect_layout(df: pd.DataFrame) -> tuple[str, str | None, str | None]:
    """`(layout, date_column, channel_column)`: long when a text column splits the repeated dates into unique rows."""
    dates = _date_columns(df)
    if not dates:
        return "wide", None, None
    date_col = dates[0]
    # Timestamps, not days: an hourly export repeats its days but not its timestamps (wide, combined per day later)
    parsed = parse_timestamps(df[date_col])
    if parsed.nunique() >= 0.95 * parsed.notna().sum():
        return "wide", date_col, None
    parsed = parsed.dt.normalize()
    text_cols = [str(c) for c in df.columns if str(c) != date_col and column_kind(df[c]) == "text"]
    ranked = sorted(text_cols, key=lambda c: 0 if _has_word(c, ("channel", "kanal", "medium", "source", "media")) else 1)
    for col in ranked:
        key = pd.DataFrame({"d": parsed, "c": df[col].astype(str)})
        if not key.duplicated().any() or key.duplicated().mean() < 0.01:
            return "long", date_col, col
    return "long", date_col, ranked[0] if ranked else None


def clean_channel_name(column: str) -> str:
    """A channel's display name from its spend column: spend/cost words dropped, short all-lower-case words upper-cased."""
    words = [w for w in re.split(r"[\s_\-./]+", str(column).strip()) if w]
    kept = [w for w in words if w.lower() not in SPEND_WORDS]
    if not kept:
        kept = words
    if all(w == w.lower() for w in kept):
        kept = [w.upper() if len(w) <= 3 else w.capitalize() for w in kept]
    return " ".join(kept)[:80]


def _strip_metric(name: str, words: tuple[str, ...]) -> str:
    return " ".join(w for w in _words(name) if w not in words and w not in SPEND_WORDS)


def suggest_mapping(df: pd.DataFrame) -> ColumnMapping:
    """A best guess of the mapping from column names and values; the wizard shows it for the user to confirm.

    Long tables use the canonical name matching (`detect_column_mapping`). In a wide table the target is the column
    named like revenue/sales (else the numeric column with the largest mean), 0/1 columns and columns named like a known
    driver (Covid, price, promotion, weather …) are controls, impressions and clicks are linked to the spend column with
    the same channel words, outcome metrics (sessions, ROAS …) are left out, and every other numeric column is a
    channel's spend.
    """
    layout, date_col, channel_col = detect_layout(df)
    columns = [str(c) for c in df.columns]
    if layout == "long":
        base = detect_column_mapping(columns)
        if date_col:
            base.date = date_col
        if channel_col:
            base.channel = channel_col
        # Optional metrics only when the column exists
        base.impressions = base.impressions if base.impressions in columns else None
        base.clicks = base.clicks if base.clicks in columns else None
        return base

    kinds = {str(c): column_kind(df[c]) for c in df.columns}
    numeric = [c for c in columns if kinds[c] == "numeric" and c != date_col]
    target: str | None = None
    exact = [c for c in numeric if _norm(c) in TARGET_WORDS]
    loose = [c for c in numeric if _has_word(c, TARGET_WORDS) and not _has_word(c, SPEND_WORDS)]
    means = {c: float(_numeric(df[c]).mean()) for c in numeric}
    for group in (exact, loose):
        if group:
            target = max(group, key=lambda c: means.get(c, 0.0))
            break
    if target is None and numeric:
        target = max(numeric, key=lambda c: means.get(c, 0.0))

    controls: list[ControlColumn] = []
    impr_cols: list[str] = []
    click_cols: list[str] = []
    spend_cols: list[str] = []
    for c in numeric:
        if c == target:
            continue
        values = _numeric(df[c]).dropna()
        binary = len(values) > 0 and set(values.unique()).issubset({0, 1})
        if _has_word(c, OUTCOME_WORDS):
            continue
        if _has_word(c, IMPRESSION_WORDS):
            impr_cols.append(c)
        elif _has_word(c, CLICK_WORDS):
            click_cols.append(c)
        elif binary or _has_word(c, CONTROL_WORDS) or (values < 0).any():
            controls.append(ControlColumn(column=c, name=clean_channel_name(c)))
        else:
            spend_cols.append(c)

    for c in columns:
        if c == date_col or kinds[c] != "text":
            continue
        if _has_word(c, CONTROL_WORDS) and not _has_word(c, OUTCOME_WORDS):
            _, cats = control_categories(df[c])
            if cats:
                controls.append(ControlColumn(column=c, name=clean_channel_name(c), categories=cats))
    col_order = {c: i for i, c in enumerate(columns)}
    controls.sort(key=lambda ctrl: col_order.get(ctrl.column, len(columns)))

    channels: list[WideChannel] = []
    used_names: set[str] = set()
    for c in spend_cols:
        name = clean_channel_name(c)
        if slugify_channel(name) in used_names:
            name = str(c)[:80]
        used_names.add(slugify_channel(name))
        channels.append(WideChannel(name=name, spend=c))

    def link(metric_cols: list[str], words: tuple[str, ...], attr: str) -> None:
        for m in metric_cols:
            stem = _strip_metric(m, words)
            match = [ch for ch in channels if _strip_metric(ch.spend, words) == stem and getattr(ch, attr) is None]
            if stem and len(match) == 1:
                setattr(match[0], attr, m)

    link(impr_cols, IMPRESSION_WORDS, "impressions")
    link(click_cols, CLICK_WORDS, "clicks")
    return ColumnMapping(
        layout="wide",
        date=date_col or (columns[0] if columns else "date"),
        target=target,
        wide_channels=channels,
        controls=controls,
        impressions=None,
        clicks=None,
    )


# --------------------------------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------------------------------


def used_columns(mapping: ColumnMapping) -> list[tuple[str, str]]:
    """`(column, what)` for every column the mapping reads, in mapping order."""
    used: list[tuple[str, str]] = [(mapping.date, "the date")]
    if mapping.layout == "long":
        used += [(mapping.channel, "the channel"), (mapping.sales, "channel sales"), (mapping.costs, "channel costs")]
        if mapping.impressions:
            used.append((mapping.impressions, "impressions"))
        if mapping.clicks:
            used.append((mapping.clicks, "clicks"))
    else:
        if mapping.target:
            used.append((mapping.target, "the target"))
        for ch in mapping.wide_channels:
            used.append((ch.spend, f"{ch.name} spend"))
            for attr, what in (("impressions", "impressions"), ("clicks", "clicks"), ("sales", "sales")):
                col = getattr(ch, attr)
                if col:
                    used.append((col, f"{ch.name} {what}"))
    used += [(c.column, f"the control {c.name}") for c in mapping.controls]
    return used


def _examples(dates: pd.Series | pd.DatetimeIndex, n: int = 3) -> str:
    return ", ".join(pd.Timestamp(d).strftime("%Y-%m-%d") for d in list(dates)[:n])


def mapping_errors(df: pd.DataFrame, mapping: ColumnMapping) -> list[str]:
    """Everything that makes `mapping` unusable on `df`, in plain words (empty when it can be applied)."""
    errors: list[str] = []
    columns = {str(c) for c in df.columns}
    used = used_columns(mapping)
    missing = [f"'{c}' ({what})" for c, what in used if c not in columns]
    if missing:
        errors.append(f"Column{'s' if len(missing) > 1 else ''} not in the sheet: {', '.join(missing)}.")
    seen: dict[str, str] = {}
    for col, what in used:
        if col in seen and seen[col] != what:
            errors.append(f"Column '{col}' is used twice: as {seen[col]} and as {what}.")
        seen.setdefault(col, what)
    if errors:
        return errors

    dates = parse_dates(df[mapping.date])
    if dates.isna().any():
        bad = df.loc[dates.isna(), mapping.date].head(3).tolist()
        errors.append(
            f"{int(dates.isna().sum())} rows of '{mapping.date}' are not dates (e.g. {', '.join(map(repr, bad))})."
        )

    # Blank cells are not a mapping problem: how they are filled (and how many may be) is the resampling's business
    # (`core/resample.plan_resample`), which the wizard's "Resample & fill" step sets
    def numeric_check(col: str, what: str, *, min_values: int = 0) -> None:
        raw = df[col]
        num = _numeric(raw)
        not_numeric = raw.notna() & num.isna()
        if not_numeric.any():
            ex = raw[not_numeric].head(2).tolist()
            errors.append(f"'{col}' ({what}) has {int(not_numeric.sum())} values that are not numbers (e.g. {ex}).")
        elif num.notna().sum() < min_values:
            errors.append(f"'{col}' ({what}) has {int(num.notna().sum())} values; it needs at least {min_values}.")

    if mapping.layout == "long":
        numeric_check(mapping.sales, "channel sales")
        numeric_check(mapping.costs, "channel costs")
        for col, what in ((mapping.impressions, "impressions"), (mapping.clicks, "clicks")):
            if col:
                numeric_check(col, what)
    else:
        if not mapping.target:
            errors.append("Choose the column with the target (the daily sales or revenue the model explains).")
        else:
            numeric_check(mapping.target, "the target", min_values=2)
        if not mapping.wide_channels:
            errors.append("Map at least one column as a channel's spend.")
        slugs: dict[str, str] = {}
        for ch in mapping.wide_channels:
            if ch.slug in slugs:
                errors.append(f"Two channels are named alike ('{slugs[ch.slug]}' and '{ch.name}'); rename one.")
            slugs[ch.slug] = ch.name
            numeric_check(ch.spend, f"{ch.name} spend")
            for attr in ("impressions", "clicks", "sales"):
                col = getattr(ch, attr)
                if col:
                    numeric_check(col, f"{ch.name} {attr}")
    keys: dict[str, str] = {}
    for c in mapping.controls:
        if c.key in keys:
            errors.append(f"Two controls are named alike ('{keys[c.key]}' and '{c.name}'); rename one.")
        keys[c.key] = c.name
        if column_kind(df[c.column]) == "text":
            _, cats = control_categories(df[c.column])
            if not cats:
                n_distinct = int(df[c.column].nunique(dropna=True))
                errors.append(
                    f"'{c.column}' (the control {c.name}) holds text with {n_distinct} distinct values; "
                    f"a text control needs 2 to {MAX_CONTROL_CATEGORIES} categories."
                )
        else:
            numeric_check(c.column, f"the control {c.name}", min_values=1)
    return errors


def canonicalise(df: pd.DataFrame, mapping: ColumnMapping) -> tuple[pd.DataFrame, list[str]]:
    """`df` in the canonical long schema (plus `kpi_total` and `ctrl_*` per-date columns), and the warnings raised."""
    out, warnings, _ = canonicalise_report(df, mapping)
    return out, warnings


def canonicalise_report(df: pd.DataFrame, mapping: ColumnMapping) -> tuple[pd.DataFrame, list[str], Any]:
    """`canonicalise` plus the resampling report (`core.resample.Prepared`) the upload wizard shows.

    Raises `WorkbookValidationError` with every mapping error, and `resample.PreparationError` (a subclass) with every
    reason the mapping's resampling cannot run: an unsupported cadence, a gap without "fill missing dates", dates that
    repeat without being combined, a column too blank for its default fill, or a control that changes within a date.
    """
    # `core.resample` builds on this module's parsing helpers, so it is imported here rather than at the top
    from mmm_studio.core.resample import PreparationError, plan_resample, run_long, run_wide

    errors = mapping_errors(df, mapping)
    if errors:
        raise WorkbookValidationError(" ".join(errors))
    plan, prep_errors = plan_resample(df, mapping)
    if plan is None:
        raise PreparationError(" ".join(prep_errors))
    warnings: list[str] = []
    ts = parse_timestamps(df[mapping.date])

    if mapping.layout == "long":
        source = df
        if plan.grid.frequency == "sub_daily":
            # Rows repeated exactly would be added twice when the day's rows are summed
            used = list(dict.fromkeys(c for c, _ in used_columns(mapping)))
            exact = df[used].assign(_ts=ts).duplicated(keep="first")
            if exact.any():
                warnings.append(f"{int(exact.sum())} exact duplicate rows (every mapped column identical) were removed.")
                source, ts = df.loc[~exact], ts.loc[~exact]
                plan.grid.row_period = plan.grid.row_period.loc[~exact]
        rows = pd.DataFrame(
            {
                "channel": source[mapping.channel],
                "sales_by_channel": source[mapping.sales],
                "costs_by_channel": source[mapping.costs],
                "impressions_by_channel": source[mapping.impressions] if mapping.impressions else np.nan,
                "clicks_by_channel": source[mapping.clicks] if mapping.clicks else np.nan,
            }
        )
        metric_cols = {
            "sales_by_channel": mapping.sales,
            "costs_by_channel": mapping.costs,
            "impressions_by_channel": mapping.impressions or "",
            "clicks_by_channel": mapping.clicks or "",
        }
        controls = source[[c.column for c in mapping.controls]]
        prepared = run_long(rows, ts, plan, metric_cols, controls)
        out = prepared.long_rows[list(CANONICAL_COLUMNS)].copy()
        day_controls = prepared.long_controls
        for c in mapping.controls:
            per_day = out["date"].map(day_controls[c.column])
            if c.column and column_kind(df[c.column]) == "text":
                if not c.categories:
                    c.categories = control_categories(df[c.column])[1]
                for cat, key in zip(c.categories, c.keys, strict=False):
                    out[key] = (per_day == cat).fillna(False).astype(float)
            else:
                out[c.key] = pd.to_numeric(per_day, errors="coerce").astype(float)
        warnings += prepared.warnings
        if prepared.grid.coarse or prepared.grid.frequency == "sub_daily":
            out = out.sort_values(["date", "channel"], kind="stable").reset_index(drop=True)
        return out, warnings, prepared

    # Wide: one row per date (or several a day, combined per column by the resampling)
    frame = df.assign(_ts=ts)
    used = list(dict.fromkeys(c for c, _ in used_columns(mapping) if c != mapping.date))
    frame = frame[["_ts", *used]]
    exact = frame.duplicated(keep="first")
    if exact.any():
        warnings.append(f"{int(exact.sum())} exact duplicate rows (every mapped column identical) were removed.")
        frame = frame.loc[~exact]
    prepared = run_wide(frame[used], ts.loc[frame.index], plan)
    warnings += prepared.warnings
    daily = prepared.daily

    target = daily[mapping.target].astype(float) if mapping.target else None
    control_series: dict[str, np.ndarray] = {}
    encoded_controls: list[str] = []
    for c in mapping.controls:
        values = daily[c.column]
        if column_kind(df[c.column]) == "text":
            baseline, cats = control_categories(df[c.column])
            if not c.categories:
                c.categories = cats
            for cat, key in zip(c.categories, c.keys, strict=False):
                control_series[key] = (values == cat).fillna(False).astype(float).to_numpy()
            base_note = f", baseline '{baseline}' omitted" if baseline else ""
            encoded_controls.append(f"'{c.column}' → {', '.join(c.categories)}{base_note}")
        else:
            control_series[c.key] = pd.to_numeric(values, errors="coerce").astype(float).to_numpy()
    if encoded_controls:
        warnings.append(f"Categorical controls were one-hot encoded into 0/1 flags ({'; '.join(encoded_controls)}).")

    parts: list[pd.DataFrame] = []
    for ch in mapping.wide_channels:
        part = pd.DataFrame(
            {
                "date": daily.index,
                "channel": ch.name,
                "sales_by_channel": daily[ch.sales].to_numpy(dtype=float) if ch.sales else np.nan,
                "costs_by_channel": daily[ch.spend].to_numpy(dtype=float),
                "impressions_by_channel": daily[ch.impressions].to_numpy(dtype=float) if ch.impressions else np.nan,
                "clicks_by_channel": daily[ch.clicks].to_numpy(dtype=float) if ch.clicks else np.nan,
            }
        )
        if target is not None:
            part[TARGET_COLUMN] = target.to_numpy()
        for key, arr in control_series.items():
            part[key] = arr
        parts.append(part)
    out = pd.concat(parts, ignore_index=True).sort_values(["date", "channel"], kind="stable").reset_index(drop=True)
    return out, warnings, prepared
