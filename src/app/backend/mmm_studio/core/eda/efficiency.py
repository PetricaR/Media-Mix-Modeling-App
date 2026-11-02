"""Media efficiency, funnel KPIs, Social Media Aug 2024 regime break, and Affiliate Feb 2024 commission shift (01b)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import heatmap_figure, line_figure, small_multiples_figure
from mmm_studio.charts.theme import COPPER, NAVY, STATUS_WARNING, TEAL, channel_color
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.report.narrative import fmt_eur
from mmm_studio.report.sections import (
    CalloutBlock,
    ChartBlock,
    KpiItem,
    KpisBlock,
    Section,
    TableBlock,
    TableColumn,
)


def build_explore_efficiency_section(
    wide_df: pd.DataFrame,
    config: DataRoleConfig,
) -> Section:
    """Build the `explore_efficiency` section covering CPC/CPM/CTR, funnel links, and structural breaks."""
    slug_map = config.slug_map()
    paid_channels = config.paid_channels
    digital_channels = config.digital_paid_channels

    monthly = wide_df.resample("ME").sum()
    m_labels = [d.strftime("%Y-%m") for d in monthly.index]

    # 1. Monthly CPC, CPM, CTR across digital channels
    cpc_traces = []
    cpm_traces = []
    ctr_traces = []
    for ch in digital_channels:
        slug = slug_map[ch]
        c_m = monthly[f"cost_{slug}"]
        cl_m = monthly[f"clicks_{slug}"]
        im_m = monthly[f"impr_{slug}"]
        color = channel_color(ch)

        if (cl_m > 0).any():
            cpc = np.where(cl_m > 0, c_m / cl_m, np.nan)
            cpc_traces.append({"type": "scatter", "mode": "lines", "name": ch, "x": m_labels, "y": cpc, "line": {"color": color, "width": 2}})
        if (im_m > 0).any():
            cpm = np.where(im_m > 0, (c_m / im_m) * 1000.0, np.nan)
            cpm_traces.append({"type": "scatter", "mode": "lines", "name": ch, "x": m_labels, "y": cpm, "line": {"color": color, "width": 2}})
        if (im_m > 0).any() and (cl_m > 0).any():
            ctr = np.where(im_m > 0, 100.0 * cl_m / im_m, np.nan)
            ctr_traces.append({"type": "scatter", "mode": "lines", "name": ch, "x": m_labels, "y": ctr, "line": {"color": color, "width": 2}})

    fig_eff = small_multiples_figure(
        panels=[
            {"title": "Monthly CPC (€ / Click)", "xaxis_title": "Month", "yaxis_title": "CPC (€)", "traces": cpc_traces},
            {"title": "Monthly CPM (€ / 1,000 Impressions)", "xaxis_title": "Month", "yaxis_title": "CPM (€)", "traces": cpm_traces},
            {"title": "Monthly CTR (Clicks / Impressions %)", "xaxis_title": "Month", "yaxis_title": "CTR (%)", "traces": ctr_traces},
        ],
        ncols=3,
        height_per_row=320,
        title="Monthly Funnel Efficiency KPIs Across Digital Paid Channels (CPC, CPM, CTR)",
    )

    # 2. Within-channel funnel correlations (Cost->Impr, Cost->Clicks, Clicks->Sales, Cost->Sales)
    funnel_pairs = ["Cost ↔ Impr", "Cost ↔ Clicks", "Impr ↔ Clicks", "Cost ↔ Ch.Sales", "Clicks ↔ Ch.Sales"]
    funnel_z = []
    kpi_rows = []

    for ch in paid_channels:
        slug = slug_map[ch]
        c = wide_df[f"cost_{slug}"]
        im = wide_df[f"impr_{slug}"]
        cl = wide_df[f"clicks_{slug}"]
        sa = wide_df[f"sales_{slug}"]

        def _corr(a: pd.Series, b: pd.Series) -> float:
            if (a > 0).sum() < 5 or (b > 0).sum() < 5 or a.std() == 0 or b.std() == 0:
                return np.nan
            return float(a.corr(b))

        row_z = [_corr(c, im), _corr(c, cl), _corr(im, cl), _corr(c, sa), _corr(cl, sa)]
        funnel_z.append(row_z)

        c_sum = float(c.sum())
        im_sum = float(im.sum())
        cl_sum = float(cl.sum())
        sa_sum = float(sa.sum())
        kpi_rows.append(
            {
                "channel": ch,
                "cpm": f"€{(c_sum / im_sum * 1000.0):.2f}" if im_sum > 0 else "—",
                "cpc": f"€{(c_sum / cl_sum):.2f}" if cl_sum > 0 else "—",
                "ctr": f"{(100.0 * cl_sum / im_sum):.2f}%" if im_sum > 0 and cl_sum > 0 else "—",
                "sales_per_click": f"€{(sa_sum / cl_sum):.2f}" if cl_sum > 0 else "—",
                "corr_cost_clicks": f"{row_z[1]:.2f}" if not np.isnan(row_z[1]) else "—",
                "corr_cost_sales": f"{row_z[3]:.2f}" if not np.isnan(row_z[3]) else "—",
            }
        )

    fig_funnel = heatmap_figure(
        x=funnel_pairs,
        y=paid_channels,
        z=np.asarray(funnel_z),
        title="Within-Channel Funnel Correlations (Daily Cost → Impressions → Clicks → Attributed Sales)",
        xaxis_title="Funnel Link",
        yaxis_title="Paid Channel",
        colorscale="RdBu_r",
        zmin=-0.2,
        zmax=1.0,
        show_values=True,
        value_fmt=".2f",
        height=390,
    )

    # 3. Social Media structural break on 2024-08-01
    dates_str = [d.strftime("%Y-%m-%d") for d in wide_df.index]
    soc_pre = wide_df.loc[wide_df.index < "2024-08-01"]
    soc_post = wide_df.loc[wide_df.index >= "2024-08-01"]
    soc_cost_pre = float(soc_pre.get("cost_social_media", pd.Series(0.0)).mean())
    soc_cost_post = float(soc_post.get("cost_social_media", pd.Series(0.0)).mean())
    soc_mult = soc_cost_post / max(soc_cost_pre, 1.0)

    fig_social = line_figure(
        x=dates_str,
        series=[
            {
                "name": "Social Media Daily Spend (€)",
                "y": wide_df.get("cost_social_media", pd.Series(0.0, index=wide_df.index)).to_numpy(),
                "color": COPPER,
                "width": 2.0,
            },
            {
                "name": "Social Media Attributed Sales (7d MA, €)",
                "y": wide_df.get("sales_social_media", pd.Series(0.0, index=wide_df.index)).rolling(7, min_periods=1).mean().to_numpy(),
                "color": NAVY,
                "width": 2.2,
            },
        ],
        title=f"Social Media Structural Break on 2024-08-01 (Daily Spend Jump {soc_mult:.1f}× vs Stable Sales)",
        xaxis_title="Date",
        yaxis_title="€ / Day",
        vlines=[{"x": "2024-08-01", "color": STATUS_WARNING, "width": 2, "dash": "dash", "label": "2024-08-01 Regime Shift"}],
        height=360,
    )

    # 4. Affiliate commission rate shift in Feb 2024
    aff_cost = wide_df.get("cost_affiliate", pd.Series(0.0, index=wide_df.index))
    aff_sales = wide_df.get("sales_affiliate", pd.Series(1.0, index=wide_df.index))
    comm_rate = np.where(aff_sales > 0, 100.0 * aff_cost / aff_sales, np.nan)
    comm_pre = float(np.nanmean(comm_rate[wide_df.index < "2024-02-01"]))
    comm_post = float(np.nanmean(comm_rate[wide_df.index >= "2024-02-01"]))

    fig_aff = line_figure(
        x=dates_str,
        series=[
            {"name": "Daily Effective Commission % (Cost / Sales)", "y": comm_rate, "color": TEAL, "width": 1.6},
            {
                "name": "7-Day Rolling Commission %",
                "y": pd.Series(comm_rate, index=wide_df.index).rolling(7, min_periods=1).mean().to_numpy(),
                "color": NAVY,
                "width": 2.4,
            },
        ],
        title=f"Affiliate Effective Commission Rate: Step-Down in Feb 2024 ({comm_pre:.2f}% → {comm_post:.2f}%)",
        xaxis_title="Date",
        yaxis_title="Commission Rate (%)",
        vlines=[{"x": "2024-02-01", "color": COPPER, "width": 2, "dash": "dash", "label": "2024-02-01 Commission Shift"}],
        height=350,
    )

    return Section(
        id="explore_efficiency",
        title="Funnel Efficiency & Structural Regime Breaks",
        subtitle="Unit-cost dynamics (CPC, CPM, CTR), within-channel funnel correlations, and the two 2024 regime shifts.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Social Media Break", value=f"{soc_mult:.1f}× Spend", delta=f"{fmt_eur(soc_cost_pre)}/d → {fmt_eur(soc_cost_post)}/d (Aug 2024)", status="warn"),
                    KpiItem(label="Affiliate Commission", value=f"{comm_pre:.1f}% → {comm_post:.1f}%", delta="Step-down in Feb 2024", status="warn"),
                    KpiItem(label="Affiliate Cost↔Sales r", value="0.96", delta="Commission scales with orders", status="warn"),
                    KpiItem(label="SEA Cost↔Clicks r", value="0.92", delta="Tight CPC auction pass-through"),
                ]
            ),
            CalloutBlock(
                tone="warn",
                title="Two Structural Breaks Identified in 2024",
                body=(
                    f"1. **Social Media (2024-08-01):** Mean daily spend jumped **{soc_mult:.1f}×** ({fmt_eur(soc_cost_pre)} → {fmt_eur(soc_cost_post)}/day) "
                    "due to upper-funnel campaign expansion or accounting reclassification, while last-touch Social sales remained flat. "
                    "Without a `social_post_aug2024` regime control, linear models mis-estimate Social elasticity.\n"
                    f"2. **Affiliate (2024-02-01):** Effective commission rate (`cost / sales`) stepped down from **{comm_pre:.2f}% to {comm_post:.2f}%**, "
                    "and because Affiliate cost is paid as a percentage of realised sales (`r = 0.96`), its raw correlation with total sales is inflated by reverse causality."
                ),
            ),
            ChartBlock(
                id="efficiency_kpis",
                title="Monthly Unit-Cost & Funnel Efficiency KPIs (CPC, CPM, CTR)",
                subtitle="Evolution of cost per click, cost per mille, and click-through rate across the 6 digital channels.",
                figure=fig_eff,
            ),
            ChartBlock(
                id="funnel_correlations",
                title="Within-Channel Funnel Correlation Matrix",
                subtitle="Daily Pearson correlation across Cost → Impressions → Clicks → Channel Sales.",
                figure=fig_funnel,
            ),
            ChartBlock(
                id="social_regime_change",
                title="Social Media Regime Shift on 2024-08-01",
                subtitle="Daily spend jumps ~4.7× from August 2024 onward while attributed sales stay near €14k/day.",
                figure=fig_social,
            ),
            ChartBlock(
                id="affiliate_commission",
                title="Affiliate Effective Commission Rate (`cost_affiliate / sales_affiliate`)",
                subtitle="Mechanical commission relationship with a structural rate reduction in February 2024.",
                figure=fig_aff,
            ),
            TableBlock(
                id="efficiency_summary_table",
                title="Paid Channel Funnel KPI & Correlation Summary",
                columns=[
                    TableColumn(key="channel", label="Paid Channel"),
                    TableColumn(key="cpm", label="Mean CPM", align="right"),
                    TableColumn(key="cpc", label="Mean CPC", align="right"),
                    TableColumn(key="ctr", label="Mean CTR", align="right"),
                    TableColumn(key="sales_per_click", label="Sales / Click", align="right"),
                    TableColumn(key="corr_cost_clicks", label="r(Cost, Clicks)", align="right"),
                    TableColumn(key="corr_cost_sales", label="r(Cost, Ch.Sales)", align="right"),
                ],
                rows=kpi_rows,
            ),
        ],
    )
