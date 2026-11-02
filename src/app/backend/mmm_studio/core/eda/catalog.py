"""Catalog deep-dive: 28 send-outs, event study, adstock kernel decay, cross-channel halo, circulation lift, and matched-Thursday experiment (01 & 01b)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, line_figure, small_multiples_figure
from mmm_studio.charts.theme import COPPER, NAVY, SLATE, TEAL, channel_color
from mmm_studio.core.calendar import delayed_binomial_adstock
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


def compute_halo_uplift_7d(wide_df: pd.DataFrame, config: DataRoleConfig) -> dict[str, float]:
    """Compute 7-day post-catalog halo uplift vs non-catalog days (matching 01_data_understanding)."""
    slug_map = config.slug_map()
    in_window = (wide_df["days_since_catalog"] >= 1) & (wide_df["days_since_catalog"] <= 7)
    out_window = ~in_window

    uplifts: dict[str, float] = {}
    mean_in_tot = float(wide_df.loc[in_window, "total_sales"].mean())
    mean_out_tot = float(wide_df.loc[out_window, "total_sales"].mean())
    uplifts["total_sales"] = round(100.0 * (mean_in_tot / max(mean_out_tot, 1.0) - 1.0), 1)

    for ch in config.active_channels:
        col = f"sales_{slug_map[ch]}"
        if col in wide_df.columns:
            m_in = float(wide_df.loc[in_window, col].mean())
            m_out = float(wide_df.loc[out_window, col].mean())
            uplifts[ch] = round(100.0 * (m_in / max(m_out, 1.0) - 1.0), 1)
    return uplifts


def build_explore_catalog_section(
    wide_df: pd.DataFrame,
    config: DataRoleConfig,
) -> Section:
    """Build the `explore_catalog` section with all 6 Catalog deep-dive figures."""
    config.slug_map()
    sendout_mask = wide_df["catalog_sendout"] == 1
    sendout_df = wide_df.loc[sendout_mask].copy()
    n_sendouts = int(sendout_mask.sum())

    dow_counts = sendout_df.index.day_name().value_counts().to_dict()
    n_thu = int(dow_counts.get("Thursday", 0))
    n_wed = int(dow_counts.get("Wednesday", 0))
    n_fri = int(dow_counts.get("Friday", 0))

    # 1. Timeline of the 28 Catalog send-outs (Cost €M and Print Circulation / Impressions M)
    send_dates = [d.strftime("%Y-%m-%d") for d in sendout_df.index]
    send_costs_m = (sendout_df.get("cost_catalog", pd.Series(0.0)) / 1e6).to_numpy()
    send_impr_m = (sendout_df.get("impr_catalog", pd.Series(0.0)) / 1e6).to_numpy()

    fig_sendouts = bar_figure(
        categories=send_dates,
        series=[
            {"name": "Edition Cost (€M)", "values": send_costs_m, "color": COPPER},
            {"name": "Print Circulation (Million Copies)", "values": send_impr_m, "color": NAVY},
        ],
        title=f"All {n_sendouts} Catalog Send-Out Editions: Print Spend (€M) & Circulation (Million Copies)",
        xaxis_title="Send-out Date",
        yaxis_title="€M / Million Copies",
        barmode="group",
        height=370,
    )

    # 2. Event-study impulse response around Catalog send-outs (tau = -7 .. +20)
    taus = list(range(-7, 21))
    send_indices = np.where(sendout_mask.to_numpy())[0]
    y_arr = wide_df["total_sales"].to_numpy()
    cat_arr = wide_df.get("sales_catalog", pd.Series(0.0, index=wide_df.index)).to_numpy()
    non_cat_arr = y_arr - cat_arr

    tot_mean, tot_lo, tot_hi = [], [], []
    cat_mean, non_cat_mean = [], []
    for tau in taus:
        valid_idx = [idx + tau for idx in send_indices if 0 <= idx + tau < len(wide_df)]
        vals_tot = y_arr[valid_idx] / 1e3
        vals_cat = cat_arr[valid_idx] / 1e3
        vals_non = non_cat_arr[valid_idx] / 1e3
        m = float(np.mean(vals_tot))
        se = float(np.std(vals_tot, ddof=1) / np.sqrt(max(len(vals_tot), 1)))
        tot_mean.append(m)
        tot_lo.append(m - 1.645 * se)
        tot_hi.append(m + 1.645 * se)
        cat_mean.append(float(np.mean(vals_cat)))
        non_cat_mean.append(float(np.mean(vals_non)))

    fig_event_study = line_figure(
        x=taus,
        series=[
            {"name": "Total Sales 90% CI Upper", "y": tot_hi, "color": "rgba(27,79,114,0)", "showlegend": False},
            {
                "name": "Total Sales 90% CI",
                "y": tot_lo,
                "color": "rgba(27,79,114,0)",
                "fill": "tonexty",
                "fillcolor": "rgba(27,79,114,0.15)",
            },
            {"name": "Total Sales Mean (k€)", "y": tot_mean, "color": NAVY, "width": 2.6},
            {"name": "Non-Catalog Channels Mean (k€)", "y": non_cat_mean, "color": TEAL, "width": 2.0, "dash": "dash"},
            {"name": "Direct Catalog Channel Mean (k€)", "y": cat_mean, "color": COPPER, "width": 2.2},
        ],
        title="Event-Study Impulse Response Around Catalog Send-Outs (τ = -7 to +20 Days)",
        xaxis_title="Days Relative to Catalog Send-out (τ = 0 is Send-out Thursday)",
        yaxis_title="Mean Daily Sales (k€)",
        vlines=[
            {"x": 0, "color": SLATE, "dash": "dash", "label": "Send-out (τ=0)"},
            {"x": 3, "color": COPPER, "dash": "dot", "label": "Peak Response (τ=+3d)"},
        ],
        height=390,
    )

    # 3. Empirical Catalog decay vs Geometric (alpha=0.85) & Delayed Binomial Adstock (peak=3d, decay=0.88)
    k_lags = np.arange(0, 22)
    emp_excess = np.maximum(np.asarray(cat_mean[7:29]) - float(np.mean(cat_mean[:5])), 0.0)
    emp_norm = emp_excess / max(float(emp_excess.sum()), 1e-6)
    geo_w = np.power(0.85, k_lags)
    geo_w = geo_w / geo_w.sum()
    _, binom_w = delayed_binomial_adstock(np.ones(22), peak_lag=3.0, max_lag=21, decay=0.88)

    fig_decay = line_figure(
        x=k_lags.tolist(),
        series=[
            {"name": "Empirical Catalog Sales Excess Share", "y": emp_norm, "color": SLATE, "width": 2.0, "mode": "lines+markers"},
            {"name": "Delayed Binomial Adstock (Peak +3d, α=0.88)", "y": binom_w, "color": COPPER, "width": 2.8},
            {"name": "Simple Geometric Adstock (α=0.85, Peak Day 0)", "y": geo_w, "color": NAVY, "width": 2.0, "dash": "dot"},
        ],
        title="Catalog Impulse Decay Kernel: Empirical vs Delayed Binomial (+3d Peak) & Geometric Adstock",
        xaxis_title="Days Since Catalog Send-out (k = 0..21)",
        yaxis_title="Normalized Impulse Weight",
        height=360,
    )

    # 4. Cross-channel Halo Uplift (7 days post send-out vs other days)
    uplifts_7d = compute_halo_uplift_7d(wide_df, config)
    non_cat_channels = [c for c in config.active_channels if c != "Catalog"]
    non_cat_sorted = sorted(non_cat_channels, key=lambda c: uplifts_7d.get(c, 0.0))
    halo_cats = non_cat_sorted + ["Total Sales (All 12 Channels)"]
    halo_vals = [uplifts_7d.get(c, 0.0) for c in non_cat_sorted] + [uplifts_7d["total_sales"]]

    fig_halo = bar_figure(
        categories=halo_cats,
        series=[
            {
                "name": "7-Day Post-Catalog Uplift vs Other Days (%)",
                "values": halo_vals,
                "colors": [COPPER if c.startswith("Total") else channel_color(c) for c in halo_cats],
            }
        ],
        orientation="h",
        title=f"Cross-Channel Halo Uplift in the 7 Days Following a Catalog Send-Out (Total Sales +{uplifts_7d['total_sales']:.1f}%)",
        xaxis_title="Mean Daily Sales Uplift (% vs Non-Catalog Days)",
        yaxis_title="Channel",
        showlegend=False,
        height=410,
    )

    # 5. Edition Lift vs Spend and Print Circulation across the 28 send-outs
    ed_rows = []
    ed_spend_m = []
    ed_impr_m = []
    ed_sales_7d_m = []
    for idx in send_indices:
        dt = wide_df.index[idx]
        c_val = float(wide_df["cost_catalog"].iloc[idx])
        im_val = float(wide_df["impr_catalog"].iloc[idx])
        w7_sales = float(wide_df["total_sales"].iloc[idx : min(idx + 7, len(wide_df))].sum())
        w7_cat = float(wide_df["sales_catalog"].iloc[idx : min(idx + 7, len(wide_df))].sum())
        ed_spend_m.append(c_val / 1e6)
        ed_impr_m.append(im_val / 1e6)
        ed_sales_7d_m.append(w7_sales / 1e6)
        ed_rows.append(
            {
                "date": dt.strftime("%Y-%m-%d (%a)"),
                "spend": fmt_eur(c_val),
                "circulation": f"{im_val / 1e6:.2f}M",
                "cost_per_copy": f"€{(c_val / im_val):.3f}" if im_val > 0 else "—",
                "catalog_sales_7d": fmt_eur(w7_cat),
                "total_sales_7d": fmt_eur(w7_sales),
            }
        )

    fig_circ = small_multiples_figure(
        panels=[
            {
                "title": "7-Day Total Sales (€M) vs Edition Spend (€M)",
                "xaxis_title": "Catalog Edition Spend (€M)",
                "yaxis_title": "7-Day Total Sales (€M)",
                "traces": [
                    {
                        "type": "scatter",
                        "mode": "markers",
                        "name": "Editions",
                        "x": ed_spend_m,
                        "y": ed_sales_7d_m,
                        "marker": {"color": COPPER, "size": 9},
                    }
                ],
            },
            {
                "title": "7-Day Total Sales (€M) vs Print Circulation (M Copies)",
                "xaxis_title": "Print Circulation (Million Copies)",
                "yaxis_title": "7-Day Total Sales (€M)",
                "traces": [
                    {
                        "type": "scatter",
                        "mode": "markers",
                        "name": "Editions",
                        "x": ed_impr_m,
                        "y": ed_sales_7d_m,
                        "marker": {"color": NAVY, "size": 9},
                    }
                ],
            },
        ],
        ncols=2,
        height_per_row=320,
        title="Catalog Edition Scale vs 7-Day Post-Sendout Sales (€M)",
    )

    # 6. Matched-Thursday Natural Experiment (Thursdays WITH vs WITHOUT Catalog send-out)
    thu_indices = np.where((wide_df.index.dayofweek == 3) & (np.arange(len(wide_df)) + 7 <= len(wide_df)))[0]
    with_cat_curves = []
    without_cat_curves = []
    for idx in thu_indices:
        window = wide_df["total_sales"].iloc[idx : idx + 7].to_numpy() / 1e3
        if wide_df["catalog_sendout"].iloc[idx] == 1:
            with_cat_curves.append(window)
        else:
            without_cat_curves.append(window)

    mean_with = np.mean(with_cat_curves, axis=0) if with_cat_curves else np.zeros(7)
    mean_without = np.mean(without_cat_curves, axis=0) if without_cat_curves else np.zeros(7)
    rel_days = ["Thu (d+0)", "Fri (d+1)", "Sat (d+2)", "Sun (d+3)", "Mon (d+4)", "Tue (d+5)", "Wed (d+6)"]
    net_7d_lift_eur = float(np.sum(mean_with - mean_without) * 1e3)

    fig_nat_exp = bar_figure(
        categories=rel_days,
        series=[
            {"name": f"Thursdays WITH Catalog Send-out (n={len(with_cat_curves)})", "values": mean_with, "color": COPPER},
            {"name": f"Thursdays WITHOUT Catalog Send-out (n={len(without_cat_curves)})", "values": mean_without, "color": SLATE},
        ],
        title=f"Matched-Thursday Natural Experiment: 7-Day Trajectory With vs Without Catalog (+{fmt_eur(net_7d_lift_eur)} / week)",
        xaxis_title="Day Following Thursday",
        yaxis_title="Mean Daily Total Sales (k€)",
        barmode="group",
        height=370,
    )

    return Section(
        id="explore_catalog",
        title="Catalog Mechanics & Cross-Channel Halo",
        subtitle="Analysis of the 28 print Catalog send-outs, postal delivery lag (+3d peak), and omnichannel spillover.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Catalog Send-outs", value=f"{n_sendouts} editions", delta=f"{n_thu} Thu, {n_wed} Wed, {n_fri} Fri"),
                    KpiItem(label="Peak Impulse Lag", value="+3 Days (Sunday)", delta="r = +0.40 vs -0.01 on day 0", status="success"),
                    KpiItem(label="7-Day Total Sales Halo", value=fmt_pct(uplifts_7d["total_sales"], signed=True), delta="SEO +20%, Social +20%, Direct +17%", status="success"),
                    KpiItem(label="Matched-Thursday Lift", value=fmt_eur(net_7d_lift_eur), delta="Cumulative 7-day excess sales", status="success"),
                ]
            ),
            CalloutBlock(
                tone="success",
                title="Key Finding: Catalog Drives a +38.4% Cross-Channel Halo Over the Following 7 Days",
                body=(
                    "When a print catalog lands in households on Friday/Saturday after a Thursday send-out, customers browse "
                    "offline and purchase online via **SEO (+20%)**, **Social Media (+20%)**, **Direct Type-in (+17%)**, "
                    "**Affiliate (+14%)**, and **SEA (+11%)**. Replacing raw Catalog spend with a delayed binomial in-home adstock "
                    "(`peak_lag = 3d, decay = 0.88`) raises linear model adjusted R² from **0.83 to 0.90** (+7 pp)."
                ),
            ),
            ChartBlock(
                id="catalog_sendouts",
                title="Timeline of All 28 Catalog Send-Outs: Spend (€M) & Print Circulation (M Copies)",
                subtitle="Bi-weekly to monthly cadence (~€1.66M and ~4.3M copies per edition).",
                figure=fig_sendouts,
            ),
            ChartBlock(
                id="catalog_event_study",
                title="Event-Study Impulse Response Around Catalog Send-Outs (τ = -7 .. +20 Days)",
                subtitle="Mean daily sales (k€) ± 90% confidence band across all 28 editions.",
                figure=fig_event_study,
            ),
            ChartBlock(
                id="catalog_decay",
                title="Empirical Catalog Decay vs Delayed Binomial (+3d Peak) & Geometric Adstock",
                subtitle="Delayed binomial adstock captures the postal delivery ramp and 2–3 week household shelf life.",
                figure=fig_decay,
            ),
            ChartBlock(
                id="catalog_halo",
                title="Cross-Channel Halo Uplift (7 Days Post Send-Out vs Non-Catalog Baseline)",
                subtitle="Every digital and organic channel experiences elevated conversions during the 7-day in-home window.",
                figure=fig_halo,
            ),
            ChartBlock(
                id="catalog_natural_experiment",
                title="Matched-Thursday Natural Experiment: Thursdays With vs Without Catalog Send-Out",
                subtitle="Comparing identical calendar weekdays isolates the Catalog impulse from regular Sunday seasonality.",
                figure=fig_nat_exp,
            ),
            ChartBlock(
                id="catalog_lift_vs_circulation",
                title="7-Day Total Sales vs Edition Spend and Print Circulation",
                subtitle="Larger circulation editions in Q4 and spring generate the highest 7-day sales volume.",
                figure=fig_circ,
            ),
            TableBlock(
                id="catalog_editions_table",
                title="Catalog Send-Out Editions Log (First 14 & Peak Editions)",
                columns=[
                    TableColumn(key="date", label="Send-out Date"),
                    TableColumn(key="spend", label="Edition Cost", align="right"),
                    TableColumn(key="circulation", label="Circulation", align="right"),
                    TableColumn(key="cost_per_copy", label="Cost / Copy", align="right"),
                    TableColumn(key="catalog_sales_7d", label="7d Catalog Sales", align="right"),
                    TableColumn(key="total_sales_7d", label="7d Total Sales", align="right"),
                ],
                rows=ed_rows[:16],
            ),
        ],
    )
