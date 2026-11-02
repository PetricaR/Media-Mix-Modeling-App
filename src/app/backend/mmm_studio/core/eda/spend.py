"""Paid marketing spend structure, cadence, YoY spend growth, and raw dose-response curves (01 & 01b)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, heatmap_figure, line_figure, small_multiples_figure
from mmm_studio.charts.theme import COPPER, NAVY, TEAL, channel_color
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


def build_explore_spend_section(
    wide_df: pd.DataFrame,
    config: DataRoleConfig,
) -> Section:
    """Build the `explore_spend` section covering the 7 paid media channels."""
    slug_map = config.slug_map()
    paid_channels = config.paid_channels
    total_cost_sum = float(wide_df["total_cost"].sum())
    paid_sales_sum = float(wide_df["paid_sales"].sum())

    full = complete_years(wide_df)
    years = sorted(set(pd.DatetimeIndex(full.index).year))
    y_prev, y_last = (years[-2], years[-1]) if len(years) >= 2 else (years[0], years[0])
    w23 = full[full.index.year == y_prev]
    w24 = full[full.index.year == y_last]
    yoy_spend_pct = 100.0 * (float(w24["total_cost"].sum()) / max(float(w23["total_cost"].sum()), 1.0) - 1.0)
    yoy_sales_pct = 100.0 * (float(w24["total_sales"].sum()) / max(float(w23["total_sales"].sum()), 1.0) - 1.0)
    cost_ratio = {yr: 100.0 * float(w["total_cost"].sum()) / max(float(w["total_sales"].sum()), 1.0) for yr, w in ((y_prev, w23), (y_last, w24))}

    spend_rows = []
    year_rows = []
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

        s23 = float(w23[s_col].sum()) if len(w23) else 0.0
        s24 = float(w24[s_col].sum()) if len(w24) else 0.0
        active = c_ser[c_ser > 0]
        year_rows.append(
            {
                "channel": ch,
                "spend_prev": f"€{c23:,.0f}",
                "spend_last": f"€{c24:,.0f}",
                "yoy_spend": fmt_pct(yoy_c, signed=True),
                "yoy_sales": fmt_pct(100.0 * (s24 / s23 - 1.0), signed=True) if s23 > 0 else "–",
                "cv_active": f"{float(active.std() / active.mean()):.2f}" if len(active) > 1 else "–",
                "_c_sum": c_sum,
            }
        )
        spend_rows.append(
            {
                "channel": ch,
                "total_spend": fmt_eur(c_sum),
                "spend_share": fmt_pct(c_share),
                "paid_sales_share": fmt_pct(s_share),
                "apparent_roas": f"{roas:.2f}×",
                "active_days": f"{active_days}/{len(wide_df)}",
                "mean_daily_spend": fmt_eur(float(c_ser[c_ser > 0].mean())) if active_days else "—",
                "max_day": fmt_eur(float(c_ser.max())),
                "yoy_spend": fmt_pct(yoy_c, signed=True),
                "_c_share": c_share,
                "_c_sum": c_sum,
                "_s_share": s_share,
                "_roas": roas,
            }
        )

    spend_rows.sort(key=lambda r: r["_c_share"], reverse=True)
    meta = {r["channel"]: {"share": r["_c_share"], "sales_share": r["_s_share"], "roas": r["_roas"], "spend": r["_c_sum"]} for r in spend_rows}
    for r in spend_rows:
        ch_ordered.append(r["channel"])
        spend_shares.append(round(r["_c_share"], 2))
        sales_shares_paid.append(round(r["_s_share"], 2))
        apparent_roas.append(round(r["_roas"], 2))
        r.pop("_c_share", None)
        r.pop("_s_share", None)
        r.pop("_roas", None)
        r.pop("_c_sum", None)

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

    # 2. Monthly spend vs total sales over time (complete years)
    monthly = full.resample("ME").sum()
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

    cat = config.print_event_channels[0] if config.print_event_channels else None
    cat_meta = meta.get(cat, {}) if cat else {}
    n_sends = int((wide_df[f"cost_{slug_map[cat]}"] > 0).sum()) if cat else 0
    cat_mean_send = float(wide_df[f"cost_{slug_map[cat]}"][wide_df[f"cost_{slug_map[cat]}"] > 0].mean()) if n_sends else 0.0
    top_roas = max(meta, key=lambda c: meta[c]["roas"]) if meta else None
    low_roas = min(meta, key=lambda c: meta[c]["roas"]) if meta else None
    day_names = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"]
    cat_peak = None
    if cat in ch_ordered:
        cat_row = idx_matrix[ch_ordered.index(cat)]
        cat_peak = (day_names[int(np.argmax(cat_row))], max(cat_row))
    # The two strongest weekday peaks among the other channels (01b: Social Media on Mondays, Affiliate on Sundays)
    other_peaks = sorted(
        ((ch, day_names[int(np.argmax(row))], max(row)) for ch, row in zip(ch_ordered, idx_matrix) if ch != cat),
        key=lambda v: -v[2],
    )[:2]
    weekly = full[["total_sales", "total_cost"]].resample("W-SUN").sum()
    wk_counts = full["total_sales"].resample("W-SUN").size()
    weekly = weekly[wk_counts == 7]
    weekly_r = float(weekly["total_sales"].corr(weekly["total_cost"])) if len(weekly) > 3 else float("nan")
    callout_body = (
        (
            f"**{cat}** takes **{cat_meta['share']:.1f}% of paid spend** ({fmt_eur(cat_meta['spend'])}) but only "
            f"**{cat_meta['sales_share']:.1f}% of last-touch paid-channel sales** (attributed ROAS {cat_meta['roas']:.2f}): its whole "
            "print and postage cost is booked on the send-out day, while orders arrive over the following weeks and are often "
            "credited to SEA, SEO and Direct Type-in. "
            if cat_meta
            else ""
        )
        + (
            f"**{top_roas}** looks most efficient (ROAS {meta[top_roas]['roas']:.2f}) and **{low_roas}** least ({meta[low_roas]['roas']:.2f}). "
            if top_roas and low_roas
            else ""
        )
        + "These ROAS are not incremental: last-touch attribution over-credits lower-funnel channels and under-credits the "
        "catalog's halo — exactly the gap the MMM closes."
    )

    return Section(
        id="explore_spend",
        title="Marketing investment",
        subtitle=f"Budget allocation across the {len(ch_ordered)} paid channels, flighting, weekday cadence and raw spend–sales shapes.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Total paid spend", value=fmt_eur(total_cost_sum), delta=f"Mean {fmt_eur(float(wide_df['total_cost'].mean()))}/day over {len(wide_df)} days"),
                    KpiItem(label=f"{y_last} vs {y_prev} spend", value=fmt_pct(yoy_spend_pct, signed=True), delta=f"€{float(w23['total_cost'].sum()):,.0f} → €{float(w24['total_cost'].sum()):,.0f}; sales {fmt_pct(yoy_sales_pct, signed=True)}", status="success" if yoy_spend_pct < yoy_sales_pct else "warn"),
                    KpiItem(label=f"{cat or 'Print'} share of spend", value=fmt_pct(cat_meta.get('share', 0.0)), delta=f"{n_sends} send-outs, Ø {fmt_eur(cat_mean_send)} each"),
                    KpiItem(label="Marketing cost ratio", value=f"{cost_ratio[y_last]:.1f}%", delta=f"spend / sales ({cost_ratio[y_prev]:.1f}% in {y_prev})"),
                ]
            ),
            CalloutBlock(
                tone="warn",
                title="Why last-touch ROAS misleads budget allocation",
                body=callout_body,
            ),
            ChartBlock(
                id="spend_vs_sales_share",
                title="Share of Marketing Spend vs Share of Attributed Paid Sales",
                subtitle=f"Across the {len(ch_ordered)} paid channels.",
                figure=fig_share,
            ),
            ChartBlock(
                id="spend_vs_sales_time",
                title="Monthly Total Sales vs Digital and Catalog Spend",
                subtitle=f"{y_last} spend {fmt_pct(yoy_spend_pct, signed=True)} while total sales {fmt_pct(yoy_sales_pct, signed=True)}; weekly spend vs weekly sales r = {weekly_r:.2f} (budgets follow the expected season).",
                figure=fig_time,
            ),
            ChartBlock(
                id="spend_small_multiples",
                title="Daily Spend Trajectories by Paid Channel (k€/day)",
                subtitle=f"{cat or 'The print channel'} is pulsed ({n_sends} send-outs, Ø {fmt_eur(cat_mean_send)}); digital channels spend every day.",
                figure=fig_sm,
            ),
            ChartBlock(
                id="spend_weekday_index",
                title="Paid Channel × Weekday Spend Index (100 = Channel Mean)",
                subtitle="; ".join(
                    ([f"{cat} cost lands on {cat_peak[0]} (index {cat_peak[1]:.0f})"] if cat_peak else [])
                    + [f"{ch} spends {v / 100:.1f}× its average on {day}" for ch, day, v in other_peaks]
                )
                + ".",
                figure=fig_dow,
            ),
            ChartBlock(
                id="spend_response",
                title="Empirical Dose–Response Curves (Spend vs Total Sales)",
                subtitle="Octile-binned mean total sales by daily spend (catalog: 14-day rolling spend); indicative only — spend and sales share the season.",
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
                    TableColumn(key="mean_daily_spend", label="Mean / active day", align="right"),
                    TableColumn(key="max_day", label="Max day", align="right"),
                    TableColumn(key="yoy_spend", label="YoY Spend", align="right"),
                ],
                rows=spend_rows,
            ),
            TableBlock(
                id="spend_by_year",
                title=f"Spend per channel: {y_prev} vs {y_last}",
                subtitle="Spend and attributed-sales growth by channel; CV is the coefficient of variation of spend on active days.",
                columns=[
                    TableColumn(key="channel", label="Paid Channel"),
                    TableColumn(key="spend_prev", label=f"Spend {y_prev}", align="right"),
                    TableColumn(key="spend_last", label=f"Spend {y_last}", align="right"),
                    TableColumn(key="yoy_spend", label="Spend YoY", align="right"),
                    TableColumn(key="yoy_sales", label="Attributed sales YoY", align="right"),
                    TableColumn(key="cv_active", label="CV (active days)", align="right"),
                ],
                rows=sorted(year_rows, key=lambda r: -r.pop("_c_sum")),
            ),
            TableBlock(
                id="spend_weekday_eur",
                title="Mean daily spend by weekday (€)",
                subtitle="Digital channels only; the print channel's spend lands on its send-out day.",
                columns=[
                    TableColumn(key="weekday", label="Weekday"),
                    *[TableColumn(key=slug_map[ch], label=ch, align="right") for ch in config.digital_paid_channels],
                ],
                rows=[
                    {
                        "weekday": day,
                        **{
                            slug_map[ch]: f"€{float(wide_df.loc[wide_df.index.dayofweek == d, f'cost_{slug_map[ch]}'].mean()):,.0f}"
                            for ch in config.digital_paid_channels
                        },
                    }
                    for d, day in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
                ],
            ),
        ],
    )
