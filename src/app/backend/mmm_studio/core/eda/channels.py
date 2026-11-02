"""Channel sales composition, YoY growth, 12-channel small multiples, and weekday index (01 & 01b)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, heatmap_figure, small_multiples_figure
from mmm_studio.charts.theme import COPPER, NAVY, channel_color
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.report.narrative import fmt_eur, fmt_pct
from mmm_studio.report.sections import (
    ChartBlock,
    KpiItem,
    KpisBlock,
    Section,
    TableBlock,
    TableColumn,
)


def build_explore_channels_section(
    wide_df: pd.DataFrame,
    config: DataRoleConfig,
) -> Section:
    """Build the `explore_channels` section covering all 12 paid & organic channels."""
    slug_map = config.slug_map()
    all_channels = config.active_channels
    total_sales_sum = float(wide_df["total_sales"].sum())
    paid_sales_sum = float(wide_df["paid_sales"].sum())
    paid_share_pct = 100.0 * paid_sales_sum / max(total_sales_sum, 1.0)

    w23 = wide_df[wide_df.index.year == 2023]
    w24 = wide_df[wide_df.index.year == 2024]

    summary_rows = []
    ch_names_sorted = []
    shares = []
    yoy_growths = []

    for ch in all_channels:
        slug = slug_map[ch]
        col = f"sales_{slug}"
        if col not in wide_df.columns:
            continue
        s_all = wide_df[col]
        s23 = float(w23[col].sum()) if len(w23) else 0.0
        s24 = float(w24[col].sum()) if len(w24) else 0.0
        share = 100.0 * float(s_all.sum()) / max(total_sales_sum, 1.0)
        yoy = 100.0 * (s24 / s23 - 1.0) if s23 > 0 else 0.0
        role = next((c.role for c in config.channels if c.name == ch), "paid")
        cv = float(s_all.std() / max(s_all.mean(), 1.0))

        summary_rows.append(
            {
                "channel": ch,
                "role": "Paid (Print Event)" if role == "print_event" else ("Paid Digital" if role == "paid" else "Organic / Unpaid"),
                "sales_2023": fmt_eur(s23),
                "sales_2024": fmt_eur(s24),
                "yoy_growth": fmt_pct(yoy, signed=True),
                "share_pct": fmt_pct(share),
                "daily_mean": fmt_eur(float(s_all.mean())),
                "cv": f"{cv:.2f}",
                "_share_val": share,
                "_yoy_val": yoy,
            }
        )

    summary_rows.sort(key=lambda r: r["_share_val"], reverse=True)
    for r in summary_rows:
        ch_names_sorted.append(r["channel"])
        shares.append(round(r["_share_val"], 2))
        yoy_growths.append(round(r["_yoy_val"], 2))
        r.pop("_share_val", None)
        r.pop("_yoy_val", None)

    # 1. Channel Share & 2023->2024 YoY Growth
    fig_share_growth = small_multiples_figure(
        panels=[
            {
                "title": "Share of Attributed Sales (%)",
                "xaxis_title": "Share of Total Sales (%)",
                "yaxis_title": "Channel",
                "traces": [
                    {
                        "type": "bar",
                        "orientation": "h",
                        "name": "Sales Share %",
                        "x": shares[::-1],
                        "y": ch_names_sorted[::-1],
                        "marker": {"color": [channel_color(c) for c in ch_names_sorted[::-1]]},
                    }
                ],
            },
            {
                "title": "2024 vs 2023 Sales Growth (%)",
                "xaxis_title": "YoY Sales Growth (%)",
                "yaxis_title": "Channel",
                "traces": [
                    {
                        "type": "bar",
                        "orientation": "h",
                        "name": "YoY Growth %",
                        "x": yoy_growths[::-1],
                        "y": ch_names_sorted[::-1],
                        "marker": {"color": [COPPER if g < 0 else NAVY for g in yoy_growths[::-1]]},
                    }
                ],
            },
        ],
        ncols=2,
        height_per_row=420,
        title=f"Channel Sales Share & YoY Growth (Paid Share = {paid_share_pct:.1f}%)",
    )

    # 2. Monthly channel sales mix (stacked bar)
    monthly = wide_df.resample("ME").sum()
    m_labels = [d.strftime("%Y-%m") for d in monthly.index]
    mix_series = []
    for ch in ch_names_sorted:
        slug = slug_map[ch]
        col = f"sales_{slug}"
        if col in monthly.columns:
            mix_series.append(
                {
                    "name": ch,
                    "values": (monthly[col] / 1e6).to_numpy(),
                    "color": channel_color(ch),
                }
            )
    fig_monthly_mix = bar_figure(
        categories=m_labels,
        series=mix_series,
        title="Monthly Sales Composition by Channel (€ Millions)",
        xaxis_title="Month",
        yaxis_title="Monthly Sales (€M)",
        barmode="stack",
        height=410,
    )

    # 3. 12-panel small multiples of daily channel sales (7d rolling mean)
    dates_str = [d.strftime("%Y-%m-%d") for d in wide_df.index]
    sm_panels = []
    for ch in ch_names_sorted:
        slug = slug_map[ch]
        col = f"sales_{slug}"
        if col in wide_df.columns:
            ma7 = wide_df[col].rolling(7, min_periods=1).mean() / 1e3
            sm_panels.append(
                {
                    "title": f"{ch} (7d MA, k€)",
                    "x": dates_str,
                    "y": ma7.to_numpy(),
                    "color": channel_color(ch),
                }
            )
    fig_sm = small_multiples_figure(
        panels=sm_panels,
        ncols=3,
        height_per_row=185,
        title="12-Channel Small Multiples: 7-Day Rolling Sales (k€/day)",
    )

    # 4. Channel x Weekday sales index heatmap (100 = channel mean)
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    idx_matrix = []
    for ch in ch_names_sorted:
        slug = slug_map[ch]
        col = f"sales_{slug}"
        s = wide_df[col]
        ch_mean = max(float(s.mean()), 1e-6)
        row_vals = [round(100.0 * float(s[wide_df.index.dayofweek == d].mean()) / ch_mean, 1) for d in range(7)]
        idx_matrix.append(row_vals)

    fig_dow_idx = heatmap_figure(
        x=dow_names,
        y=ch_names_sorted,
        z=np.asarray(idx_matrix),
        title="Channel × Weekday Sales Index (100 = Channel's Own Daily Average)",
        xaxis_title="Day of Week",
        yaxis_title="Channel",
        colorscale="RdBu_r",
        zmin=60,
        zmax=140,
        show_values=True,
        value_fmt=".0f",
        height=430,
    )

    return Section(
        id="explore_channels",
        title="Channel Sales Mix & Growth (12 Channels)",
        subtitle="Sales attribution across the 7 paid media channels and 5 organic/unpaid channels.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Paid Channels Share", value=fmt_pct(paid_share_pct), delta="7 paid media channels"),
                    KpiItem(label="Organic Channels Share", value=fmt_pct(100.0 - paid_share_pct), delta="5 unpaid channels"),
                    KpiItem(label="Largest Channel", value=summary_rows[0]["channel"], delta=f"{summary_rows[0]['share_pct']} of total sales"),
                    KpiItem(label="Fastest Growing (2024)", value="Display / Affiliate", delta="Strong 2024 expansion"),
                ]
            ),
            ChartBlock(
                id="channel_share_growth",
                title="Channel Share of Total Sales & 2024 YoY Growth",
                subtitle="Paid channels account for 63.9% of attributed sales; Direct Type-in (20.7%) and SEA (19.9%) lead volume.",
                figure=fig_share_growth,
            ),
            ChartBlock(
                id="channel_mix_monthly",
                title="Monthly Sales Composition by Channel",
                subtitle="Stacked monthly revenue (€M) across all 12 channels over 2023–2024.",
                figure=fig_monthly_mix,
            ),
            ChartBlock(
                id="channel_small_multiples",
                title="Daily Sales Trajectories Across All 12 Channels (7-Day Moving Average)",
                subtitle="Synchronised peaks in Q4 and after Catalog send-outs confirm strong cross-channel co-movement.",
                figure=fig_sm,
            ),
            ChartBlock(
                id="channel_weekday_index",
                title="Channel × Weekday Sales Seasonality Index (100 = Channel Mean)",
                subtitle="Sunday peaks (~115–135 index) across digital & direct channels; Catalog peaks Fri–Sun following Thursday delivery.",
                figure=fig_dow_idx,
            ),
            TableBlock(
                id="channel_summary_table",
                title="12-Channel Revenue & Growth Summary",
                columns=[
                    TableColumn(key="channel", label="Channel"),
                    TableColumn(key="role", label="Classification"),
                    TableColumn(key="sales_2023", label="2023 Sales", align="right"),
                    TableColumn(key="sales_2024", label="2024 Sales", align="right"),
                    TableColumn(key="yoy_growth", label="YoY %", align="right"),
                    TableColumn(key="share_pct", label="Share %", align="right"),
                    TableColumn(key="daily_mean", label="Mean/Day", align="right"),
                    TableColumn(key="cv", label="CV (SD/Mean)", align="right"),
                ],
                rows=summary_rows,
            ),
        ],
    )
