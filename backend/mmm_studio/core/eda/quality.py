"""Data quality, completeness grid, metric availability, and anomaly inventory."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import heatmap_figure, line_figure
from mmm_studio.charts.theme import NAVY, STATUS_DANGER, STATUS_WARNING
from mmm_studio.core.schema import DataProfile
from mmm_studio.core.tables import build_completeness_grid, sort_and_clean
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


def build_data_quality_section(
    raw_df: pd.DataFrame,
    wide_df: pd.DataFrame,
    profile: DataProfile,
) -> Section:
    """Build the `data_quality` section covering completeness, NaN semantics, and anomalies."""
    rc = profile.role_config
    clean = sort_and_clean(raw_df, config=rc)
    presence = build_completeness_grid(clean, config=rc)

    total_expected = len(presence) * len(rc.active_channels)
    total_missing = int((presence == 0).sum().sum())
    # What this upload has: a wide table (one row per day, a target column), a TV channel with sales, a print channel
    wide = rc.columns.layout == "wide"
    has_sales = rc.has_channel_sales
    has_tv = has_sales and any(c.lower() == "tv" for c in rc.active_channels)
    has_print = bool(rc.print_event_channels)

    # 1. Column inventory table
    col_rows = []
    for col_name in ["date", "channel", "sales_by_channel", "costs_by_channel", "impressions_by_channel", "clicks_by_channel"]:
        if col_name in clean.columns:
            s = clean[col_name]
            n_null = int(s.isna().sum())
            pct_null = round(100.0 * n_null / max(len(clean), 1), 1)
            if col_name == "costs_by_channel":
                sem = "Paid spend in €; NaN on unpaid channels (not applicable) and non-sendout Catalog days (€0)"
            elif col_name == "sales_by_channel":
                sem = "Attributed channel sales in €; sums across channels to daily total_sales (target)"
            elif col_name == "impressions_by_channel":
                sem = "Ad impressions / Catalog print circulation; NaN on unpaid channels and PSM"
            elif col_name == "clicks_by_channel":
                sem = "Ad clicks; 0 on Catalog, NaN on unpaid channels"
            elif col_name == "date":
                sem = "Calendar date (shuffled in raw sheet; sorted to daily index)"
            else:
                rc0 = profile.role_config
                sem = f"Marketing channel ({len(rc0.active_channels)} channels: {len(rc0.paid_channels)} with costs, {len(rc0.unpaid_channels)} without)"
            col_rows.append(
                {
                    "column": col_name,
                    "dtype": str(s.dtype),
                    "non_null": len(clean) - n_null,
                    "null_count": n_null,
                    "null_pct": f"{pct_null:.1f}%",
                    "semantics": sem,
                }
            )

    if wide:
        col_rows = _wide_column_rows(rc, wide_df)

    # 2. Per-channel coverage & metric availability
    ch_rows = []
    avail_matrix = []
    avail_channels = rc.active_channels
    for ch in avail_channels:
        sub = clean[clean["channel"] == ch]
        n_rows = len(sub)
        miss_days = int((presence[ch] == 0).sum()) if ch in presence.columns else 0
        sales_pos = round(100.0 * float((sub["sales_by_channel"].fillna(0) > 0).mean()), 1) if n_rows else 0.0
        cost_pos = round(100.0 * float((sub["costs_by_channel"].fillna(0) > 0).mean()), 1) if n_rows else 0.0
        impr_pos = round(100.0 * float((sub["impressions_by_channel"].fillna(0) > 0).mean()), 1) if n_rows else 0.0
        click_pos = round(100.0 * float((sub["clicks_by_channel"].fillna(0) > 0).mean()), 1) if n_rows else 0.0
        role = next((c.role for c in rc.channels if c.name == ch), "paid")
        ch_rows.append(
            {
                "channel": ch,
                "role": role,
                "rows": n_rows,
                "missing_days": miss_days,
                "sales_active_pct": f"{sales_pos:.1f}%",
                "cost_active_pct": f"{cost_pos:.1f}%",
                "impr_active_pct": f"{impr_pos:.1f}%",
                "clicks_active_pct": f"{click_pos:.1f}%",
                "total_sales": fmt_eur(float(sub["sales_by_channel"].fillna(0).sum())),
                "total_cost": fmt_eur(float(sub["costs_by_channel"].fillna(0).sum())),
            }
        )
        avail_matrix.append([sales_pos, cost_pos, impr_pos, click_pos])

    # Chart 1: Monthly missing channel-days heatmap + bar summary
    monthly_missing = (1 - presence).resample("ME").sum()
    month_labels = [d.strftime("%Y-%m") for d in monthly_missing.index]
    fig_missing = heatmap_figure(
        x=month_labels,
        y=avail_channels,
        z=monthly_missing[avail_channels].T.to_numpy(),
        title="Missing Channel-Days by Month and Channel (0 = Complete)",
        xaxis_title="Month",
        yaxis_title="Channel",
        colorscale="YlOrRd",
        zmin=0,
        show_values=True,
        value_fmt=".0f",
        height=420,
    )

    # Chart 2: Metric availability matrix (a wide upload shows only the metrics it has)
    metric_labels = ["Sales (>0)", "Costs (>0)", "Impressions (>0)", "Clicks (>0)"]
    keep = list(range(4))
    if wide:
        matrix = np.asarray(avail_matrix) if avail_matrix else np.zeros((0, 4))
        keep = [j for j in range(4) if j == 1 or (len(matrix) and float(matrix[:, j].max()) > 0)]
    fig_avail = heatmap_figure(
        x=[metric_labels[j] for j in keep],
        y=avail_channels,
        z=np.asarray([[row[j] for j in keep] for row in avail_matrix]),
        title="Metric Availability (% of Present Days with Positive Value)",
        xaxis_title="Metric",
        yaxis_title="Channel",
        colorscale="Blues",
        zmin=0,
        zmax=100,
        show_values=True,
        value_fmt=".1f",
        height=420,
    )

    # Chart 3: TV sales timeline highlighting 38 missing days and 8 near-zero (<1 EUR) days
    tv_fig_blocks = []
    if "sales_tv" in wide_df.columns:
        dates_str = [d.strftime("%Y-%m-%d") for d in wide_df.index]
        tv_sales = wide_df["sales_tv"].to_numpy()
        miss_dates = [d.strftime("%Y-%m-%d") for d in wide_df.index[wide_df.get("missing_tv", pd.Series(0, index=wide_df.index)) == 1]]
        low_mask = (wide_df.get("missing_tv", pd.Series(0, index=wide_df.index)) == 0) & (wide_df["sales_tv"] < 1.0)
        low_dates = [d.strftime("%Y-%m-%d") for d in wide_df.index[low_mask]]
        fig_tv = line_figure(
            x=dates_str,
            series=[
                {"name": "TV Daily Sales (€)", "y": tv_sales, "color": NAVY, "width": 1.6},
                {
                    "name": f"Missing Row ({len(miss_dates)}d)",
                    "x": miss_dates,
                    "y": [0.0] * len(miss_dates),
                    "mode": "markers",
                    "marker": {"color": STATUS_DANGER, "size": 7, "symbol": "x"},
                },
                {
                    "name": f"Near-Zero <1€ ({len(low_dates)}d)",
                    "x": low_dates,
                    "y": wide_df.loc[low_mask, "sales_tv"].to_numpy(),
                    "mode": "markers",
                    "marker": {"color": STATUS_WARNING, "size": 8, "symbol": "circle"},
                },
            ],
            title=None,
            xaxis_title="Date",
            yaxis_title="Sales (€)",
            height=340,
        )
        tv_fig_blocks.append(
            ChartBlock(
                id="quality_tv_outages",
                title="TV Tracking Outages & Near-Zero Anomaly Windows",
                subtitle=f"{len(miss_dates)} missing channel-days plus {len(low_dates)} present days with sales below €1, right next to the outage windows.",
                figure=fig_tv,
                caption="Both missing rows and <€1 days represent tracking outages rather than genuine zero demand; flagged via `tv_anomaly`.",
            )
        )

    # Anomaly inventory table
    anomaly_rows = [
        {
            "issue": "Shuffled long-format rows",
            "affected": f"All {profile.raw_row_count:,} rows",
            "magnitude": f"{profile.calendar_days} calendar days × {len(avail_channels)} channels (expected {total_expected:,}, gap = {total_missing})",
            "treatment": "Sort by (date, channel) and pivot to regular daily index before any lag or rolling computation.",
        }
        if not wide
        else {
            "issue": "Wide table: one row per day",
            "affected": f"{profile.calendar_days} days",
            "magnitude": f"{len(avail_channels)} channel spend columns, target `{rc.columns.target}`, {len(rc.columns.controls)} control(s)",
            "treatment": "Read as one cost series per channel; every day between the first and last date must be present.",
        },
        {
            "issue": "Missing channel-date rows",
            "affected": ", ".join(f"{k} ({v}d)" for k, v in profile.missing_channel_days.items()) or "None",
            "magnitude": f"{total_missing} missing channel-days total",
            "treatment": "Impute 0 sales on missing rows and add explicit `missing_<channel>` indicator columns.",
        },
        {
            "issue": "TV near-zero sales days (< €1)",
            "affected": "TV",
            "magnitude": f"{profile.tv_low_sales_days} present days with sales < €1",
            "treatment": f"Tracking outage, not zero demand: flagged with `missing_tv` in a `tv_anomaly` flag ({profile.missing_channel_days.get('TV', 0) + profile.tv_low_sales_days} days). TV is not a model input anyway.",
        },
        {
            "issue": "Catalog episodic cost booking",
            "affected": "Catalog",
            "magnitude": f"{profile.catalog_sendout_days} send-out days with cost > 0; NaN on remaining {profile.calendar_days - profile.catalog_sendout_days} days",
            "treatment": (
                f"No send-out means €0 spend (not missing); the spend is shifted to its in-home date (+{rc.print_shift} "
                f"day{'s' if rc.print_shift != 1 else ''}, the run setting) with adstock."
            ),
        },
    ]
    if not has_tv:
        anomaly_rows = [r for r in anomaly_rows if r["issue"] != "TV near-zero sales days (< €1)"]
    if not has_print:
        anomaly_rows = [r for r in anomaly_rows if r["issue"] != "Catalog episodic cost booking"]
    anomaly_rows += [
        {
            "issue": "Trailing isolated partial year",
            "affected": ", ".join(profile.trailing_isolated_dates) or "None",
            "magnitude": f"{len(profile.trailing_isolated_dates)} day(s) in trailing year",
            "treatment": (
                f"Excluded from the models ({profile.start_date} to {profile.modelling_end_date}, {profile.modelling_days} days)."
                if profile.role_config.drop_trailing_isolated_days
                else f"Kept in the models ({profile.modelling_days} days); left out of yearly and monthly comparisons."
            ),
        },
    ]

    sendout_days_txt = ""
    if "catalog_sendout" in wide_df.columns:
        sd = pd.DatetimeIndex(wide_df.index[wide_df["catalog_sendout"] == 1]).day_name().value_counts()
        sendout_days_txt = ", ".join(f"{n} {day[:3]}" for day, n in sd.items())
    miss_parts = []
    for ch, n in sorted(profile.missing_channel_days.items(), key=lambda t: -t[1]):
        mcol = f"missing_{profile.role_config.slug_map().get(ch, ch)}"
        months = (
            sorted({d.strftime("%b %Y") for d in wide_df.index[wide_df[mcol] == 1]}, key=lambda m: pd.Timestamp(m))
            if mcol in wide_df.columns
            else []
        )
        miss_parts.append(f"{ch} {n} ({', '.join(months)})" if months else f"{ch} {n}")
    missing_caption = (
        "Missing channel-days: " + "; ".join(miss_parts) + ". They count as 0 sales in the target and are flagged."
        if miss_parts
        else "No channel-days are missing."
    )

    # Count, min, median and max of every metric per channel over the raw rows
    metric_names = {
        "sales_by_channel": "Sales",
        "impressions_by_channel": "Impressions",
        "clicks_by_channel": "Clicks",
        "costs_by_channel": "Costs",
    }
    range_rows = []
    for ch in avail_channels:
        sub_df = clean[clean["channel"] == ch]
        for col, metric in metric_names.items():
            s = sub_df[col].dropna() if col in sub_df else pd.Series(dtype=float)
            if len(s):
                range_rows.append(
                    {
                        "channel": ch,
                        "metric": metric,
                        "count": len(s),
                        "min": f"{s.min():,.0f}",
                        "median": f"{s.median():,.0f}",
                        "max": f"{s.max():,.0f}",
                    }
                )

    # Sales lost on missing channel-days: missing days × the channel's mean daily sales
    channel_mean = clean.groupby("channel")["sales_by_channel"].mean()
    raw_total = float(clean["sales_by_channel"].sum())
    lost_rows = [
        {
            "channel": ch,
            "missing_days": int(n),
            "mean_sales": f"€{float(channel_mean.get(ch, 0.0)):,.0f}",
            "lost": f"€{n * float(channel_mean.get(ch, 0.0)):,.0f}",
            "share": f"{100 * n * float(channel_mean.get(ch, 0.0)) / raw_total:.3f}%" if raw_total else "–",
        }
        for ch, n in sorted(profile.missing_channel_days.items(), key=lambda t: -t[1])
        if n
    ]
    lost_blocks = (
        [
            TableBlock(
                id="quality_missing_sales_lost",
                title="Estimated sales lost on missing channel-days",
                subtitle="Missing days × the channel's mean daily sales; the share of total sales shows the gaps are negligible.",
                columns=[
                    TableColumn(key="channel", label="Channel"),
                    TableColumn(key="missing_days", label="Missing days", align="right"),
                    TableColumn(key="mean_sales", label="Mean sales / day", align="right"),
                    TableColumn(key="lost", label="Est. sales lost", align="right"),
                    TableColumn(key="share", label="Est. share of total sales", align="right"),
                ],
                rows=lost_rows,
            )
        ]
        if lost_rows
        else []
    )

    return Section(
        id="data_quality",
        title="Data quality audit",
        subtitle="Structure, completeness, what each metric means per channel, and how every anomaly is treated.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Raw rows", value=f"{profile.raw_row_count:,}", delta=f"Expected {total_expected:,}")
                    if not wide
                    else KpiItem(label="Rows", value=f"{profile.calendar_days:,}", delta="one per day (wide table)"),
                    KpiItem(label="Calendar span", value=f"{profile.calendar_days} days", delta=f"{profile.start_date} → {profile.end_date}"),
                    KpiItem(
                        label="Missing channel-days",
                        value=str(total_missing),
                        delta=", ".join(f"{k}: {v}" for k, v in sorted(profile.missing_channel_days.items(), key=lambda t: -t[1])) or "none",
                        status="warn" if total_missing else "success",
                    ),
                    *(
                        [
                            KpiItem(
                                label="TV outage / < €1 days",
                                value=str(profile.missing_channel_days.get("TV", 0) + profile.tv_low_sales_days),
                                delta=f"{profile.missing_channel_days.get('TV', 0)} missing + {profile.tv_low_sales_days} below €1",
                                status="warn",
                            )
                        ]
                        if has_tv
                        else []
                    ),
                    *([KpiItem(label="Catalog send-outs", value=str(profile.catalog_sendout_days), delta=sendout_days_txt)] if has_print else []),
                    *(
                        [KpiItem(label="Your controls", value=str(len(rc.columns.controls)), delta=", ".join(c.name for c in rc.columns.controls))]
                        if rc.columns.controls
                        else []
                    ),
                ]
            ),
            CalloutBlock(
                tone="danger",
                title="Target Leakage Guardrail: `sales_by_channel` vs `costs_by_channel`" if not wide else "Target leakage guardrail",
                body=(
                    (
                        f"Daily total sales is the sum of `sales_by_channel` across all {len(avail_channels)} channels. "
                        "Channel-level sales, same-day clicks, and same-day impressions are simultaneous outcomes of demand "
                        "and **must never** be used as predictors at forecast time. Only planned **costs** of the "
                        f"{len(profile.role_config.paid_channels)} paid channels, "
                        "deterministic calendar/holiday indicators, and structural regime flags are valid model inputs."
                    )
                    if not wide
                    else (
                        f"The target is the column `{rc.columns.target}`. Same-day clicks, impressions and attributed sales are "
                        "outcomes of demand and **must never** be used as predictors at forecast time. Only planned **costs** of "
                        f"the {len(rc.paid_channels)} paid channels, calendar/holiday indicators"
                        + (f" and your controls ({', '.join(c.name for c in rc.columns.controls)})" if rc.columns.controls else "")
                        + " are model inputs."
                    )
                ),
            ),
            TableBlock(
                id="quality_column_inventory",
                title="Workbook Column Inventory & NaN Semantics",
                columns=[
                    TableColumn(key="column", label="Column"),
                    TableColumn(key="dtype", label="Type"),
                    TableColumn(key="non_null", label="Non-Null", align="right"),
                    TableColumn(key="null_count", label="Nulls", align="right"),
                    TableColumn(key="null_pct", label="Null %", align="right"),
                    TableColumn(key="semantics", label="Domain Semantics & NaN Interpretation"),
                ],
                rows=col_rows,
            ),
            ChartBlock(
                id="fig01_completeness",
                title=f"Missing channel-days by month across all {len(avail_channels)} channels",
                subtitle=f"Count of missing calendar dates per month and channel (total = {total_missing} missing channel-days).",
                figure=fig_missing,
                caption=missing_caption,
            ),
            *lost_blocks,
            *tv_fig_blocks,
            ChartBlock(
                id="metric_availability",
                title="Channel × Metric Availability Matrix",
                subtitle="Percentage of present rows with strictly positive (> 0) values per metric.",
                figure=fig_avail,
                caption=(
                    f"{len(profile.role_config.paid_channels)} channels report costs"
                    + (
                        f"; {len(profile.role_config.unpaid_channels)} have none ({', '.join(profile.role_config.unpaid_channels)}), so they cannot be budget levers."
                        if profile.role_config.unpaid_channels
                        else "."
                    )
                    + (" For the catalog, impressions are the print run." if has_print else "")
                ),
            ),
            TableBlock(
                id="quality_channel_coverage",
                title="Per-Channel Coverage & Metric Summary",
                columns=[
                    TableColumn(key="channel", label="Channel"),
                    TableColumn(key="role", label="Role"),
                    TableColumn(key="rows", label="Rows", align="right"),
                    TableColumn(key="missing_days", label="Missing Days", align="right"),
                    *([TableColumn(key="sales_active_pct", label="Sales >0", align="right")] if has_sales else []),
                    TableColumn(key="cost_active_pct", label="Cost >0", align="right"),
                    *([TableColumn(key="impr_active_pct", label="Impr >0", align="right")] if 2 in keep else []),
                    *([TableColumn(key="clicks_active_pct", label="Clicks >0", align="right")] if 3 in keep else []),
                    *([TableColumn(key="total_sales", label="Total Sales", align="right")] if has_sales else []),
                    TableColumn(key="total_cost", label="Total Spend", align="right"),
                ],
                rows=ch_rows,
            ),
            TableBlock(
                id="quality_metric_ranges",
                title="Metric ranges per channel (raw rows)",
                subtitle="Non-missing count, minimum, median and maximum of each metric; a channel without a metric has no row for it.",
                columns=[
                    TableColumn(key="channel", label="Channel"),
                    TableColumn(key="metric", label="Metric"),
                    TableColumn(key="count", label="Count", align="right"),
                    TableColumn(key="min", label="Min", align="right"),
                    TableColumn(key="median", label="Median", align="right"),
                    TableColumn(key="max", label="Max", align="right"),
                ],
                rows=range_rows,
            ),
            TableBlock(
                id="quality_anomaly_inventory",
                title="Data Quality & Structural Anomaly Inventory",
                columns=[
                    TableColumn(key="issue", label="Finding"),
                    TableColumn(key="affected", label="Affected Scope"),
                    TableColumn(key="magnitude", label="Magnitude"),
                    TableColumn(key="treatment", label="Pipeline Treatment"),
                ],
                rows=anomaly_rows,
            ),
        ],
    )


def _wide_column_rows(rc: Any, wide_df: pd.DataFrame) -> list[dict[str, Any]]:
    """The column inventory of a wide upload: the date, the target, each channel's columns and the controls."""
    cols = rc.columns
    n = len(wide_df)
    rows: list[dict[str, Any]] = [
        {"column": cols.date, "dtype": "date", "non_null": n, "null_count": 0, "null_pct": "0.0%", "semantics": "Calendar date, one row per day"},
        {
            "column": cols.target or "—",
            "dtype": "number",
            "non_null": int(wide_df["total_sales"].notna().sum()),
            "null_count": int(wide_df["total_sales"].isna().sum()),
            "null_pct": f"{100.0 * float(wide_df['total_sales'].isna().mean()):.1f}%",
            "semantics": "Target: daily sales the model explains (total_sales)",
        },
    ]
    for ch in cols.wide_channels:
        for attr, what in (("spend", "Daily spend"), ("impressions", "Daily impressions"), ("clicks", "Daily clicks"), ("sales", "Attributed sales")):
            col = getattr(ch, attr)
            if col:
                rows.append(
                    {"column": col, "dtype": "number", "non_null": n, "null_count": 0, "null_pct": "0.0%", "semantics": f"{what} of {ch.name}"}
                )
    for c in cols.controls:
        if c.categories:
            keys_str = ", ".join(f"`{k}`" for k in c.keys)
            rows.append(
                {
                    "column": c.column,
                    "dtype": "category",
                    "non_null": n,
                    "null_count": 0,
                    "null_pct": "0.0%",
                    "semantics": f"Categorical control {keys_str} ({c.name})",
                }
            )
        else:
            rows.append(
                {
                    "column": c.column,
                    "dtype": "number",
                    "non_null": n,
                    "null_count": 0,
                    "null_pct": "0.0%",
                    "semantics": f"Control `{c.key}` ({c.name})",
                }
            )
    return rows
