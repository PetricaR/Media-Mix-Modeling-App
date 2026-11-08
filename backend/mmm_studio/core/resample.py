"""Resample an upload onto the model's daily grid and fill its gaps, column by column.

MMM Studio models one value per day. An upload can be finer (several rows a day: hourly exports, one row per region),
daily, or coarser (one row per week or per calendar month), and it can have blank cells or missing dates.
`ResampleConfig` (on `ColumnMapping.resample`) says how each mapped column reaches the daily grid, in this order:

1. **Several rows a day** are combined per column (`aggregate`: sum, mean, median, min, max, first, last, mode).
2. **Missing dates** (a day, week or month without a row) are added only when `fill_missing_dates` is on; otherwise a
   gap is an error, as before.
3. **Blank cells** and the added dates are filled per column at the data's own frequency (`fill`: zero, a constant,
   the baseline category, forward / backward fill, nearest value, linear / quadratic / cubic / PCHIP / Akima
   interpolation, the column's mean / median / mode, a moving average, the same weekday, or a value spread over the
   blank rows after it). A blank that the method cannot reach (before the first value, after the last, or too far for
   the moving average) takes the nearest observed value, and the report counts it.
4. **Weekly or monthly rows** are split into days per column (`disaggregate`: spread evenly, a smooth curve that keeps
   each period's total, all on the first day, repeat the value, or a line / curve through the periods' middles).

A column without a choice keeps its role's default, which is what the app did before this step existed (blank spend
is no spend, the target and numeric controls are linearly interpolated with at most 20 % blank rows, 0/1 flags are 0,
a blank category is the baseline, optional channel metrics stay blank, weekly amounts are spread evenly and weekly
controls repeated). Nothing happens silently: every conversion is counted per column (`ColumnReport`) and described
in the warnings that the run keeps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from mmm_studio.core.mapping import (
    MAX_BLANK_SHARE,
    WorkbookValidationError,
    _numeric,
    column_kind,
    parse_timestamps,
)
from mmm_studio.core.schema import ColumnMapping, ResampleConfig

Kind = Literal["flow", "level", "flag", "category"]
ALL_KINDS: tuple[Kind, ...] = ("flow", "level", "flag", "category")


class PreparationError(WorkbookValidationError):
    """The upload cannot reach the daily grid with the chosen resampling; fixable in the wizard's "Resample & fill"."""


# --------------------------------------------------------------------------------------------------------------------
# The method catalog: one definition for the wizard, Gemini's prompt and the checks
# --------------------------------------------------------------------------------------------------------------------

FREQUENCIES: dict[str, tuple[str, str]] = {
    "auto": ("Detect from the dates", "Use the spacing the dates have"),
    "sub_daily": ("Several rows a day → days", "Rows that share a day (hours, regions, stores) are combined per column"),
    "daily": ("Daily", "One row per day"),
    "weekly": ("Weekly → days", "One row per week, split into its 7 days per column (finer rows are combined by week)"),
    "monthly": ("Monthly → days", "One row per calendar month, split into its days per column (finer rows are combined by month)"),
}

# value: (label, help, kinds it applies to)
AGGREGATES: dict[str, tuple[str, str, tuple[Kind, ...]]] = {
    "sum": ("Sum", "Add up the day's rows: the right choice for amounts (sales, spend, clicks)", ("flow", "level")),
    "mean": ("Mean", "The average of the day's rows: levels such as a price or a temperature", ("flow", "level")),
    "median": ("Median", "The middle value of the day's rows; a stray row does not move it", ("flow", "level")),
    "min": ("Minimum", "The smallest of the day's rows", ("flow", "level", "flag")),
    "max": ("Maximum", "The largest of the day's rows; for a 0/1 flag: on when any row is on", ("flow", "level", "flag")),
    "first": ("First row", "The day's first row", ALL_KINDS),
    "last": ("Last row", "The day's last row (an end-of-day level)", ALL_KINDS),
    "mode": ("Most frequent", "The value most of the day's rows have", ("level", "flag", "category")),
}

FILLS: dict[str, tuple[str, str, tuple[Kind, ...]]] = {
    "none": ("Leave blank", "Keep the gap: only for a channel's optional metrics, which describe it but are not model inputs", ("flow",)),
    "zero": ("Zero", "Nothing happened on that date: no spend, no sales, the flag is off", ("flow", "level", "flag")),
    "constant": ("A fixed value", "The value you enter (0 or 1 for a flag)", ("flow", "level", "flag")),
    "baseline": ("Baseline category", "The category the flags are measured against: every flag off", ("category",)),
    "ffill": ("Forward fill", "Carry the last value forward: levels that change in steps (prices, flags, campaigns)", ALL_KINDS),
    "bfill": ("Backward fill", "Carry the next value back", ALL_KINDS),
    "nearest": ("Nearest value", "The value of the closest observed date (the earlier one on a tie)", ALL_KINDS),
    "linear": ("Linear interpolation", "A straight line between the values around the gap", ("flow", "level")),
    "quadratic": ("Quadratic spline", "A smooth curve of degree 2 through the values; can overshoot them", ("flow", "level")),
    "cubic": ("Cubic spline", "A smooth curve of degree 3 through the values; can overshoot near jumps", ("flow", "level")),
    "pchip": ("PCHIP (shape-preserving)", "A smooth cubic that never overshoots the values around the gap", ("flow", "level")),
    "akima": ("Akima spline", "A smooth curve that an outlier barely bends; little overshoot", ("flow", "level")),
    "mean": ("Column mean", "The average of the column's observed values", ("flow", "level")),
    "median": ("Column median", "The middle of the column's observed values; robust to outliers", ("flow", "level")),
    "mode": ("Most frequent value", "The value the column has most often", ("level", "flag", "category")),
    "moving_average": ("Moving average", "The mean of the observed values within 3 rows on either side (a centred 7-row window)", ("flow", "level")),
    "seasonal": ("Same weekday", "A line between the same weekday of the weeks around the gap: keeps the weekly pattern (daily data)", ("flow", "level")),
    "spread": ("Spread over the blanks after it", "A value followed by blank rows is shared evenly with them (spend booked weekly or monthly); keeps the total", ("flow",)),
}

DISAGGREGATES: dict[str, tuple[str, str, tuple[Kind, ...]]] = {
    "even": ("Spread evenly", "Each day gets an equal share of the period's amount; totals are kept", ("flow",)),
    "smooth": ("Smooth curve", "Amounts: a smooth daily curve that keeps each period's total. Levels: a smooth curve through each period's middle", ("flow", "level")),
    "first_day": ("All on the first day", "The whole amount lands on the period's first day (a one-off booking)", ("flow",)),
    "repeat": ("Repeat", "Every day of the period gets the period's value", ("level", "flag", "category")),
    "linear": ("Linear", "A straight line through each period's middle", ("level",)),
}

