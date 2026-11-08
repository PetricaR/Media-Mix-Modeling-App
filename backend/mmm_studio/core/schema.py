"""Schema and data-role mapping models for uploaded marketing workbooks."""

from __future__ import annotations

import re
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from mmm_studio.core.model_settings import (
    DEFAULT_HOLDOUT_DAYS,
    DEFAULT_IN_HOME_SHIFT,
    WEATHER_CONTROLS,
    ModelSettings,
)

ChannelRole = Literal["paid", "print_event", "commission", "unpaid", "ignored", "digital_paid", "control"]
Layout = Literal["long", "wide"]
# Channel names that mark a print/event channel (send-out date ≠ in-home date)
PRINT_EVENT_KEYWORDS: tuple[str, ...] = ("catalog", "catalogue", "katalog", "print", "mailing", "flyer", "direct_mail", "beilage")
# A print drop is episodic: a channel named like one but with spend on more than this share of days is continuous media
# (e.g. "Facebook Product Catalog Sales", a digital catalogue ad format)
PRINT_EVENT_MAX_ACTIVE_SHARE = 0.5
# The countries a run can be set in (ISO 3166): each has a Google Calendar holiday calendar (`core/holidays.py`) and
# Open-Meteo weather locations (`core/weather.py`)
COUNTRIES: dict[str, str] = {
    "DE": "Germany",
    "AT": "Austria",
    "CH": "Switzerland",
    "NL": "Netherlands",
    "BE": "Belgium",
    "LU": "Luxembourg",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "PT": "Portugal",
    "GB": "United Kingdom",
    "IE": "Ireland",
    "DK": "Denmark",
    "SE": "Sweden",
    "NO": "Norway",
    "FI": "Finland",
    "PL": "Poland",
    "CZ": "Czechia",
    "SK": "Slovakia",
    "HU": "Hungary",
    "RO": "Romania",
    "BG": "Bulgaria",
    "GR": "Greece",
    "HR": "Croatia",
    "SI": "Slovenia",
    "TR": "Türkiye",
    "US": "United States",
    "CA": "Canada",
    "MX": "Mexico",
    "BR": "Brazil",
    "AU": "Australia",
    "NZ": "New Zealand",
    "IN": "India",
    "JP": "Japan",
    "ZA": "South Africa",
}
DEFAULT_COUNTRY = "DE"


def slugify_channel(name: str) -> str:
    """Convert a channel display name into a canonical snake_case identifier."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "channel"


class WideChannel(BaseModel):
    """One channel of a wide table (one row per day): its name and the columns holding its daily values."""

    name: str = Field(min_length=1, max_length=80)
    spend: str = Field(min_length=1)
    impressions: str | None = None
    clicks: str | None = None
    # The channel's own attributed sales (last-touch revenue), when the table has them
    sales: str | None = None

    @property
    def slug(self) -> str:
        return slugify_channel(self.name)


class ControlColumn(BaseModel):
    """A column of the upload that enters the models as a control variable (e.g. a Covid flag, a price index or a
    categorical promotion column one-hot encoded into 0/1 flags)."""

    column: str = Field(min_length=1)
    name: str = ""
    categories: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_name(self) -> ControlColumn:
        if not self.name.strip():
            self.name = self.column
        return self

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if not data.get("categories"):
            data.pop("categories", None)
        return data

    @property
    def key(self) -> str:
        """The control's base column in the daily panel and the Meridian input table."""
        return f"ctrl_{slugify_channel(self.name)}"

    @property
    def keys(self) -> list[str]:
        """The control's column(s) in the daily panel (one per non-baseline category when categorical)."""
        if self.categories:
            return [f"{self.key}_{slugify_channel(cat)}" for cat in self.categories]
        return [self.key]

    @property
    def names(self) -> dict[str, str]:
        """Panel column key → display name."""
        if self.categories:
            return {f"{self.key}_{slugify_channel(cat)}": f"{self.name}: {cat}" for cat in self.categories}
        return {self.key: self.name}


