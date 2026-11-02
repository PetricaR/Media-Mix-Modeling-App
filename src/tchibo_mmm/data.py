"""Load the case-study workbook and reshape it into daily wide tables."""

from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = PROJECT_ROOT / "data" / "DataScienceMarketingUseCase.xlsx"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

# Channels that carry costs, i.e. the budget levers. Order fixes plot colors.
PAID_CHANNELS = ["SEA", "Catalog", "Display", "Affiliate", "Display Network", "Social Media", "PSM"]
# Channels with sales but no cost data at all.
UNPAID_CHANNELS = ["Direct Type-in", "SEO", "TV", "Direct Referred", "Owned"]
ALL_CHANNELS = PAID_CHANNELS + UNPAID_CHANNELS


def slug(channel: str) -> str:
    """'Display Network' -> 'display_network' (safe column / Meridian channel name)."""
    return channel.lower().replace(" ", "_").replace("-", "_")


def load_raw(path: Path = DATA_PATH) -> pd.DataFrame:
    """Long table (date x channel), sorted. The source rows are shuffled."""
    raw = pd.read_excel(path, sheet_name="data")
    raw["date"] = pd.to_datetime(raw["date"])
    return raw.sort_values(["date", "channel"]).reset_index(drop=True)


def load_info(path: Path = DATA_PATH) -> pd.DataFrame:
    """Column dictionary from the 'info' sheet."""
    return pd.read_excel(path, sheet_name="info")


def full_calendar(raw: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.date_range(raw["date"].min(), raw["date"].max(), freq="D", name="date")


def pivot_metric(raw: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Daily wide table of one metric, one column per channel, on the full calendar.

    Missing (date, channel) rows stay NaN so callers decide how to fill them.
    """
    wide = raw.pivot(index="date", columns="channel", values=metric)
    return wide.reindex(index=full_calendar(raw), columns=ALL_CHANNELS)


def sales_by_channel(raw: pd.DataFrame) -> pd.DataFrame:
    """Daily attributed sales per channel; a missing channel-day counts as 0 sales."""
    return pivot_metric(raw, "sales_by_channel").fillna(0.0)


def costs_by_channel(raw: pd.DataFrame) -> pd.DataFrame:
    """Daily costs per channel; no recorded cost means 0 spend.

    For Catalog that is correct (cost is booked only on send-out days). For the
    unpaid channels the column is all zeros, which is the point: they cannot be
    budget levers.
    """
    return pivot_metric(raw, "costs").fillna(0.0)


def build_wide_table(raw: pd.DataFrame) -> pd.DataFrame:
    """The table the brief asks for: all channels as daily cost columns + total sales.

    Columns: ``cost_<channel>`` for all 12 channels and ``total_sales`` (sum of
    sales_by_channel over all channels), indexed by date.
    """
    costs = costs_by_channel(raw)
    wide = costs.rename(columns=lambda c: f"cost_{slug(c)}")
    wide["total_sales"] = sales_by_channel(raw).sum(axis=1)
    return wide


def missing_channel_days(raw: pd.DataFrame) -> pd.Series:
    """Number of calendar days without a row, per channel."""
    present = raw.groupby("channel")["date"].nunique()
    return (len(full_calendar(raw)) - present).reindex(ALL_CHANNELS).rename("missing_days")


def catalog_send_out_dates(raw: pd.DataFrame) -> pd.DatetimeIndex:
    """Days on which the catalog cost is booked (one per send-out)."""
    cat = raw[(raw["channel"] == "Catalog") & raw["costs"].notna()]
    return pd.DatetimeIndex(cat["date"].sort_values().unique(), name="date")
