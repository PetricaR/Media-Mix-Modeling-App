"""Gemini's data mapping for the upload wizard: what it is told about an upload, and how its answer is checked.

Gemini sees the upload before any run exists: the sheets, every column's profile (kind, blanks, range, zero share,
samples), the first rows, small side tables (e.g. a sheet of channel notes or attributions) and the app's own guess. It
answers in JSON: the layout, the date and target columns, one entry per channel (its columns and its role), the
controls, the columns to leave out, the holiday calendar and notes, each with a reason.

Nothing it says is applied blindly. `review_mapping` checks every item against the sheet: a column that does not exist,
is not numeric, or is already used is rejected with the reason, a channel role outside the allowed set or for a channel
the data does not have is rejected, and the result is run through the same `mapping_errors` as an upload. Applying the
accepted mapping is the user's choice in the wizard; nothing is stored here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from mmm_studio.core.mapping import (
    column_kind,
    control_categories,
    mapping_errors,
    parse_dates,
    used_columns,
)
from mmm_studio.core.schema import (
    COUNTRIES,
    ColumnMapping,
    ControlColumn,
    WideChannel,
    slugify_channel,
)
from mmm_studio.explain.context import clean_text

PROMPT_VERSION = "mapping-v3"
MAX_PROMPT_CHARS = 60_000
MAX_SAMPLE_ROWS = 8
MAX_SIDE_ROWS = 20
MAX_LONG_CHANNELS = 60
ROLES: tuple[str, ...] = ("paid", "print_event", "commission", "unpaid", "ignored")

SYSTEM_INSTRUCTION = """\
You are a senior marketing-mix-modelling (MMM) data engineer. You map the columns of a table a user uploaded onto the \
roles MMM Studio needs. MMM Studio fits Google Meridian (a Bayesian MMM) on daily data: total daily sales (the target) \
explained by each channel's daily spend, calendar controls and the user's own controls. The next wizard step ("Resample \
& fill") brings any table onto the daily grid and fills its gaps column by column: several rows a day (hourly, per \
region) are combined, weekly or monthly rows are split into days, and blank cells and missing dates are imputed with a \
method chosen per column. So map a column by what it means, even when it is weekly, monthly or partly blank.

