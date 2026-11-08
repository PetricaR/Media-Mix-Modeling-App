"""Gemini's resampling and gap-filling plan for the upload wizard's "Resample & fill" step.

Gemini sees the mapped upload: how its dates are spaced (detected frequency, rows per date, missing dates, the weekday of
weekly rows, the anchor of monthly ones), and for every mapped column the facts that decide how to impute it (blanks and
the longest blank run, leading and trailing blanks, zero share, how often a value repeats the previous one,
autocorrelation at lag 1 and 7, on which weekday or day of the month non-zero values fall), with the methods allowed for
that column and its default. It answers in JSON: the frequency to read the dates at, whether a weekly date is its week's
first or last day, whether to add the missing dates, and per column the aggregation, fill and disaggregation, each with
a reason.

Nothing it says is applied blindly. `review_resample` checks every item: the frequency must fit the dates, a column must
be mapped, a method must be one the column allows, and the accepted plan is dry-run through the same checks as an upload
(`core.resample.plan_resample`, then the real resampling). Applying it is the user's choice in the wizard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from mmm_studio.core.mapping import WorkbookValidationError, canonicalise_report, parse_timestamps
from mmm_studio.core.resample import (
    AGGREGATES,
    DISAGGREGATES,
    FILLS,
    FREQUENCIES,
    PreparationError,
    allowed_methods,
    build_grid,
    column_specs,
    default_methods,
    resample_errors,
)
from mmm_studio.core.schema import ColumnMapping, ColumnResample, ResampleConfig
from mmm_studio.explain.context import clean_text

PROMPT_VERSION = "resample-v1"
MAX_PROMPT_CHARS = 60_000
MAX_SAMPLE_ROWS = 8

SYSTEM_INSTRUCTION = """\
You are a senior marketing-mix-modelling (MMM) data engineer. MMM Studio fits Google Meridian on a daily grid: one value \
per day for the target, each channel's spend and each control. You prepare a user's uploaded table for that grid. You \
choose (1) how to read its dates (the frequency) and (2) for every mapped column how to combine several rows of a day \
(aggregate), how to fill blank cells and missing dates (fill, applied at the data's own frequency) and how to split a \
weekly or monthly value into its days (disaggregate). Use only the methods allowed for each column.

Frequency:
- Confirm the detected frequency unless the facts or errors call for another choice. "sub_daily" combines rows that \
share a day (hourly exports, one row per region or store). "weekly" and "monthly" split weekly or monthly rows into \
days (and, when chosen on finer or irregular dates, first combine rows within each week or month per column, or align \
shifted weekly dates). "daily" reads one row per day (with fill_missing_dates on, coarser or irregular dates can also \
be filled directly on the daily grid).
- Weekly rows: period_label "start" when a date is the first day of its week, "end" when it is the last (a column named \
like "week ending", or dates on the day a reporting week closes). Monthly rows always use the date's calendar month.
- fill_missing_dates: switch it on when the missing dates are few or explainable (a missing export, a closed day) and \
every column can be filled sensibly; leave it off when the gaps are long or many, and say why.

Columns (amounts are the target, spend, impressions, clicks and attributed sales; levels are numeric controls such as a \
price, an index or a temperature; flags are 0/1 controls; categories are text controls):
- Aggregate amounts with sum; levels with mean (last for an end-of-day level); flags with max; categories with mode.
- Spend: a blank usually means no spend: zero. Use "spread" when spend is booked on one day per week or month (non-zero \
values concentrated on one weekday or on the 1st, blanks between). Interpolate spend only when blanks are missing \
reports of continuous spend.
- Target: never zero unless the business was closed. With a strong weekly pattern (high autocorrelation at lag 7) use \
"seasonal"; short gaps in a smooth series: "linear" or "pchip"; avoid "cubic" and "quadratic" near jumps (they \
overshoot); long gaps: "seasonal" or "moving_average", and say the imputed share is high.
- Levels: "ffill" when they change in steps (the value repeats the previous row most of the time), "linear" or "pchip" \
when they drift smoothly, "median" for a noisy level without trend.
- Flags: a blank usually means off: zero; a gap inside a long on-period (a lockdown): "ffill" or "nearest".
- Categories: "baseline" unless a gap sits inside a run of one category ("ffill" or "nearest").
- Optional channel metrics (impressions, clicks, attributed sales) are not model inputs: "none" keeps their gaps.
- Splitting weeks or months: amounts "even" (equal shares), "smooth" when they move smoothly over time (keeps each \
period's total), "first_day" for one-off bookings; levels "repeat" for steps, "linear" or "smooth" for smooth levels.
- Keep a column's default when it is right, and say so briefly.

Rules:
- Give every mapped column exactly one entry, using the column name exactly as written.
- Write "default" to keep a column's default method, and for an operation that does not apply: aggregate unless the \
dates are read as several rows a day, disaggregate unless they are weekly or monthly, fill when the column has no blanks \
and no dates are added.
- fill_value only matters for fill "constant"; otherwise write 0.
- Quote only numbers that appear in the facts you were given. Keep every reason to one sentence.
"""


AggregateChoice = Literal["default", "sum", "mean", "median", "min", "max", "first", "last", "mode"]
FillChoice = Literal[
    "default",
    "none",
    "zero",
    "constant",
    "baseline",
    "ffill",
    "bfill",
    "nearest",
    "linear",
    "quadratic",
    "cubic",
    "pchip",
    "akima",
    "mean",
    "median",
    "mode",
    "moving_average",
    "seasonal",
    "spread",
]
DisaggregateChoice = Literal["default", "even", "smooth", "first_day", "repeat", "linear"]


class ColumnAdvice(BaseModel):
    column: str = Field(description="A mapped column, exactly as written.")
    aggregate: AggregateChoice = Field(description="How several rows of a day become one, or 'default'.")
    fill: FillChoice = Field(description="How blank cells and added dates are filled, or 'default'.")
    fill_value: float = Field(description="The value for fill 'constant'; otherwise 0.")
    disaggregate: DisaggregateChoice = Field(description="How a weekly or monthly value is split into days, or 'default'.")
    reason: str = Field(description="One sentence: why these methods suit this column's facts.")
    confidence: Literal["high", "medium", "low"]


class ResampleAdvice(BaseModel):
    """Gemini's plan for one mapped table."""

    summary: str = Field(description="2–3 sentences: what the dates and gaps look like and how the table reaches the daily grid.")
    frequency: Literal["sub_daily", "daily", "weekly", "monthly"]
    frequency_reason: str
    period_label: Literal["start", "end"]
    period_label_reason: str = Field(description="Weekly rows: why the date is the week's first or last day; otherwise empty string.")
    fill_missing_dates: bool
    missing_dates_reason: str
    columns: list[ColumnAdvice]
    notes: list[str] = Field(description="Up to 4 short observations the user should know (e.g. a column that is mostly imputed).")


class ResampleAdviceFailed(ValueError):
    """Gemini's answer could not be read as a resampling plan."""


def parse_resample_advice(text: str) -> ResampleAdvice:
    try:
        return ResampleAdvice.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ResampleAdviceFailed("Gemini's resampling plan could not be read. Try again.") from exc


# --------------------------------------------------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------------------------------------------------


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.4g}" if abs(v) < 1e4 else f"{v:,.0f}"
    return clean_text(str(v))[:60]


def _dates_text(d: dict[str, Any]) -> str:
    bits = [
        f"detected frequency: {d.get('detected')}",
        f"{d.get('rows')} rows, {d.get('dates')} distinct dates, {d.get('first')} … {d.get('last')}",
        f"time of day present: {'yes' if d.get('has_time') else 'no'}",
        f"at most {d.get('rows_per_date')} rows per date{' and channel' if d.get('long') else ''}",
    ]
    if d.get("repeated_dates"):
        bits.append(f"{d['repeated_dates']} dates with more than one row")
    if "missing_dates" in d:
        unit = {"weekly": "weeks", "monthly": "months"}.get(str(d.get("detected")), "days")
        bits.append(f"{d['missing_dates']} missing {unit}")
    if d.get("missing_examples"):
        bits.append("e.g. " + ", ".join(d["missing_examples"]))
    if d.get("longest_gap"):
        bits.append(f"longest run of missing days {d['longest_gap']}")
    if "weekdays_present" in d and len(d["weekdays_present"]) < 7:
        names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        bits.append("only weekdays " + ", ".join(names[i] for i in d["weekdays_present"]))
    if d.get("weekday"):
        bits.append(f"weekly rows fall on {d['weekday']}")
    if d.get("shifted"):
        bits.append(f"{d['shifted']} shifted weeks")
    if d.get("anchor"):
        bits.append(f"monthly dates are month {d['anchor']}s" if d["anchor"] != "mixed" else "monthly dates fall on mixed days")
    if d.get("repeated_months"):
        bits.append(f"{d['repeated_months']} months with two rows")
    return "; ".join(bits)


def _column_text(c: dict[str, Any]) -> str:
    bits = [f"- {c['column']!r}: {c['label']} ({c['kind']})", f"{c['rows']} rows", f"blank {c['blank']}"]
    for key, label in (
        ("leading_blank", "leading blanks"),
        ("trailing_blank", "trailing blanks"),
        ("longest_blank_run", "longest blank run"),
    ):
        if c.get(key):
            bits.append(f"{label} {c[key]}")
    if "min" in c:
        bits += [f"min {_fmt(c['min'])}", f"max {_fmt(c['max'])}", f"mean {_fmt(c['mean'])}"]
        bits.append(f"zero on {100 * float(c['zero_share']):.0f}% of values")
        if c.get("negative"):
            bits.append(f"{c['negative']} negative")
        bits.append(f"{c['distinct']} distinct values")
    if c.get("same_as_previous_share") is not None:
        bits.append(f"equal to the previous row on {100 * float(c['same_as_previous_share']):.0f}% of rows")
    if c.get("autocorr_lag1") is not None:
        bits.append(f"autocorrelation lag 1: {c['autocorr_lag1']}")
    if c.get("autocorr_lag7") is not None:
        bits.append(f"lag 7: {c['autocorr_lag7']}")
    if c.get("top_weekday"):
        bits.append(f"{100 * float(c['top_weekday_share_of_nonzero']):.0f}% of non-zero values on a {c['top_weekday']}")
    if c.get("first_of_month_share_of_nonzero"):
        bits.append(f"{100 * float(c['first_of_month_share_of_nonzero']):.0f}% of non-zero values on the 1st of a month")
    if c.get("categories"):
        bits.append(f"{c['categories']} categories")
    allowed = c["allowed"]
    bits.append(
        f"allowed: aggregate [{', '.join(allowed['aggregate'])}], fill [{', '.join(allowed['fill'])}], "
        f"disaggregate [{', '.join(allowed['disaggregate'])}]"
    )
    d = c["default"]
    bits.append(f"default: aggregate {d['aggregate']}, fill {d['fill']}, disaggregate {d['disaggregate']}")
    return "; ".join(bits)


def _catalog_text() -> str:
    lines = ["Frequencies: " + "; ".join(f"{k} = {v[1]}" for k, v in FREQUENCIES.items() if k != "auto")]
    for name, table in (("Aggregate", AGGREGATES), ("Fill", FILLS), ("Disaggregate", DISAGGREGATES)):
        lines.append(f"{name} methods: " + "; ".join(f"{k} = {v[1]}" for k, v in table.items()))
    return "\n".join(lines)


def build_resample_prompt(inspection: dict[str, Any], diagnostics: dict[str, Any], mapping: ColumnMapping) -> str:
    """The user prompt: the mapped table's dates and columns as the resampling needs to see them."""
    dates = {**diagnostics["dates"], "long": mapping.layout == "long"}
    names = [c["name"] for c in inspection["columns"]]
    rows = inspection["sample_rows"][:MAX_SAMPLE_ROWS]
    head = " | ".join(names)
    body = "\n".join(" | ".join(_fmt(r.get(c)) for c in names) for r in rows)
    current = mapping.resample.model_dump(mode="json")
    parts = [
        f"File: {inspection['filename']}, sheet {inspection['selected_sheet']!r}, {inspection['n_rows']} rows, "
        f"{'long (one row per date and channel)' if mapping.layout == 'long' else 'wide (one row per date)'} table.",
        f"Date column {mapping.date!r}: {_dates_text(dates)}.",
        "\nMapped columns (the facts are computed at the data's own frequency):\n"
        + "\n".join(_column_text(c) for c in diagnostics["columns"]),
        "\nFirst rows:\n" + f"{head}\n{body}",
        "\nThe user's current choices (empty columns list = every column on its default):\n" + json.dumps(current),
        "\n" + _catalog_text(),
    ]
    if mapping.layout == "long":
        parts.append(
            "\nIn a long table, a missing channel-day stays missing (the model flags it with missing-row controls): the "
            "channel columns only allow fill 'none', and fill_missing_dates must be false."
        )
    if inspection.get("prep_errors"):
        parts.append("\nWhat currently stops the table from reaching the daily grid:\n" + "\n".join(f"- {e}" for e in inspection["prep_errors"]))
    return "\n".join(parts)[:MAX_PROMPT_CHARS]


# --------------------------------------------------------------------------------------------------------------------
# Review
# --------------------------------------------------------------------------------------------------------------------


@dataclass
class ResampleReview:
    config: ResampleConfig
    column_notes: dict[str, dict[str, str]] = field(default_factory=dict)
    rejected: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unreviewed: list[str] = field(default_factory=list)
    frequency_reason: str = ""
    period_label_reason: str = ""
    missing_dates_reason: str = ""


def review_resample(advice: ResampleAdvice, df: pd.DataFrame, mapping: ColumnMapping) -> ResampleReview:
    """Gemini's plan checked item by item against the mapped sheet; what fails is rejected with the reason."""
    rejected: list[dict[str, str]] = []
    ts = parse_timestamps(df[mapping.date])
    keys = df[mapping.channel] if mapping.layout == "long" and mapping.channel in df.columns else None
    long = mapping.layout == "long"

    frequency = advice.frequency
    label = advice.period_label if frequency == "weekly" else "start"
    fill_missing = bool(advice.fill_missing_dates)
    if fill_missing and long:
        rejected.append({"item": "fill missing dates", "reason": "a long table's missing channel-days stay missing"})
        fill_missing = False
    # Gaps are judged by the dry run below; here only whether the dates can be read at this frequency
    grid_obj = None
    try:
        grid_obj = build_grid(ts, ResampleConfig(frequency=frequency, period_label=label, fill_missing_dates=not long), mapping.layout, keys)
    except PreparationError as exc:
        rejected.append({"item": f"frequency “{FREQUENCIES[frequency][0]}”", "reason": str(exc)})
        frequency, label = "auto", "start"
    if grid_obj is None:
        try:
            grid_obj = build_grid(ts, ResampleConfig(fill_missing_dates=not long), mapping.layout, keys)
        except PreparationError:
            grid_obj = None
    grid_freq = grid_obj.frequency if grid_obj is not None else (frequency if frequency != "auto" else "daily")
    combines_rows = grid_freq == "sub_daily" or (
        grid_obj is not None and grid_obj.combine_periods and bool(grid_obj.row_period.duplicated().any())
    )

    specs = {s.column: s for s in column_specs(df, mapping)}
    entries: list[ColumnResample] = []
    notes: dict[str, dict[str, str]] = {}
    tables = {"aggregate": AGGREGATES, "fill": FILLS, "disaggregate": DISAGGREGATES}
    for item in advice.columns:
        col = item.column.strip()
        spec = specs.get(col)
        if spec is None:
            rejected.append({"item": f"'{col}'", "reason": "not a column of the mapping"})
            continue
        if col in notes:
            rejected.append({"item": f"'{col}'", "reason": "given twice; the first entry is used"})
            continue
        allowed = allowed_methods(spec)
        default = default_methods(spec)
        chosen: dict[str, Any] = {}
        for op in ("aggregate", "fill", "disaggregate"):
            val = getattr(item, op)
            if not val or val == "default":
                continue
            if op == "aggregate" and not combines_rows:
                continue  # not used: no two rows share a period
            if op == "disaggregate" and grid_freq not in ("weekly", "monthly"):
                continue  # not used: the rows are already days
            if val not in allowed[op]:
                rejected.append(
                    {
                        "item": f"'{col}' {op} “{tables[op][val][0]}”",
                        "reason": f"not allowed for {spec.label}; it allows {', '.join(tables[op][v][0] for v in allowed[op])}",
                    }
                )
                continue
            if op == "fill" and val == "seasonal" and grid_freq in ("weekly", "monthly"):
                rejected.append({"item": f"'{col}' fill “Same weekday”", "reason": "it needs daily data"})
                continue
            if val != default[op]:
                chosen[op] = val
        if chosen.get("fill") == "constant":
            if spec.kind == "flag" and item.fill_value not in (0, 1):
                rejected.append({"item": f"'{col}' fill {item.fill_value:g}", "reason": "a 0/1 flag is filled with 0 or 1"})
                chosen.pop("fill")
            else:
                chosen["fill_value"] = float(item.fill_value)
        notes[col] = {"reason": item.reason, "confidence": item.confidence}
        if chosen:
            entries.append(ColumnResample(column=col, **chosen))

    config = ResampleConfig(frequency=frequency, period_label=label, fill_missing_dates=fill_missing, columns=entries)
    checked = mapping.model_copy(update={"resample": config})
    errors = resample_errors(df, checked)
    if not errors:
        try:
            canonicalise_report(df, checked)
        except WorkbookValidationError as exc:
            errors = [str(exc)]
    unreviewed = [c for c in specs if c not in notes]
    return ResampleReview(
        config=config,
        column_notes=notes,
        rejected=rejected,
        errors=errors,
        unreviewed=unreviewed,
        frequency_reason=advice.frequency_reason,
        period_label_reason=advice.period_label_reason if label == "end" or frequency == "weekly" else "",
        missing_dates_reason=advice.missing_dates_reason,
    )


def advice_text(advice: ResampleAdvice) -> str:
    """Everything Gemini wrote, for the unverified-number check."""
    parts = [advice.summary, advice.frequency_reason, advice.period_label_reason, advice.missing_dates_reason]
    parts += [c.reason for c in advice.columns]
    return "\n".join([*parts, *advice.notes])
