"""Channel sales composition, YoY growth, 12-channel small multiples, and weekday index (01 & 01b)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, heatmap_figure, small_multiples_figure
from mmm_studio.charts.theme import COPPER, NAVY, channel_color
from mmm_studio.core.features import complete_years
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

    full = complete_years(wide_df)
    years = sorted(set(pd.DatetimeIndex(full.index).year))
    y_prev, y_last = (years[-2], years[-1]) if len(years) >= 2 else (years[0], years[0])
    w23 = full[full.index.year == y_prev]
    w24 = full[full.index.year == y_last]

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
        # Means and CV over the days the channel has a row (missing channel-days are not zero-sales days)
        miss_col = f"missing_{slug}"
        present = s_all[wide_df[miss_col] == 0] if miss_col in wide_df.columns else s_all
        cv = float(present.std() / max(present.mean(), 1.0))

        summary_rows.append(
            {
                "channel": ch,
                "role": {
                    "print_event": "Paid (Print Event)",
                    "paid": "Paid Digital",
                    "digital_paid": "Paid Digital",
                    "commission": "Paid (commission, model control)",
                    "control": "Paid (model control)",
                }.get(role, "Organic / Unpaid"),
                "sales_2023": fmt_eur(s23),
                "sales_2024": fmt_eur(s24),
                "yoy_growth": fmt_pct(yoy, signed=True),
                "share_pct": fmt_pct(share),
                "daily_mean": fmt_eur(float(present.mean())),
                "missing_days": int(len(s_all) - len(present)),
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

    # 2. Monthly channel sales mix (stacked bar), complete years only
    monthly = full.resample("ME").sum()
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
        title=None,
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

    big = [r for r in zip(ch_names_sorted, shares, yoy_growths, strict=False) if r[1] >= 1.0]
    fastest, _, fastest_growth = max(big or list(zip(ch_names_sorted, shares, yoy_growths, strict=False)), key=lambda t: t[2])
    top3 = ch_names_sorted[:3]
    top3_share = sum(shares[:3])
    mix_rows = []
    org = [c for c in ch_names_sorted if c in config.unpaid_channels]
    for yr in (y_prev, y_last):
        w = full[full.index.year == yr]
        tot = float(w["total_sales"].sum()) or 1.0
        mix_rows.append(
            {
                "year": str(yr),
                "organic": fmt_pct(100.0 * sum(float(w[f"sales_{slug_map[c]}"].sum()) for c in org) / tot),
                **{slug_map[c]: fmt_pct(100.0 * float(w[f"sales_{slug_map[c]}"].sum()) / tot) for c in config.paid_channels},
            }
        )

    # Weekday where the print channel's sales peak, and the usual send-out weekday (01b §6.3)
    cat_dow_text = "Weekday profile of each channel's attributed sales."
    cat_ch = config.print_event_channels[0] if config.print_event_channels else None
    if cat_ch in ch_names_sorted:
        cat_row = idx_matrix[ch_names_sorted.index(cat_ch)]
        peak_d = int(np.argmax(cat_row))
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        cat_dow_text = f"{cat_ch} sales peak on {day_names[peak_d]} (index {cat_row[peak_d]:.0f})"
        if "catalog_sendout" in wide_df and int(wide_df["catalog_sendout"].sum()):
            send_d = int(pd.Series(wide_df.index[wide_df["catalog_sendout"] == 1].dayofweek).mode().iloc[0])
            cat_dow_text += f", {(peak_d - send_d) % 7} days after the usual {day_names[send_d]} send-out"
        cat_dow_text += "."

    return Section(
        id="explore_channels",
        title=f"Channel landscape ({len(config.active_channels)} channels)",
        subtitle=f"Attributed sales across {len(config.paid_channels)} channels with costs and {len(config.unpaid_channels)} without: where sales are credited, not what caused them.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Channels with cost data", value=fmt_pct(paid_share_pct), delta=f"of attributed sales · {len(config.paid_channels)} channels"),
                    KpiItem(label="Channels without costs", value=fmt_pct(100.0 - paid_share_pct), delta=f"{len(config.unpaid_channels)} channels: the baseline"),
                    KpiItem(label="Largest channel", value=summary_rows[0]["channel"], delta=f"{summary_rows[0]['share_pct']} of total sales"),
                    KpiItem(label=f"Fastest growing ({y_last})", value=fastest, delta=f"{fastest_growth:+.0f}% vs {y_prev}", status="warn" if fastest_growth > 100 else "neutral"),
                ]
            ),
            ChartBlock(
                id="channel_share_growth",
                title="Channel Share of Total Sales & 2024 YoY Growth",
                subtitle=(
                    f"{', '.join(top3)} carry {top3_share:.0f}% of attributed sales; channels with cost data {paid_share_pct:.1f}%. "
                    f"{fastest} grew {fastest_growth:+.0f}% in {y_last}."
                ),
                figure=fig_share_growth,
            ),
            ChartBlock(
                id="channel_mix_monthly",
                title="Monthly Sales Composition by Channel",
                subtitle=f"Stacked monthly attributed sales (€M), {y_prev}–{y_last}; the unpaid layer is part of the base demand.",
                figure=fig_monthly_mix,
            ),
            ChartBlock(
                id="channel_small_multiples",
                title=f"Daily sales of all {len(sm_panels)} channels (7-day moving average)",
                subtitle="Most channels share the same seasonal shape; the catalog saw-tooth resets with every send-out.",
                figure=fig_sm,
            ),
            ChartBlock(
                id="channel_weekday_index",
                title="Channel × Weekday Sales Seasonality Index (100 = Channel Mean)",
                subtitle=cat_dow_text,
                figure=fig_dow_idx,
            ),
            TableBlock(
                id="channel_summary_table",
                title=f"{len(summary_rows)}-channel sales and growth summary",
                columns=[
                    TableColumn(key="channel", label="Channel"),
                    TableColumn(key="role", label="Classification"),
                    TableColumn(key="sales_2023", label="2023 Sales", align="right"),
                    TableColumn(key="sales_2024", label="2024 Sales", align="right"),
                    TableColumn(key="yoy_growth", label="YoY %", align="right"),
                    TableColumn(key="share_pct", label="Share %", align="right"),
                    TableColumn(key="daily_mean", label="Mean/Day", align="right"),
                    TableColumn(key="cv", label="CV (daily)", align="right"),
                    TableColumn(key="missing_days", label="Missing days", align="right"),
                ],
                rows=summary_rows,
                caption="Mean / day and CV are over the days a channel has a row; missing channel-days count as 0 in total sales.",
            ),
            TableBlock(
                id="channel_mix_table",
                title="Share of total sales by year",
                columns=[
                    TableColumn(key="year", label="Year"),
                    TableColumn(key="organic", label="Organic & unpaid", align="right"),
                    *[TableColumn(key=slug_map[c], label=c, align="right") for c in config.paid_channels],
                ],
                rows=mix_rows,
            ),
        ],
    )