INTERPOLATIONS = ("linear", "quadratic", "cubic", "pchip", "akima")
MIN_VALUES: dict[str, int] = {"linear": 2, "quadratic": 3, "cubic": 4, "pchip": 2, "akima": 3, "seasonal": 2, "spread": 1}
PHRASES: dict[str, str] = {
    "zero": "filled with 0",
    "constant": "filled with {value}",
    "baseline": "set to the baseline category",
    "ffill": "forward filled",
    "bfill": "backward filled",
    "nearest": "filled with the nearest value",
    "linear": "linearly interpolated",
    "quadratic": "filled by a quadratic spline",
    "cubic": "filled by a cubic spline",
    "pchip": "filled by PCHIP interpolation",
    "akima": "filled by an Akima spline",
    "mean": "filled with the column mean",
    "median": "filled with the column median",
    "mode": "filled with the most frequent value",
    "moving_average": "filled with a 7-row moving average",
    "seasonal": "interpolated between the same weekdays",
    "spread": "given a share of the value before them",
}
UNIT = {"sub_daily": "day", "daily": "day", "weekly": "week", "monthly": "month"}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def method_catalog() -> dict[str, Any]:
    """The frequencies and methods with their labels, help texts and the column kinds they apply to (for the wizard)."""

    def rows(table: dict[str, tuple[str, str, tuple[Kind, ...]]]) -> list[dict[str, Any]]:
        return [{"value": k, "label": v[0], "help": v[1], "kinds": list(v[2])} for k, v in table.items()]

    return {
        "frequencies": [{"value": k, "label": v[0], "help": v[1]} for k, v in FREQUENCIES.items()],
        "aggregate": rows(AGGREGATES),
        "fill": rows(FILLS),
        "disaggregate": rows(DISAGGREGATES),
        "max_blank_share": MAX_BLANK_SHARE,
    }


# --------------------------------------------------------------------------------------------------------------------
# Columns
# --------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnSpec:
    """One mapped column: what it is in the model and what kind of series it is."""

    column: str
    role: str  # target | spend | impressions | clicks | sales | costs | control
    label: str  # "the target", "TV spend", "the control Covid"
    kind: Kind  # flow (an amount per day) | level | flag (0/1) | category (text)
    metric: bool = False  # an optional channel metric (or a long table's channel column): may stay blank
    long: bool = False


def _control_kind(values: pd.Series) -> Kind:
    if column_kind(values) == "text":
        return "category"
    present = _numeric(values).dropna()
    return "flag" if len(present) and set(present.unique()).issubset({0, 1}) else "level"


def column_specs(df: pd.DataFrame, mapping: ColumnMapping) -> list[ColumnSpec]:
    """Every column the mapping reads (except the date and a long table's channel), in mapping order."""
    specs: list[ColumnSpec] = []
    if mapping.layout == "long":
        for col, role, label in (
            (mapping.sales, "sales", "channel sales"),
            (mapping.costs, "costs", "channel costs"),
            (mapping.impressions, "impressions", "impressions"),
            (mapping.clicks, "clicks", "clicks"),
        ):
            if col:
                specs.append(ColumnSpec(col, role, label, "flow", metric=True, long=True))
    else:
        if mapping.target:
            specs.append(ColumnSpec(mapping.target, "target", "the target", "flow"))
        for ch in mapping.wide_channels:
            specs.append(ColumnSpec(ch.spend, "spend", f"{ch.name} spend", "flow"))
            for attr in ("impressions", "clicks", "sales"):
                col = getattr(ch, attr)
                if col:
                    specs.append(ColumnSpec(col, attr, f"{ch.name} {attr}", "flow", metric=True))
    for c in mapping.controls:
        if c.column in df.columns:
            specs.append(
                ColumnSpec(c.column, "control", f"the control {c.name}", _control_kind(df[c.column]), long=mapping.layout == "long")
            )
    return specs


def column_choices(df: pd.DataFrame, mapping: ColumnMapping) -> list[dict[str, Any]]:
    """Every mapped column with its kind, blank cells, default and allowed methods: what the wizard offers per column,
    known even when the data cannot reach the daily grid yet."""
    out = []
    for spec in column_specs(df, mapping):
        raw = df[spec.column]
        present = raw.notna() if spec.kind == "category" else _numeric(raw).notna()
        out.append(
            {
                "column": spec.column,
                "role": spec.role,
                "label": spec.label,
                "kind": spec.kind,
                "blank": int((~present).sum()),
                "defaults": default_methods(spec),
                "allowed": allowed_methods(spec),
            }
        )
    return out


def default_methods(spec: ColumnSpec) -> dict[str, str]:
    """The role's default: what the app did before resampling could be chosen."""
    if spec.kind == "flow":
        fill = "none" if spec.metric else ("linear" if spec.role == "target" else "zero")
        return {"aggregate": "sum", "fill": fill, "disaggregate": "even"}
    if spec.kind == "level":
        return {"aggregate": "mean", "fill": "linear", "disaggregate": "repeat"}
    if spec.kind == "flag":
        return {"aggregate": "max", "fill": "zero", "disaggregate": "repeat"}
    return {"aggregate": "mode", "fill": "baseline", "disaggregate": "repeat"}


def allowed_methods(spec: ColumnSpec) -> dict[str, list[str]]:
    """The methods that apply to a column of this kind and role."""
    fills = [k for k, v in FILLS.items() if spec.kind in v[2]]
    if spec.long and spec.role != "control":
        fills = ["none"]  # a long table's missing channel-day stays missing: the missing-row controls flag it
    elif not spec.metric:
        fills = [f for f in fills if f != "none"]
    return {
        "aggregate": [k for k, v in AGGREGATES.items() if spec.kind in v[2]],
        "fill": fills,
        "disaggregate": [k for k, v in DISAGGREGATES.items() if spec.kind in v[2]],
    }


# --------------------------------------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------------------------------------


def _iso(d: Any) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def _examples(dates: Any, n: int = 3, fmt: str = "%Y-%m-%d") -> str:
    return ", ".join(pd.Timestamp(d).strftime(fmt) for d in list(dates)[:n])


def _day_steps(idx: pd.DatetimeIndex) -> np.ndarray:
    """Days between consecutive dates (whatever the index's time unit)."""
    if len(idx) < 2:
        return np.array([], dtype=int)
    return np.asarray((idx[1:] - idx[:-1]).days, dtype=int)


def _longest_run(mask: np.ndarray) -> int:
    best = run = 0
    for m in mask:
        run = run + 1 if m else 0
        best = max(best, run)
    return best


