"""Data sorting, completeness grid, and wide daily panel builders."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.core.schema import DataRoleConfig

CANONICAL_PAID_CHANNELS: tuple[str, ...] = (
    "SEA",
    "Catalog",
    "Display",
    "Affiliate",
    "Display Network",
    "Social Media",
    "PSM",
)

CANONICAL_UNPAID_CHANNELS: tuple[str, ...] = (
    "Direct Type-in",
    "SEO",
    "TV",
    "Direct Referred",
    "Owned",
)


def sort_and_clean(raw: pd.DataFrame, config: DataRoleConfig | None = None) -> pd.DataFrame:
    """Sort raw long-format data by (date, channel) and standardise column names."""
    df = raw.copy()
    if config is not None:
        rename_map: dict[str, str] = {
            config.columns.date: "date",
            config.columns.channel: "channel",
            config.columns.sales: "sales_by_channel",
            config.columns.costs: "costs_by_channel",
        }
        if config.columns.impressions and config.columns.impressions in df.columns:
            rename_map[config.columns.impressions] = "impressions_by_channel"
        if config.columns.clicks and config.columns.clicks in df.columns:
            rename_map[config.columns.clicks] = "clicks_by_channel"
        df = df.rename(columns=rename_map)

    for optional_col in ("impressions_by_channel", "clicks_by_channel"):
        if optional_col not in df.columns:
            df[optional_col] = np.nan

    df["date"] = pd.to_datetime(df["date"])
    df["channel"] = df["channel"].astype(str)
    for num_col in ("sales_by_channel", "costs_by_channel", "impressions_by_channel", "clicks_by_channel"):
        df[num_col] = pd.to_numeric(df[num_col], errors="coerce")

    if config is not None:
        active = set(config.active_channels)
        df = df[df["channel"].isin(active)].copy()

    df = df.sort_values(["date", "channel"]).reset_index(drop=True)
    return df


def build_completeness_grid(df: pd.DataFrame, config: DataRoleConfig | None = None) -> pd.DataFrame:
    """Return a date x channel binary presence matrix over the full calendar range."""
    full_dates = pd.date_range(df["date"].min(), df["date"].max(), freq="D", name="date")
    channels = (
        config.active_channels
        if config is not None
        else sorted(df["channel"].unique().tolist())
    )
    present = (
        df.assign(present=1)
        .pivot_table(index="date", columns="channel", values="present", fill_value=0)
        .reindex(full_dates, fill_value=0)
    )
    for ch in channels:
        if ch not in present.columns:
            present[ch] = 0
    return present[channels]


def build_daily_wide_table(
    df: pd.DataFrame,
    config: DataRoleConfig | None = None,
    drop_2025: bool = False,
) -> pd.DataFrame:
    """Pivot long channel-day rows into a regular daily wide table with clean NaN handling."""
    clean = sort_and_clean(df, config=config)
    full_dates = pd.date_range(clean["date"].min(), clean["date"].max(), freq="D", name="date")
    presence = build_completeness_grid(clean, config=config)

    if config is not None:
        slug_map = config.slug_map()
        paid_channels = config.paid_channels
        unpaid_channels = config.unpaid_channels
        print_channels = config.print_event_channels
    else:
        all_ch = sorted(clean["channel"].unique().tolist())
        from mmm_studio.core.schema import slugify_channel

        slug_map = {c: slugify_channel(c) for c in all_ch}
        paid_channels = [c for c in CANONICAL_PAID_CHANNELS if c in all_ch]
        unpaid_channels = [c for c in CANONICAL_UNPAID_CHANNELS if c in all_ch]
        print_channels = ["Catalog"] if "Catalog" in all_ch else []

    sales_wide = (
        clean.pivot_table(index="date", columns="channel", values="sales_by_channel", aggfunc="sum")
        .reindex(full_dates)
    )
    costs_wide = (
        clean.pivot_table(index="date", columns="channel", values="costs_by_channel", aggfunc="sum")
        .reindex(full_dates)
    )
    impr_wide = (
        clean.pivot_table(index="date", columns="channel", values="impressions_by_channel", aggfunc="sum")
        .reindex(full_dates)
    )
    clicks_wide = (
        clean.pivot_table(index="date", columns="channel", values="clicks_by_channel", aggfunc="sum")
        .reindex(full_dates)
    )

    out = pd.DataFrame(index=full_dates)
    out["total_sales"] = sales_wide.fillna(0.0).sum(axis=1)
    paid_cols_present = [c for c in paid_channels if c in sales_wide.columns]
    unpaid_cols_present = [c for c in unpaid_channels if c in sales_wide.columns]
    out["paid_sales"] = sales_wide[paid_cols_present].fillna(0.0).sum(axis=1) if paid_cols_present else 0.0
    out["unpaid_sales"] = sales_wide[unpaid_cols_present].fillna(0.0).sum(axis=1) if unpaid_cols_present else 0.0

    for ch, slug in slug_map.items():
        if ch in sales_wide.columns:
            out[f"sales_{slug}"] = sales_wide[ch].fillna(0.0)
        else:
            out[f"sales_{slug}"] = 0.0

    for ch in paid_channels:
        slug = slug_map[ch]
        out[f"cost_{slug}"] = costs_wide[ch].fillna(0.0) if ch in costs_wide.columns else 0.0
        out[f"impr_{slug}"] = impr_wide[ch].fillna(0.0) if ch in impr_wide.columns else 0.0
        out[f"clicks_{slug}"] = clicks_wide[ch].fillna(0.0) if ch in clicks_wide.columns else 0.0

    # Missingness flags for channels with missing rows (plus canonical TV, Catalog, Owned)
    flag_channels = set(["TV", "Catalog", "Owned"]) | {
        ch for ch in presence.columns if (presence[ch] == 0).any()
    }
    for ch in sorted(flag_channels):
        if ch in slug_map and ch in presence.columns:
            slug = slug_map[ch]
            out[f"missing_{slug}"] = (presence[ch] == 0).astype(int)

    # TV outage / near-zero sales flag (< 1 EUR or missing)
    if "TV" in slug_map:
        out["tv_anomaly"] = ((out.get("missing_tv", 0) == 1) | (out["sales_tv"] < 1.0)).astype(int)
    else:
        out["tv_anomaly"] = 0

    cost_cols = [f"cost_{slug_map[c]}" for c in paid_channels if c in slug_map]
    digital_cost_cols = [
        f"cost_{slug_map[c]}" for c in paid_channels if c not in print_channels and c in slug_map
    ]
    out["total_cost"] = out[cost_cols].sum(axis=1) if cost_cols else 0.0
    out["digital_cost"] = out[digital_cost_cols].sum(axis=1) if digital_cost_cols else 0.0

    # Catalog send-out indicator & days since most recent send-out
    print_col = f"cost_{slug_map[print_channels[0]]}" if print_channels else (
        "cost_catalog" if "cost_catalog" in out.columns else None
    )
    if print_col and print_col in out.columns:
        sendout = out[print_col] > 0
        out["catalog_sendout"] = sendout.astype(int)
        days_since: list[float] = []
        last_seen: int | None = None
        for idx, is_send in enumerate(sendout.to_numpy()):
            if is_send:
                last_seen = idx
            days_since.append(float(idx - last_seen) if last_seen is not None else np.nan)
        out["days_since_catalog"] = days_since
    else:
        out["catalog_sendout"] = 0
        out["days_since_catalog"] = np.nan

    if drop_2025:
        # Exclude trailing isolated partial year (e.g. 2025-01-01)
        year_counts = pd.Series(out.index.year).value_counts()
        if len(year_counts) > 1:
            max_year = int(out.index.max().year)
            if int(year_counts.get(max_year, 0)) <= 7:
                out = out[out.index.year < max_year].copy()

    return out
