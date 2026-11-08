"""Modelling readiness: Q4 peak-season anatomy, naive baseline floor, and Phase 1 Q1–Q3 synthesis."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, line_figure
from mmm_studio.charts.theme import NAVY, SLATE, STATUS_DANGER, STATUS_SUCCESS
from mmm_studio.core.as_of import leak_free
from mmm_studio.core.eda.correlations import compute_vif_table
from mmm_studio.core.eda.stats import readiness_diagnostics
from mmm_studio.core.features import complete_years
from mmm_studio.core.meridian.spec import MeridianSpecConfig, channel_label, meridian_roles, meridian_table
from mmm_studio.core.metrics import causal_naive_baselines, in_sample_naive_baselines
from mmm_studio.core.model_settings import DEFAULT_IN_HOME_SHIFT
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.report.facts import billing_links, billing_sentence, compute_case_facts, join_names
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


def _std_without_iqr_outliers(series: pd.Series) -> float:
    """Standard deviation of a scaled series after removing 1.5×IQR outliers (MeridianEDA check_std rule)."""
    arr = series.dropna().to_numpy(dtype=float)
    if arr.size < 2:
        return 0.0
    scale = float(np.std(arr))
    if scale <= 0:
        return 0.0
    normed = arr / scale
    q25, q75 = np.percentile(normed, [25.0, 75.0])
    iqr = q75 - q25
    inliers = normed[(normed >= q25 - 1.5 * iqr) & (normed <= q75 + 1.5 * iqr)]
    if inliers.size < 2:
        return 0.0
    return float(np.std(inliers))


def compute_meridian_pre_modeling_checks(
    wide_df: pd.DataFrame,
    config: DataRoleConfig | None = None,
) -> dict[str, Any]:
    """Compute Meridian's official pre-modeling EDA checks & data-to-parameter ratio (`pre-modeling/perform-eda`)."""
    cfg = config or DataRoleConfig()
    spec_cfg = MeridianSpecConfig.from_role_config(cfg, wide=wide_df) if cfg.channels else MeridianSpecConfig()
    tbl = meridian_table(wide_df, spec_cfg)
    spend_cols, media_names, controls = meridian_roles(tbl, spec_cfg)

    n_geos = 1
    n_times = int(len(tbl))
    n_train_times = max(0, n_times - int(cfg.holdout_days))
    n_knots = int(spec_cfg.knots) if spec_cfg.knots is not None else 1
    n_treatments = int(len(media_names))
    n_controls = int(len(controls))
    n_parameters = (n_geos - 1) + n_knots + n_controls + n_treatments
    ratio_full = float(n_times / max(n_parameters, 1))
    ratio_train = float(n_train_times / max(n_parameters, 1))
    # Contrast with full time-varying knots (n_knots = n_times) on a national model
    n_params_full_knots = n_times + n_controls + n_treatments
    ratio_full_knots = float(n_times / max(n_params_full_knots, 1))

    kpi_std_no_outliers = _std_without_iqr_outliers(wide_df["total_sales"])
    sparse_channels: list[str] = []
    for ch in media_names:
        if ch in tbl and _std_without_iqr_outliers(tbl[ch]) < 1e-4:
            sparse_channels.append(channel_label(ch))

    # National VIF across treatments + controls (Meridian threshold: >10 REVIEW, >1000 FAIL)
    pred_cols = [c for c in [*media_names, *controls] if c in tbl.columns and float(tbl[c].std()) > 0]
    vif_all = compute_vif_table(tbl[pred_cols]) if len(pred_cols) >= 2 else {}
    max_vif_col = max(vif_all, key=vif_all.get) if vif_all else "—"
    max_vif_val = float(vif_all[max_vif_col]) if vif_all else 1.0

    # Pairwise correlation across treatments and controls (Meridian threshold: |r| > 0.70 REVIEW, > 0.999 FAIL)
    max_pair_r = 0.0
    max_pair_names = ("—", "—")
    if len(pred_cols) >= 2:
        corr_mat = tbl[pred_cols].corr().abs()
        for i, c1 in enumerate(pred_cols):
            for c2 in pred_cols[i + 1 :]:
                val = float(corr_mat.loc[c1, c2])
                if np.isfinite(val) and val > max_pair_r:
                    max_pair_r = val
                    max_pair_names = (channel_label(c1), channel_label(c2))

    # Spend share check for paid media channels
    spends = {channel_label(m): float(tbl[s].sum()) for s, m in zip(spend_cols, media_names) if s in tbl}
    total_media_spend = float(sum(spends.values()))
    small_spend_channels = [
        f"{name} ({100.0 * sp / total_media_spend:.1f}%)"
        for name, sp in spends.items()
        if total_media_spend > 0 and (sp / total_media_spend) < 0.02
    ]

    # Print events (pulsed spend on send-out days), counted on the panel, for the treatment-variation guidance
    pulsed = [
        f"{channel_label(s)} ({int((wide_df[f'cost_{s}'] > 0).sum())} send-outs)"
        for s in spec_cfg.print_slugs
        if s in media_names and f"cost_{s}" in wide_df
    ]
    pulsed_guidance = (
        f"{join_names(pulsed)} {'is a pulsed print event' if len(pulsed) == 1 else 'are pulsed print events'} rather than "
        f"continuous spend; binomial adstock + in-home shift spreads the impulse across {spec_cfg.max_lag} days so "
        "Meridian can identify its decay."
        if pulsed
        else "Media spend that is zero on most days (flighted) leaves little variation to identify its effect and decay."
    )
    vif_guidance = (
        "All media and control predictors remain below Meridian's VIF = 10 review threshold."
        if max_vif_val <= 10.0
        else f"{channel_label(max_vif_col)} is largely explained by the other predictors: its effect is hard to separate "
        "from theirs, so expect a wide interval or drop a redundant control."
    )
    pair_guidance = (
        "No pair of predictors moves together above |r| = 0.70."
        if max_pair_r <= 0.70
        else "Predictors that move together above |r| = 0.70 split their credit by the priors, not the data; read their "
        "ROIs with their intervals or calibrate one of them with a lift test."
    )

    checks_rows = [
        {
            "check": "Data-to-parameter ratio (National model)",
            "metric": (
                f"{ratio_train:.1f} obs/param in train ({n_train_times} days / {n_parameters} params: "
                f"{n_knots} knot + {n_treatments} media + {n_controls} controls); {ratio_full:.1f} on full panel"
            ),
            "threshold": "≥ 5.0 PASS (≥ 8.0 ideal; full knots=T would be " + f"{ratio_full_knots:.2f})",
            "status": "PASS" if ratio_train >= 5.0 else "REVIEW",
            "guidance": (
                "National models have 1 geo per timestamp; setting knots=1 + seasonal/calendar controls preserves "
                "degrees of freedom as recommended by Meridian's National Models guide."
            ),
        },
        {
            "check": "KPI variation without IQR outliers",
            "metric": f"σ(inliers) = {kpi_std_no_outliers:.2f} (scaled KPI)",
            "threshold": "σ > 1e-4 PASS",
            "status": "PASS" if kpi_std_no_outliers >= 1e-4 else "FAIL",
            "guidance": "Continuous daily sales variation, not only a few outlier days, gives the likelihood its curvature.",
        },
        {
            "check": "Treatment variation without IQR outliers",
            "metric": (
                f"Pulsed/flighted (IQR = 0): {', '.join(sparse_channels)}"
                if sparse_channels
                else "All media channels have continuous inlier variation"
            ),
            "threshold": "σ > 1e-4 PASS (pulsed channels expected REVIEW)",
            "status": "REVIEW" if sparse_channels else "PASS",
            "guidance": pulsed_guidance,
        },
        {
            "check": "National predictor multicollinearity (VIF)",
            "metric": f"Max VIF = {max_vif_val:.2f} ({channel_label(max_vif_col)})",
            "threshold": "≤ 10 PASS · 10–1000 REVIEW · > 1000 FAIL",
            "status": "FAIL" if max_vif_val > 1000.0 else ("REVIEW" if max_vif_val > 10.0 else "PASS"),
            "guidance": vif_guidance,
        },
        {
            "check": "Pairwise predictor correlation",
            "metric": f"Max |r| = {max_pair_r:.2f} ({max_pair_names[0]} vs {max_pair_names[1]})",
            "threshold": "≤ 0.70 PASS · 0.70–0.999 REVIEW · > 0.999 FAIL",
            "status": "FAIL" if max_pair_r > 0.999 else ("REVIEW" if max_pair_r > 0.70 else "PASS"),
            "guidance": pair_guidance,
        },
        {
            "check": "Low spend-share channels (< 2% of media budget)",
            "metric": ", ".join(small_spend_channels) if small_spend_channels else "None (all paid media ≥ 2% of spend)",
            "threshold": "≥ 2% spend share PASS",
            "status": "REVIEW" if small_spend_channels else "PASS",
            "guidance": (
                "Channels with < 2% of total media spend have low signal-to-noise relative to residual sales variance; "
                "expect wider posterior ROI credible intervals and lower prior-posterior shift."
            ),
        },
    ]

    return {
        "n_times": n_times,
        "n_train_times": n_train_times,
        "n_knots": n_knots,
        "n_treatments": n_treatments,
        "n_controls": n_controls,
        "n_parameters": n_parameters,
        "ratio_full": ratio_full,
        "ratio_train": ratio_train,
        "ratio_full_knots": ratio_full_knots,
        "data_to_parameter_ratio_full": ratio_full,
        "data_to_parameter_ratio_train": ratio_train,
        "full_knots_ratio": ratio_full_knots,
        "kpi_scaled_std_no_outliers": kpi_std_no_outliers,
        "max_vif": max_vif_val,
        "max_pair_r": max_pair_r,
        "checks": checks_rows,
        "checks_rows": checks_rows,
    }