def describe_timestamps(ts: pd.Series, keys: pd.Series | None = None) -> dict[str, Any]:
    """What the dates look like: the detected frequency, how many rows share a date, missing dates, the weekday of
    weekly rows and the anchor of monthly ones. `keys` (a long table's channel) makes a repeated date normal."""
    valid = ts.notna()
    ts = ts[valid]
    if ts.empty:
        return {"detected": "unknown", "rows": 0, "dates": 0}
    days = ts.dt.normalize()
    has_time = bool((ts != days).any())
    frame = pd.DataFrame({"d": days.to_numpy()})
    if keys is not None:
        frame["k"] = keys[valid].astype(str).to_numpy()
    per = frame.groupby(list(frame.columns)).size()
    rows_per_date = int(per.max())
    uniq = pd.DatetimeIndex(days.unique()).sort_values()
    diffs = _day_steps(uniq)
    step = int(np.median(diffs)) if len(diffs) else None
    if rows_per_date > 1 and has_time:
        detected = "sub_daily"
    elif step is None:
        detected = "unknown"
    elif step <= 1:
        detected = "daily"
    elif 6 <= step <= 8:
        detected = "weekly"
    elif 28 <= step <= 31:
        detected = "monthly"
    else:
        detected = "irregular"
    info: dict[str, Any] = {
        "detected": detected,
        "rows": int(len(ts)),
        "dates": int(len(uniq)),
        "first": _iso(uniq[0]),
        "last": _iso(uniq[-1]),
        "has_time": has_time,
        "rows_per_date": rows_per_date,
        "repeated_dates": int((per > 1).sum()),
        "step_days": step,
    }
    if detected in ("daily", "sub_daily"):
        full = pd.date_range(uniq[0], uniq[-1], freq="D")
        missing = full.difference(uniq)
        info.update(
            missing_dates=int(len(missing)),
            missing_examples=[_iso(d) for d in missing[:3]],
            longest_gap=_longest_run(~full.isin(uniq)),
            weekdays_present=sorted({int(d) for d in uniq.dayofweek}),
        )
    elif detected == "weekly":
        info.update(
            weekday=WEEKDAYS[int(pd.Series(uniq.dayofweek).mode().iloc[0])],
            missing_dates=int(sum(int(d) // 7 - 1 for d in diffs if d % 7 == 0 and d > 7)),
            shifted=int(sum(1 for d in diffs if d % 7 != 0)),
        )
    elif detected == "monthly":
        months = uniq.to_period("M")
        span = pd.period_range(months.min(), months.max(), freq="M")
        anchor = "start" if (uniq.day == 1).all() else "end" if uniq.is_month_end.all() else "mixed"
        info.update(
            missing_dates=int(len(span.difference(months.unique()))),
            repeated_months=int(len(uniq) - months.nunique()),
            anchor=anchor,
        )
    return info


def describe_dates(values: pd.Series, keys: pd.Series | None = None) -> dict[str, Any]:
    """`describe_timestamps` of a raw date column."""
    return describe_timestamps(parse_timestamps(values), keys)


FREQ_WORDS = {
    "sub_daily": "several rows a day",
    "daily": "daily",
    "weekly": "weekly",
    "monthly": "monthly",
    "irregular": "irregular",
    "unknown": "a single date",
}


@dataclass
class Grid:
    """The periods the rows fall into (gaps included) and the daily grid they cover."""

    detected: str
    frequency: str  # sub_daily | daily | weekly | monthly
    period_label: str
    starts: pd.DatetimeIndex  # every period's first day, missing ones included
    lengths: np.ndarray  # days per period
    present: np.ndarray  # the period has a row
    row_period: pd.Series  # the period (first day) of every source row
    info: dict[str, Any]
    combine_periods: bool = False  # True when multiple rows in the same period should be combined per column

    @property
    def unit(self) -> str:
        return UNIT[self.frequency]

    @property
    def days(self) -> pd.DatetimeIndex:
        return pd.date_range(self.starts[0], self.starts[-1] + pd.Timedelta(days=int(self.lengths[-1]) - 1), freq="D")

    @property
    def coarse(self) -> bool:
        return self.frequency in ("weekly", "monthly")


def build_grid(ts: pd.Series, config: ResampleConfig, layout: str, keys: pd.Series | None = None) -> Grid:
    """The grid the rows fall on, checked against the chosen frequency (raises `PreparationError`)."""
    info = describe_timestamps(ts, keys)
    detected = info["detected"]
    want = config.frequency
    if detected == "unknown":
        raise PreparationError("The date column holds fewer than two distinct days.")
    if want == "auto":
        if detected == "irregular":
            raise PreparationError(
                f"The dates are every {info['step_days']} days ({info['first']} … {info['last']}); MMM Studio reads daily, "
                "weekly or monthly rows, or several rows a day. Choose how to read them in Resample & fill."
            )
        freq = detected
    else:
        if want == "sub_daily" and info["repeated_dates"] == 0:
            fallback = detected if detected in FREQUENCIES and detected != "sub_daily" else "daily"
            raise PreparationError(
                f"No two rows share a {'date and channel' if layout == 'long' else 'date'}, so there is nothing to "
                f"combine; read the dates as “{FREQUENCIES[fallback][0]}”."
            )
        freq = want
    combine_periods = freq == "sub_daily" or want in ("weekly", "monthly")
    days = ts.dt.normalize()
    label = config.period_label
    if freq in ("sub_daily", "daily"):
        row_period = days
        starts = pd.date_range(days.min(), days.max(), freq="D")
        lengths = np.ones(len(starts), dtype=int)
    elif freq == "weekly":
        if detected == "weekly":
            raw_period = days if label == "start" else days - pd.Timedelta(days=6)
            uniq = pd.DatetimeIndex(raw_period.unique()).sort_values()
            diffs = _day_steps(uniq)
            shifted = [f"{uniq[i]:%Y-%m-%d} → {uniq[i + 1]:%Y-%m-%d} ({int(d)}d)" for i, d in enumerate(diffs) if d % 7 != 0]
            if shifted and want == "auto":
                raise PreparationError(
                    f"The dates are weekly ({uniq[0]:%Y-%m-%d} … {uniq[-1]:%Y-%m-%d}) but skip or shift weeks "
                    f"(e.g. {', '.join(shifted[:3])}): weekly rows must be 7 days apart, on the same weekday, "
                    "or choose “Weekly → days” in Resample & fill to align them by week."
                )
            if shifted:
                anchor_dow = int(pd.Series(days.dt.dayofweek).mode().iloc[0])
                delta = ((days.dt.dayofweek - anchor_dow + 3) % 7) - 3
                snapped = days - pd.to_timedelta(delta, unit="D")
                row_period = snapped if label == "start" else snapped - pd.Timedelta(days=6)
                uniq = pd.DatetimeIndex(row_period.unique()).sort_values()
            else:
                row_period = raw_period
            starts = pd.date_range(uniq[0], uniq[-1], freq="7D")
            lengths = np.full(len(starts), 7, dtype=int)
        else:
            d0 = days.min()
            week_idx = (days - d0).dt.days // 7
            row_period = d0 + pd.to_timedelta(week_idx * 7, unit="D")
            starts = pd.date_range(row_period.min(), row_period.max(), freq="7D")
            lengths = np.full(len(starts), 7, dtype=int)
            if detected in ("daily", "sub_daily"):
                lengths[-1] = max(1, min(7, int((days.max() - starts[-1]).days) + 1))
    else:
        row_period = days.dt.to_period("M").dt.start_time
        starts = pd.date_range(row_period.min(), row_period.max(), freq="MS")
        lengths = starts.days_in_month.to_numpy().astype(int)
    present = starts.isin(pd.DatetimeIndex(row_period.unique()))
    missing = starts[~present]
    if config.fill_missing_dates and layout == "long":
        raise PreparationError(
            "“Fill missing dates” needs a wide table (one row per date). In a long table a missing channel-day stays "
            "missing, and the model flags those days with missing-row controls."
        )
    if len(missing) and not config.fill_missing_dates and not (layout == "long" and freq in ("daily", "sub_daily")):
        n = len(missing)
        hint = "switch on “Fill missing dates” in Resample & fill" if layout == "wide" else "add them"
        if freq in ("daily", "sub_daily"):
            raise PreparationError(
                f"The table skips {n} day{'s' if n > 1 else ''} (e.g. {_examples(missing)}); a daily model needs every day "
                f"between the first and the last date. Add the missing days, or {hint}."
            )
        if freq == "weekly":
            raise PreparationError(
                f"The dates are weekly ({_iso(starts[0])} … {_iso(starts[-1])}) but skip or shift weeks: {n} week"
                f"{'s are' if n > 1 else ' is'} missing (e.g. {_examples(missing)}). Add {'them' if n > 1 else 'it'}"
                f"{', or ' + hint if layout == 'wide' else ''}."
            )
        raise PreparationError(
            f"The dates are monthly ({starts[0]:%Y-%m} … {starts[-1]:%Y-%m}) but skip {n} month{'s' if n > 1 else ''} "
            f"(e.g. {_examples(missing, fmt='%Y-%m')}). Add {'them' if n > 1 else 'it'}{', or ' + hint if layout == 'wide' else ''}."
        )
    return Grid(
        detected=detected,
        frequency=freq,
        period_label=label,
        starts=starts,
        lengths=lengths,
        present=np.asarray(present),
        row_period=row_period,
        info=info,
        combine_periods=combine_periods,
    )


# --------------------------------------------------------------------------------------------------------------------
# The three operations on one series
# --------------------------------------------------------------------------------------------------------------------


def _nearest(s: pd.Series) -> pd.Series:
    """`s` with every blank set to the closest observed value by position (the earlier one on a tie)."""
    miss = s.isna().to_numpy()
    ok = np.flatnonzero(~miss)
    if not miss.any() or not len(ok):
        return s
    idx = np.flatnonzero(miss)
    right = np.searchsorted(ok, idx)
    r = ok[np.clip(right, 0, len(ok) - 1)]
    left = ok[np.clip(right - 1, 0, len(ok) - 1)]
    use_left = (right > 0) & ((right >= len(ok)) | (idx - left <= r - idx))
    src = np.where(use_left, left, r)
    out = s.copy()
    out.iloc[idx] = s.iloc[src].to_numpy()
    return out


def fill_series(s: pd.Series, method: str, value: float | None = None, *, weekdays: np.ndarray | None = None) -> tuple[pd.Series, int]:
    """`s` (one value per period, in order) with its blanks filled by `method`, and how many blanks took the nearest
    observed value because the method could not reach them (before the first value, after the last …)."""
    blank = s.isna().to_numpy()
    if not blank.any() or method in ("none", "baseline"):
        return s, 0
    pos = s.reset_index(drop=True)
    present = pos.dropna()
    if method in ("zero", "constant"):
        out = pos.fillna(0.0 if method == "zero" else float(value if value is not None else 0.0))
        out.index = s.index
        return out, 0
    if present.empty:
        return s, 0
    if method == "ffill":
        out = pos.ffill()
    elif method == "bfill":
        out = pos.bfill()
    elif method == "nearest":
        out = _nearest(pos)
    elif method in INTERPOLATIONS:
        out = pos.astype(float).interpolate(method=method, limit_area="inside")
    elif method == "mean":
        out = pos.fillna(float(present.mean()))
    elif method == "median":
        out = pos.fillna(float(present.median()))
    elif method == "mode":
        out = pos.fillna(present.mode().iloc[0])
    elif method == "moving_average":
        out = pos.fillna(pos.astype(float).rolling(7, center=True, min_periods=1).mean())
    elif method == "seasonal":
        if weekdays is None:
            raise PreparationError("“Same weekday” needs daily data.")
        out = pos.astype(float).groupby(np.asarray(weekdays)).transform(lambda g: g.interpolate(method="linear", limit_area="inside"))
        # A weekday without a later or earlier value of its own takes its nearest same-weekday value
        out = out.groupby(np.asarray(weekdays)).transform(lambda g: g.ffill().bfill())
    elif method == "spread":
        group = pos.notna().cumsum()
        share = pos.groupby(group).transform("first") / pos.groupby(group).transform("size")
        out = share.where(group > 0)
    else:  # pragma: no cover - the plan checks the method
        raise PreparationError(f"Unknown fill method '{method}'.")
    leftover = out.isna().to_numpy() & blank
    if leftover.any():
        out = _nearest(out)
    out.index = s.index
    return out, int(leftover.sum())


def aggregate_series(values: pd.Series, key: pd.Series, method: str) -> pd.Series:
    """One value per key (a day) from several rows."""
    return _agg(values.groupby(key.to_numpy()), method)


def _agg(g: Any, method: str) -> pd.Series:
    if method == "sum":
        return g.sum(min_count=1)
    if method == "mode":
        return g.agg(lambda x: x.mode().iloc[0] if x.notna().any() else np.nan)
    if method in ("first", "last"):
        return getattr(g, method)()
    return g.agg(method)


def _smooth_totals(values: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    """Daily amounts that add up to each period's amount: the steps of a monotone cubic (PCHIP) through the running
    total at the period boundaries. Non-negative amounts stay non-negative. A blank period breaks the curve."""
    from scipy.interpolate import PchipInterpolator

    edges = np.concatenate([[0], np.cumsum(lengths)]).astype(int)
    out = np.full(int(edges[-1]), np.nan)
    finite = np.isfinite(values)
    i, n = 0, len(values)
    while i < n:
        if not finite[i]:
            i += 1
            continue
        j = i
        while j < n and finite[j]:
            j += 1
        seg, seg_len = values[i:j], lengths[i:j]
        if len(seg) == 1:
            daily = np.full(int(seg_len[0]), seg[0] / seg_len[0])
        else:
            x = np.concatenate([[0], np.cumsum(seg_len)]).astype(float)
            y = np.concatenate([[0.0], np.cumsum(seg)])
            daily = np.diff(PchipInterpolator(x, y)(np.arange(0, x[-1] + 1)))
        out[edges[i] : edges[j]] = daily
        i = j
    return out


def _through_middles(values: np.ndarray, lengths: np.ndarray, method: str) -> np.ndarray:
    """Daily levels on a line (`linear`) or a shape-preserving curve (`smooth`) through each period's middle day; the
    days before the first middle and after the last keep the edge value."""
    edges = np.concatenate([[0], np.cumsum(lengths)]).astype(int)
    mids = edges[:-1] + (lengths - 1) / 2.0
    finite = np.isfinite(values)
    x, y = mids[finite], values[finite]
    days = np.arange(edges[-1], dtype=float)
    if len(x) == 0:
        return np.full(len(days), np.nan)
    if len(x) == 1:
        return np.full(len(days), y[0])
    if method == "linear":
        return np.interp(days, x, y)
    from scipy.interpolate import PchipInterpolator

    out = PchipInterpolator(x, y, extrapolate=False)(days)
    out[days < x[0]] = y[0]
    out[days > x[-1]] = y[-1]
    return out


def disaggregate_series(values: pd.Series, lengths: np.ndarray, method: str) -> np.ndarray:
    """One value per day from one value per period (`values` in period order, `lengths` days per period)."""
    arr = values.to_numpy()
    if method == "repeat":
        return np.repeat(arr, lengths)
    num = values.astype(float).to_numpy()
    if method == "even":
        return np.repeat(num / lengths, lengths)
    if method == "first_day":
        out = np.zeros(int(lengths.sum()))
        edges = np.concatenate([[0], np.cumsum(lengths)[:-1]]).astype(int)
        out[edges] = num
        blank = np.repeat(~np.isfinite(num), lengths)
        out[blank] = np.nan
        return out
    if method == "smooth":
        return _smooth_totals(num, lengths)
    raise PreparationError(f"Unknown disaggregation '{method}'.")  # pragma: no cover


def disaggregate_level(values: pd.Series, lengths: np.ndarray, method: str) -> np.ndarray:
    if method in ("linear", "smooth"):
        return _through_middles(values.astype(float).to_numpy(), lengths, method)
    return np.repeat(values.to_numpy(), lengths)


# --------------------------------------------------------------------------------------------------------------------
# The plan: the grid and every column's methods, checked before anything runs
# --------------------------------------------------------------------------------------------------------------------


@dataclass
class ColumnPlan:
    spec: ColumnSpec
    aggregate: str
    fill: str
    fill_value: float | None
    disaggregate: str
    explicit: tuple[str, ...]  # the operations the user (or Gemini) chose; the rest are the role's default


@dataclass
class Plan:
    grid: Grid
    columns: list[ColumnPlan]
    layout: str


def _values(raw: pd.Series, kind: Kind) -> pd.Series:
    if kind == "category":
        text = raw.astype("string").str.strip()
        return pd.Series([None if pd.isna(v) or v == "" else str(v) for v in text], index=raw.index, dtype=object)
    return _numeric(raw).astype(float)


def plan_resample(df: pd.DataFrame, mapping: ColumnMapping) -> tuple[Plan | None, list[str]]:
    """The checked plan, or every reason it cannot run (in plain words). The date column must already parse."""
    config = mapping.resample
    errors: list[str] = []
    ts = parse_timestamps(df[mapping.date])
    keys = df[mapping.channel] if mapping.layout == "long" and mapping.channel in df.columns else None
    try:
        grid = build_grid(ts, config, mapping.layout, keys)
    except PreparationError as exc:
        errors.append(str(exc))
        grid = None
    specs = column_specs(df, mapping)
    by_col = {s.column: s for s in specs}
    seen: set[str] = set()
    for entry in config.columns:
        if entry.column in seen:
            errors.append(f"Resampling is set twice for '{entry.column}'.")
        seen.add(entry.column)
        if entry.column not in by_col:
            errors.append(f"Resampling is set for '{entry.column}', which the mapping does not use.")
    plans: list[ColumnPlan] = []
    n_rows = len(df)
    for spec in specs:
        entry = config.for_column(spec.column)
        default = default_methods(spec)
        allowed = allowed_methods(spec)
        chosen: dict[str, Any] = {}
        explicit: list[str] = []
        for op in ("aggregate", "fill", "disaggregate"):
            val = getattr(entry, op) if entry is not None else None
            if val is None:
                chosen[op] = default[op]
                continue
            if val not in allowed[op]:
                table = {"aggregate": AGGREGATES, "fill": FILLS, "disaggregate": DISAGGREGATES}[op]
                options = ", ".join(table[v][0] for v in allowed[op])
                why = (
                    "a long table's missing channel-days stay missing (the missing-row controls flag them)"
                    if op == "fill" and spec.long and spec.role != "control"
                    else f"it does not apply to {'a ' + spec.kind if spec.kind != 'flow' else 'an amount'} column"
                )
                errors.append(f"'{spec.column}' ({spec.label}): “{table[val][0]}” cannot be used: {why}. Choose {options}.")
                chosen[op] = default[op]
                continue
            chosen[op] = val
            explicit.append(op)
        fill = chosen["fill"]
        fill_value = entry.fill_value if entry is not None else None
        if fill == "constant":
            if fill_value is None or not np.isfinite(fill_value):
                errors.append(f"'{spec.column}' ({spec.label}): enter the value that fills its gaps.")
            elif spec.kind == "flag" and fill_value not in (0, 1):
                errors.append(f"'{spec.column}' ({spec.label}) is a 0/1 flag: fill it with 0 or 1.")
            elif spec.role in ("spend", "costs", "impressions", "clicks") and fill_value < 0:
                errors.append(f"'{spec.column}' ({spec.label}) cannot be negative: fill it with 0 or more.")
        if fill == "seasonal" and grid is not None and grid.coarse:
            errors.append(f"'{spec.column}' ({spec.label}): “Same weekday” needs daily data; the rows are {grid.frequency}.")
        raw = df[spec.column]
        present = int(raw.notna().sum()) if spec.kind == "category" else int(_numeric(raw).notna().sum())
        n_blank = n_rows - present
        if "fill" not in explicit and spec.role in ("target", "control") and spec.kind != "category" and n_blank:
            share = n_blank / n_rows if n_rows else 1.0
            if share > MAX_BLANK_SHARE or present < 2:
                errors.append(
                    f"'{spec.column}' ({spec.label}) is blank on {n_blank} rows ({100 * share:.0f}%); fill them in, or "
                    "choose how to fill them in Resample & fill."
                )
        need = 0 if fill in ("none", "baseline", "zero", "constant") else MIN_VALUES.get(fill, 1)
        if n_blank and present < need:
            errors.append(
                f"'{spec.column}' ({spec.label}) has {present} value{'s' if present != 1 else ''}; "
                f"“{FILLS[fill][0]}” needs at least {need}."
            )
        plans.append(
            ColumnPlan(
                spec=spec,
                aggregate=chosen["aggregate"],
                fill=fill,
                fill_value=fill_value if fill == "constant" else None,
                disaggregate=chosen["disaggregate"],
                explicit=tuple(explicit),
            )
        )
    if errors or grid is None:
        return None, errors
    return Plan(grid=grid, columns=plans, layout=mapping.layout), []


def resample_errors(df: pd.DataFrame, mapping: ColumnMapping) -> list[str]:
    """Why the mapping's resampling cannot run on `df` (empty when it can); the date column must already parse."""
    return plan_resample(df, mapping)[1]


# --------------------------------------------------------------------------------------------------------------------
# Running the plan
# --------------------------------------------------------------------------------------------------------------------


@dataclass
class ColumnReport:
    column: str
    role: str
    label: str
    kind: str
    aggregate: str
    fill: str
    fill_value: float | None
    disaggregate: str
    explicit: list[str]
    defaults: dict[str, str]
    allowed: dict[str, list[str]]
    rows_merged: int = 0
    blank: int = 0
    added: int = 0
    filled: int = 0
    nearest_fallback: int = 0
    spread_values: int = 0
    periods: int = 0
    imputed_days: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Prepared:
    """The daily values of every mapped column (wide: by source column; long: the canonical rows) and the report."""

    grid: Grid
    columns: list[ColumnReport]
    warnings: list[str]
    daily: pd.DataFrame  # index: the daily grid; one column per mapped source column (long: per-day totals/controls)
    imputed: pd.DataFrame  # same shape, True on days whose value was imputed
    long_rows: pd.DataFrame | None = None
    long_controls: pd.DataFrame | None = None
    added_periods: list[str] = field(default_factory=list)
    merged_rows: int = 0

    def report(self, max_series: int = 40) -> dict[str, Any]:
        """What the wizard shows: the grid, each column's methods and counts, and the daily series for the preview."""
        g = self.grid
        numeric = [c for c in self.columns if c.kind != "category"][:max_series]
        series = []
        for c in numeric:
            vals = pd.to_numeric(self.daily[c.column], errors="coerce").to_numpy(dtype=float)
            series.append(
                {
                    "column": c.column,
                    "label": c.label,
                    "values": [None if not np.isfinite(v) else round(float(v), 6) for v in vals],
                    "imputed": [int(i) for i in np.flatnonzero(self.imputed[c.column].to_numpy())],
                }
            )
        return {
            "grid": {
                **g.info,
                "frequency": g.frequency,
                "period_label": g.period_label,
                "periods": int(len(g.starts)),
                "added": len(self.added_periods),
                "added_examples": self.added_periods[:3],
                "days": int(len(self.daily)),
                "first_day": _iso(self.daily.index[0]) if len(self.daily) else None,
                "last_day": _iso(self.daily.index[-1]) if len(self.daily) else None,
                "merged_rows": self.merged_rows,
            },
            "columns": [c.to_dict() for c in self.columns],
            "series": {"dates": [_iso(d) for d in self.daily.index], "columns": series},
        }


def _report(cp: ColumnPlan) -> ColumnReport:
    return ColumnReport(
        column=cp.spec.column,
        role=cp.spec.role,
        label=cp.spec.label,
        kind=cp.spec.kind,
        aggregate=cp.aggregate,
        fill=cp.fill,
        fill_value=cp.fill_value,
        disaggregate=cp.disaggregate,
        explicit=list(cp.explicit),
        defaults=default_methods(cp.spec),
        allowed=allowed_methods(cp.spec),
    )


def _check_negative(cp: ColumnPlan, before: pd.Series, after: pd.Series) -> None:
    """An amount that is never negative must not become negative by imputation (a spline can overshoot below 0)."""
    if cp.spec.kind != "flow" or cp.fill not in ("quadratic", "cubic", "akima"):
        return
    observed = before.dropna()
    if len(observed) and float(observed.min()) >= 0:
        neg = int(((after < 0) & before.isna()).sum())
        if neg:
            raise PreparationError(
                f"'{cp.spec.column}' ({cp.spec.label}) is never negative, but {FILLS[cp.fill][0].lower()} gives {neg} "
                f"negative value{'s' if neg > 1 else ''}; choose PCHIP or linear interpolation (they stay between the "
                "values around a gap)."
            )


def _period_series(cp: ColumnPlan, values: pd.Series, grid: Grid, key: pd.Series, report: ColumnReport) -> tuple[pd.Series, pd.Series]:
    """The column's filled value per period and the mask of imputed periods."""
    if grid.combine_periods and (grid.frequency == "sub_daily" or key.duplicated().any()):
        by_period = aggregate_series(values, key, cp.aggregate)
        by_period.index = pd.DatetimeIndex(by_period.index)
    else:
        by_period = pd.Series(values.to_numpy(), index=pd.DatetimeIndex(key.to_numpy()))
    series = by_period.reindex(grid.starts)
    if series.dtype != object and cp.spec.kind != "category":
        series = series.astype(float)
    gap = series.isna()
    report.periods = int(len(series))
    report.blank = int((gap & grid.present).sum())
    report.added = int((gap & ~grid.present).sum())
    weekdays = grid.starts.dayofweek.to_numpy() if not grid.coarse else None
    filled, fallback = fill_series(series, cp.fill, cp.fill_value, weekdays=weekdays)
    _check_negative(cp, series, filled)
    if cp.fill in ("none", "baseline"):
        imputed = pd.Series(cp.fill == "baseline", index=series.index) & gap
    else:
        imputed = gap & filled.notna()
    report.filled = int(imputed.sum())
    report.nearest_fallback = fallback
    if cp.fill == "spread":
        report.spread_values = int((series.notna() & gap.shift(-1, fill_value=False)).sum())
    return filled, imputed


def _to_days(cp: ColumnPlan, filled: pd.Series, grid: Grid) -> np.ndarray:
    if not grid.coarse:
        return filled.to_numpy()
    if cp.spec.kind == "flow":
        return disaggregate_series(filled, grid.lengths, cp.disaggregate)
    return disaggregate_level(filled, grid.lengths, cp.disaggregate)


def run_wide(frame: pd.DataFrame, ts: pd.Series, plan: Plan) -> Prepared:
    """The daily value of every mapped column of a wide table (rows already free of exact duplicates)."""
    grid = plan.grid
    key = grid.row_period.loc[frame.index]
    if not grid.combine_periods:
        repeated = key.duplicated(keep=False)
        if repeated.any():
            unit = grid.unit
            raise PreparationError(
                f"{int(repeated.sum())} rows share a {'date' if unit == 'day' else unit} but have different values "
                f"(e.g. {_examples(pd.DatetimeIndex(key[repeated].unique()).sort_values())}). A wide table has one row per "
                f"{unit}; combine or remove the extra rows, or read the dates as “Several rows a day” in Resample & fill "
                "to combine them per column."
                if unit == "day"
                else f"{int(repeated.sum())} rows fall in the same {unit} (e.g. "
                f"{_examples(pd.DatetimeIndex(key[repeated].unique()).sort_values())}); {grid.frequency} data has one row per {unit}."
            )
    days = grid.days
    daily: dict[str, Any] = {}
    imputed: dict[str, Any] = {}
    reports: list[ColumnReport] = []
    merged = int(len(frame) - key.nunique()) if grid.combine_periods else 0
    for cp in plan.columns:
        rep = _report(cp)
        values = _values(frame[cp.spec.column], cp.spec.kind)
        if grid.combine_periods:
            rep.rows_merged = merged
        filled, imp = _period_series(cp, values, grid, key, rep)
        daily[cp.spec.column] = _to_days(cp, filled, grid)
        imputed[cp.spec.column] = np.repeat(imp.to_numpy(dtype=bool), grid.lengths)
        rep.imputed_days = int(imputed[cp.spec.column].sum())
        reports.append(rep)
    out = pd.DataFrame(daily, index=days)
    imp_frame = pd.DataFrame(imputed, index=days)
    added = [_iso(d) for d in grid.starts[~grid.present]]
    prepared = Prepared(grid=grid, columns=reports, warnings=[], daily=out, imputed=imp_frame, added_periods=added, merged_rows=merged)
    prepared.warnings = _warnings(prepared, plan)
    return prepared


def run_long(rows: pd.DataFrame, ts: pd.Series, plan: Plan, metric_cols: dict[str, str], controls: pd.DataFrame) -> Prepared:
    """A long table on the daily grid: its canonical rows (`long_rows`) and one value per day of every control.

    `rows` holds `channel` and the canonical metric columns (`sales_by_channel` …), `metric_cols` maps each canonical
    column to its source column ("" when the upload has none), `controls` the raw control columns by source name. A
    channel-day without a row stays without one: the model flags it with the missing-row controls.
    """
    grid = plan.grid
    by_col = {cp.spec.column: cp for cp in plan.columns}
    key = grid.row_period
    reports = {cp.spec.column: _report(cp) for cp in plan.columns}
    merged = 0
    days = grid.days
    if grid.frequency == "daily":
        out = rows.assign(date=key.to_numpy())
    elif grid.frequency == "sub_daily":
        frame = rows.assign(date=key.to_numpy())
        for canon in metric_cols:
            frame[canon] = pd.to_numeric(frame[canon], errors="coerce")
        grouped = frame.groupby(["date", "channel"], sort=True)
        out = pd.DataFrame(
            {canon: _agg(grouped[canon], by_col[src].aggregate if src in by_col else "first") for canon, src in metric_cols.items()}
        ).reset_index()
        merged = int(len(frame) - len(out))
        for src in metric_cols.values():
            if src in reports:
                reports[src].rows_merged = merged
    else:
        frame = rows.assign(_period=key.to_numpy()).drop_duplicates()
        conflicts = frame.duplicated(subset=["_period", "channel"], keep=False)
        if conflicts.any():
            if not grid.combine_periods:
                ex = frame.loc[conflicts, ["_period", "channel"]].drop_duplicates().head(3)
                sample = "; ".join(f"{pd.Timestamp(d):%Y-%m-%d} · {c}" for d, c in ex.itertuples(index=False))
                raise PreparationError(
                    f"Rows share a {grid.unit} and a channel but have different values (e.g. {sample}); each channel has one row "
                    f"per {grid.unit}."
                )
            for canon in metric_cols:
                frame[canon] = pd.to_numeric(frame[canon], errors="coerce")
            grouped = frame.groupby(["_period", "channel"], sort=True)
            agg_frame = pd.DataFrame(
                {canon: _agg(grouped[canon], by_col[src].aggregate if src in by_col else "sum") for canon, src in metric_cols.items()}
            ).reset_index()
            merged = int(len(frame) - len(agg_frame))
            frame = agg_frame
            for src in metric_cols.values():
                if src in reports:
                    reports[src].rows_merged = merged
        pieces: list[pd.DataFrame] = []
        for ch, part in frame.groupby("channel", sort=False):
            part = part.set_index("_period")
            cols = {
                canon: disaggregate_series(
                    pd.to_numeric(part[canon], errors="coerce").reindex(grid.starts),
                    grid.lengths,
                    by_col[src].disaggregate if src in by_col else "even",
                )
                for canon in metric_cols
                for src in (metric_cols[canon],)
            }
            keep = np.repeat(grid.starts.isin(part.index), grid.lengths)
            pieces.append(pd.DataFrame({"date": days, "channel": ch, **cols})[keep])
        out = pd.concat(pieces, ignore_index=True)
    # Controls: one value per date (the same on every channel's row), filled and disaggregated like a wide table's
    daily_ctrl: dict[str, Any] = {}
    imputed: dict[str, Any] = {}
    for cp in (p for p in plan.columns if p.spec.role == "control"):
        rep = reports[cp.spec.column]
        values = _values(controls[cp.spec.column], cp.spec.kind)
        coarsening = grid.combine_periods and grid.coarse
        group_key = ts if grid.frequency == "sub_daily" else (ts.dt.normalize() if coarsening else key)
        what = "time" if grid.frequency == "sub_daily" else ("day" if coarsening else grid.unit)
        spread = values.groupby(group_key.to_numpy()).nunique()
        if (spread > 1).any():
            raise PreparationError(
                f"The control '{cp.spec.column}' differs between rows of the same {what} (e.g. "
                f"{_examples(pd.DatetimeIndex(spread.index[spread > 1]))}); a control has one value per {what}."
            )
        per_key = values.groupby(group_key.to_numpy()).first()
        stamps = pd.DatetimeIndex(per_key.index)
        if grid.frequency == "sub_daily":
            key_k = pd.Series(stamps.normalize())
        elif coarsening:
            map_period = pd.Series(key.to_numpy(), index=ts.dt.normalize().to_numpy()).groupby(level=0).first()
            key_k = pd.Series(map_period.reindex(stamps).to_numpy())
        else:
            key_k = pd.Series(stamps)
        if grid.combine_periods:
            rep.rows_merged = int(len(per_key) - key_k.nunique())
        filled, imp = _period_series(cp, per_key.reset_index(drop=True), grid, key_k, rep)
        daily_ctrl[cp.spec.column] = _to_days(cp, filled, grid)
        imputed[cp.spec.column] = np.repeat(imp.to_numpy(dtype=bool), grid.lengths)
        rep.imputed_days = int(imputed[cp.spec.column].sum())
    # The preview shows each metric's daily total over the channels
    totals: dict[str, Any] = {}
    for canon, src in metric_cols.items():
        if src in reports:
            vals = pd.to_numeric(out[canon], errors="coerce")
            totals[src] = vals.groupby(pd.DatetimeIndex(out["date"])).sum(min_count=1).reindex(days).to_numpy()
            imputed[src] = np.zeros(len(days), dtype=bool)
            reports[src].periods = int(len(grid.starts))
    preview = pd.DataFrame({**totals, **daily_ctrl}, index=days)
    prepared = Prepared(
        grid=grid,
        columns=[reports[cp.spec.column] for cp in plan.columns],
        warnings=[],
        daily=preview,
        imputed=pd.DataFrame({k: imputed[k] for k in preview.columns}, index=days),
        long_rows=out,
        long_controls=pd.DataFrame(daily_ctrl, index=days),
        merged_rows=merged,
    )
    prepared.warnings = _warnings(prepared, plan)
    return prepared


# --------------------------------------------------------------------------------------------------------------------
# Warnings: every conversion in plain words
# --------------------------------------------------------------------------------------------------------------------


def _phrase(rep: ColumnReport) -> str:
    phrase = PHRASES.get(rep.fill, rep.fill)
    if rep.fill == "constant":
        phrase = phrase.format(value=f"{rep.fill_value:g}")
    return phrase


def _entry(rep: ColumnReport, name: str) -> str:
    text = f"{name}: {rep.filled} {_phrase(rep)}"
    if rep.nearest_fallback:
        text += f" ({rep.nearest_fallback} at the edges took the nearest observed value)"
    return text


def _warnings(prepared: Prepared, plan: Plan) -> list[str]:
    grid = prepared.grid
    unit = grid.unit
    out: list[str] = []
    if prepared.merged_rows:
        groups: dict[str, list[str]] = {}
        for rep in prepared.columns:
            groups.setdefault(AGGREGATES[rep.aggregate][0].lower(), []).append(rep.column)
        how = "; ".join(f"{k}: {', '.join(v)}" for k, v in groups.items())
        if grid.frequency == "sub_daily":
            out.append(f"{prepared.merged_rows} rows that share a day were combined into one row per day ({how}).")
        else:
            out.append(f"{prepared.merged_rows} rows that share a {unit} were combined into one row per {unit} ({how}).")
    if prepared.added_periods:
        n = len(prepared.added_periods)
        out.append(
            f"{n} missing {unit}{'s were' if n > 1 else ' was'} added (e.g. {', '.join(prepared.added_periods[:3])}) and "
            "filled like blank cells."
        )
    by_role: dict[str, list[ColumnReport]] = {}
    for rep in prepared.columns:
        if rep.filled:
            by_role.setdefault("spend" if rep.role == "spend" else "metric" if rep.role in ("impressions", "clicks", "sales", "costs") else rep.role, []).append(rep)
    for rep in by_role.get("target", []):
        extra = f" ({rep.nearest_fallback} at the edges took the nearest observed value)" if rep.nearest_fallback else ""
        what = "blank or missing" if rep.added else "blank"
        out.append(
            f"'{rep.column}' (the target) was {what} on {rep.filled} {'row' if unit == 'day' else unit}"
            f"{'s' if rep.filled > 1 else ''}; missing values were {_phrase(rep)}{extra}."
        )
    spend = by_role.get("spend", [])
    if spend:
        listed = ", ".join(f"{r.label.removesuffix(' spend')}: {r.filled}" for r in spend if r.fill == "zero")
        if listed:
            out.append(f"Blank spend cells were read as no spend ({listed}).")
        others = "; ".join(_entry(r, r.label.removesuffix(" spend")) for r in spend if r.fill != "zero")
        if others:
            out.append(f"Blank spend cells were imputed ({others}).")
    metrics = by_role.get("metric", [])
    if metrics:
        out.append(f"Blank channel metric cells were imputed ({'; '.join(_entry(r, r.label) for r in metrics)}).")
    controls = by_role.get("control", [])
    if controls:
        out.append(
            f"Blank control cells were imputed ({'; '.join(_entry(r, r.label.removeprefix('the control ')) for r in controls)})."
        )
    for rep in prepared.columns:
        if rep.spread_values:
            out.append(f"'{rep.column}': {rep.spread_values} values were spread evenly over the blank rows after them (totals kept).")
        if rep.periods and rep.filled > 0.5 * rep.periods and rep.fill not in ("none",):
            out.append(
                f"Most of '{rep.column}' is imputed ({rep.filled} of {rep.periods} {unit}s): what the model learns from "
                "it rests on the fill method."
            )
    if grid.coarse:
        n = int(len(grid.starts))
        days = prepared.daily.index
        groups2: dict[str, list[str]] = {}
        for rep in prepared.columns:
            groups2.setdefault(DISAGGREGATES[rep.disaggregate][0].lower(), []).append(rep.column)
        how = "; ".join(f"{k}: {', '.join(v)}" for k, v in groups2.items())
        out.append(
            f"{grid.frequency.capitalize()} data ({n} {unit}s, {_iso(grid.starts[0])} … {_iso(grid.starts[-1])}) was "
            f"disaggregated into {len(days)} daily rows ({_iso(days[0])} … {_iso(days[-1])}) per column ({how})."
        )
    return out


# --------------------------------------------------------------------------------------------------------------------
# What Gemini sees of each column, to choose its methods
# --------------------------------------------------------------------------------------------------------------------


def _autocorr(x: pd.Series, lag: int) -> float | None:
    x = x.astype(float)
    if x.notna().sum() < lag + 3:
        return None
    v = x.autocorr(lag)
    return None if v is None or not np.isfinite(v) else round(float(v), 2)


def column_diagnostics(df: pd.DataFrame, mapping: ColumnMapping) -> dict[str, Any]:
    """The facts that decide how to resample and fill each mapped column: blanks and the longest blank run (at the
    data's own frequency, missing dates included), zero share, steps, autocorrelation, and on which weekday or day of
    the month the values fall."""
    ts = parse_timestamps(df[mapping.date])
    keys = df[mapping.channel] if mapping.layout == "long" and mapping.channel in df.columns else None
    info = describe_timestamps(ts, keys)
    out: list[dict[str, Any]] = []
    for spec in column_specs(df, mapping):
        raw = df[spec.column]
        values = _values(raw, spec.kind)
        d: dict[str, Any] = {
            "column": spec.column,
            "role": spec.role,
            "label": spec.label,
            "kind": spec.kind,
            "rows": int(len(values)),
            "blank": int(values.isna().sum()),
            "allowed": allowed_methods(spec),
            "default": default_methods(spec),
        }
        if mapping.layout == "wide" and info["detected"] in ("daily", "weekly", "monthly"):
            order = ts.sort_values().index
            s = values.loc[order]
            d.update(
                leading_blank=int(s.notna().cumsum().eq(0).sum()),
                trailing_blank=int(s[::-1].notna().cumsum().eq(0).sum()),
                longest_blank_run=_longest_run(s.isna().to_numpy()),
            )
        if spec.kind != "category":
            num = values.dropna()
            if len(num):
                d.update(
                    min=round(float(num.min()), 4),
                    max=round(float(num.max()), 4),
                    mean=round(float(num.mean()), 4),
                    zero_share=round(float((num == 0).mean()), 3),
                    negative=int((num < 0).sum()),
                    distinct=int(num.nunique()),
                )
                if mapping.layout == "wide":
                    order = ts.sort_values().index
                    s = values.loc[order].reset_index(drop=True)
                    d["same_as_previous_share"] = round(float((s.diff() == 0).mean()), 3)
                    d["autocorr_lag1"] = _autocorr(s, 1)
                    if info["detected"] == "daily":
                        d["autocorr_lag7"] = _autocorr(s, 7)
                        nz = ts.loc[order][values.loc[order].fillna(0) != 0]
                        if len(nz):
                            wd = nz.dt.dayofweek.value_counts(normalize=True)
                            d["top_weekday_share_of_nonzero"] = round(float(wd.max()), 2)
                            d["top_weekday"] = WEEKDAYS[int(wd.idxmax())]
                            d["first_of_month_share_of_nonzero"] = round(float((nz.dt.day == 1).mean()), 2)
        else:
            d["categories"] = int(values.nunique())
        out.append(d)
    return {"dates": info, "columns": out, "layout": mapping.layout}
