"""Media efficiency, funnel KPIs, Social Media Aug 2024 regime break, and Affiliate Feb 2024 commission shift (01b)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import heatmap_figure, line_figure, small_multiples_figure
from mmm_studio.charts.theme import COPPER, NAVY, STATUS_WARNING, TEAL, channel_color
from mmm_studio.core.eda.notebook_stats import social_regime
from mmm_studio.core.features import complete_years
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.report.facts import AFFILIATE_BREAK, SOCIAL_BREAK
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

    monthly = complete_years(wide_df).resample("ME").sum()
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
    ctr_over_100: list[str] = []
    no_impressions: list[str] = []

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
        if im_sum <= 0:
            no_impressions.append(ch)
        elif cl_sum > im_sum:
            ctr_over_100.append(ch)
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

    # 3. Social Media structural break on 2024-08-01: Jan–Jul vs Aug–Dec of the break year (01b §8.3)
    dates_str = [d.strftime("%Y-%m-%d") for d in wide_df.index]
    has_social = "cost_social_media" in wide_df and wide_df.index.min() < SOCIAL_BREAK < wide_df.index.max()
    regime = social_regime(wide_df, SOCIAL_BREAK) if has_social else None
    soc_mult = float(regime.loc["Spend", "ratio"]) if regime is not None else float("nan")
    soc_cost_pre = float(regime.loc["Spend", "before"]) if regime is not None else float("nan")
    soc_cost_post = float(regime.loc["Spend", "after"]) if regime is not None else float("nan")
    soc_sales_mult = float(regime.loc["Attributed sales", "ratio"]) if regime is not None else float("nan")
    soc_clicks_mult = float(regime.loc["Clicks", "ratio"]) if regime is not None else float("nan")

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
        title=None,
        xaxis_title="Date",
        yaxis_title="€ / Day",
        vlines=[{"x": "2024-08-01", "color": STATUS_WARNING, "width": 2, "dash": "dash", "label": "2024-08-01 Regime Shift"}],
        height=360,
    )

    # 4. Affiliate commission rate shift in Feb 2024
    aff_cost = wide_df.get("cost_affiliate", pd.Series(0.0, index=wide_df.index))
    aff_sales = wide_df.get("sales_affiliate", pd.Series(1.0, index=wide_df.index))
    comm_rate = np.where(aff_sales > 0, 100.0 * aff_cost / aff_sales, np.nan)
    # Commission as a share of sales over each period (ratio of sums, robust to low-sales days)
    pre_mask, post_mask = wide_df.index < AFFILIATE_BREAK, wide_df.index >= AFFILIATE_BREAK
    comm_pre = 100.0 * float(aff_cost[pre_mask].sum()) / max(float(aff_sales[pre_mask].sum()), 1.0)
    comm_post = 100.0 * float(aff_cost[post_mask].sum()) / max(float(aff_sales[post_mask].sum()), 1.0)
    comm_word = "step-up" if comm_post > comm_pre else "step-down"
    r_aff = float(aff_cost.corr(aff_sales)) if aff_cost.std() > 0 else float("nan")
    r_sea = float(wide_df["cost_sea"].corr(wide_df["clicks_sea"])) if "cost_sea" in wide_df and "clicks_sea" in wide_df else float("nan")

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
        title=None,
        xaxis_title="Date",
        yaxis_title="Commission Rate (%)",
        vlines=[{"x": "2024-02-01", "color": COPPER, "width": 2, "dash": "dash", "label": "2024-02-01 Commission Shift"}],
        height=350,
    )

    return Section(
        id="explore_efficiency",
        title="Media efficiency and regime breaks",
        subtitle="Unit-cost dynamics (CPC, CPM, CTR), within-channel funnel correlations, and the two 2024 regime shifts.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Social Media regime (Aug 2024)", value=f"×{soc_mult:.1f} spend", delta=f"clicks ×{soc_clicks_mult:.1f}, attributed sales ×{soc_sales_mult:.1f}", status="warn"),
                    KpiItem(label="Affiliate commission", value=f"{comm_pre:.1f}% → {comm_post:.1f}%", delta=f"of own sales; {comm_word} in Feb 2024", status="warn"),
                    KpiItem(label="Affiliate r(cost, own sales)", value=f"{r_aff:.2f}", delta="Cost follows sales (commission)", status="warn"),
                    KpiItem(label="SEA r(cost, clicks)", value=f"{r_sea:.2f}", delta="Billed per click"),
                ]
            ),
            CalloutBlock(
                tone="warn",
                title="Two Structural Breaks Identified in 2024",
                body=(
                    f"1. **Social Media ({SOCIAL_BREAK:%d %b %Y}):** daily spend ×{soc_mult:.1f} ({fmt_eur(soc_cost_pre)} → {fmt_eur(soc_cost_post)}), "
                    f"clicks ×{soc_clicks_mult:.1f} and attributed sales ×{soc_sales_mult:.1f} (Aug–Dec vs Jan–Jul). This looks like a change of "
                    "campaign type or tracking set-up, not a gradual gain: a single Social coefficient across the period is mis-specified, "
                    "and the Q4 hold-out lies entirely in the new regime.\n"
                    f"2. **Affiliate ({AFFILIATE_BREAK:%b %Y}):** commission {comm_pre:.1f}% → {comm_post:.1f}% of its own sales. Because the "
                    f"cost is a commission on realised sales (r = {r_aff:.2f}), its high correlation with total sales is largely reverse causality."
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
                subtitle=f"Daily spend ×{soc_mult:.1f}, clicks ×{soc_clicks_mult:.1f}, attributed sales ×{soc_sales_mult:.1f} (Aug–Dec vs Jan–Jul {SOCIAL_BREAK.year}).",
                figure=fig_social,
            ),
            *(
                [
                    TableBlock(
                        id="social_regime_table",
                        title=f"Social Media before and after {SOCIAL_BREAK:%d %b %Y}",
                        columns=[
                            TableColumn(key="metric", label="Daily mean"),
                            TableColumn(key="before", label=f"Jan–Jul {SOCIAL_BREAK.year}", align="right"),
                            TableColumn(key="after", label=f"Aug–Dec {SOCIAL_BREAK.year}", align="right"),
                            TableColumn(key="ratio", label="Ratio", align="right"),
                        ],
                        rows=[
                            {
                                "metric": m,
                                "before": f"{100 * regime.loc[m, 'before']:.2f}%" if m == "CTR" else (f"{regime.loc[m, 'before']:,.0f}" if m == "Clicks" else fmt_eur(float(regime.loc[m, 'before']))),
                                "after": f"{100 * regime.loc[m, 'after']:.2f}%" if m == "CTR" else (f"{regime.loc[m, 'after']:,.0f}" if m == "Clicks" else fmt_eur(float(regime.loc[m, 'after']))),
                                "ratio": f"×{regime.loc[m, 'ratio']:.1f}",
                            }
                            for m in regime.index
                        ],
                    )
                ]
                if regime is not None
                else []
            ),
            ChartBlock(
                id="affiliate_commission",
                title="Affiliate Effective Commission Rate (`cost_affiliate / sales_affiliate`)",
                subtitle=f"Commission {comm_pre:.1f}% of own sales before {AFFILIATE_BREAK:%b %Y}, {comm_post:.1f}% after.",
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
                caption=" ".join(
                    part
                    for part in (
                        f"{', '.join(ctr_over_100)}: more clicks than impressions (CTR > 100 %), so CPM and CTR are not meaningful."
                        if ctr_over_100
                        else "",
                        f"{', '.join(no_impressions)}: no impressions recorded." if no_impressions else "",
                    )
                    if part
                )
                or None,
            ),
        ],
    )
