"""Paid marketing spend structure, cadence, YoY spend growth, and raw dose-response curves (01 & 01b)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, heatmap_figure, line_figure, small_multiples_figure
from mmm_studio.charts.theme import COPPER, NAVY, TEAL, channel_color
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.report.narrative import fmt_eur, fmt_pct
from mmm_studio.report.sections import (
    CalloutBlock,
    ChartBlock,
    KpiItem,
    KpisBlock,
    Section,
    TableBlock,
    TableColumn,
)


def build_explore_spend_section(
    wide_df: pd.DataFrame,
    config: DataRoleConfig,
) -> Section:
    """Build the `explore_spend` section covering the 7 paid media channels."""
    slug_map = config.slug_map()
    paid_channels = config.paid_channels
    total_cost_sum = float(wide_df["total_cost"].sum())
    paid_sales_sum = float(wide_df["paid_sales"].sum())

    w23 = wide_df[wide_df.index.year == 2023]
    w24 = wide_df[wide_df.index.year == 2024]
    yoy_spend_pct = (
        100.0 * (float(w24["total_cost"].sum()) / max(float(w23["total_cost"].sum()), 1.0) - 1.0)
        if len(w23) and len(w24)
        else 0.0
    )

    spend_rows = []
    ch_ordered = []
    spend_shares = []
    sales_shares_paid = []
    apparent_roas = []

    for ch in paid_channels:
        slug = slug_map[ch]
        c_col = f"cost_{slug}"
        s_col = f"sales_{slug}"
        c_ser = wide_df[c_col]
        s_ser = wide_df[s_col]
        c_sum = float(c_ser.sum())
        s_sum = float(s_ser.sum())
        c23 = float(w23[c_col].sum()) if len(w23) else 0.0
        c24 = float(w24[c_col].sum()) if len(w24) else 0.0
        yoy_c = 100.0 * (c24 / c23 - 1.0) if c23 > 0 else 0.0
        active_days = int((c_ser > 0).sum())
        c_share = 100.0 * c_sum / max(total_cost_sum, 1.0)
        s_share = 100.0 * s_sum / max(paid_sales_sum, 1.0)
        roas = s_sum / max(c_sum, 1.0)

        spend_rows.append(
            {
                "channel": ch,
                "total_spend": fmt_eur(c_sum),
                "spend_share": fmt_pct(c_share),
                "paid_sales_share": fmt_pct(s_share),
                "apparent_roas": f"{roas:.2f}×",
                "active_days": f"{active_days}/{len(wide_df)}",
                "mean_daily_spend": fmt_eur(float(c_ser.mean())),
                "yoy_spend": fmt_pct(yoy_c, signed=True),
                "_c_share": c_share,
                "_s_share": s_share,
                "_roas": roas,
            }
        )

    spend_rows.sort(key=lambda r: r["_c_share"], reverse=True)
    for r in spend_rows:
        ch_ordered.append(r["channel"])
        spend_shares.append(round(r["_c_share"], 2))
        sales_shares_paid.append(round(r["_s_share"], 2))
        apparent_roas.append(round(r["_roas"], 2))
        r.pop("_c_share", None)
        r.pop("_s_share", None)
        r.pop("_roas", None)

    # 1. Spend share vs Paid Sales share & Apparent ROAS
    fig_share = bar_figure(
        categories=ch_ordered,
        series=[
            {"name": "Share of Paid Spend (%)", "values": spend_shares, "color": COPPER},
            {"name": "Share of Paid Channel Sales (%)", "values": sales_shares_paid, "color": NAVY},
        ],
        title="Paid Media: Share of Spend vs Share of Last-Touch Paid Sales (%)",
        xaxis_title="Paid Channel",
        yaxis_title="Share (%)",
        barmode="group",
        height=370,
    )

    # 2. Monthly spend vs total sales over time
    monthly = wide_df.resample("ME").sum()
    m_labels = [d.strftime("%Y-%m") for d in monthly.index]
    fig_time = line_figure(
        x=m_labels,
        series=[
            {"name": "Monthly Total Sales (€M)", "y": (monthly["total_sales"] / 1e6).to_numpy(), "color": NAVY, "width": 2.5},
            {"name": "Monthly Digital Spend (€M)", "y": (monthly["digital_cost"] / 1e6).to_numpy(), "color": TEAL, "width": 2.0},
            {"name": "Monthly Catalog Spend (€M)", "y": (monthly.get("cost_catalog", pd.Series(0, index=monthly.index)) / 1e6).to_numpy(), "color": COPPER, "width": 2.0, "dash": "dot"},
        ],
        title=f"Monthly Total Sales vs Digital & Catalog Spend (€M, YoY Spend {fmt_pct(yoy_spend_pct, signed=True)})",
        xaxis_title="Month",
        yaxis_title="€ Millions",
        height=370,
    )

    # 3. 7-panel small multiples of daily spend
    dates_str = [d.strftime("%Y-%m-%d") for d in wide_df.index]
    sm_panels = []
    for ch in ch_ordered:
        slug = slug_map[ch]
        c_col = f"cost_{slug}"
        s = wide_df[c_col] / 1e3
        sm_panels.append(
            {
                "title": f"{ch} Daily Spend (k€)",
                "x": dates_str,
                "y": s.to_numpy(),
                "color": channel_color(ch),
            }
        )
    fig_sm = small_multiples_figure(
        panels=sm_panels,
        ncols=3,
        height_per_row=190,
        title="7 Paid Channels: Daily Marketing Spend Trajectories (k€/day)",
    )

    # 4. Paid channel x weekday spend index heatmap
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    idx_matrix = []
    for ch in ch_ordered:
        slug = slug_map[ch]
        c_col = f"cost_{slug}"
        s = wide_df[c_col]
        ch_mean = max(float(s.mean()), 1e-6)
        idx_matrix.append(
            [round(100.0 * float(s[wide_df.index.dayofweek == d].mean()) / ch_mean, 1) for d in range(7)]
        )
    fig_dow = heatmap_figure(
        x=dow_names,
        y=ch_ordered,
        z=np.asarray(idx_matrix),
        title="Paid Channel × Weekday Spend Index (100 = Channel Daily Mean)",
        xaxis_title="Day of Week",
        yaxis_title="Paid Channel",
        colorscale="YlOrBr",
        zmin=0,
        zmax=350,
        show_values=True,
        value_fmt=".0f",
        height=360,
    )

    # 5. Raw dose-response binned curves across the 7 paid channels
    resp_panels = []
    y_k = wide_df["total_sales"].to_numpy() / 1e3
    for ch in ch_ordered:
        slug = slug_map[ch]
        c_col = f"cost_{slug}"
        x_raw = (
            wide_df[c_col].rolling(14, min_periods=1).mean().to_numpy() / 1e3
            if ch in config.print_event_channels
            else wide_df[c_col].to_numpy() / 1e3
        )
        quantiles = np.unique(np.quantile(x_raw, np.linspace(0, 1, 9)))
        if len(quantiles) >= 3:
            bins = np.digitize(x_raw, quantiles[1:-1])
            bx = [float(x_raw[bins == b].mean()) for b in range(len(quantiles) - 1) if (bins == b).any()]
            by = [float(y_k[bins == b].mean()) for b in range(len(quantiles) - 1) if (bins == b).any()]
        else:
            bx, by = x_raw.tolist(), y_k.tolist()

        resp_panels.append(
            {
                "title": f"{ch} ({'14d Roll' if ch in config.print_event_channels else 'Daily'} k€ → Sales k€)",
                "xaxis_title": "Spend (k€)",
                "yaxis_title": "Total Sales (k€)",
                "traces": [
                    {
                        "type": "scatter",
                        "mode": "markers",
                        "name": ch,
                        "x": x_raw[::2],
                        "y": y_k[::2],
                        "marker": {"color": channel_color(ch), "size": 4, "opacity": 0.30},
                    },
                    {
                        "type": "scatter",
                        "mode": "lines+markers",
                        "name": f"{ch} Binned Mean",
                        "x": bx,
                        "y": by,
                        "line": {"color": NAVY, "width": 2.4},
                    },
                ],
            }
        )
    fig_resp = small_multiples_figure(
        panels=resp_panels,
        ncols=3,
        height_per_row=210,
        title="Binned Dose–Response Curves: Channel Spend (k€) vs Daily Total Sales (k€)",
    )

    return Section(
        id="explore_spend",
        title="Marketing Spend Structure & Cadence",
        subtitle="Budget allocation across the 7 paid levers, weekday cadence, and empirical dose-response shapes.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Total 2-Year Spend", value=fmt_eur(total_cost_sum), delta=f"Mean {fmt_eur(float(wide_df['total_cost'].mean()))}/day"),
                    KpiItem(label="2024 vs 2023 Spend", value=fmt_pct(yoy_spend_pct, signed=True), delta="vs +20.7% sales growth", status="success"),
                    KpiItem(label="Catalog Share of Spend", value=spend_rows[0]["spend_share"], delta=f"{spend_rows[0]['total_spend']} across 28 editions"),
                    KpiItem(label="Digital Share of Spend", value=fmt_pct(100.0 * float(wide_df["digital_cost"].sum()) / max(total_cost_sum, 1.0)), delta="6 continuous digital levers"),
                ]
            ),
            CalloutBlock(
                tone="warn",
                title="Why Apparent Last-Touch ROAS Misleads Budget Allocation",
                body=(
                    "Catalog accounts for **55.5% of paid spend** (€46.5M) but only **18.0% of last-touch paid sales** "
                    "(apparent ROAS 1.43×), because its entire printing & postage cost is booked on a single Thursday send-out day "
                    "while the induced orders arrive over the next 14–21 days and spill over into SEA, SEO, and Direct Type-in. "
                    "Conversely, Affiliate (`17.6×` apparent ROAS) pays commission on realised orders. Proper adstock + Bayesian MMM resolves this."
                ),
            ),
            ChartBlock(
                id="spend_vs_sales_share",
                title="Share of Marketing Spend vs Share of Attributed Paid Sales",
                subtitle="Comparison across the 7 paid media levers.",
                figure=fig_share,
            ),
            ChartBlock(
                id="spend_vs_sales_time",
                title="Monthly Total Sales vs Digital and Catalog Spend",
                subtitle="2024 spend grew +12.4% while total sales grew +20.7%.",
                figure=fig_time,
            ),
            ChartBlock(
                id="spend_small_multiples",
                title="Daily Spend Trajectories by Paid Channel (k€/day)",
                subtitle="Catalog is a 28-impulse point process (~€1.66M per edition); the 6 digital channels run continuously.",
                figure=fig_sm,
            ),
            ChartBlock(
                id="spend_weekday_index",
                title="Paid Channel × Weekday Spend Index (100 = Channel Mean)",
                subtitle="Catalog spikes on Thursdays (index ~650); digital channels follow Sunday/Monday consumer search demand.",
                figure=fig_dow,
            ),
            ChartBlock(
                id="spend_response",
                title="Empirical Dose–Response Curves (Spend vs Total Sales)",
                subtitle="Octile-binned mean sales response showing diminishing marginal returns at high daily spend.",
                figure=fig_resp,
            ),
            TableBlock(
                id="spend_summary_table",
                title="7 Paid Channels: Spend, Cadence & Apparent Return Summary",
                columns=[
                    TableColumn(key="channel", label="Paid Channel"),
                    TableColumn(key="total_spend", label="Total Spend", align="right"),
                    TableColumn(key="spend_share", label="Spend Share", align="right"),
                    TableColumn(key="paid_sales_share", label="Paid Sales Share", align="right"),
                    TableColumn(key="apparent_roas", label="Apparent ROAS", align="right"),
                    TableColumn(key="active_days", label="Active Days", align="right"),
                    TableColumn(key="mean_daily_spend", label="Mean/Day", align="right"),
                    TableColumn(key="yoy_spend", label="YoY Spend", align="right"),
                ],
                rows=spend_rows,
            ),
        ],
    )
