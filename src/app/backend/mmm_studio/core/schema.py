"""Schema and data-role mapping models for uploaded marketing workbooks."""

from __future__ import annotations

import re
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

ChannelRole = Literal["paid", "print_event", "unpaid", "ignored"]


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
    slug: str
    role: ChannelRole
    row_count: int = 0
    cost_days: int = 0
    total_sales: float = 0.0
    total_cost: float = 0.0
    has_impressions: bool = False
    has_clicks: bool = False


class DataRoleConfig(BaseModel):
    """Confirmed data roles for a run."""

    sheet_name: str | None = None
    columns: ColumnMapping = Field(default_factory=ColumnMapping)
    channels: list[ChannelConfig] = Field(default_factory=list)
    drop_trailing_isolated_days: bool = True
    holdout_days: int = 92

    @property
    def paid_channels(self) -> list[str]:
        """All paid media channels (including print_event channels like Catalog)."""
        return [c.name for c in self.channels if c.role in ("paid", "print_event")]

    @property
    def digital_paid_channels(self) -> list[str]:
        """Continuous paid media channels (excluding print_event channels)."""
        return [c.name for c in self.channels if c.role == "paid"]

    @property
    def print_event_channels(self) -> list[str]:
        """Episodic print/event channels (e.g. Catalog)."""
        return [c.name for c in self.channels if c.role == "print_event"]

    @property
    def unpaid_channels(self) -> list[str]:
        """Organic / unpaid channels."""
        return [c.name for c in self.channels if c.role == "unpaid"]

    @property
    def active_channels(self) -> list[str]:
        """All non-ignored channels."""
        return [c.name for c in self.channels if c.role != "ignored"]

    def slug_map(self) -> dict[str, str]:
        return {c.name: c.slug for c in self.channels if c.role != "ignored"}


class DataProfile(BaseModel):
    """Summary profile produced after inspecting an uploaded workbook."""

    filename: str
    sheet_names: list[str]
    selected_sheet: str | None
    raw_row_count: int
    calendar_days: int
    start_date: str
    end_date: str
    modelling_end_date: str
    modelling_days: int
    trailing_isolated_dates: list[str]
    available_columns: list[str]
    sample_rows: list[dict[str, Any]]
    role_config: DataRoleConfig
    missing_channel_days: dict[str, int]
    tv_low_sales_days: int
    catalog_sendout_days: int
    total_sales_sum: float
    total_cost_sum: float
    warnings: list[str] = Field(default_factory=list)


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