The two layouts:
- wide: one row per day (or week), the target in its own column (e.g. "Revenue" or "sales") and one spend column per \
channel. Most MMM exports look like this.
- long: one row per date and channel, with a channel column, a sales column (that channel's attributed sales; their \
daily sum is the target) and a cost column; impressions and clicks columns are optional.

Column roles in a wide table:
- date: the day (or week-start date) of the row.
- target: the outcome the model explains (revenue, sales, orders, conversions). Exactly one. Blank rows are imputed \
in the next step, so still map the column as the target and mention the blank rows in notes.
- a channel's spend: money spent on a marketing channel. Every spend column becomes one channel. Blank spend cells \
are read as no spend unless the next step imputes them otherwise (mention them in notes when present).
- a channel's impressions or clicks, or its own attributed sales: optional metrics of a channel that already has a \
spend column; they describe the channel and are never model inputs.
- control: a driver outside marketing that moves sales: a 0/1 flag (Covid, lockdown, holiday, promotion, strike, \
outage), a categorical text column with 2–12 repeatable categories (e.g. promotion type such as Normal / Coupons / \
BOGO / Flash Sale, which MMM Studio automatically one-hot encodes into 0/1 flags omitting the baseline category), a \
price or price index, weather, competitor activity, or an event. A control reported less often than the rows (a monthly \
price index in a daily table, blank on most rows) is still a control: the next step fills it (forward fill, \
interpolation …), so map it and mention the blank rows in notes.
- ignore: anything else, and every outcome measured after the media worked (sessions, visits, orders when the target is \
revenue, ROAS, CPC, CTR): using an outcome as an input leaks the target.

Channel roles:
- paid: continuous paid media (search, social, display, video, TV, radio, influencers, print ads in magazines, \
newspaper, OTT). The default.
- print_event: an episodic print drop (a catalogue or mailing sent on a few days, whose effect starts days later). Only \
for spend on few days; a digital "catalog" ad format with spend on most days is paid.
- commission: cost that is a share of the channel's own sales (affiliate); it enters as a control, not as media.
- unpaid: (long tables only) a channel with sales but no cost (SEO, direct); it is part of the baseline.
- ignored: leave the channel out.

Rules:
- Use column names and channel names exactly as written in the data. Never invent a column.
- Write an empty string for a column field that does not apply (e.g. no clicks column).
- Give every column of the sheet a place: the date, the target, a channel's column, a control, or ignored (with a reason).
- Map categorical control columns (2–12 distinct text categories such as promotion types) in controls, not ignored.
- Use the side tables when they describe the channels (delays, attributions, notes): mention what they say in a \
channel's reason or in the notes. They are not daily data.
- The holiday calendar is the country whose public holidays the data follows: an ISO code from the list given, chosen \
from evidence (language of the names, currency, a country in a name); leave it empty when nothing points to one.
- If another sheet holds a better daily table (longer, more complete), name it in better_sheet with the reason.
- Quote only numbers that appear in the data you were given. Keep every reason to one sentence.
"""


class ChannelAdvice(BaseModel):
    """One channel of the mapping."""

    name: str = Field(
        description="Display name. Wide: a short readable name for the spend column. Long: the channel value exactly as in the data."
    )
    spend_column: str = Field(description="Wide: the channel's daily spend column, exactly as written. Long: empty string.")
    impressions_column: str = Field(description="Wide: the channel's impressions column, or empty string.")
    clicks_column: str = Field(description="Wide: the channel's clicks column, or empty string.")
    sales_column: str = Field(description="Wide: the channel's own attributed sales column, or empty string.")
    role: Literal["paid", "print_event", "commission", "unpaid", "ignored"]
    reason: str = Field(description="One sentence: why this column is this channel and why this role.")
    confidence: Literal["high", "medium", "low"]


class ControlAdvice(BaseModel):
    column: str = Field(description="The control's column, exactly as written.")
    name: str = Field(description="A short display name for the control.")
    reason: str = Field(description="One sentence: what the control captures.")


class IgnoredAdvice(BaseModel):
    column: str = Field(description="A column that stays out of the model, exactly as written.")
    reason: str = Field(description="One sentence: why it stays out.")


class MappingAdvice(BaseModel):
    """Gemini's mapping of one sheet."""

    summary: str = Field(description="2–3 sentences: what the table is and how it maps onto the MMM.")
    layout: Literal["long", "wide"]
    date_column: str
    date_reason: str
    target_column: str = Field(description="Wide: the target column. Long: empty string (the target is the sum of channel sales).")
    target_reason: str
    channel_column: str = Field(description="Long: the channel column. Wide: empty string.")
    sales_column: str = Field(description="Long: the channel sales column. Wide: empty string.")
    cost_column: str = Field(description="Long: the channel cost column. Wide: empty string.")
    impressions_column: str = Field(description="Long: the impressions column or empty string. Wide: empty string.")
    clicks_column: str = Field(description="Long: the clicks column or empty string. Wide: empty string.")
    channels: list[ChannelAdvice]
    controls: list[ControlAdvice]
    ignored: list[IgnoredAdvice]
    country: str = Field(description="ISO code of the holiday calendar from the allowed list, or empty string.")
    country_reason: str
    better_sheet: str = Field(description="Another sheet that is a better daily table, or empty string.")
    sheet_reason: str
    notes: list[str] = Field(description="Up to 4 short observations the user should know (e.g. what a side table says).")


class MappingAdviceFailed(ValueError):
    """Gemini's answer could not be read as a mapping."""


def parse_mapping_advice(text: str) -> MappingAdvice:
    try:
        return MappingAdvice.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise MappingAdviceFailed("Gemini's mapping could not be read. Try again.") from exc


# --------------------------------------------------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------------------------------------------------


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.4g}" if abs(v) < 1e4 else f"{v:,.0f}"
    return clean_text(str(v))[:60]


