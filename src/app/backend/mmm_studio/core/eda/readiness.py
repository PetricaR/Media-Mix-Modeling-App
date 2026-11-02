"""Modelling readiness: Q4 peak-season anatomy, naive baseline floor, and Phase 1 Q1–Q3 synthesis (01b)."""

from __future__ import annotations

import pandas as pd

from mmm_studio.charts.builders import bar_figure, line_figure
from mmm_studio.charts.theme import NAVY, SLATE, STATUS_DANGER, STATUS_SUCCESS
from mmm_studio.core.eda.notebook_stats import readiness_diagnostics
from mmm_studio.core.features import complete_years
from mmm_studio.core.metrics import in_sample_naive_baselines
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.report.facts import compute_case_facts
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


def build_explore_readiness_section(wide_df: pd.DataFrame, config: DataRoleConfig | None = None) -> Section:
    """Build the `explore_readiness` section: naive baselines, MMM readiness by channel, peak season, implications."""
    y = wide_df["total_sales"]
    nb = in_sample_naive_baselines(y)

    # 1. Peak season: 10 Nov – 31 Dec of the last two complete years, aligned by calendar date
    full = complete_years(wide_df)
    years = sorted(set(pd.DatetimeIndex(full.index).year))[-2:]
    q4 = {yr: full.loc[f"{yr}-11-10":f"{yr}-12-31"] for yr in years}
    ref = q4[years[-1]] if years else full.iloc[0:0]
    fig_q4 = line_figure(
        x=[d.strftime("%d %b") for d in ref.index],
        series=[
            {"name": f"{yr} daily sales (k€)", "y": (q4[yr]["total_sales"].reindex(q4[yr].index) / 1e3).round(1).tolist(), "color": NAVY if yr == years[-1] else SLATE, "width": 2.2 if yr == years[-1] else 1.6}
            for yr in years
        ],
        xaxis_title="Date",
        yaxis_title="k€ / day",
        height=360,
    )

    # 2. Naive baselines vs 15% case-study target threshold
    b_models = nb["model"].tolist()
    b_pcts = [round(100.0 * float(v), 1) for v in nb["MAE_over_mean"].tolist()]

    fig_baselines = bar_figure(
        categories=b_models,
        series=[
            {
                "name": "MAE / Mean Sales (%)",
                "values": b_pcts,
                "colors": [STATUS_DANGER if v > 15.0 else STATUS_SUCCESS for v in b_pcts],
            }
        ],
        xaxis_title="Baseline rule",
        yaxis_title="MAE / Mean Sales (%)",
        hlines=[{"y": 15.0, "color": STATUS_SUCCESS, "width": 2.2, "dash": "dash", "label": "target ≤ 15 %"}],
        showlegend=False,
        height=350,
    )

    nb_rows = []
    for _, row in nb.iterrows():
        pct = 100.0 * float(row["MAE_over_mean"])
        nb_rows.append(
            {
                "model": str(row["model"]),
                "mae": fmt_eur(float(row["MAE"])),
                "rmse": fmt_eur(float(row["RMSE"])),
                "mae_over_mean": fmt_pct(pct),
                "mape": fmt_pct(100.0 * float(row["MAPE"])),
                "status": "Fails (>15%)" if pct > 15.0 else "Meets (≤15%)",
            }
        )

    facts = compute_case_facts(wide_df, config) if config is not None else {}
    diag = readiness_diagnostics(wide_df, config) if config is not None else pd.DataFrame()
    diag_rows = [
        {
            "channel": ch,
            "active_days": f"{100 * r['active_days']:.0f}%",
            "cv": f"{r['cv']:.2f}",
            "seasonal_r2": f"{100 * r['seasonal_r2']:.0f}%",
            "max_r_other": f"{r['max_r_other']:.2f}",
            "r_same_day": f"{r['r_same_day']:.2f}",
            "r_best_lag": f"{r['r_best_lag']:.2f}",
            "best_lag": int(r["best_lag"]),
        }
        for ch, r in diag.iterrows()
    ]
    digital_diag = diag.drop(index=[c for c in (config.print_event_channels if config else []) if c in diag.index], errors="ignore")
    halo = facts.get("halo_7d", {})
    implications_rows = [
        {
            "finding": "Long, shuffled rows; missing channel-days",
            "evidence": ", ".join(f"{k}: {v} days" for k, v in facts.get("missing_channel_days", {}).items()) or "none",
            "action": "Sort and pivot to a regular daily table; missing channel-days count as 0 sales and are flagged.",
        },
        {
            "finding": "Catalog cost booked on the send-out day; effect peaks days later",
            "evidence": (
                f"same-day r = {facts.get('catalog_lag0_r', float('nan')):+.2f}, +{facts.get('catalog_best_lag', 0)} days r = "
                f"{facts.get('catalog_best_lag_r', float('nan')):+.2f}; 7-day halo {halo.get('total_sales', float('nan')):+.1f}%"
            ),
            "action": "Shift catalog spend to its in-home date (k = 2) with a binomial adstock up to 21 days; model total sales.",
        },
        {
            "finding": "Trend, weekly cycle, Q4 peak and holidays",
            "evidence": (
                f"{facts.get('yoy_sales_pct', float('nan')):+.1f}% sales year on year; strongest weekday {facts.get('best_weekday', '—')} "
                f"({facts.get('best_weekday_clean', '—')} without a recent catalog)"
            ),
            "action": "Trend, weekday dummies, yearly Fourier terms, German holidays, Christmas window and Black Week as controls.",
        },
        {
            "finding": "Budgets follow the season; digital channels are always on",
            "evidence": (
                f"trend + calendar explain {100 * digital_diag['seasonal_r2'].min():.0f}–{100 * digital_diag['seasonal_r2'].max():.0f}% of digital spend variance"
                if len(digital_diag)
                else "—"
            ),
            "action": "Bayesian priors (Meridian) for weakly identified digital effects; read their ROIs with their intervals.",
        },
        {
            "finding": "Costs that follow demand",
            "evidence": f"Affiliate r(cost, own sales) = {facts.get('affiliate_cost_sales_r', float('nan')):.2f}; SEA r(cost, clicks) = {facts.get('sea_cost_clicks_r', float('nan')):.2f}",
            "action": "Affiliate as a control; SEA's ROI as an upper bound until tested.",
        },
        {
            "finding": "Structural breaks",
            "evidence": (
                f"Social Media from {facts['social_break']}: spend ×{facts['social_spend_ratio']:.1f}, attributed sales ×{facts['social_sales_ratio']:.1f}"
                if "social_break" in facts
                else "none detected"
            ),
            "action": "Interpret Social Media's ROI with care; the hold-out lies in the new regime.",
        },
        {
            "finding": "Target leakage",
            "evidence": "channel sales add up to total sales; clicks and impressions are same-day outcomes",
            "action": "Use only planned costs and the calendar as forecast inputs.",
        },
    ]
    nb_by = {r["model"]: float(r["MAE_over_mean"]) for _, r in nb.iterrows()}
    best_naive = min(nb_by, key=nb_by.get)

    return Section(
        id="explore_readiness",
        title="MMM readiness",
        subtitle="Why a model is needed (naive baselines), how well each channel can be identified, and what the EDA implies for modelling.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(
                        label="Target",
                        value="≤ 15%",
                        delta=f"MAE / mean daily sales: an error budget of €{0.15 * float(y.mean()):,.0f}/day",
                        status="success",
                    ),
                    *[
                        KpiItem(
                            label=name.replace("Naive: ", "Naive · "),
                            value=f"{100 * v:.1f}%",
                            delta=f"{100 * (v - 0.15):+.1f} pp vs target",
                            status="danger" if v > 0.15 else "success",
                        )
                        for name, v in [*list(nb_by.items())[:2], (best_naive, nb_by[best_naive])]
                    ],
                ]
            ),
            CalloutBlock(
                tone="info",
                title="None of the naive forecasts meets the 15 % target",
                body=(
                    f"Even the best rule, **{best_naive.replace('Naive: ', '')}**, scores **{100 * nb_by[best_naive]:.1f}% MAE / mean** — and it "
                    "needs yesterday's actual sales, which a budget-based forecast weeks ahead does not have. A model is needed that "
                    "combines the paid channels (with the catalog's delay) with calendar, seasonality and trend controls."
                ),
            ),
            ChartBlock(
                id="readiness_baselines",
                title="Naive baselines vs the 15 % target",
                subtitle="MAE / mean over the full period for four rules that use no marketing data.",
                figure=fig_baselines,
            ),
            ChartBlock(
                id="peak_season",
                title="Peak season: catalog Sundays and Black Friday drive the peaks; demand collapses from 23 Dec",
                subtitle="10 Nov – 31 Dec of each year; only two Q4s and two Black Fridays exist, so peak-season effects carry the most uncertainty.",
                figure=fig_q4,
            ),
            TableBlock(
                id="readiness_baselines_table",
                title="Naive baselines",
                columns=[
                    TableColumn(key="model", label="Baseline Rule"),
                    TableColumn(key="mae", label="MAE (€/day)", align="right"),
                    TableColumn(key="rmse", label="RMSE (€/day)", align="right"),
                    TableColumn(key="mae_over_mean", label="MAE / Mean", align="right"),
                    TableColumn(key="mape", label="MAPE", align="right"),
                    TableColumn(key="status", label="vs 15% Target"),
                ],
                rows=nb_rows,
            ),
            TableBlock(
                id="readiness_implications_table",
                title="Implications for data preparation and modelling",
                columns=[
                    TableColumn(key="finding", label="Finding"),
                    TableColumn(key="evidence", label="Evidence"),
                    TableColumn(key="action", label="Action"),
                ],
                rows=implications_rows,
            ),
            *(
                [
                    TableBlock(
                        id="readiness_channel_diagnostics",
                        title="MMM readiness by paid channel",
                        subtitle="An MMM separates channel effects only if spend moves independently of other channels and of the season.",
                        columns=[
                            TableColumn(key="channel", label="Channel"),
                            TableColumn(key="active_days", label="Active days", align="right"),
                            TableColumn(key="cv", label="CV daily spend", align="right"),
                            TableColumn(key="seasonal_r2", label="Spend variance explained by trend + calendar", align="right"),
                            TableColumn(key="max_r_other", label="Max |r| with another channel", align="right"),
                            TableColumn(key="r_same_day", label="r with sales (same day)", align="right"),
                            TableColumn(key="r_best_lag", label="r with sales (best lag 0–21)", align="right"),
                            TableColumn(key="best_lag", label="Best lag", align="right"),
                        ],
                        rows=diag_rows,
                        caption="The catalog is pulsed and nearly independent of the calendar: the best-identified medium. Digital channels are always on and seasonal: weakly identified.",
                    )
                ]
                if diag_rows
                else []
            ),
        ],
    )
