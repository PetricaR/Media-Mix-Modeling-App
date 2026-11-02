"""Same-day and lagged correlations, cross-channel VIF, and Pearson/Spearman toggles (01 & 01b)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, heatmap_figure, line_figure
from mmm_studio.charts.theme import COPPER, NAVY, SLATE, TEAL, channel_color
from mmm_studio.core.eda.notebook_stats import correlation_robustness, vif_variants
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.report.sections import (
    CalloutBlock,
    ChartBlock,
    KpiItem,
    KpisBlock,
    Section,
    TableBlock,
    TableColumn,
)


def compute_vif_table(df_costs: pd.DataFrame) -> dict[str, float]:
    """Compute Variance Inflation Factors (VIF = diag(R^{-1})) for a set of cost columns."""
    clean = df_costs.dropna()
    if clean.shape[1] < 2:
        return {c: 1.0 for c in df_costs.columns}
    corr = clean.corr().to_numpy(dtype=float)
    try:
        inv_corr = np.linalg.inv(corr)
        vifs = np.diag(inv_corr)
    except np.linalg.LinAlgError:
        vifs = np.ones(clean.shape[1])
    return {col: round(float(v), 2) for col, v in zip(df_costs.columns, vifs)}


def build_explore_correlations_section(
    wide_df: pd.DataFrame,
    config: DataRoleConfig,
) -> Section:
    """Build the `explore_correlations` section with Pearson/Spearman toggles and lag profiles."""
    slug_map = config.slug_map()
    all_channels = config.active_channels
    paid_channels = config.paid_channels
    digital_channels = config.digital_paid_channels
    y = wide_df["total_sales"]

    # 1. Channel sales correlation with total_sales & rest-of-business (other_sales) — Pearson & Spearman
    def _build_channel_sales_corr_fig(method: str) -> dict:
        names = []
        r_total = []
        r_other = []
        for ch in all_channels:
            col = f"sales_{slug_map[ch]}"
            s = wide_df[col]
            other = y - s
            names.append(ch)
            r_total.append(round(float(s.corr(y, method=method)), 3))
            r_other.append(round(float(s.corr(other, method=method)), 3))
        order = np.argsort(r_total)
        return bar_figure(
            categories=[names[i] for i in order],
            series=[
                {"name": f"Corr with Total Sales ({method.title()})", "values": [r_total[i] for i in order], "color": NAVY},
                {"name": f"Corr with Other Channels' Sales ({method.title()})", "values": [r_other[i] for i in order], "color": TEAL},
            ],
            orientation="h",
            title=f"Channel Sales Correlation with Total Sales vs Rest-of-Business ({method.title()})",
            xaxis_title=f"{method.title()} r",
            yaxis_title="Channel",
            barmode="group",
            height=420,
        )

    fig_ch_sales_pearson = _build_channel_sales_corr_fig("pearson")
    fig_ch_sales_spearman = _build_channel_sales_corr_fig("spearman")

    # 2. Paid channel Cost vs Total Sales and Channel Sales — Pearson & Spearman
    corr_cost_pearson: dict[str, float] = {}
    corr_cost_spearman: dict[str, float] = {}
    for ch in paid_channels:
        c_ser = wide_df[f"cost_{slug_map[ch]}"]
        corr_cost_pearson[ch] = round(float(c_ser.corr(y, method="pearson")), 2)
        corr_cost_spearman[ch] = round(float(c_ser.corr(y, method="spearman")), 2)

    def _build_cost_corr_fig(method: str) -> dict:
        ch_order = sorted(paid_channels, key=lambda c: float(wide_df[f"cost_{slug_map[c]}"].corr(y, method=method)))
        r_cost_tot = [round(float(wide_df[f"cost_{slug_map[c]}"].corr(y, method=method)), 2) for c in ch_order]
        r_sales_tot = [round(float(wide_df[f"sales_{slug_map[c]}"].corr(y, method=method)), 2) for c in ch_order]
        return bar_figure(
            categories=ch_order,
            series=[
                {"name": f"r(Channel Cost, Total Sales) [{method.title()}]", "values": r_cost_tot, "color": COPPER},
                {"name": f"r(Channel Sales, Total Sales) [{method.title()}]", "values": r_sales_tot, "color": NAVY},
            ],
            orientation="h",
            title=f"Same-Day Correlation with Total Sales: Channel Spend vs Channel Sales ({method.title()})",
            xaxis_title=f"{method.title()} Correlation with Daily Total Sales",
            yaxis_title="Paid Channel",
            barmode="group",
            vlines=[{"x": 0, "color": SLATE, "dash": "solid"}],
            height=380,
        )

    fig_cost_pearson = _build_cost_corr_fig("pearson")
    fig_cost_spearman = _build_cost_corr_fig("spearman")

    # 3. Cross-channel daily spend correlation matrix (Pearson & Spearman) + VIF
    cost_df = pd.DataFrame({ch: wide_df[f"cost_{slug_map[ch]}"] for ch in paid_channels})
    cost_df["Total Sales"] = y

    def _build_matrix_fig(method: str) -> dict:
        mat = cost_df.corr(method=method)
        return heatmap_figure(
            x=mat.columns.tolist(),
            y=mat.index.tolist(),
            z=mat.to_numpy(),
            title=f"Cross-Channel Daily Spend & Total Sales Correlation Matrix ({method.title()})",
            xaxis_title="Channel / Target",
            yaxis_title="Channel / Target",
            colorscale="RdBu_r",
            zmin=-0.3,
            zmax=1.0,
            show_values=True,
            value_fmt=".2f",
            height=430,
        )

    fig_mat_pearson = _build_matrix_fig("pearson")
    fig_mat_spearman = _build_matrix_fig("spearman")

    # Digital raw cost VIF on the full modelling panel (notebook 02 §5)
    dig_cost_df = pd.DataFrame({ch: wide_df[f"cost_{slug_map[ch]}"] for ch in digital_channels})
    vif_digital = compute_vif_table(dig_cost_df)
    vif_var = vif_variants(wide_df, config)

    # 4. How robust are the correlations? Same day, weekly totals, trend & calendar removed (01b §9.2)
    robust = correlation_robustness(wide_df, config)
    rob_ch = sorted(paid_channels, key=lambda c: corr_cost_pearson[c], reverse=True)
    fig_robust = bar_figure(
        categories=robust.index.tolist(),
        series=[
            {"name": "Daily, same day", "values": robust["daily"].round(2).tolist(), "color": NAVY},
            {"name": "Weekly totals", "values": robust["weekly"].round(2).tolist(), "color": TEAL},
            {"name": "Daily, trend & calendar removed", "values": robust["deseasonalised"].round(2).tolist(), "color": COPPER},
        ],
        xaxis_title="Paid channel",
        yaxis_title="Pearson r with total sales",
        barmode="group",
        height=370,
    )

    # 5. Cross-correlation lag profile k = 0..21 days: corr(cost_{c, t}, total_sales_{t+k})
    lags = list(range(0, 22))
    lag_series = []
    catalog_lag3_r = 0.0
    for ch in paid_channels:
        c_ser = wide_df[f"cost_{slug_map[ch]}"]
        r_lags = [round(float(c_ser.corr(y.shift(-k))), 3) for k in lags]
        if ch == "Catalog" and len(r_lags) > 3:
            catalog_lag3_r = round(r_lags[3], 2)
        lag_series.append(
            {
                "name": ch,
                "y": r_lags,
                "color": channel_color(ch),
                "width": 2.8 if ch == "Catalog" else 1.8,
            }
        )

    fig_lag = line_figure(
        x=lags,
        series=lag_series,
        title="Lagged Cross-Correlation: corr(Cost at Day t, Total Sales at Day t + k) for k = 0..21 Days",
        xaxis_title="Forward Lag k (Days After Spend)",
        yaxis_title="Pearson r with Total Sales(t + k)",
        hlines=[{"y": 0, "color": SLATE, "dash": "solid"}],
        vlines=[{"x": 3, "color": COPPER, "dash": "dot", "label": f"Catalog Peak (+3d, r={catalog_lag3_r:.2f})"}],
        height=400,
    )

    # Summary table
    table_rows = []
    for ch in rob_ch:
        slug = slug_map[ch]
        c_ser = wide_df[f"cost_{slug}"]
        r_l0 = float(c_ser.corr(y, method="pearson"))
        r_sp = float(c_ser.corr(y, method="spearman"))
        r_l3 = float(c_ser.corr(y.shift(-3)))
        r_l7 = float(c_ser.corr(y.shift(-7)))
        r_lags_all = [float(c_ser.corr(y.shift(-k))) for k in range(0, 15)]
        best_k = int(np.nanargmax(r_lags_all))
        best_r = r_lags_all[best_k]
        table_rows.append(
            {
                "channel": ch,
                "pearson_lag0": f"{r_l0:+.2f}",
                "spearman_lag0": f"{r_sp:+.2f}",
                "pearson_lag3": f"{r_l3:+.2f}",
                "pearson_lag7": f"{r_l7:+.2f}",
                "peak_lag": f"+{best_k}d ({best_r:+.2f})",
                "digital_vif": f"{vif_digital[ch]:.2f}" if ch in vif_digital else "–",
            }
        )

    top2 = sorted(corr_cost_pearson, key=corr_cost_pearson.get, reverse=True)[:2]
    vif_sorted = sorted(vif_digital, key=vif_digital.get, reverse=True)
    cat = config.print_event_channels[0] if config.print_event_channels else None
    cat_r0 = corr_cost_pearson.get(cat, float("nan")) if cat else float("nan")
    cat_lags = [float(wide_df[f"cost_{slug_map[cat]}"].corr(y.shift(-k))) for k in lags] if cat else []
    cat_best = int(np.nanargmax(cat_lags)) if cat_lags else 0
    cat_best_r = cat_lags[cat_best] if cat_lags else float("nan")
    dig_pairs = cost_df[[c for c in digital_channels if c in cost_df]].corr().to_numpy()
    off = dig_pairs[~np.eye(len(dig_pairs), dtype=bool)] if len(dig_pairs) > 1 else np.array([np.nan])
    rob_rows = [
        {
            "channel": ch,
            "daily": f"{r['daily']:.2f}",
            "weekly": "–" if r["weekly"] != r["weekly"] else f"{r['weekly']:.2f}",
            "deseasonalised": f"{r['deseasonalised']:.2f}",
        }
        for ch, r in robust.iterrows()
    ]
    vif_rows = [
        {"channel": ch, "daily": f"{r['daily']:.1f}", "weekly": f"{r['weekly']:.1f}", "with_calendar": f"{r['with_calendar']:.1f}"}
        for ch, r in vif_var.iterrows()
    ]

    return Section(
        id="explore_correlations",
        title="Correlations, lags and collinearity",
        subtitle="Same-day vs lagged cross-correlations, Pearson/Spearman toggles, and Variance Inflation Factors (VIF).",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Top same-day r (cost, sales)", value=f"{top2[0]} {corr_cost_pearson[top2[0]]:.2f}", delta=f"{top2[1]} {corr_cost_pearson[top2[1]]:.2f} · both bill on demand" if len(top2) > 1 else ""),
                    KpiItem(label=f"{cat or 'Catalog'}: same day → best lag", value=f"{cat_r0:+.2f} → {cat_best_r:+.2f}", delta=f"peaks +{cat_best} days after send-out", status="success"),
                    KpiItem(label="Digital cost VIF", value=f"{min(vif_digital.values()):.2f} – {max(vif_digital.values()):.2f}", delta="Moderate collinearity (< 5)", status="success"),
                    KpiItem(label="Digital spends move together", value=f"r {np.nanmin(off):.2f}–{np.nanmax(off):.2f}", delta=f"highest VIF: {', '.join(f'{c} {vif_digital[c]:.2f}' for c in vif_sorted[:2])}"),
                ]
            ),
            CalloutBlock(
                tone="info",
                title=f"Same-day correlation says the {cat or 'catalog'} does not matter — that is wrong",
                body=(
                    f"The {cat or 'catalog'} cost is booked on the send-out day while its effect comes days later: same-day "
                    f"r = **{cat_r0:+.2f}**, but r = **{cat_best_r:+.2f}** at **+{cat_best} days** (a Thursday send-out hits the "
                    "following Sunday). A standard lag-0 adstock is the wrong shape; shift the spend to its in-home date. "
                    f"Meanwhile the two highest same-day correlations ({', '.join(top2)}) belong to the channels whose cost is "
                    "mechanically tied to demand (commission and cost-per-click)."
                ),
            ),
            ChartBlock(
                id="corr_cost_vs_channel_sales",
                title="Same-Day Correlation with Total Sales: Channel Spend vs Channel Sales",
                subtitle="Toggle between Pearson (linear) and Spearman (rank) correlation.",
                figure=fig_cost_pearson,
                alt_figures={"Pearson": fig_cost_pearson, "Spearman": fig_cost_spearman},
                caption=f"Strongest same-day spend correlation: {', '.join(f'{c} ({corr_cost_pearson[c]:.2f})' for c in top2)}; {cat or 'catalog'} {cat_r0:+.2f}.",
            ),
            ChartBlock(
                id="lag_correlation",
                title="Distributed Lag Cross-Correlation Profile (Lags 0 to +21 Days)",
                subtitle="Correlation between paid channel spend on day t and total sales on day t + k.",
                figure=fig_lag,
                caption=f"Digital channels peak at lag 0 and decay slowly (spend itself is smooth); {cat or 'the catalog'} peaks at +{cat_best} days (r = {cat_best_r:.2f}).",
            ),
            ChartBlock(
                id="corr_matrix_costs",
                title="Cross-Channel Spend & Total Sales Correlation Matrix",
                subtitle="Pairwise co-movement across all 7 paid levers and daily total sales (Pearson / Spearman toggle).",
                figure=fig_mat_pearson,
                alt_figures={"Pearson": fig_mat_pearson, "Spearman": fig_mat_spearman},
            ),
            ChartBlock(
                id="corr_channel_sales",
                title="Channel Sales Correlation with Total Sales vs Rest-of-Business",
                subtitle="Measuring how strongly each channel's revenue co-moves with the other 11 channels combined.",
                figure=fig_ch_sales_pearson,
                alt_figures={"Pearson": fig_ch_sales_pearson, "Spearman": fig_ch_sales_spearman},
            ),
            ChartBlock(
                id="corr_robustness",
                title="How robust are the correlations?",
                subtitle="Daily same-day vs weekly totals vs daily with trend, month and weekday removed: much of the raw link is shared seasonality.",
                figure=fig_robust,
            ),
            TableBlock(
                id="corr_vif_table",
                title="Paid Channel Correlation & Collinearity (VIF) Summary",
                columns=[
                    TableColumn(key="channel", label="Paid Channel"),
                    TableColumn(key="pearson_lag0", label="Pearson (Lag 0)", align="right"),
                    TableColumn(key="spearman_lag0", label="Spearman (Lag 0)", align="right"),
                    TableColumn(key="pearson_lag3", label="Pearson (Lag +3d)", align="right"),
                    TableColumn(key="pearson_lag7", label="Pearson (Lag +7d)", align="right"),
                    TableColumn(key="peak_lag", label="Peak Forward Lag", align="right"),
                    TableColumn(key="digital_vif", label="Raw cost VIF", align="right"),
                ],
                rows=table_rows,
            ),
            TableBlock(
                id="corr_robustness_table",
                title="Correlation with total sales: three views",
                columns=[
                    TableColumn(key="channel", label="Channel"),
                    TableColumn(key="daily", label="Daily, same day", align="right"),
                    TableColumn(key="weekly", label="Weekly totals", align="right"),
                    TableColumn(key="deseasonalised", label="Trend & calendar removed", align="right"),
                ],
                rows=rob_rows,
            ),
            TableBlock(
                id="corr_vif_variants_table",
                title="Multicollinearity of log(1 + spend)",
                subtitle="VIFs stay moderate in every variant; the harder problem is that budgets follow the season and two costs follow demand.",
                columns=[
                    TableColumn(key="channel", label="Channel"),
                    TableColumn(key="daily", label="Daily", align="right"),
                    TableColumn(key="weekly", label="Weekly", align="right"),
                    TableColumn(key="with_calendar", label="Daily + trend & calendar", align="right"),
                ],
                rows=vif_rows,
            ),
        ],
    )