def _column_line(c: dict[str, Any]) -> str:
    bits = [f"- {c['name']!r}: {c['kind']}", f"blank {c['blank']}", f"distinct {c['distinct']}"]
    if c["kind"] == "numeric":
        bits += [f"min {_fmt(c.get('min'))}", f"max {_fmt(c.get('max'))}", f"mean {_fmt(c.get('mean'))}"]
        if c.get("zero_share") is not None:
            bits.append(f"zero on {100 * float(c['zero_share']):.0f}% of rows")
        if c.get("negative"):
            bits.append(f"{c['negative']} negative")
        if c.get("binary"):
            bits.append("only 0/1")
    elif c["kind"] == "date":
        bits += [f"{c.get('min')} … {c.get('max')}", f"{c.get('distinct_dates')} distinct dates"]
        if c.get("step_days") == 7:
            bits.append("weekly (7-day step; split into days in the next step)")
        elif c.get("step_days") is not None and 28 <= c["step_days"] <= 31:
            bits.append("monthly (split into days in the next step)")
        elif c.get("step_days") == 1:
            bits.append("daily")
    elif c["kind"] == "text" and c.get("categories"):
        bits.append(
            f"categorical control (auto one-hot encoded into {', '.join(c['categories'])}; baseline {c.get('baseline')!r} omitted)"
        )
    bits.append("samples " + ", ".join(_fmt(s) for s in c.get("samples", [])))
    return "; ".join(bits)


def _rows_text(columns: list[str], rows: list[dict[str, Any]]) -> str:
    head = " | ".join(columns)
    body = "\n".join(" | ".join(_fmt(r.get(c)) for c in columns) for r in rows)
    return f"{head}\n{body}"


def build_mapping_prompt(inspection: dict[str, Any]) -> str:
    """The user prompt: the upload as Gemini needs to see it (no results exist yet)."""
    cols = inspection["columns"]
    names = [c["name"] for c in cols]
    parts = [
        f"File: {inspection['filename']} ({inspection['format']}).",
        "Sheets: "
        + "; ".join(
            f"{s['name']!r} {s['rows']} rows × {s['columns']} columns{' (has a date column)' if s['has_date'] else ''}"
            for s in inspection["sheets"]
        ),
        f"Sheet to map: {inspection['selected_sheet']!r}, {inspection['n_rows']} rows.",
        "\nColumns:\n" + "\n".join(_column_line(c) for c in cols),
        "\nFirst rows:\n" + _rows_text(names, inspection["sample_rows"][:MAX_SAMPLE_ROWS]),
    ]
    for side in inspection.get("side_tables", []):
        parts.append(
            f"\nSide table on sheet {side['sheet']!r} ({side['n_rows']} rows):\n"
            + _rows_text(side["columns"], side["rows"][:MAX_SIDE_ROWS])
        )
    channels = (inspection.get("preview") or {}).get("role_config", {}).get("channels", [])
    if inspection["mapping"].get("layout") == "long" and channels:
        parts.append(
            "\nChannels in the data (long table): "
            + "; ".join(
                f"{c['name']!r} rows {c.get('row_count')}, spend days {c.get('cost_days')}, total cost {_fmt(c.get('total_cost'))}, "
                f"total sales {_fmt(c.get('total_sales'))}"
                for c in channels[:MAX_LONG_CHANNELS]
            )
        )
    parts.append("\nThe app's own guess (from names and values; correct it where it is wrong):\n" + json.dumps(inspection["mapping"]))
    parts.append(
        f"\nThe model needs at least {inspection.get('min_days', 379)} days on the daily grid (about 55 weekly or 13 monthly "
        "rows once they are split into days). Allowed holiday calendars: "
        + ", ".join(f"{k} ({v})" for k, v in COUNTRIES.items())
        + "."
    )
    prompt = "\n".join(parts)
    return prompt[:MAX_PROMPT_CHARS]


