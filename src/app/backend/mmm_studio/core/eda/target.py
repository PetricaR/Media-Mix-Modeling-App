"""Target (daily total sales): level, distribution, YoY, seasonality, STL, stationarity, ACF and events (01 & 01b)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import acf, pacf

from mmm_studio.charts.builders import bar_figure, heatmap_figure, line_figure, small_multiples_figure
from mmm_studio.charts.theme import COPPER, CORAL, INK_MUTED, NAVY, SLATE, STATUS_SUCCESS, TEAL
from mmm_studio.core.calendar import add_calendar_and_adstock_features
from mmm_studio.core.eda.notebook_stats import DOW, event_lifts, stationarity_table, stl_stats
from mmm_studio.core.features import complete_years
from mmm_studio.report.narrative import fmt_eur, fmt_pct
from mmm_studio.report.sections import (
    ChartBlock,
    KpiItem,
    KpisBlock,
    MarkdownBlock,
    Section,
    TableBlock,
    TableColumn,
)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def build_explore_target_section(wide_df: pd.DataFrame) -> Section:
    """Build the `explore_target` section on the full calendar (yearly and monthly views use complete years)."""
    panel = wide_df if "dayofweek" in wide_df.columns else add_calendar_and_adstock_features(wide_df)
    y = panel["total_sales"]
    idx = pd.DatetimeIndex(panel.index)
    dates_str = [d.strftime("%Y-%m-%d") for d in idx]
    dss = panel["days_since_catalog"] if "days_since_catalog" in panel else None
    full = complete_years(panel)
    years = sorted(set(pd.DatetimeIndex(full.index).year))

    mean_sales, std_sales = float(y.mean()), float(y.std())
    skew_raw, skew_log = float(y.skew()), float(np.log(y).skew())
    cv = std_sales / mean_sales
    yoy_txt, yoy_pct = "—", float("nan")
    by_year = {yr: float(full.loc[full.index.year == yr, "total_sales"].sum()) for yr in years}
    if len(years) >= 2:
        y0, y1 = years[-2], years[-1]
        yoy_pct = 100.0 * (by_year[y1] / by_year[y0] - 1.0)
        yoy_txt = f"{fmt_eur(by_year[y1])} ({y1}) vs {fmt_eur(by_year[y0])} ({y0})"

    # 1. Time series with a 28-day centred mean, catalog send-outs and the top days
    roll28 = y.rolling(28, center=True, min_periods=14).mean()
    send_mask = panel["catalog_sendout"] == 1 if "catalog_sendout" in panel else pd.Series(False, index=idx)
    top5 = y.nlargest(5)
    fig_ts = line_figure(
        x=dates_str,
        series=[
            {"name": "Daily total sales", "y": y.round(0).tolist(), "color": INK_MUTED, "width": 0.8, "opacity": 0.7},
            {"name": "28-day centred mean", "y": roll28.round(0).tolist(), "color": NAVY, "width": 2.2},
            {
                "name": "Catalog send-out",
                "x": [d.strftime("%Y-%m-%d") for d in idx[send_mask.to_numpy()]],
                "y": y[send_mask.to_numpy()].round(0).tolist(),
                "mode": "markers",
                "marker": {"color": COPPER, "size": 7, "symbol": "diamond"},
            },
            {
                "name": "Top-5 days",
                "x": [d.strftime("%Y-%m-%d") for d in top5.index],
                "y": top5.round(0).tolist(),
                "mode": "markers",
                "marker": {"color": CORAL, "size": 9, "symbol": "circle-open", "line": {"width": 2}},
            },
        ],
        yaxis_title="Total sales (€)",
        height=400,
    )
    top5_sundays = sum(1 for d in top5.index if d.dayofweek == 6 and dss is not None and dss.get(d) == 3)

    # 2. Distribution: raw vs log, by year
    counts_raw, bins_raw = np.histogram(y / 1e3, bins=45)
    counts_log, bins_log = np.histogram(np.log(y), bins=45)
    fig_dist = small_multiples_figure(
        panels=[
            {
                "title": f"Raw scale — skew {skew_raw:.2f}",
                "xaxis_title": "Daily sales (k€)",
                "traces": [{"type": "bar", "name": "Days", "x": (0.5 * (bins_raw[:-1] + bins_raw[1:])).round(1), "y": counts_raw, "marker": {"color": NAVY}}],
            },
            {
                "title": f"Log scale — skew {skew_log:.2f}",
                "xaxis_title": "log(total sales)",
                "traces": [{"type": "bar", "name": "Days", "x": (0.5 * (bins_log[:-1] + bins_log[1:])).round(3), "y": counts_log, "marker": {"color": TEAL}}],
            },
            {
                "title": "By year",
                "traces": [
                    {"type": "box", "name": str(yr), "y": full.loc[full.index.year == yr, "total_sales"].round(0).tolist(), "marker": {"color": [INK_MUTED, NAVY][i % 2]}}
                    for i, yr in enumerate(years)
                ],
            },
        ],
        ncols=3,
        height_per_row=300,
    )

    # 3. Year over year: 7-day centred mean by day of year + monthly growth
    yoy_series = []
    for i, yr in enumerate(years):
        s = full.loc[full.index.year == yr, "total_sales"].rolling(7, center=True, min_periods=4).mean()
        yoy_series.append(
            {"name": str(yr), "x": s.index.dayofyear.tolist(), "y": s.round(0).tolist(), "color": NAVY if i == len(years) - 1 else INK_MUTED, "width": 2.2 if i == len(years) - 1 else 1.6}
        )
    monthly = full["total_sales"].resample("MS").mean()
    growth = []
    if len(years) >= 2:
        m0 = monthly[monthly.index.year == years[-2]]
        m1 = monthly[monthly.index.year == years[-1]]
        if len(m0) == 12 and len(m1) == 12:
            growth = list(100.0 * (m1.to_numpy() / m0.to_numpy() - 1.0))
    fig_yoy = line_figure(x=list(range(1, 367)), series=yoy_series, xaxis_title="Day of year", yaxis_title="7-day centred mean (€)", height=340)
    fig_growth = bar_figure(
        categories=MONTHS[: len(growth)],
        series=[{"name": "Growth of average daily sales", "values": [round(g, 1) for g in growth], "colors": [NAVY if g >= 0 else CORAL for g in growth], "text": [f"{g:+.0f}%" for g in growth]}],
        yaxis_title=f"{years[-1]} vs {years[-2]} (%)" if len(years) >= 2 else "%",
        height=280,
        showlegend=False,
    )
    best_m = MONTHS[int(np.argmax(growth))] if growth else "—"

    # 4. Seasonality: weekday (all days vs no recent catalog) and weekday × month
    dow_all = y.groupby(idx.dayofweek).mean()
    clean = panel["days_since_catalog"] >= 7 if dss is not None else pd.Series(True, index=idx)
    dow_clean = y[clean].groupby(idx[clean].dayofweek).mean().reindex(range(7))
    fig_seasonality = bar_figure(
        categories=DOW,
        series=[
            {"name": "All days", "values": dow_all.round(0).tolist(), "color": NAVY},
            {"name": "≥ 7 days after a catalog send-out", "values": dow_clean.round(0).tolist(), "color": TEAL},
        ],
        yaxis_title="Mean daily sales (€)",
        height=320,
    )
    wk_month = full["total_sales"].groupby([pd.DatetimeIndex(full.index).dayofweek, pd.DatetimeIndex(full.index).to_period("M")]).mean().unstack()
    fig_wk_month = heatmap_figure(
        x=[p.strftime("%b %y") for p in wk_month.columns],
        y=DOW,
        z=wk_month.to_numpy() / 1e3,
        colorscale="YlOrBr",
        show_values=False,
        height=300,
    )
    best_all, best_clean = DOW[int(dow_all.idxmax())], DOW[int(dow_clean.idxmax())]

    # 5. STL (weekly period, 91-day trend)
    stl = stl_stats(y, dss)
    res = stl["stl"]
    fig_stl = small_multiples_figure(
        panels=[
            {"title": "Observed (€)", "x": dates_str, "y": y.round(0).tolist(), "color": SLATE},
            {"title": "Trend, 91-day window (€)", "x": dates_str, "y": res.trend.round(0).tolist(), "color": NAVY},
            {"title": "Weekly seasonal (€)", "x": dates_str, "y": res.seasonal.round(0).tolist(), "color": TEAL},
            {"title": "Residual (€)", "x": dates_str, "y": res.resid.round(0).tolist(), "color": COPPER},
        ],
        ncols=1,
        height_per_row=150,
    )

    # 6. ACF & PACF
    nlags = min(35, len(y) // 4)
    acf_vals = acf(y, nlags=nlags)[1:]
    pacf_vals = pacf(y, nlags=nlags, method="ywm")[1:]
    band = 1.96 / np.sqrt(len(y))
    fig_acf = bar_figure(
        categories=list(range(1, nlags + 1)),
        series=[{"name": "ACF", "values": np.round(acf_vals, 3), "color": NAVY}, {"name": "PACF", "values": np.round(pacf_vals, 3), "color": COPPER}],
        xaxis_title="Lag (days)",
        yaxis_title="Correlation",
        hlines=[{"y": band, "color": SLATE, "dash": "dot"}, {"y": -band, "color": SLATE, "dash": "dot"}],
        height=320,
    )

    # 7. Holidays and retail events vs the same weekday in the surrounding ±4 weeks
    ev = event_lifts(y, dss)
    blocks_events: list = []
    if len(ev):
        ev_sorted = ev.sort_values("lift")
        labels = [
            f"{n} {d.year}{' †' if 0 <= c <= 6 else ''}" for d, n, c in zip(ev_sorted.index, ev_sorted["event"], ev_sorted["days_since_catalog"].fillna(99), strict=False)
        ]
        fig_events = bar_figure(
            categories=labels,
            series=[{"name": "Lift", "values": (100 * ev_sorted["lift"]).round(1).tolist(), "colors": [STATUS_SUCCESS if v >= 0 else CORAL for v in ev_sorted["lift"]], "text": [f"{100 * v:+.0f}%" for v in ev_sorted["lift"]]}],
            orientation="h",
            xaxis_title="Sales vs median of the same weekday in the surrounding ±4 weeks (%)",
            vlines=[{"x": 0, "color": SLATE, "dash": "solid"}],
            showlegend=False,
            height=max(380, 22 * len(ev_sorted)),
        )
        xmas = ev[ev["event"] == "Christmas Eve"]["lift"]
        bf = ev[ev["event"] == "Black Friday"]["lift"]
        blocks_events.append(
            ChartBlock(
                id="events_lift",
                title="Holidays depress demand; Black Friday lifts it",
                subtitle=(
                    (f"Christmas Eve {', '.join(f'{100 * v:+.0f}%' for v in xmas)}; " if len(xmas) else "")
                    + (f"Black Friday {', '.join(f'{100 * v:+.0f}%' for v in bf)}. " if len(bf) else "")
                    + "† = 0–6 days after a catalog send-out, so the catalog, not the holiday, may explain the lift."
                ),
                figure=fig_events,
            )
        )

    # Extremes table (10 highest and 10 lowest days)
    events = dict(zip(ev.index, ev["event"], strict=False)) if len(ev) else {}

    def describe(days: pd.Index, group: str) -> list[dict]:
        rows = []
        for rank, d in enumerate(days, 1):
            ds = dss.get(d) if dss is not None else np.nan
            rows.append(
                {
                    "group": f"{group} #{rank}",
                    "date": d.strftime("%Y-%m-%d (%a)"),
                    "total_sales": fmt_eur(float(y[d])),
                    "days_since_catalog": "—" if ds != ds else f"{int(ds)}",
                    "event": events.get(d, ""),
                }
            )
        return rows

    extreme_rows = describe(y.nlargest(10).index, "Highest") + describe(y.nsmallest(10).index, "Lowest")
    stat_rows = [
        {
            "series": r["series"],
            "adf": f"{r['adf_stat']:.2f} (p {r['adf_p']:.3f})",
            "kpss": f"{r['kpss_stat']:.2f} (p {r['kpss_p']:.3f})",
            "verdict": r["verdict"],
        }
        for r in stationarity_table(y)
    ]

    blocks = [
        KpisBlock(
            items=[
                KpiItem(label="Mean daily sales", value=fmt_eur(mean_sales), delta=f"SD {fmt_eur(std_sales)} · CV {cv:.2f}"),
                KpiItem(label="Median daily sales", value=fmt_eur(float(y.median())), delta=f"Range {fmt_eur(float(y.min()))} – {fmt_eur(float(y.max()))}"),
                KpiItem(label=f"{years[-1]} vs {years[-2]} sales" if len(years) >= 2 else "Year-on-year", value=fmt_pct(yoy_pct, signed=True), delta=yoy_txt, status="success"),
                KpiItem(label="Skewness", value=f"{skew_raw:.2f}", delta=f"Log scale {skew_log:.2f}: a log / multiplicative model is natural"),
            ]
        ),
        ChartBlock(
            id="target_timeseries",
            title="Daily total sales: trend, Q4 peaks and spikes after catalog send-outs",
            subtitle=f"{len(y)} days, {idx.min():%d %b %Y} → {idx.max():%d %b %Y}; {top5_sundays} of the top-5 days are the Sunday 3 days after a send-out.",
            figure=fig_ts,
        ),
        ChartBlock(
            id="target_distribution",
            title="Daily sales are right-skewed; close to symmetric on a log scale",
            subtitle=f"Skewness {skew_raw:.2f} raw vs {skew_log:.2f} log: catalog peaks and Q4 form the heavy right tail.",
            figure=fig_dist,
        ),
        ChartBlock(
            id="target_yoy",
            title=f"Year over year: {years[-1]} runs above {years[-2]}" if len(years) >= 2 else "Year over year",
            subtitle=f"7-day centred mean aligned by day of year; total {fmt_pct(yoy_pct, signed=True)}.",
            figure=fig_yoy,
        ),
    ]
    if growth:
        blocks.append(
            ChartBlock(
                id="target_yoy_monthly",
                title=f"Growth of average daily sales by month: widest gap in {best_m}",
                subtitle="Month-level growth is partly marketing-driven (catalog timing, Social Media regime).",
                figure=fig_growth,
            )
        )
    blocks += [
        ChartBlock(
            id="target_seasonality",
            title=f"{best_all} looks strongest — but without a recent catalog it is {best_clean}",
            subtitle="Mean daily sales by weekday on all days vs days ≥ 7 days after a send-out; the weekday dummy and catalog lag are confounded.",
            figure=fig_seasonality,
        ),
        ChartBlock(
            id="target_weekday_month",
            title="Mean daily sales by weekday × month (k€)",
            subtitle="Weak spring/summer of the first year, a strong Q4 and a higher plateau afterwards.",
            figure=fig_wk_month,
        ),
        ChartBlock(
            id="target_stl",
            title="STL decomposition: trend and catalog-driven spikes dominate; the weekly cycle is small",
            subtitle=(
                f"Variance relative to sales: trend {100 * stl['var_trend']:.0f}%, weekly {100 * stl['var_weekly']:.0f}%, "
                f"residual {100 * stl['var_resid']:.0f}%"
                + (f"; {100 * stl['top_resid_after_catalog']:.0f}% of the 15 largest residuals fall 0–6 days after a send-out." if "top_resid_after_catalog" in stl else ".")
            ),
            figure=fig_stl,
        ),
        TableBlock(
            id="target_stationarity_table",
            title="Stationarity tests",
            subtitle="ADF (H0: unit root) and KPSS (H0: stationary).",
            columns=[
                TableColumn(key="series", label="Series"),
                TableColumn(key="adf", label="ADF stat (p)", align="right"),
                TableColumn(key="kpss", label="KPSS stat (p)", align="right"),
                TableColumn(key="verdict", label="Verdict"),
            ],
            rows=stat_rows,
        ),
        ChartBlock(
            id="target_acf",
            title="Strong persistence with weekly echoes",
            subtitle=f"ACF at lag 1 = {acf_vals[0]:.2f}, lag 7 = {acf_vals[6]:.2f}; dotted lines are the 95 % band.",
            figure=fig_acf,
            caption="Autocorrelated residuals call for time-based hold-outs and HAC standard errors.",
        ),
        *blocks_events,
        TableBlock(
            id="target_extremes_table",
            title="10 highest and 10 lowest days",
            subtitle="Peaks are marketing (the Sunday after a send-out, Black Friday); troughs are calendar (Christmas, Easter, the catalog-free autumn).",
            columns=[
                TableColumn(key="group", label="Rank"),
                TableColumn(key="date", label="Date"),
                TableColumn(key="total_sales", label="Total sales", align="right"),
                TableColumn(key="days_since_catalog", label="Days since catalog", align="right"),
                TableColumn(key="event", label="Event"),
            ],
            rows=extreme_rows,
        ),
        MarkdownBlock(
            title="What this means for the model",
            body=(
                f"- A **trend / level** component is needed: {years[-1] if years else ''} is above the prior year almost every month.\n"
                "- **Log or multiplicative effects** fit the right-skewed target; evaluate on the original scale (MAE-based KPI).\n"
                "- **Weekday dummies and holiday / Christmas / Black Week flags** are required; the catalog lag must be estimated jointly with them.\n"
                "- With only two years, yearly seasonality needs a **low-order Fourier** representation."
            ),
        ),
    ]
    return Section(
        id="explore_target",
        title="Target: daily total sales",
        subtitle="Level, distribution, year-over-year trajectory, seasonality, STL, stationarity, autocorrelation and calendar events.",
        blocks=blocks,
    )
