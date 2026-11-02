"""Modelling readiness: Q4 peak-season anatomy, naive baseline floor, and Phase 1 Q1–Q3 synthesis (01b)."""

from __future__ import annotations

import pandas as pd

from mmm_studio.charts.builders import bar_figure, line_figure
from mmm_studio.charts.theme import COPPER, NAVY, SLATE, STATUS_DANGER, STATUS_SUCCESS
from mmm_studio.core.metrics import in_sample_naive_baselines
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


def build_explore_readiness_section(wide_df: pd.DataFrame) -> Section:
    """Build the `explore_readiness` section synthesising Q4 anatomy, naive baselines, and Phase 1 Q1–Q3."""
    y = wide_df["total_sales"]
    nb = in_sample_naive_baselines(y)

    # 1. Q4 Peak-Season Anatomy (Oct–Dec 2023 vs Oct–Dec 2024 aligned by month-day)
    q4_23 = wide_df.loc["2023-10-01":"2023-12-31"].copy()
    q4_24 = wide_df.loc["2024-10-01":"2024-12-31"].copy()
    q4_labels = [d.strftime("%b-%d") for d in q4_24.index]

    fig_q4 = line_figure(
        x=q4_labels,
        series=[
            {"name": "Q4 2023 Daily Sales (k€)", "y": (q4_23["total_sales"] / 1e3).to_numpy(), "color": SLATE, "width": 1.8, "dash": "dot"},
            {"name": "Q4 2024 Daily Sales (k€)", "y": (q4_24["total_sales"] / 1e3).to_numpy(), "color": NAVY, "width": 2.4},
            {"name": "Q4 2024 Digital Spend (k€)", "y": (q4_24["digital_cost"] / 1e3).to_numpy(), "color": COPPER, "width": 1.8},
        ],
        title="Q4 Peak-Season Anatomy: Oct–Dec 2023 vs Oct–Dec 2024 Daily Sales & Digital Spend (k€)",
        xaxis_title="Calendar Date (Oct 1 – Dec 31)",
        yaxis_title="k€ / Day",
        height=380,
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
        title="Naive Forecasting Baselines vs 15.0% Case-Study Target Ceiling (MAE / Mean Sales %)",
        xaxis_title="Baseline Predictor",
        yaxis_title="MAE / Mean Sales (%)",
        hlines=[{"y": 15.0, "color": STATUS_SUCCESS, "width": 2.2, "dash": "dash", "label": "15.0% Target Ceiling"}],
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

    implications_rows = [
        {
            "finding": "Long format with shuffled rows & 44 missing channel-days",
            "evidence": "8,740 rows vs 8,784 expected (TV: 38 missing + 8 <€1, Catalog: 4, Owned: 2); 1 isolated 2025 day",
            "action": "Sort, pivot to regular 731-day wide table (2023–2024), impute 0 with `missing_*` and `tv_anomaly` flags.",
        },
        {
            "finding": "Catalog cost booked on Thursday send-out; sales peak +3 days later",
            "evidence": "Lag-0 r = -0.01 vs Lag +3d r = +0.40; +38.4% 7-day cross-channel halo",
            "action": "Transform `cost_catalog` via delayed binomial adstock (peak +3d, decay 0.88, 21d horizon) + `days_since_catalog`.",
        },
        {
            "finding": "Digital carry-over & diminishing marginal returns",
            "evidence": "Search/Affiliate decay in 1–2 days; Display/Social persist 3–5 days with concave response",
            "action": "Channel-specific geometric adstock + Hill saturation in Meridian (and log-adstock in OLS benchmark).",
        },
        {
            "finding": "Strong weekly rhythm, Q4 peak, and +20.7% YoY baseline growth",
            "evidence": "Sunday peak (+20%), Saturday trough (-23%); Black Week +55%; 2024 vs 2023 +20.7%",
            "action": "Include weekday dummies, 3-harmonic annual Fourier terms, German holiday/Q4 flags, and trend/knots.",
        },
        {
            "finding": "2024 structural breaks in Social Media & Affiliate",
            "evidence": "Social spend jumps 4.7× on 2024-08-01; Affiliate commission rate drops 6.2% → 5.3% in Feb 2024",
            "action": "Include `social_post_aug2024` regime indicator and informative Bayesian ROI priors in Meridian.",
        },
        {
            "finding": "Target leakage in channel sales, clicks, and impressions",
            "evidence": "`sales_by_channel` sums to `total_sales`; clicks & impressions are simultaneous demand outcomes",
            "action": "Exclude channel sales, clicks, and impressions from forecast features; predict strictly from the 7 paid cost levers + calendar.",
        },
    ]

    return Section(
        id="explore_readiness",
        title="Modelling Readiness & Phase 1 Synthesis",
        subtitle="Q4 peak-season anatomy, naive baseline floor, and structured translation from EDA findings to model design.",
        blocks=[
            KpisBlock(
                items=[
                    KpiItem(label="Target Threshold", value="≤ 15.0%", delta="MAE / Mean Daily Sales", status="success"),
                    KpiItem(label="Naive Constant Mean", value="26.1%", delta="Fails target by +11.1 pp", status="danger"),
                    KpiItem(label="Naive Weekday Mean", value="24.9%", delta="Weekly cycle alone insufficient", status="danger"),
                    KpiItem(label="Naive Lag-1 (1-step)", value="19.1%", delta="Unavailable for multi-week horizon", status="warn"),
                ]
            ),
            CalloutBlock(
                tone="info",
                title="Why Naive Baselines Fail the 15% Target",
                body=(
                    "Even a 1-step-ahead yesterday persistence rule (`Lag-1`, which is unavailable when forecasting a full "
                    "quarter from planned media budgets) achieves **19.1% MAE/mean**—well above the **15.0% case-study ceiling**. "
                    "Beating 15% requires combining the **7 paid media levers** (especially delayed Catalog adstock) with "
                    "**calendar seasonality, German retail events, and trend controls**."
                ),
            ),
            ChartBlock(
                id="readiness_baselines",
                title="Naive Baseline Error Floor vs 15.0% Case-Study Target",
                subtitle="Full-series MAE / mean sales (%) for four zero-marketing baseline rules.",
                figure=fig_baselines,
            ),
            ChartBlock(
                id="peak_season",
                title="Q4 Peak-Season Anatomy (Oct–Dec 2023 vs Oct–Dec 2024)",
                subtitle="Hold-out evaluation on Q4 2024 tests the model on the hardest, highest-variance quarter of the year.",
                figure=fig_q4,
            ),
            TableBlock(
                id="readiness_baselines_table",
                title="Naive Baseline Accuracy Summary",
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
                title="Phase 1 Findings → Phase 2/3 Feature & Model Engineering Blueprint",
                columns=[
                    TableColumn(key="finding", label="EDA Finding"),
                    TableColumn(key="evidence", label="Quantitative Evidence"),
                    TableColumn(key="action", label="Design Action in Preparation & Meridian MMM"),
                ],
                rows=implications_rows,
            ),
        ],
    )
