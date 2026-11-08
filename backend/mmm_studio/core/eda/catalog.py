"""Catalog deep-dive: send-outs, event study, adstock kernel decay, cross-channel halo, circulation lift, and matched-Thursday experiment."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, line_figure, small_multiples_figure
from mmm_studio.charts.theme import BASELINE_FILL, COPPER, NAVY, SLATE, TEAL, TEXT_PRIMARY, channel_color
from mmm_studio.core.eda.stats import (
    catalog_decay,
    catalog_event_study,
    catalog_halo_0_6,
    per_sendout_lifts,
)
from mmm_studio.core.features import complete_years
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

    peak_tau = max((t for t in taus if t >= 0), key=lambda t: tot_mean[taus.index(t)])
    fig_event_study = line_figure(
        x=taus,
        series=[
            {"name": "Total Sales 90% CI Upper", "y": tot_hi, "color": "rgba(27,49,91,0)", "showlegend": False},
            {
                "name": "Total Sales 90% CI",
                "y": tot_lo,
                "color": "rgba(27,49,91,0)",
                "fill": "tonexty",
                "fillcolor": "rgba(27,49,91,0.14)",
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
            {"x": peak_tau, "color": COPPER, "dash": "dot", "label": f"Peak Response (τ=+{peak_tau}d)"},
        ],
        height=390,
    )

    # 3. How long does the catalog keep selling? Catalog-attributed sales by day since send-out and
    #    the cumulative share of their excess over the tail level
    dec = catalog_decay(wide_df, "sales_catalog") if "sales_catalog" in wide_df else None
    if dec is not None:
        days = [int(k) for k in dec["curve"].index]
        fig_decay = small_multiples_figure(
            panels=[
                {
                    "title": "Mean catalog-attributed sales by day since send-out (k€)",
                    "x": days,
                    "xaxis_title": "Days since send-out",
                    "traces": [
                        {"name": "Catalog sales", "y": (dec["curve"] / 1e3).round(1).tolist(), "kind": "bar", "color": COPPER},
                        {"name": "Tail level (days ≥ 21)", "y": [round(dec["floor"] / 1e3, 1)] * len(days), "color": SLATE, "line": {"color": SLATE, "dash": "dash", "width": 1.4}},
                    ],
                },
                {
                    "title": f"Cumulative share of excess sales: 50 % by day {dec['half_day']}, 90 % by day {dec['p90_day']}",
                    "x": days,
                    "xaxis_title": "Days since send-out",
                    "traces": [{"name": "Cumulative share (%)", "y": (100 * dec["cum_share"]).round(1).tolist(), "color": NAVY, "mode": "lines+markers"}],
                },
            ],
            ncols=2,
            height_per_row=320,
        )
    else:
        fig_decay = small_multiples_figure(panels=[{"title": "No catalog sales", "x": [], "y": []}], ncols=1)

    # 4. Cross-channel Halo Uplift (7 days post send-out vs other days)
    uplifts_7d = compute_halo_uplift_7d(wide_df, config)
    cat_name = config.print_event_channels[0] if config.print_event_channels else "Catalog"
    non_cat_channels = [c for c in config.active_channels if c != cat_name]
    non_cat_sorted = sorted(non_cat_channels, key=lambda c: uplifts_7d.get(c, 0.0))
    halo_cats = non_cat_sorted + ["Total sales"]
    halo_vals = [uplifts_7d.get(c, 0.0) for c in non_cat_sorted] + [uplifts_7d["total_sales"]]

    fig_halo = bar_figure(
        categories=halo_cats,
        series=[
            {
                "name": "7-Day Post-Catalog Uplift vs Other Days (%)",
                "values": halo_vals,
                "colors": [COPPER if c == "Total sales" else channel_color(c) for c in halo_cats],
                "text": [f"{v:+.0f}%" for v in [uplifts_7d.get(c, 0.0) for c in non_cat_sorted] + [uplifts_7d["total_sales"]]],
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

    # Lift of the 7 days from the send-out vs the 7 days before, against circulation
    per_send = per_sendout_lifts(wide_df, "cost_catalog", "impr_catalog")
    lift_circ_r = float(per_send["circulation"].corr(per_send["lift"])) if len(per_send) > 3 else float("nan")
    lift_by_year = per_send.groupby(per_send.index.year)["lift"].median() if len(per_send) else pd.Series(dtype=float)
    fig_circ = small_multiples_figure(
        panels=[
            {
                "title": f"{yr}",
                "xaxis_title": "Circulation (million copies)",
                "yaxis_title": "7-day lift vs week before (%)",
                "traces": [
                    {
                        "type": "scatter",
                        "mode": "markers",
                        "name": str(yr),
                        "x": (per_send.loc[per_send.index.year == yr, "circulation"] / 1e6).round(2).tolist(),
                        "y": (100 * per_send.loc[per_send.index.year == yr, "lift"]).round(1).tolist(),
                        "marker": {"color": [SLATE, COPPER][i % 2], "size": 9},
                    }
                ],
            }
            for i, yr in enumerate(sorted(set(per_send.index.year)))
        ]
        if len(per_send)
        else [{"title": "No send-outs", "x": [], "y": []}],
        ncols=2,
        height_per_row=300,
    )
    study = catalog_event_study(wide_df)
    decay = catalog_decay(wide_df, f"sales_{config.slug_map().get(cat_name, 'catalog')}") if "sales_catalog" in wide_df or config.print_event_channels else None
    halo06, total06, cat06 = catalog_halo_0_6(wide_df, config)
    lag_r = {k: float(wide_df["total_sales"].corr(wide_df["cost_catalog"].shift(k))) for k in range(0, 22)}
    best_lag = max(lag_r, key=lag_r.get)
    halo_top = sorted(((c, v) for c, v in uplifts_7d.items() if c not in ("total_sales", cat_name) and float(wide_df[f"sales_{config.slug_map()[c]}"].sum()) > 0.01 * float(wide_df["total_sales"].sum())), key=lambda t: -t[1])[:5]
    halo_txt = ", ".join(f"{c} {v:+.0f}%" for c, v in halo_top)
    peak_lift = float(study.loc[best_lag, "lift"]) if best_lag in study.index else float("nan")
    cat_by_k = (
        wide_df["sales_catalog"].groupby(wide_df["days_since_catalog"]).mean()
        if "sales_catalog" in wide_df and "days_since_catalog" in wide_df
        else pd.Series(dtype=float)
    )
    study_rows = [
        {
            "k": int(k),
            "mean_sales": fmt_eur(float(r["mean"])),
            "mean_catalog": fmt_eur(float(cat_by_k.get(k, float("nan")))) if k in cat_by_k.index else "–",
            "lift": f"{100 * float(r['lift']):+.0f}%",
            "n": int(r["n"]),
        }
        for k, r in study.loc[0:14].iterrows()
    ]
    send_costs = wide_df.loc[sendout_mask, "cost_catalog"]
    send_impr = wide_df.loc[sendout_mask, "impr_catalog"] if "impr_catalog" in wide_df else pd.Series(dtype=float)

    send_year = sendout_df.assign(cpc=sendout_df["cost_catalog"] / sendout_df["impr_catalog"]).groupby(sendout_df.index.year)
    year_rows = [
        {
            "year": int(yr),
            "send_outs": len(g),
            "cost": f"€{g['cost_catalog'].mean():,.0f}",
            "circulation": f"{g['impr_catalog'].mean():,.0f}",
            "cost_per_copy": f"€{g['cpc'].mean():.3f}",
        }
        for yr, g in send_year
    ] if "impr_catalog" in sendout_df else []

    year_trend = None
    if len(year_rows) >= 2:
        g0, g1 = send_year.get_group(year_rows[0]["year"]), send_year.get_group(year_rows[-1]["year"])
        c0, c1 = g0["impr_catalog"].mean(), g1["impr_catalog"].mean()
        p0, p1 = g0["cpc"].mean(), g1["cpc"].mean()
        year_trend = (
            f"{year_rows[0]['year']} → {year_rows[-1]['year']}: circulation {100 * (c1 / c0 - 1):+.0f}%, "
            f"cost per copy {100 * (p1 / p0 - 1):+.0f}%."
        )

    y_sales = wide_df["total_sales"]
    top25 = y_sales.nlargest(25)
    near_top = sum(any(0 <= (d - s).days <= 7 for s in sendout_df.index) for d in top25.index)
    # Same calendar month in two complete years, one without and one with a send-out: the largest such contrast
    full = complete_years(wide_df)
    month_mean = full["total_sales"].groupby([full.index.year, full.index.month]).mean()
    month_sends = sendout_df.groupby([sendout_df.index.year, sendout_df.index.month]).size()
    contrast = None
    for (y0, m), v0 in month_mean.items():
        if month_sends.get((y0, m), 0):
            continue
        for (y1, m1), v1 in month_mean.items():
            if m1 == m and y1 != y0 and month_sends.get((y1, m1), 0) and (contrast is None or v1 / v0 > contrast[4] / contrast[2]):
                contrast = (m, y0, v0, y1, v1, int(month_sends[(y1, m1)]))
    evidence = [f"**{near_top} of the 25** highest-sales days fall within 7 days after a send-out."]
    if contrast:
        m, y0, v0, y1, v1, n1 = contrast
        month_name = pd.Timestamp(year=y0, month=m, day=1).strftime("%B")
        evidence.append(
            f"{month_name} {y0} had no send-out and averaged **{fmt_eur(v0)}/day**; {month_name} {y1} had {n1} and averaged "
            f"**{fmt_eur(v1)}/day** ({v1 / v0:.1f}×)."
        )

    # Natural experiment: the month without a send-out against the same month a year later
    gap_block = None
    if contrast:
        m, y0, v0, y1, v1, _ = contrast
        month_name = pd.Timestamp(year=y0, month=m, day=1).strftime("%B")
        gap_start = max((d for d in sendout_df.index if d < pd.Timestamp(year=y0, month=m, day=1)), default=None)
        gap_end = min((d for d in sendout_df.index if d > pd.Timestamp(year=y0, month=m, day=1) + pd.offsets.MonthEnd(0)), default=None)
        panels, ymax = [], 0.0
        for yr in (y0, y1):
            m_start = pd.Timestamp(year=yr, month=m, day=1)
            w_start, w_end = m_start - pd.offsets.MonthBegin(1), m_start + pd.offsets.MonthEnd(2)
            w = y_sales.loc[w_start:w_end]
            ymax = max(ymax, float(w.max()))
            sends = [d for d in sendout_df.index if w_start <= d <= w_end]
            m_mean = float(y_sales.loc[m_start : m_start + pd.offsets.MonthEnd(0)].mean())
            panels.append(
                {
                    "title": f"{yr}: {w_start.day} {w_start:%b} – {w_end.day} {w_end:%b} · {month_name} mean {fmt_eur(m_mean)}/day",
                    "traces": [
                        {"name": "Total sales", "x": [d.strftime("%Y-%m-%d") for d in w.index], "y": w.round(0).tolist(), "color": TEAL},
                        {
                            "name": f"{month_name} mean",
                            "x": [m_start.strftime("%Y-%m-%d"), (m_start + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")],
                            "y": [round(m_mean), round(m_mean)],
                            "line": {"color": TEXT_PRIMARY, "width": 2.5},
                        },
                        {
                            "name": "Catalog send-out",
                            "x": [d.strftime("%Y-%m-%d") for d in sends],
                            "y": [0] * len(sends),
                            "mode": "markers",
                            "marker": {"symbol": "line-ns-open", "size": 14, "color": COPPER, "line": {"width": 2.5, "color": COPPER}},
                        },
                    ],
                }
            )
        fig_gap = small_multiples_figure(panels=panels, ncols=1, height_per_row=260)
        for axis in ("yaxis", "yaxis2"):
            fig_gap["layout"][axis]["range"] = [0, ymax * 1.08]
        if gap_start is not None and gap_end is not None:
            gap_days = (gap_end - gap_start).days
            fig_gap["layout"]["shapes"] = [
                {
                    "type": "rect", "xref": "x", "yref": "y domain", "x0": gap_start.strftime("%Y-%m-%d"),
                    "x1": gap_end.strftime("%Y-%m-%d"), "y0": 0, "y1": 1, "fillcolor": BASELINE_FILL, "opacity": 0.5,
                    "line": {"width": 0}, "layer": "below",
                }
            ]
            fig_gap["layout"]["annotations"].append(
                {
                    "x": (gap_start + (gap_end - gap_start) / 2).strftime("%Y-%m-%d"), "y": 0.92, "xref": "x", "yref": "y domain",
                    "text": f"{gap_days} days without a catalog", "showarrow": False, "font": {"size": 11, "color": SLATE},
                }
            )
        lowest = v0 <= float(month_mean.min()) + 1e-6
        gap_block = ChartBlock(
            id="catalog_natural_experiment",
            title=f"Without a catalog, {month_name} {y0} fell to {fmt_eur(v0)}/day" + (" — the lowest month of the period" if lowest else ""),
            subtitle=f"Daily total sales around {month_name} in {y0} (no send-out) and {y1}; the dark line marks the {month_name} mean.",
            figure=fig_gap,
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
        title="Catalog deep-dive: delayed effect and halo",
        subtitle=f"{n_sendouts} send-outs: the delay between booking and effect, how long it lasts, and the lift it gives other channels.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Catalog Send-outs", value=f"{n_sendouts} editions", delta=f"{n_thu} Thu, {n_wed} Wed, {n_fri} Fri"),
                    KpiItem(label="Peak response", value=f"+{best_lag} days", delta=f"r = {lag_r[best_lag]:+.2f} vs {lag_r[0]:+.2f} on the send-out day; weekday-adjusted lift {100 * peak_lift:+.0f}%", status="success"),
                    KpiItem(label="Total sales, 7 days after", value=fmt_pct(uplifts_7d["total_sales"], signed=True), delta=f"vs other days; days 0–6 vs ≥ 14: {100 * total06:+.0f}%", status="success"),
                    KpiItem(label="Effect duration", value=f"50% by day {decay['half_day']}" if decay else "—", delta=f"90% by day {decay['p90_day']} (catalog-attributed excess)" if decay else ""),
                ]
            ),
            CalloutBlock(tone="info", title="Sales peaks follow the catalog", body=" ".join(evidence)),
            CalloutBlock(
                tone="success",
                title=f"The catalog creates a halo: total sales {uplifts_7d['total_sales']:+.1f}% in the week after a send-out",
                body=(
                    f"Customers browse the catalog and then buy via search or by typing the URL, so sales credited to other channels "
                    f"rise too: **{halo_txt}**. Catalog-attributed sales in days 0–6 are {100 * cat06:+.0f}% above their tail level. "
                    "Last-touch attribution credits those channels, so the catalog's attributed ROAS **understates** it — the reason "
                    "to model **total** sales."
                ),
            ),
            ChartBlock(
                id="catalog_sendouts",
                title=f"Timeline of All {n_sendouts} Catalog Send-Outs: Spend (€M) & Print Circulation (M Copies)",
                subtitle=(
                    f"{n_sendouts} editions ({n_thu} on a Thursday); cost {fmt_eur(float(send_costs.min()))}–{fmt_eur(float(send_costs.max()))} "
                    f"(Ø {fmt_eur(float(send_costs.mean()))}), circulation Ø {float(send_impr.mean()) / 1e6:.2f}M copies."
                    if n_sendouts
                    else "No send-outs in the data."
                ),
                figure=fig_sendouts,
            ),
            ChartBlock(
                id="catalog_event_study",
                title="Event-Study Impulse Response Around Catalog Send-Outs (τ = -7 .. +20 Days)",
                subtitle=f"Mean daily sales (k€) around all {n_sendouts} send-outs, with a 90 % band; τ = 0 is the send-out day.",
                figure=fig_event_study,
            ),
            ChartBlock(
                id="catalog_decay",
                title="How long does the catalog keep selling?",
                subtitle=(
                    f"Catalog-attributed sales reach 50 % of their excess by day {decay['half_day']} and 90 % by day {decay['p90_day']}: "
                    "a delayed peak, then a tail that sets the adstock prior."
                    if decay
                    else "Empirical response vs illustrative adstock kernels."
                ),
                figure=fig_decay,
            ),
            ChartBlock(
                id="catalog_halo",
                title="Cross-Channel Halo Uplift (7 Days Post Send-Out vs Non-Catalog Baseline)",
                subtitle="Mean daily sales in the 7 days after a send-out vs all other days.",
                figure=fig_halo,
            ),
            *([gap_block] if gap_block else []),
            ChartBlock(
                id="catalog_matched_thursdays",
                title="Matched-Thursday Natural Experiment: Thursdays With vs Without Catalog Send-Out",
                subtitle="Thursdays with a send-out vs Thursdays without: the same weekdays isolate the catalog from the Sunday pattern.",
                figure=fig_nat_exp,
            ),
            ChartBlock(
                id="catalog_lift_vs_circulation",
                title="Does a bigger catalog lift more?",
                subtitle=(
                    f"Median 7-day lift per send-out {100 * float(per_send['lift'].median()):+.0f}% (range {100 * float(per_send['lift'].min()):+.0f}% to "
                    f"{100 * float(per_send['lift'].max()):+.0f}%); median by year "
                    + ", ".join(f"{yr} {100 * v:+.0f}%" for yr, v in lift_by_year.items())
                    + f"; lift vs circulation r = {lift_circ_r:.2f} — content and season matter more than size."
                    if len(per_send)
                    else "No send-outs."
                ),
                figure=fig_circ,
            ),
            TableBlock(
                id="catalog_editions_table",
                title="All catalog send-outs",
                columns=[
                    TableColumn(key="date", label="Send-out Date"),
                    TableColumn(key="spend", label="Edition Cost", align="right"),
                    TableColumn(key="circulation", label="Circulation", align="right"),
                    TableColumn(key="cost_per_copy", label="Cost / Copy", align="right"),
                    TableColumn(key="catalog_sales_7d", label="7d Catalog Sales", align="right"),
                    TableColumn(key="total_sales_7d", label="7d Total Sales", align="right"),
                ],
                rows=ed_rows,
            ),
            *(
                [
                    TableBlock(
                        id="catalog_by_year",
                        title="Average per send-out by year",
                        subtitle=year_trend,
                        columns=[
                            TableColumn(key="year", label="Year"),
                            TableColumn(key="send_outs", label="Send-outs", align="right"),
                            TableColumn(key="cost", label="Cost", align="right"),
                            TableColumn(key="circulation", label="Circulation", align="right"),
                            TableColumn(key="cost_per_copy", label="Cost per copy", align="right"),
                        ],
                        rows=year_rows,
                    )
                ]
                if year_rows
                else []
            ),
            TableBlock(
                id="catalog_event_table",
                title="Event study: total sales by days since send-out",
                subtitle="Weekday-adjusted lift vs days ≥ 14 after a send-out (same year and weekday).",
                columns=[
                    TableColumn(key="k", label="Days since send-out", align="right"),
                    TableColumn(key="mean_sales", label="Mean total sales", align="right"),
                    TableColumn(key="mean_catalog", label="Mean catalog sales", align="right"),
                    TableColumn(key="lift", label="Weekday-adjusted lift", align="right"),
                    TableColumn(key="n", label="Send-outs", align="right"),
                ],
                rows=study_rows,
            ),
        ],
    )
