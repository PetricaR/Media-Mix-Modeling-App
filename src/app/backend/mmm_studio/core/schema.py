"""Schema and data-role mapping models for uploaded marketing workbooks."""

from __future__ import annotations

import re
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, model_validator

ChannelRole = Literal["paid", "print_event", "commission", "unpaid", "ignored", "digital_paid", "control"]


def slugify_channel(name: str) -> str:
    """Convert a channel display name into a canonical snake_case identifier."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "channel"


class ColumnMapping(BaseModel):
    """Mapping from semantic MMM roles to physical workbook column names."""

    date: str = "date"
    channel: str = "channel"
    sales: str = "sales_by_channel"
    costs: str = "costs_by_channel"
    impressions: str | None = "impressions_by_channel"
    clicks: str | None = "clicks_by_channel"


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
    in_home_shift_days: int | None = None

    @model_validator(mode="after")
    def _populate_channel_defaults(self) -> ChannelConfig:
        if not self.slug:
            self.slug = slugify_channel(self.name)
        if self.role == "ignored":
            self.enabled = False
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
        if self.rationale is None:
            if self.role == "print_event":
                self.rationale = f"Episodic print drop ({self.cost_days} send-out days); in-home shift + slow adstock"
            elif self.role in ("commission", "control") or (self.role == "paid" and self.slug == "affiliate"):
                self.rationale = "Commission on its own sales (cost follows demand); Meridian control, no ROI"
            elif self.role in ("paid", "digital_paid"):
                self.rationale = f"Continuous paid media ({self.cost_days} active spend days)"
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
            self.in_home_shift_days = 2 if self.role == "print_event" else 0
        return self


class DataRoleConfig(BaseModel):
    """Confirmed data roles for a run."""

    sheet_name: str | None = None
    columns: ColumnMapping = Field(default_factory=ColumnMapping)
    channels: list[ChannelConfig] = Field(default_factory=list)
    # Notebooks 01–05 keep the isolated trailing day (2025-01-01) in every model; it is only left out of yearly
    # and monthly comparisons. The default hold-out (93 days) is Q4 2024 through 2025-01-01, as in notebook 03.
    drop_trailing_isolated_days: bool = False
    holdout_days: int = 93
    structural_break_year: int = 2023
    target_column: str = "total_sales"
    date_column: str = "date"
    allow_single_year: bool = False

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
        return [
            c.slug
            for c in self.channels
            if c.enabled and (c.role in ("commission", "control") or c.slug == "affiliate")
        ]

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


def default_role_config() -> DataRoleConfig:
    """Return a default `DataRoleConfig` with the 12 channels of the case-study workbook."""
    return DataRoleConfig(
        channels=[
            ChannelConfig(name="SEA", role="paid"),
            ChannelConfig(name="Catalog", role="print_event"),
            ChannelConfig(name="Display", role="paid"),
            ChannelConfig(name="Affiliate", role="paid"),
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

        if total_cost <= 0 or cost_days == 0:
            role: ChannelRole = "unpaid"
        elif "catalog" in slug or "print" in slug or (cost_days / total_days <= 0.15):
            role = "print_event"
        else:
            role = "paid"

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
            )
        )

    # Canonical ordering: paid + print_event first, then unpaid
    role_rank = {"paid": 0, "print_event": 1, "unpaid": 2, "ignored": 3}
    configs.sort(key=lambda c: (role_rank.get(c.role, 9), c.name))
    return configs