# --------------------------------------------------------------------------------------------------------------------
# Review
# --------------------------------------------------------------------------------------------------------------------


@dataclass
class MappingReview:
    mapping: ColumnMapping
    channel_roles: list[dict[str, str]] = field(default_factory=list)
    column_notes: dict[str, dict[str, str]] = field(default_factory=dict)
    rejected: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unreviewed: list[str] = field(default_factory=list)
    country: str | None = None
    country_reason: str = ""
    better_sheet: str | None = None
    sheet_reason: str = ""


def review_mapping(advice: MappingAdvice, df: pd.DataFrame, sheets: list[str], current_sheet: str, channel_names: list[str]) -> MappingReview:
    """Gemini's mapping checked item by item against the sheet; what fails is rejected with the reason.

    `channel_names` are the channel values of a long table (empty for a wide one).
    """
    columns = {str(c) for c in df.columns}
    kinds = {str(c): column_kind(df[c]) for c in df.columns}
    rejected: list[dict[str, str]] = []
    rejected_cols: set[str] = set()
    notes: dict[str, dict[str, str]] = {}
    used: set[str] = set()

    def reject(item: str, why: str, col: str | None = None) -> None:
        rejected.append({"item": item, "reason": why})
        if col and col in columns:
            rejected_cols.add(col)

    def take(col: str, what: str, *, numeric: bool = True) -> bool:
        col = col.strip()
        if not col:
            return False
        if col not in columns:
            reject(f"{what}: '{col}'", "the sheet has no such column")
            return False
        if col in used:
            reject(f"{what}: '{col}'", "the column is already used for another role")
            return False
        if numeric and kinds.get(col) != "numeric":
            reject(f"{what}: '{col}'", f"the column holds {kinds.get(col)} values, not numbers", col=col)
            return False
        used.add(col)
        return True

    date_col = advice.date_column.strip()
    if date_col not in columns or parse_dates(df[date_col]).isna().all():
        reject(f"date: '{date_col}'", "not a date column of this sheet", col=date_col)
        date_col = next((c for c in df.columns if kinds[str(c)] == "date"), str(df.columns[0]))
    used.add(str(date_col))
    notes[str(date_col)] = {"role": "date", "reason": advice.date_reason}

    roles: list[dict[str, str]] = []
    if advice.layout == "long":
        mapping = ColumnMapping(layout="long", date=str(date_col), impressions=None, clicks=None)
        for attr, col, what, numeric in (
            ("channel", advice.channel_column, "channel column", False),
            ("sales", advice.sales_column, "channel sales column", True),
            ("costs", advice.cost_column, "channel cost column", True),
            ("impressions", advice.impressions_column, "impressions column", True),
            ("clicks", advice.clicks_column, "clicks column", True),
        ):
            if take(col, what, numeric=numeric):
                setattr(mapping, attr, col.strip())
                notes[col.strip()] = {"role": attr, "reason": ""}
        known = set(channel_names)
        for ch in advice.channels:
            if ch.name not in known:
                reject(f"channel '{ch.name}'", "no such channel value in the data")
                continue
            roles.append({"name": ch.name, "role": ch.role, "reason": ch.reason, "confidence": ch.confidence})
    else:
        mapping = ColumnMapping(layout="wide", date=str(date_col), impressions=None, clicks=None)
        if take(advice.target_column, "target"):
            mapping.target = advice.target_column.strip()
            notes[mapping.target] = {"role": "target", "reason": advice.target_reason}
        slugs: set[str] = set()
        for ch in advice.channels:
            if ch.role == "unpaid":
                reject(f"channel '{ch.name}'", "a wide table's channel has a spend column, so it cannot be unpaid")
                continue
            if not take(ch.spend_column, f"{ch.name} spend"):
                if not ch.spend_column.strip():
                    reject(f"channel '{ch.name}'", "no spend column given")
                continue
            name = (ch.name.strip() or ch.spend_column.strip())[:80]
            if slugify_channel(name) in slugs:
                name = ch.spend_column.strip()[:80]
            slugs.add(slugify_channel(name))
            wc = WideChannel(name=name, spend=ch.spend_column.strip())
            notes[wc.spend] = {"role": "spend", "reason": ch.reason, "channel": name}
            for attr, col in (("impressions", ch.impressions_column), ("clicks", ch.clicks_column), ("sales", ch.sales_column)):
                if take(col, f"{name} {attr}"):
                    setattr(wc, attr, col.strip())
                    notes[col.strip()] = {"role": attr, "reason": ch.reason, "channel": name}
            mapping.wide_channels.append(wc)
            roles.append({"name": name, "role": ch.role, "reason": ch.reason, "confidence": ch.confidence})

    keys: set[str] = set()
    for c in advice.controls:
        col = c.column.strip()
        if not col:
            continue
        what = f"control {c.name or col}"
        if col not in columns:
            reject(f"{what}: '{col}'", "the sheet has no such column")
            continue
        if col in used:
            reject(f"{what}: '{col}'", "the column is already used for another role")
            continue
        kind = kinds.get(col)
        cats: list[str] = []
        if kind == "text":
            _, cats = control_categories(df[col])
            if not cats:
                reject(
                    f"control '{col}'",
                    f"holds text with {int(df[col].nunique(dropna=True))} distinct values; a text control needs 2 to 12 categories",
                    col=col,
                )
                continue
        elif kind != "numeric":
            # (A partly blank numeric control is fine: the "Resample & fill" step imputes it with a chosen method)
            reject(f"{what}: '{col}'", f"the column holds {kind} values, not numbers", col=col)
            continue
        used.add(col)
        ctrl = ControlColumn(column=col, name=(c.name.strip() or col)[:80], categories=cats)
        if ctrl.key in keys:
            ctrl = ControlColumn(column=col, name=col[:80], categories=cats)
        keys.add(ctrl.key)
        mapping.controls.append(ctrl)
        notes[col] = {"role": "control", "reason": c.reason}
    for ig in advice.ignored:
        col = ig.column.strip()
        if col in columns and col not in used:
            notes[col] = {"role": "ignore", "reason": ig.reason}

    country = advice.country.strip().upper() or None
    if country and country not in COUNTRIES:
        reject(f"holiday calendar '{country}'", "not one of the supported calendars")
        country = None
    better = advice.better_sheet.strip() or None
    if better and (better not in sheets or better == current_sheet):
        better = None

    errors = mapping_errors(df, mapping)
    mapped = {c for c, _ in used_columns(mapping)}
    unreviewed = [str(c) for c in df.columns if str(c) not in notes and str(c) not in mapped and str(c) not in rejected_cols]
    return MappingReview(
        mapping=mapping,
        channel_roles=roles,
        column_notes=notes,
        rejected=rejected,
        errors=errors,
        unreviewed=unreviewed,
        country=country,
        country_reason=advice.country_reason if country else "",
        better_sheet=better,
        sheet_reason=advice.sheet_reason if better else "",
    )


def advice_text(advice: MappingAdvice) -> str:
    """Everything Gemini wrote, for the unverified-number check."""
    reasons = [advice.summary, advice.date_reason, advice.target_reason, advice.country_reason, advice.sheet_reason]
    reasons += [c.reason for c in advice.channels] + [c.reason for c in advice.controls] + [c.reason for c in advice.ignored]
    return "\n".join([*reasons, *advice.notes])
