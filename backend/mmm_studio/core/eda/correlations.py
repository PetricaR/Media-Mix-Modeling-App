"""Same-day and lagged correlations, cross-channel VIF, and Pearson/Spearman toggles."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, heatmap_figure, line_figure
from mmm_studio.charts.theme import COPPER, NAVY, SLATE, TEAL, channel_color
from mmm_studio.core.eda.stats import correlation_robustness, vif_variants
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
    from mmm_studio.core.meridian.spec import demand_following_slugs

    slug_map = config.slug_map()
    all_channels = config.active_channels
    paid_channels = config.paid_channels
    digital_channels = config.digital_paid_channels
    y = wide_df["total_sales"]
    # Channel-sales views need sales per channel (a wide upload with one target column has none)
    has_sales = config.has_channel_sales
    tied_to_demand = set(demand_following_slugs(config, wide_df)) | set(config.commission_channels)

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

    # Only drawn with sales per channel: without them every sales_* column is constant zero and r is undefined
    if has_sales:
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
        series = [{"name": f"r(Channel Cost, Total Sales) [{method.title()}]", "values": r_cost_tot, "color": COPPER}]
        if has_sales:
            r_sales_tot = [round(float(wide_df[f"sales_{slug_map[c]}"].corr(y, method=method)), 2) for c in ch_order]
            series.append({"name": f"r(Channel Sales, Total Sales) [{method.title()}]", "values": r_sales_tot, "color": NAVY})
        return bar_figure(
            categories=ch_order,
            series=series,
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

    # Digital raw cost VIF on the full modelling panel
    dig_cost_df = pd.DataFrame({ch: wide_df[f"cost_{slug_map[ch]}"] for ch in digital_channels})
    vif_digital = compute_vif_table(dig_cost_df)
    vif_var = vif_variants(wide_df, config)

    # 4. How robust are the correlations? Same day, weekly totals, trend & calendar removed
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
    cat = config.print_event_channels[0] if config.print_event_channels else None
    cat_peak_k, cat_peak_r = 0, float("nan")
    for ch in paid_channels:
        c_ser = wide_df[f"cost_{slug_map[ch]}"]
        r_lags = [round(float(c_ser.corr(y.shift(-k))), 3) for k in lags]
        if ch == cat:
            cat_peak_k = int(np.nanargmax(r_lags))
            cat_peak_r = r_lags[cat_peak_k]
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
        vlines=[{"x": cat_peak_k, "color": COPPER, "dash": "dot", "label": f"{cat} peak (+{cat_peak_k}d, r={cat_peak_r:.2f})"}] if cat else None,
        height=400,
    )

    # 6. Attributed sales of all channels, ordered by hierarchical clustering (lower triangle), and spend × channel sales
    from scipy.cluster.hierarchy import leaves_list, linkage

    sales_df = pd.DataFrame({ch: wide_df[f"sales_{slug_map[ch]}"] for ch in all_channels})
    if not has_sales:
        sales_df = sales_df.iloc[:, :0]
    sales_corr = sales_df.corr()
    n_ch = len(sales_corr)
    if n_ch > 2 and np.isfinite(sales_corr.to_numpy()).all():
        sales_order = list(sales_corr.index[leaves_list(linkage(1 - sales_corr.to_numpy()[np.triu_indices(n_ch, 1)], "average"))])
        sales_corr = sales_corr.loc[sales_order, sales_order]
    sales_vals = sales_corr.to_numpy()
    off_mask = ~np.eye(n_ch, dtype=bool)
    sales_mean_r = float(sales_vals[off_mask].mean()) if n_ch > 1 else float("nan")
    off_vals = np.where(off_mask, sales_vals, np.nan)
    pair_i, pair_j = np.unravel_index(np.nanargmax(off_vals), sales_vals.shape) if np.isfinite(off_vals).any() else (0, 0)
    lower = sales_vals.copy()
    lower[np.triu_indices(n_ch, 1)] = np.nan
    fig_sales_matrix = heatmap_figure(
        x=list(sales_corr.columns), y=list(sales_corr.index), z=lower, colorscale="RdBu_r", zmin=-1, zmax=1, height=560
    )
    fig_sales_matrix["layout"]["yaxis"] = {**fig_sales_matrix["layout"].get("yaxis", {}), "autorange": "reversed"}

    cross = pd.DataFrame(
        {t_ch: [float(wide_df[f"cost_{slug_map[c]}"].corr(sales_df[t_ch])) for c in paid_channels] for t_ch in sales_corr.columns},
        index=[f"{c} cost" for c in paid_channels],
    )
    if cat:
        cat_shifted = wide_df[f"cost_{slug_map[cat]}"].shift(cat_peak_k)
        cross.loc[f"{cat} cost (lag {cat_peak_k})"] = [float(cat_shifted.corr(sales_df[t_ch])) for t_ch in sales_corr.columns]
    fig_cross = heatmap_figure(
        x=list(cross.columns), y=list(cross.index), z=cross.to_numpy(), colorscale="RdBu_r", zmin=-1, zmax=1, height=420
    )
    fig_cross["layout"]["yaxis"] = {**fig_cross["layout"].get("yaxis", {}), "autorange": "reversed"}
    spill_vals = [(r, c, cross.loc[r, c]) for r in cross.index for c in cross.columns if not r.startswith(f"{c} cost")]
    spill_top = max(spill_vals, key=lambda v: v[2]) if spill_vals else None

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
    top2_tied = len(top2) > 1 and all(slug_map[c] in tied_to_demand for c in top2)
    vif_max = max(vif_digital.values()) if vif_digital else float("nan")
    vif_verdict = "Moderate collinearity (< 5)" if vif_max < 5 else ("High collinearity (5–10)" if vif_max < 10 else "Severe collinearity (≥ 10)")
    vif_sorted = sorted(vif_digital, key=vif_digital.get, reverse=True)
    send_day_text = ""
    if cat and "catalog_sendout" in wide_df and int(wide_df["catalog_sendout"].sum()):
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        send_d = int(pd.Series(wide_df.index[wide_df["catalog_sendout"] == 1].dayofweek).mode().iloc[0])
        send_day_text = f" (a {day_names[send_d]} send-out hits the following {day_names[(send_d + cat_peak_k) % 7]})"
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

    top_delta = ""
    if len(top2) > 1:
        top_delta = f"{top2[1]} {corr_cost_pearson[top2[1]]:.2f}" + (" · both bill on demand" if top2_tied else "")
    kpis = [
        KpiItem(label="Top same-day r (cost, sales)", value=f"{top2[0]} {corr_cost_pearson[top2[0]]:.2f}", delta=top_delta),
    ]
    if cat:
        kpis.append(KpiItem(label=f"{cat}: same day → best lag", value=f"{cat_r0:+.2f} → {cat_best_r:+.2f}", delta=f"peaks +{cat_best} days after send-out", status="success"))
    if vif_digital:
        kpis.append(
            KpiItem(
                label="Digital cost VIF",
                value=f"{min(vif_digital.values()):.2f} – {vif_max:.2f}",
                delta=vif_verdict,
                status="success" if vif_max < 5 else "warn",
            )
        )
    if len(vif_digital) > 1:
        kpis.append(KpiItem(label="Digital spends move together", value=f"r {np.nanmin(off):.2f}–{np.nanmax(off):.2f}", delta=f"highest VIF: {', '.join(f'{c} {vif_digital[c]:.2f}' for c in vif_sorted[:2])}"))
    lead: list = [KpisBlock(items=kpis)]
    if cat:
        lead.append(
            CalloutBlock(
                tone="info",
                title=f"Same-day correlation says the {cat} does not matter — that is wrong",
                body=(
                    f"The {cat} cost is booked on the send-out day while its effect comes days later: same-day "
                    f"r = **{cat_r0:+.2f}**, but r = **{cat_best_r:+.2f}** at **+{cat_best} days**{send_day_text}. "
                    "A standard lag-0 adstock is the wrong shape; shift the spend to its in-home date. "
                    + (
                        f"Meanwhile the two highest same-day correlations ({', '.join(top2)}) belong to the channels whose cost is "
                        "mechanically tied to demand (commission and cost-per-click)."
                        if top2_tied
                        else ""
                    )
                ).strip(),
            )
        )
    elif top2_tied:
        lead.append(
            CalloutBlock(
                tone="info",
                title="The strongest same-day correlations are not evidence of impact",
                body=(
                    f"The two highest same-day correlations ({', '.join(top2)}) belong to channels whose cost is mechanically "
                    "tied to demand (commission or cost-per-click): their spend follows sales as much as it drives them."
                ),
            )
        )
    peak_lags = {ch: int(np.nanargmax([float(wide_df[f"cost_{slug_map[ch]}"].corr(y.shift(-k))) for k in lags])) for ch in paid_channels}
    lag_caption = (
        f"Digital channels peak at lag 0 and decay slowly (spend itself is smooth); {cat} peaks at +{cat_best} days (r = {cat_best_r:.2f})."
        if cat
        else "Peak lag per channel: " + ", ".join(f"{ch} +{k}d" for ch, k in peak_lags.items()) + "."
    )
    blocks = [
        *lead,
        ChartBlock(
            id="corr_cost_and_sales_vs_total",
            title="Same-Day Correlation with Total Sales: Channel Spend vs Channel Sales" if has_sales else "Same-Day Correlation of Channel Spend with Total Sales",
            subtitle="Toggle between Pearson (linear) and Spearman (rank) correlation.",
            figure=fig_cost_pearson,
            alt_figures={"Pearson": fig_cost_pearson, "Spearman": fig_cost_spearman},
            caption=(
                f"Strongest same-day spend correlation: {', '.join(f'{c} ({corr_cost_pearson[c]:.2f})' for c in top2)}"
                + (f"; {cat} {cat_r0:+.2f}." if cat else ".")
            ),
        ),
        ChartBlock(
            id="lag_correlation",
            title="Distributed Lag Cross-Correlation Profile (Lags 0 to +21 Days)",
            subtitle="Correlation between paid channel spend on day t and total sales on day t + k.",
            figure=fig_lag,
            caption=lag_caption,
        ),
        ChartBlock(
            id="corr_matrix_costs",
            title="Cross-Channel Spend & Total Sales Correlation Matrix",
            subtitle=f"Pairwise co-movement across all {len(paid_channels)} paid levers and daily total sales (Pearson / Spearman toggle).",
            figure=fig_mat_pearson,
            alt_figures={"Pearson": fig_mat_pearson, "Spearman": fig_mat_spearman},
        ),
    ]
    if has_sales:
        blocks += [
        ChartBlock(
                id="corr_channel_sales",
                title=(
                    f"Attributed sales move together: mean r = {sales_mean_r:.2f} across channel pairs, up to "
                    f"{sales_vals[pair_i, pair_j]:.2f} ({sales_corr.index[pair_i]} × {sales_corr.columns[pair_j]})"
                ),
                subtitle="Pearson r of daily attributed sales, ordered by hierarchical clustering: one demand signal, many channels.",
                figure=fig_sales_matrix,
            ),
            ChartBlock(
                id="corr_cost_vs_other_channel_sales",
                title="Spend in one channel correlates with sales credited to others — spill-over and shared demand",
                subtitle=(
                    f"Pearson r of each channel's daily cost with every channel's attributed sales; strongest cross-channel link: "
                    f"{spill_top[0]} × {spill_top[1]} sales (r = {spill_top[2]:.2f})."
                    if spill_top
                    else "Pearson r of each channel's daily cost with every channel's attributed sales."
                ),
                figure=fig_cross,
            ),
            ChartBlock(
                id="corr_channel_vs_rest",
                title="Channel Sales Correlation with Total Sales vs Rest-of-Business",
                subtitle=f"Measuring how strongly each channel's revenue co-moves with the other {len(all_channels) - 1} channels combined.",
                figure=fig_ch_sales_pearson,
                alt_figures={"Pearson": fig_ch_sales_pearson, "Spearman": fig_ch_sales_spearman},
            ),
        ]
    blocks += [
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
                subtitle=(
                    "VIFs stay moderate in every variant; the harder problem is that budgets follow the season and two costs follow demand."
                    if cat and top2_tied
                    else f"Largest VIF across the variants: {float(np.nanmax(vif_var.to_numpy(dtype=float))):.1f}."
                    if len(vif_var)
                    else None
                ),
                columns=[
                    TableColumn(key="channel", label="Channel"),
                    TableColumn(key="daily", label="Daily", align="right"),
                    TableColumn(key="weekly", label="Weekly", align="right"),
                    TableColumn(key="with_calendar", label="Daily + trend & calendar", align="right"),
                ],
                rows=vif_rows,
            ),
    ]
    return Section(
        id="explore_correlations",
        title="Correlations, lags and collinearity",
        subtitle="Same-day vs lagged cross-correlations, Pearson/Spearman toggles, and Variance Inflation Factors (VIF).",
        blocks=blocks,
    )