# How the dates of an upload are read before they meet the model's daily grid (`core/resample.py`): `auto` detects it,
# `sub_daily` aggregates several rows of a day (hours, regions …), `weekly` / `monthly` disaggregate a period into days
SourceFrequency = Literal["auto", "sub_daily", "daily", "weekly", "monthly"]
# Several rows of one day → one value
AggregateMethod = Literal["sum", "mean", "median", "min", "max", "first", "last", "mode"]
# A blank cell (or an added missing date) → a value, at the data's own frequency
FillMethod = Literal[
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
# A weekly or monthly value → its days
DisaggregateMethod = Literal["even", "smooth", "first_day", "repeat", "linear"]


class ColumnResample(BaseModel):
    """How one mapped column gets onto the daily grid; a method left as None keeps the role's default."""

    column: str = Field(min_length=1)
    aggregate: AggregateMethod | None = None
    fill: FillMethod | None = None
    # The value of `fill="constant"`
    fill_value: float | None = None
    disaggregate: DisaggregateMethod | None = None


class ResampleConfig(BaseModel):
    """Resampling and gap filling of an upload (the wizard's "Resample & fill" step).

    The default (auto frequency, no added dates, every column on its role's default) is what the app did before the
    step existed, so stored runs and the sample workbook read exactly as before.
    """

    frequency: SourceFrequency = "auto"
    # Weekly rows: whether a row's date is the first or the last day of its week (monthly rows use the date's month)
    period_label: Literal["start", "end"] = "start"
    # Add the days, weeks or months without a row and fill them like blank cells (otherwise a gap is an error)
    fill_missing_dates: bool = False
    columns: list[ColumnResample] = Field(default_factory=list)

    def for_column(self, column: str) -> ColumnResample | None:
        return next((c for c in self.columns if c.column == column), None)


class ColumnMapping(BaseModel):
    """Mapping from semantic MMM roles to physical workbook column names.

    `long`: one row per date and channel (`channel`, `sales`, `costs`, optional `impressions`/`clicks`); the target is
    the daily sum of the channels' sales. `wide`: one row per date, the target in its own column (`target`) and one or
    more columns per channel (`wide_channels`). Either layout can carry `controls`, constant within a date.
    `resample` says how the rows reach the daily grid and how gaps are filled (`core/resample.py`).
    """

    layout: Layout = "long"
    date: str = "date"
    channel: str = "channel"
    sales: str = "sales_by_channel"
    costs: str = "costs_by_channel"
    impressions: str | None = "impressions_by_channel"
    clicks: str | None = "clicks_by_channel"
    target: str | None = None
    wide_channels: list[WideChannel] = Field(default_factory=list)
    controls: list[ControlColumn] = Field(default_factory=list)
    resample: ResampleConfig = Field(default_factory=ResampleConfig)


class ChannelConfig(BaseModel):
    """Per-channel role classification and metadata."""

    name: str
    slug: str = ""
    role: ChannelRole
    enabled: bool = True
    row_count: int = 0
    cost_days: int = 0
    total_sales: float = 0.0
    total_cost: float = 0.0
    has_impressions: bool = False
    has_clicks: bool = False
    cost_column: str | None = None
    exposure_column: str | None = None
    exposure_type: str | None = None
    rationale: str | None = None
    adstock_decay: float | None = None
    in_home_shift_days: int | None = Field(None, ge=0, le=14)
    # Meridian adstock shape; None means the role's default (binomial for print events, geometric otherwise)
    adstock_shape: Literal["geometric", "binomial"] | None = None
    saturation: Literal["hill", "none"] = "hill"
    # The role the app inferred from the workbook, so a user's change can be told apart from the inference
    inferred_role: ChannelRole | None = None
    # Cost billed per click that follows demand (daily r(cost, clicks) ≥ 0.9): its ROI is an upper bound and the
    # optimiser reports it apart. None means "infer from the data".
    demand_following: bool | None = None
    inferred_demand_following: bool | None = None

    @model_validator(mode="after")
    def _populate_channel_defaults(self) -> ChannelConfig:
        if not self.slug:
            self.slug = slugify_channel(self.name)
        # Affiliate's cost is a commission on its own sales, so the model defaults to using it as a control.
        # Older configs stored it as "paid" with inferred_role unset; preserve explicit user
        # overrides when inferred_role is already recorded as "commission".
        if self.slug == "affiliate" and self.role == "paid" and self.inferred_role != "commission":
            self.role = "commission"
        # The role is the only switch the UI offers, so a channel moved out of "ignored" is modelled again
        self.enabled = self.role != "ignored"
        if self.cost_column is None and (
            self.role in ("paid", "print_event", "commission", "digital_paid", "control") or self.total_cost > 0
        ):
            self.cost_column = f"cost_{self.slug}"
        if self.exposure_column is None:
            if self.role == "print_event":
                self.exposure_column = f"{self.slug}_sendout"
                self.exposure_type = self.exposure_type or "sendout"
            elif self.has_impressions:
                self.exposure_column = f"impr_{self.slug}"
                self.exposure_type = self.exposure_type or "impressions"
            elif self.has_clicks:
                self.exposure_column = f"clicks_{self.slug}"
                self.exposure_type = self.exposure_type or "clicks"
        if self.demand_following is None and self.inferred_demand_following is not None:
            self.demand_following = self.inferred_demand_following
        if self.rationale and self.rationale.startswith("Changed from the inferred role"):
            self.rationale = None  # recomputed below for the current role
        if self.inferred_role is not None and self.role != self.inferred_role:
            # The stored rationale explained the inferred role; say what the user's choice means instead
            self.rationale = f"Changed from the inferred role ({self.inferred_role.replace('_', ' ')})"
            if self.inferred_role == "commission" and self.role in ("paid", "digital_paid"):
                self.rationale += "; its cost is a commission on its own sales, so its ROI is biased upwards"
        elif self.rationale is None:
            if self.role == "print_event":
                self.rationale = f"Episodic print drop ({self.cost_days} send-out days); in-home shift + slow adstock"
            elif self.role in ("commission", "control"):
                self.rationale = "Commission on its own sales (cost follows demand); Meridian control, no ROI"
            elif self.role in ("paid", "digital_paid"):
                self.rationale = f"Continuous paid media ({self.cost_days} active spend days)"
                if self.demand_following:
                    self.rationale += "; cost follows its clicks, so its ROI is an upper bound"
            elif self.role == "unpaid":
                self.rationale = "Organic / baseline demand channel (€0 media spend)"
            else:
                self.rationale = "Excluded from modelling"
        if self.adstock_decay is None:
            self.adstock_decay = (
                0.85
                if self.role == "print_event"
                else (0.50 if self.role in ("paid", "digital_paid", "commission", "control") else 0.0)
            )
        if self.in_home_shift_days is None:
            self.in_home_shift_days = DEFAULT_IN_HOME_SHIFT if self.role == "print_event" else 0
        if self.inferred_role is None:
            self.inferred_role = self.role
        return self


class DataRoleConfig(BaseModel):
    """Confirmed data roles for a run."""

    sheet_name: str | None = None
    columns: ColumnMapping = Field(default_factory=ColumnMapping)
    channels: list[ChannelConfig] = Field(default_factory=list)
    # The isolated trailing day (e.g. 2025-01-01 in the sample workbook) stays in every model; it is only left out of yearly
    # and monthly comparisons. The default hold-out (93 days) covers Q4 2024 through 2025-01-01.
    drop_trailing_isolated_days: bool = False
    holdout_days: int = Field(DEFAULT_HOLDOUT_DAYS, ge=14, le=365)
    target_column: str = "total_sales"
    date_column: str = "date"
    # The market the data comes from: its Google Calendar public holidays enter the holiday control and the holiday
    # views, and its Open-Meteo weather the (optional) weather controls
    country: str = DEFAULT_COUNTRY
    model_settings: ModelSettings = Field(default_factory=ModelSettings)

    @field_validator("country")
    @classmethod
    def _check_country(cls, value: str) -> str:
        code = value.strip().upper()
        if code not in COUNTRIES:
            raise ValueError(f"country '{value}' is not supported; choose one of {', '.join(COUNTRIES)}")
        return code

    @property
    def country_name(self) -> str:
        return COUNTRIES[self.country]

    @property
    def has_channel_sales(self) -> bool:
        """True when the data has sales per channel (always in the long layout; per channel column in the wide one)."""
        if self.columns.layout == "wide":
            return any(ch.sales for ch in self.columns.wide_channels)
        return True

    @property
    def control_keys(self) -> list[str]:
        """The extra controls in the daily panel: the user's (`ctrl_{slug}`) and, when switched on, the weather (`wx_*`)."""
        weather = list(WEATHER_CONTROLS) if self.model_settings.weather_controls else []
        return [k for c in self.columns.controls for k in c.keys] + weather

    def control_names(self) -> dict[str, str]:
        """Control column → display name."""
        names: dict[str, str] = {}
        for c in self.columns.controls:
            names.update(c.names)
        if self.model_settings.weather_controls:
            names.update(WEATHER_CONTROLS)
        return names

    @property
    def paid_channels(self) -> list[str]:
        """All paid media channels (including print_event and commission/control channels)."""
        return [
            c.name
            for c in self.channels
            if c.enabled and c.role in ("paid", "print_event", "commission", "digital_paid", "control")
        ]

    @property
    def paid_slugs(self) -> list[str]:
        """Canonical snake_case slugs for all active paid/print/commission channels."""
        return [
            c.slug
            for c in self.channels
            if c.enabled and c.role in ("paid", "print_event", "commission", "digital_paid", "control")
        ]

    @property
    def commission_channels(self) -> list[str]:
        """Slugs of commission/control channels (e.g. affiliate) treated as non-media controls."""
        return [c.slug for c in self.channels if c.enabled and c.role in ("commission", "control")]

    @property
    def digital_paid_channels(self) -> list[str]:
        """Continuous paid media channels (excluding print_event channels)."""
        return [
            c.name
            for c in self.channels
            if c.enabled and c.role in ("paid", "digital_paid", "commission", "control")
        ]

    @property
    def print_event_channels(self) -> list[str]:
        """Episodic print/event channels (e.g. Catalog)."""
        return [c.name for c in self.channels if c.enabled and c.role == "print_event"]

    @property
    def unpaid_channels(self) -> list[str]:
        """Organic / unpaid channels."""
        return [c.name for c in self.channels if c.enabled and c.role == "unpaid"]

    @property
    def active_channels(self) -> list[str]:
        """All non-ignored channels."""
        return [c.name for c in self.channels if c.enabled and c.role != "ignored"]

    def slug_map(self) -> dict[str, str]:
        return {c.name: c.slug for c in self.channels if c.enabled and c.role != "ignored"}

    @property
    def print_shift(self) -> int:
        """In-home shift (days) of the first print-event channel: the shift every model and narrative uses."""
        ch = next((c for c in self.channels if c.enabled and c.role == "print_event"), None)
        if ch is None or ch.in_home_shift_days is None:
            return DEFAULT_IN_HOME_SHIFT
        return int(ch.in_home_shift_days)


def default_role_config() -> DataRoleConfig:
    """Return a default `DataRoleConfig` with the 12 standard channels of the sample workbook."""
    return DataRoleConfig(
        channels=[
            ChannelConfig(name="SEA", role="paid"),
            ChannelConfig(name="Catalog", role="print_event"),
            ChannelConfig(name="Display", role="paid"),
            ChannelConfig(name="Affiliate", role="commission"),
            ChannelConfig(name="Display Network", role="paid"),
            ChannelConfig(name="Social Media", role="paid"),
            ChannelConfig(name="PSM", role="paid"),
            ChannelConfig(name="Direct Type-in", role="unpaid"),
            ChannelConfig(name="SEO", role="unpaid"),
            ChannelConfig(name="TV", role="unpaid"),
            ChannelConfig(name="Direct Referred", role="unpaid"),
            ChannelConfig(name="Owned", role="unpaid"),
        ]
    )


def extract_role_config(raw: dict[str, Any] | DataRoleConfig | None) -> DataRoleConfig:
    """Safely extract and validate a `DataRoleConfig` from a stored run config dict or instance."""
    if isinstance(raw, DataRoleConfig):
        return raw if raw.channels else default_role_config()
    if not raw or not isinstance(raw, dict):
        return default_role_config()
    inner = raw.get("role_config")
    target = inner if isinstance(inner, dict) else raw
    if not target:
        return default_role_config()
    cfg = DataRoleConfig.model_validate(target)
    if not cfg.channels:
        cfg.channels = default_role_config().channels
    return cfg


class DataProfile(BaseModel):
    """Summary profile produced after inspecting an uploaded workbook."""

    filename: str
    sha256: str = ""
    sheet_names: list[str]
    selected_sheet: str | None
    raw_row_count: int
    n_rows: int = 0
    n_columns: int = 0
    calendar_days: int
    start_date: str
    end_date: str
    date_min: str = ""
    date_max: str = ""
    years_present: list[int] = Field(default_factory=list)
    modelling_end_date: str
    modelling_days: int
    trailing_isolated_dates: list[str]
    available_columns: list[str]
    sample_rows: list[dict[str, Any]]
    role_config: DataRoleConfig
    missing_channel_days: dict[str, int]
    missing_dates_count: int = 0
    duplicate_rows_count: int = 0
    negative_values_count: int = 0
    tv_low_sales_days: int
    catalog_sendout_days: int
    total_sales_sum: float
    total_cost_sum: float
    total_media_spend_sum: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    layout: Layout = "long"
    has_channel_sales: bool = True
    control_names: list[str] = Field(default_factory=list)
    # The hold-out (test window) a new run starts with: about an eighth of the days, at most about a quarter, with an
    # equally long validation window before it and a year of training before that (`features.default_holdout_days`;
    # 93 days on the 2-year sample panel), and the longest one that still leaves a full year of training days
    default_holdout_days: int = DEFAULT_HOLDOUT_DAYS
    max_holdout_days: int | None = None
    # Prior probability that media earn more than all sales under Meridian's default ROI prior, and the ROI prior a new
    # run of this data starts with (the total-media-contribution prior when that probability is too high)
    prior_media_over_100_prob: float | None = None
    default_roi_prior: str = "meridian"

    @model_validator(mode="after")
    def _sync_frontend_aliases(self) -> DataProfile:
        if not self.n_rows:
            self.n_rows = self.raw_row_count
        if not self.n_columns:
            self.n_columns = len(self.available_columns)
        if not self.date_min:
            self.date_min = self.start_date
        if not self.date_max:
            self.date_max = self.end_date
        if not self.total_media_spend_sum:
            self.total_media_spend_sum = self.total_cost_sum
        if not self.missing_dates_count and self.missing_channel_days:
            self.missing_dates_count = sum(self.missing_channel_days.values())
        return self


def detect_column_mapping(columns: list[str]) -> ColumnMapping:
    """Heuristically map workbook column names to semantic MMM columns."""
    lower_map = {c.lower().strip(): c for c in columns}

    def pick(candidates: list[str], fallback: str | None = None) -> str | None:
        for cand in candidates:
            if cand in lower_map:
                return lower_map[cand]
        for col_lower, orig in lower_map.items():
            if any(cand in col_lower for cand in candidates):
                return orig
        return fallback

    date_col = pick(["date", "day", "datum", "time", "dt"], columns[0] if columns else "date")
    channel_col = pick(["channel", "kanal", "medium", "source"], columns[1] if len(columns) > 1 else "channel")
    sales_col = pick(["sales_by_channel", "sales", "revenue", "umsatz", "kpi", "target"], "sales_by_channel")
    costs_col = pick(["costs_by_channel", "costs", "cost", "spend", "kosten"], "costs_by_channel")
    impr_col = pick(["impressions_by_channel", "impressions", "impr", "views"], None)
    clicks_col = pick(["clicks_by_channel", "clicks", "click"], None)

    return ColumnMapping(
        date=date_col or "date",
        channel=channel_col or "channel",
        sales=sales_col or "sales_by_channel",
        costs=costs_col or "costs_by_channel",
        impressions=impr_col,
        clicks=clicks_col,
    )


def infer_channel_configs(df: pd.DataFrame, mapping: ColumnMapping) -> list[ChannelConfig]:
    """Infer channel roles (paid, print_event, unpaid) from the raw table."""
    ch_col = mapping.channel
    sales_col = mapping.sales
    cost_col = mapping.costs
    impr_col = mapping.impressions
    clicks_col = mapping.clicks

    total_days = max(int(pd.to_datetime(df[mapping.date]).nunique()), 1)
    configs: list[ChannelConfig] = []

    for ch_name, grp in df.groupby(ch_col):
        ch_str = str(ch_name)
        slug = slugify_channel(ch_str)
        costs = pd.to_numeric(grp[cost_col], errors="coerce").fillna(0.0) if cost_col in grp else pd.Series(dtype=float)
        sales = pd.to_numeric(grp[sales_col], errors="coerce").fillna(0.0) if sales_col in grp else pd.Series(dtype=float)
        cost_days = int((costs > 0).sum())
        total_cost = float(costs.sum())
        total_sales = float(sales.sum())

        has_impr = bool(
            impr_col
            and impr_col in grp
            and (pd.to_numeric(grp[impr_col], errors="coerce").fillna(0.0) > 0).any()
        )
        has_clicks = bool(
            clicks_col
            and clicks_col in grp
            and (pd.to_numeric(grp[clicks_col], errors="coerce").fillna(0.0) > 0).any()
        )

        demand_following = False
        if clicks_col and clicks_col in grp and cost_days > 0:
            clicks = pd.to_numeric(grp[clicks_col], errors="coerce").fillna(0.0)
            if costs.std() > 0 and clicks.std() > 0:
                demand_following = bool(costs.corr(clicks) >= 0.9)

        # Print events are recognised by name: a digital channel that is active on few days (a late launch, a paused
        # campaign) must not get a send-out shift, and sparse spend is pointed out in the rationale instead. A print
        # name with spend on most days is a continuous ad format (a "product catalog" campaign), not a print drop.
        if total_cost <= 0 or cost_days == 0:
            role: ChannelRole = "unpaid"
        elif any(k in slug for k in PRINT_EVENT_KEYWORDS) and cost_days / total_days <= PRINT_EVENT_MAX_ACTIVE_SHARE:
            role = "print_event"
        else:
            role = "paid"
        sparse_note = None
        if role == "paid" and cost_days / total_days <= 0.15:
            sparse_note = (
                f"Spend on only {cost_days} of {total_days} days: if this is a print or event channel, set its role to "
                "Print event"
            )

        configs.append(
            ChannelConfig(
                name=ch_str,
                slug=slug,
                role=role,
                row_count=len(grp),
                cost_days=cost_days,
                total_sales=round(total_sales, 2),
                total_cost=round(total_cost, 2),
                has_impressions=has_impr,
                has_clicks=has_clicks,
                rationale=sparse_note,
                inferred_demand_following=demand_following if role == "paid" else False,
            )
        )

    # Canonical ordering: paid + print_event first, then unpaid
    role_rank = {"paid": 0, "print_event": 1, "unpaid": 2, "ignored": 3}
    configs.sort(key=lambda c: (role_rank.get(c.role, 9), c.name))
    return configs