def _demand_rows(wide_df: pd.DataFrame, config: DataRoleConfig | None) -> list[dict[str, str]]:
    """The "costs that follow demand" implication, naming the channels the model treats that way."""
    if config is None:
        return []
    links = billing_links(wide_df, config)
    if not links["upper"] and not links["commission"]:
        return []
    upper = [n for n, _ in links["upper"]]
    actions = []
    if links["commission"]:
        actions.append(f"{links['commission'][0]} as a control")
    if upper:
        actions.append(f"{join_names(upper)} ROI{'s as upper bounds' if len(upper) > 1 else ' as an upper bound'} until tested")
    action = "; ".join(actions)
    return [{"finding": "Costs that follow demand", "evidence": billing_sentence(links), "action": action[0].upper() + action[1:] + "."}]


def build_explore_readiness_section(wide_df: pd.DataFrame, config: DataRoleConfig | None = None) -> Section:
    """Build the `explore_readiness` section: naive baselines, MMM readiness by channel, peak season, implications."""
    y = wide_df["total_sales"]
    # Leak-free: each day is forecast from the days before it; in-sample means also see the day itself
    causal = leak_free(config)
    nb = causal_naive_baselines(y) if causal else in_sample_naive_baselines(y)

    # 1. Peak season: 10 Nov – 31 Dec of the last two complete years, aligned by calendar date
    full = complete_years(wide_df)
    years = sorted(set(pd.DatetimeIndex(full.index).year))[-2:]
    cat = config.print_event_channels[0] if config is not None and config.print_event_channels else None
    wide_layout = config is not None and config.columns.layout == "wide"
    country = config.country_name if config is not None else "Germany"
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

    # 2. Naive baselines vs 15% target threshold
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
    yoy = facts.get("yoy_sales_pct")
    always_on = bool(len(digital_diag)) and bool((digital_diag["active_days"] >= 0.95).all())
    implications_rows = [
        {
            "finding": "Long, shuffled rows; missing channel-days",
            "evidence": ", ".join(f"{k}: {v} days" for k, v in facts.get("missing_channel_days", {}).items()) or "none",
            "action": "Sort and pivot to a regular daily table; missing channel-days count as 0 sales and are flagged.",
        }
        if not wide_layout
        else {
            "finding": "One row per day (wide table)",
            "evidence": f"{len(wide_df)} days without gaps; the target is its own column",
            "action": "Each spend column is a channel's daily cost; the target column is the dependent variable.",
        },
    ]
    if cat:
        implications_rows.append({
            "finding": "Catalog cost booked on the send-out day; effect peaks days later",
            "evidence": (
                f"same-day r = {facts.get('catalog_lag0_r', float('nan')):+.2f}, +{facts.get('catalog_best_lag', 0)} days r = "
                f"{facts.get('catalog_best_lag_r', float('nan')):+.2f}; 7-day halo {halo.get('total_sales', float('nan')):+.1f}%"
            ),
            "action": (
                f"Shift catalog spend to its in-home date (k = {config.print_shift if config else DEFAULT_IN_HOME_SHIFT}, the run "
                f"setting) with a binomial adstock up to {config.model_settings.max_lag if config else 21} days; model total sales."
            ),
        })
    implications_rows += [
        {
            "finding": "Trend, weekly cycle, Q4 peak and holidays",
            "evidence": (
                (f"{yoy:+.1f}% sales year on year; " if yoy is not None and yoy == yoy else "fewer than two complete years; ")
                + f"strongest weekday {facts.get('best_weekday', '—')}"
                + (f" ({facts.get('best_weekday_clean', '—')} without a recent catalog)" if cat else "")
            ),
            "action": (
                "Trend, weekday dummies, yearly Fourier terms, "
                + ("German holidays" if country == "Germany" else f"public holidays ({country})")
                + ", Christmas window and Black Week as controls."
            ),
        },
        {
            "finding": "Budgets follow the season" + ("; digital channels are always on" if always_on else ""),
            "evidence": (
                f"trend + calendar explain {100 * digital_diag['seasonal_r2'].min():.0f}–{100 * digital_diag['seasonal_r2'].max():.0f}% of digital spend variance"
                if len(digital_diag)
                else "—"
            ),
            "action": "Bayesian priors (Meridian) for weakly identified digital effects; read their ROIs with their intervals.",
        },
        *_demand_rows(wide_df, config),
        {
            "finding": "Structural breaks",
            "evidence": (
                f"Social Media from {facts['social_break']}: spend ×{facts['social_spend_ratio']:.1f}, attributed sales ×{facts['social_sales_ratio']:.1f}"
                if "social_break" in facts
                else "none detected"
            ),
            "action": "Interpret Social Media's ROI with care; the hold-out lies in the new regime." if "social_break" in facts else "—",
        },
        {
            "finding": "Target leakage",
            "evidence": (
                "channel sales add up to total sales; clicks and impressions are same-day outcomes"
                if not wide_layout
                else "clicks, impressions and attributed sales are same-day outcomes of demand"
            ),
            "action": "Use only planned costs and the calendar as forecast inputs"
            + (f", plus your controls ({', '.join(c.name for c in config.columns.controls)})." if config is not None and config.columns.controls else "."),
        },
    ]
    nb_by = {r["model"]: float(r["MAE_over_mean"]) for _, r in nb.iterrows()}
    best_naive = min(nb_by, key=nb_by.get)
    pre_checks = compute_meridian_pre_modeling_checks(wide_df, config)

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
                    KpiItem(
                        label="Data-to-parameter ratio",
                        value=f"{pre_checks['ratio_train']:.1f}×",
                        delta=(
                            f"{pre_checks['n_train_times']} train days / {pre_checks['n_parameters']} params "
                            f"(Meridian target ≥ 5.0×)"
                        ),
                        status="success" if pre_checks["ratio_train"] >= 5.0 else "warn",
                    ),
                ]
            ),
            CalloutBlock(
                tone="info",
                title="None of the naive forecasts meets the 15 % target",
                body=(
                    f"Even the best rule, **{best_naive.replace('Naive: ', '')}**, scores **{100 * nb_by[best_naive]:.1f}% MAE / mean** — and it "
                    "needs yesterday's actual sales, which a budget-based forecast weeks ahead does not have. A model is needed that "
                    + ("combines the paid channels (with the catalog's delay)" if cat else "combines the paid channels")
                    + " with calendar, seasonality and trend controls."
                ),
            ),
            ChartBlock(
                id="readiness_baselines",
                title="Naive baselines vs the 15 % target",
                subtitle=(
                    "MAE / mean over the full period for four rules that use no marketing data"
                    + ("; each day is forecast from the days before it." if causal else ".")
                ),
                figure=fig_baselines,
            ),
            ChartBlock(
                id="peak_season",
                title=(
                    "Peak season: catalog Sundays and Black Friday drive the peaks; demand collapses from 23 Dec"
                    if cat
                    else "Peak season: 10 Nov – 31 Dec"
                ),
                subtitle=(
                    "10 Nov – 31 Dec of each year; only two Q4s and two Black Fridays exist, so peak-season effects carry the most uncertainty."
                    if cat
                    else f"10 Nov – 31 Dec of each year in the data ({len(years)}); with few peak seasons, their effects carry the most uncertainty."
                ),
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
                id="readiness_meridian_eda_checks",
                title="Meridian pre-modeling automated EDA checks",
                subtitle=(
                    "Meridian's 5 pre-modeling readiness checks: data-to-parameter degrees of freedom, inlier variation "
                    "(without 1.5×IQR outliers), national VIF, pairwise collinearity, and spend share."
                ),
                columns=[
                    TableColumn(key="check", label="Meridian Check"),
                    TableColumn(key="metric", label="Measured Value"),
                    TableColumn(key="threshold", label="Meridian Threshold"),
                    TableColumn(key="status", label="Status"),
                    TableColumn(key="guidance", label="Modelling Implication"),
                ],
                rows=pre_checks["checks_rows"],
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
                        caption=(
                            "The catalog is pulsed and nearly independent of the calendar: the best-identified medium. Digital channels are always on and seasonal: weakly identified."
                            if cat
                            else "A channel whose spend the trend and calendar explain well, or that moves with another channel, is weakly identified."
                        ),
                    )
                ]
                if diag_rows
                else []
            ),
        ],
    )
