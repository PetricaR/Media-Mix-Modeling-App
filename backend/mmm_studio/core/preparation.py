"""Data preparation and feature engineering."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, line_figure, small_multiples_figure
from mmm_studio.charts.theme import COPPER, INK_MUTED, NAVY, SLATE, TEAL, channel_color
from mmm_studio.core.as_of import leak_free, scoring_cutoff
from mmm_studio.core.features import (
    CATALOG_INHOME_DECAY,
    CATALOG_MAX_LAG,
    DEFAULT_CATALOG_SHIFT,
    binomial_decay_weights,
    calendar_controls,
    catalog_inhome_adstock,
    convolve_weights,
    days_since_send_out,
    geometric_decay_weights,
    seasonality_controls,
    shift_spend,
)
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.report.facts import billing_links, billing_sentence, join_names
from mmm_studio.report.narrative import fmt_eur
from mmm_studio.report.sections import (
    CalloutBlock,
    ChartBlock,
    KpiItem,
    KpisBlock,
    MarkdownBlock,
    Section,
    TableBlock,
    TableColumn,
)

SHIFT_SCAN_GRID: dict[str, list[float]] = {
    "geometric": [0.5, 0.6, 0.7, 0.8, 0.85, 0.9],
    "binomial": [0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
}


def _channel_lists(panel: pd.DataFrame, config: DataRoleConfig | None) -> tuple[list[str], str | None, dict[str, str]]:
    """Return `(digital cost columns, catalog cost column, {cost column: display name})` in channel order."""
    if config is not None and config.paid_channels:
        slug_map = config.slug_map()
        paid = list(config.paid_channels)
        prints = list(config.print_event_channels)
    else:
        from mmm_studio.core.schema import slugify_channel
        from mmm_studio.core.tables import CANONICAL_PAID_CHANNELS

        paid = [c for c in CANONICAL_PAID_CHANNELS if f"cost_{slugify_channel(c)}" in panel.columns]
        slug_map = {c: slugify_channel(c) for c in paid}
        prints = ["Catalog"] if "Catalog" in paid else []
    names = {f"cost_{slug_map[c]}": c for c in paid if f"cost_{slug_map[c]}" in panel.columns}
    catalog_col = next((f"cost_{slug_map[c]}" for c in prints if f"cost_{slug_map[c]}" in panel.columns), None)
    digital = [col for col in names if col != catalog_col]
    return digital, catalog_col, names


def _calendar(panel: pd.DataFrame, config: DataRoleConfig | None) -> pd.DataFrame:
    """The calendar controls with the run's holiday country, plus the user's own controls (`ctrl_*`)."""
    idx = pd.DatetimeIndex(panel.index)
    groups = ("weekdays", "holidays", "christmas", "black_week")
    if config is not None and not config.model_settings.calendar.holidays:
        groups = ("weekdays", "christmas", "black_week")
    cal = calendar_controls(idx, groups=groups, country=config.country if config is not None else "DE")
    keys = [k for k in (config.control_keys if config is not None else []) if k in panel.columns]
    return cal.join(panel[keys].astype(float)) if keys else cal


def _print_shift(config: DataRoleConfig | None) -> int:
    """The run's in-home shift of the print-event channel (the default without a config)."""
    return config.print_shift if config is not None else DEFAULT_CATALOG_SHIFT


def _ols(y: pd.Series, x: pd.DataFrame, hac: bool = False) -> Any:
    import statsmodels.api as sm

    model = sm.OLS(y, sm.add_constant(x, has_constant="add"))
    return model.fit(cov_type="HAC", cov_kwds={"maxlags": 7}) if hac else model.fit()


def compute_preparation_ols_ladder(
    panel: pd.DataFrame,
    config: DataRoleConfig | None = None,
) -> list[dict[str, Any]]:
    """In-sample adjusted-R² ladder (raw costs → + calendar → + seasonality → catalog in-home)."""
    digital, catalog_col, _ = _channel_lists(panel, config)
    y = panel["total_sales"]
    idx = pd.DatetimeIndex(panel.index)
    cal = _calendar(panel, config)
    seas = seasonality_controls(idx)
    raw = panel[digital + ([catalog_col] if catalog_col else [])]
    steps: list[tuple[str, pd.DataFrame]] = [
        (f"Raw costs ({raw.shape[1]} channels)", raw),
        ("+ calendar controls" + (" & your controls" if config is not None and config.control_keys else ""), raw.join(cal)),
        ("+ seasonality & trend", raw.join(cal).join(seas)),
    ]
    if catalog_col:
        steps.append(
            (
                "Catalog raw → in-home adstock",
                panel[digital].join(cal).join(seas).join(catalog_inhome_adstock(panel[catalog_col], shift=_print_shift(config))),
            )
        )
    rows = []
    for name, x in steps:
        fit = _ols(y, x)
        resid = np.asarray(fit.resid, dtype=float)
        mae = float(np.mean(np.abs(resid)))
        rows.append(
            {
                "step": name,
                "r2": float(fit.rsquared),
                "adj_r2": float(fit.rsquared_adj),
                "aic": float(fit.aic),
                "mae": mae,
                "rmse": float(np.sqrt(np.mean(resid**2))),
                "mae_over_mean": mae / float(y.mean()),
            }
        )
    return rows


def compute_catalog_shift_scan(panel: pd.DataFrame, config: DataRoleConfig | None = None) -> pd.DataFrame:
    """Correlation of the residual (sales ~ calendar + seasonality + digital costs) with shifted, adstocked catalog spend.

    Returns one row per (shift k, decay family) with the best correlation over the α grid.
    """
    digital, catalog_col, _ = _channel_lists(panel, config)
    if not catalog_col:
        return pd.DataFrame(columns=["k", "decay", "alpha", "corr"])
    idx = pd.DatetimeIndex(panel.index)
    ctrl = _calendar(panel, config).join(seasonality_controls(idx))
    resid = _ols(panel["total_sales"], ctrl.join(panel[digital])).resid
    rows = []
    for k in range(0, 6):
        shifted = shift_spend(panel[catalog_col], k)
        for decay, fn in (("geometric", geometric_decay_weights), ("binomial", binomial_decay_weights)):
            for a in SHIFT_SCAN_GRID[decay]:
                ad = convolve_weights(shifted, fn(a, CATALOG_MAX_LAG))
                rows.append({"k": k, "decay": decay, "alpha": a, "corr": float(np.corrcoef(resid, ad)[0, 1])})
    scan = pd.DataFrame(rows)
    return scan.loc[scan.groupby(["k", "decay"])["corr"].idxmax()].reset_index(drop=True)


def compute_channel_importance(panel: pd.DataFrame, config: DataRoleConfig | None = None) -> tuple[pd.DataFrame, float]:
    """Standardised OLS effects with Newey-West (HAC, 7 lags) intervals and contribution shares."""
    digital, catalog_col, names = _channel_lists(panel, config)
    idx = pd.DatetimeIndex(panel.index)
    y = panel["total_sales"]
    x = panel[digital].copy()
    media = list(digital)
    labels = {c: names[c] for c in digital}
    if catalog_col:
        x = x.join(catalog_inhome_adstock(panel[catalog_col], shift=_print_shift(config)))
        media.append("catalog_inhome_adstock")
        labels["catalog_inhome_adstock"] = names[catalog_col]
    x = x.join(_calendar(panel, config)).join(seasonality_controls(idx))
    fit = _ols(y, x, hac=True)
    sd = x[media].std()
    conf = fit.conf_int()
    out = pd.DataFrame(
        {
            "std_effect": fit.params[media] * sd,
            "ci_low": conf.loc[media, 0] * sd,
            "ci_high": conf.loc[media, 1] * sd,
            "p_value": fit.pvalues[media],
            "contribution_share": fit.params[media] * x[media].mean() / float(y.mean()),
        }
    ).rename(index=labels)
    return out.sort_values("std_effect"), float(fit.rsquared)


def compute_digital_vif(panel: pd.DataFrame, config: DataRoleConfig | None = None) -> dict[str, float]:
    """Variance inflation factors of the raw digital costs (with a constant)."""
    import statsmodels.api as sm
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    digital, _, names = _channel_lists(panel, config)
    if len(digital) < 2:
        return {names[c]: 1.0 for c in digital}
    v = sm.add_constant(panel[digital])
    return {
        names[c]: float(variance_inflation_factor(v.values, i))
        for i, c in enumerate(v.columns)
        if c != "const"
    }


def build_preparation_section(
    wide_df: pd.DataFrame,
    config: DataRoleConfig,
) -> Section:
    """Build the `preparation` section on the full modelling panel.

    With leak-free validation the scans and fits that inform modelling choices (in-home shift scan, measured catalog
    response, saturation curves, feature ladder, channel importance, VIF) use only `fit_panel`, the days before the test
    window, so a choice made from this page never sees the window it is scored on. Descriptive tables use every day.
    """
    panel = wide_df
    idx = pd.DatetimeIndex(panel.index)
    fit_until = scoring_cutoff(idx, config.holdout_days) if leak_free(config) else None
    fit_panel = panel.loc[:fit_until] if fit_until is not None else panel
    fit_idx = pd.DatetimeIndex(fit_panel.index)
    digital, catalog_col, names = _channel_lists(panel, config)
    slug_map = config.slug_map()
    unpaid = list(config.unpaid_channels)
    shift = _print_shift(config)
    print_name = names[catalog_col] if catalog_col else "Catalog"
    links = billing_links(panel, config)
    billing = billing_sentence(links)
    upper_names = [n for n, _ in links["upper"]]
    commission_name = links["commission"][0] if links["commission"] else None

    # 1. The wide daily table the brief asks for: one cost column per channel + total sales.
    cost_rows = []
    for ch in config.active_channels:
        col = f"cost_{slug_map[ch]}"
        s = panel[col] if col in panel.columns else pd.Series(0.0, index=idx)
        cost_rows.append(
            {
                "column": f"cost_{slug_map[ch]}",
                "mean": fmt_eur(float(s.mean())),
                "median": fmt_eur(float(s.median())),
                "p95": fmt_eur(float(s.quantile(0.95))),
                "max": fmt_eur(float(s.max())),
                "zero_days": int((s == 0).sum()),
            }
        )
    zero_cols = [r["column"] for r in cost_rows if r["zero_days"] == len(panel)]

    # 2. Catalog: booked on the send-out day, effect over the following weeks.
    charts: list[ChartBlock] = []
    best_k_text = "—"
    scan = compute_catalog_shift_scan(fit_panel, config)
    if catalog_col:
        send_outs = idx[panel[catalog_col] > 0]
        win = panel.loc[: idx[-1]]
        win = win[win.index >= (pd.DatetimeIndex(win.index)[-1] - pd.Timedelta(days=138))]
        win_send = [d.strftime("%Y-%m-%d") for d in pd.DatetimeIndex(win.index) if d in send_outs]
        charts.append(
            ChartBlock(
                id="prep_catalog_booking_vs_effect",
                title="Catalog cost is booked in one lump; sales respond over the following days",
                subtitle="Last ~4.5 months of the panel: catalog cost (€) on the send-out day vs daily total sales (€).",
                figure=small_multiples_figure(
                    panels=[
                        {
                            "title": "Catalog cost (€), booked on the send-out day",
                            "x": [d.strftime("%Y-%m-%d") for d in win.index],
                            "traces": [{"name": "Catalog cost", "y": win[catalog_col].round(0).tolist(), "kind": "bar", "color": COPPER}],
                        },
                        {
                            "title": "Daily total sales (€)",
                            "x": [d.strftime("%Y-%m-%d") for d in win.index],
                            "traces": [{"name": "Total sales", "y": win["total_sales"].round(0).tolist(), "color": NAVY}],
                        },
                    ],
                    ncols=1,
                    height_per_row=200,
                ),
                caption=f"Send-outs in the window: {', '.join(win_send) or 'none'}.",
            )
        )

        # Measured catalog response vs Meridian's lag-0-peaked decay, with and without the in-home shift
        dss = days_since_send_out(fit_idx, fit_idx[fit_panel[catalog_col] > 0])
        cat_sales_col = f"sales_{slug_map[names[catalog_col]]}"
        prof = fit_panel[cat_sales_col].groupby(dss).mean().reindex(range(CATALOG_MAX_LAG + 1))
        prof = (prof - prof.loc[15:21].mean()).clip(lower=0)
        prof = prof / prof.max() if prof.max() > 0 else prof
        geo = geometric_decay_weights(CATALOG_INHOME_DECAY, CATALOG_MAX_LAG)
        geo_shift = np.r_[np.zeros(shift), geo[: len(geo) - shift]]
        lags = list(range(CATALOG_MAX_LAG + 1))
        peak_day = int(prof.fillna(0).idxmax()) if prof.max() > 0 else 0
        charts.append(
            ChartBlock(
                id="prep_catalog_adstock_shift",
                title="Shifting catalog spend to the in-home date lines Meridian's decay up with the real response",
                subtitle=(
                    f"Meridian's geometric and binomial decays both peak at lag 0, but the measured {print_name} response peaks "
                    f"{peak_day} day{'s' if peak_day != 1 else ''} after send-out. The models move its spend {shift} "
                    f"day{'s' if shift != 1 else ''} forward (the run setting)."
                ),
                figure=bar_figure(
                    categories=lags,
                    series=[
                        {"name": "Measured catalog response (normalised)", "values": prof.fillna(0).round(3).tolist(), "color": COPPER},
                    ],
                    xaxis_title="Days since send-out",
                    yaxis_title="Relative effect",
                    height=340,
                    extra_traces=[
                        {"type": "scatter", "mode": "lines+markers", "name": "Geometric decay α = 0.8, no shift", "x": lags, "y": np.round(geo, 3).tolist(), "line": {"color": INK_MUTED, "width": 2}},
                        {"type": "scatter", "mode": "lines+markers", "name": f"Same decay after a {shift}-day in-home shift", "x": lags, "y": np.round(geo_shift / geo_shift.max(), 3).tolist(), "line": {"color": NAVY, "width": 2}},
                    ],
                ),
            )
        )
        if len(scan):
            piv = scan.pivot(index="k", columns="decay", values="corr")
            best = scan.loc[scan["corr"].idxmax()]
            best_k_text = f"k = {int(best['k'])} ({best['decay']}, α = {best['alpha']:.2f}, r = {best['corr']:.3f})"
            charts.append(
                ChartBlock(
                    id="prep_shift_scan",
                    title="Choosing the in-home shift k from the data",
                    subtitle=(
                        "Correlation between the residual of sales ~ calendar + seasonality + digital costs and the shifted, "
                        "adstocked catalog spend (best decay rate α per k)."
                    ),
                    figure=line_figure(
                        x=[int(k) for k in piv.index],
                        series=[
                            {"name": dec, "y": piv[dec].round(3).tolist(), "color": NAVY if dec == "binomial" else TEAL, "mode": "lines+markers"}
                            for dec in piv.columns
                        ],
                        xaxis_title="Shift k (days after send-out)",
                        yaxis_title="Correlation with residual",
                        height=320,
                    ),
                    caption=(
                        f"Best fit: {best_k_text}. The models use k = {shift} (the run setting); the selection grid on the "
                        "Model page compares shifts on the validation window before the hold-out."
                    ),
                )
            )

    # 3. Saturation: own attributed sales vs daily cost deciles (only when the data has sales per channel)
    sat_panels = []
    for col in digital if config.has_channel_sales else []:
        ch = names[col]
        s_col = f"sales_{slug_map[ch]}"
        if s_col not in fit_panel.columns:
            continue
        bins = pd.qcut(fit_panel[col], 10, duplicates="drop")
        binned = pd.DataFrame({"cost": fit_panel[col], "sales": fit_panel[s_col]}).groupby(bins, observed=True).mean()
        sat_panels.append(
            {
                "title": ch,
                "x": (binned["cost"] / 1e3).round(2).tolist(),
                "traces": [{"name": ch, "y": (binned["sales"] / 1e3).round(2).tolist(), "color": channel_color(ch), "mode": "lines+markers"}],
                "xaxis_title": "Daily cost (k€)",
            }
        )
    if sat_panels:
        charts.append(
            ChartBlock(
                id="prep_saturation",
                title="Own attributed sales vs daily cost (cost deciles)",
                subtitle="Mostly near-linear in the observed range; saturation matters once a scenario goes beyond today's spend.",
                figure=small_multiples_figure(panels=sat_panels, ncols=3, height_per_row=220),
                caption="These are attributed sales, which also move with the season, so the curves are only indicative.",
            )
        )

    # 4. Feature ladder, channel importance, VIF
    ladder = compute_preparation_ols_ladder(fit_panel, config)
    charts.append(
        ChartBlock(
            id="prep_r2_ladder",
            title="What each feature group adds: in-sample adjusted R² of a plain OLS",
            subtitle="Out-of-sample validation follows in the Meridian and benchmark pages.",
            figure=bar_figure(
                categories=[r["step"] for r in ladder],
                series=[{"name": "Adjusted R²", "values": [round(r["adj_r2"], 3) for r in ladder], "color": NAVY, "text": [f"{r['adj_r2']:.2f}" for r in ladder]}],
                yaxis_title="Adjusted R²",
                height=330,
                showlegend=False,
            ),
        )
    )
    importance, imp_r2 = compute_channel_importance(fit_panel, config)
    charts.append(
        ChartBlock(
            id="prep_channel_importance",
            title="Which channels matter most? Standardised OLS effect with 95% HAC interval",
            subtitle=f"Sales effect of a one-standard-deviation change in (adstocked) spend; OLS R² = {imp_r2:.3f}, Newey-West errors (7 lags).",
            figure=bar_figure(
                categories=importance.index.tolist(),
                series=[
                    {
                        "name": "Standardised effect (€/day)",
                        "values": importance["std_effect"].round(0).tolist(),
                        "colors": [channel_color(c) for c in importance.index],
                        "error_x": {
                            "type": "data",
                            "symmetric": False,
                            "array": (importance["ci_high"] - importance["std_effect"]).round(0).tolist(),
                            "arrayminus": (importance["std_effect"] - importance["ci_low"]).round(0).tolist(),
                        },
                    }
                ],
                orientation="h",
                xaxis_title="Sales effect of +1 SD spend (€ per day)",
                vlines=[{"x": 0, "color": SLATE, "dash": "solid"}],
                height=340,
                showlegend=False,
            ),
            caption=(
                f"Preliminary and observational: {billing}, so {'their effects are' if len(upper_names) + bool(commission_name) > 1 else 'its effect is'} "
                "inflated by reverse causality."
                if billing
                else "Preliminary and observational: budgets follow expected demand, so effects are partly reverse causality."
            ),
        )
    )
    vif = compute_digital_vif(fit_panel, config)
    digital_monthly = fit_panel[digital].sum(axis=1).resample("ME").sum()
    monthly_corr = float(digital_monthly.corr(fit_panel["total_sales"].resample("ME").sum()))
    charts.append(
        ChartBlock(
            id="prep_vif",
            title="Multicollinearity of the digital costs (VIF)",
            subtitle=(
                f"All VIFs ≤ {max(vif.values()):.1f}: only moderate collinearity. Monthly digital spend vs monthly sales: r = {monthly_corr:.2f}."
                if vif and max(vif.values()) < 5
                else f"Largest VIF {max(vif.values()):.1f}: collinear costs blur the split between channels. Monthly spend vs monthly sales: r = {monthly_corr:.2f}."
                if vif
                else None
            ),
            figure=bar_figure(
                categories=list(vif.keys()),
                series=[{"name": "VIF", "values": [round(v, 2) for v in vif.values()], "colors": [channel_color(c) for c in vif]}],
                yaxis_title="Variance inflation factor",
                hlines=[{"y": 5.0, "color": COPPER, "dash": "dash", "label": "VIF = 5"}],
                height=300,
                showlegend=False,
            ),
        )
    )

    # 5. Tables
    ladder_rows = [
        {
            "step": r["step"],
            "r2": f"{r['r2']:.3f}",
            "adj_r2": f"{r['adj_r2']:.3f}",
            "mae_over_mean": f"{100.0 * r['mae_over_mean']:.1f}%",
            "aic": f"{r['aic']:,.0f}",
        }
        for r in ladder
    ]
    importance_rows = [
        {
            "channel": ch,
            "std_effect": f"{r['std_effect']:,.0f}",
            "ci": f"[{r['ci_low']:,.0f}, {r['ci_high']:,.0f}]",
            "p_value": f"{r['p_value']:.3f}",
            "contribution_share": f"{100.0 * r['contribution_share']:.1f}%",
        }
        for ch, r in importance.iloc[::-1].iterrows()
    ]
    scan_rows = []
    if len(scan):
        for k, grp in scan.groupby("k"):
            row: dict[str, Any] = {"k": int(k)}
            for _, r in grp.iterrows():
                row[r["decay"]] = f"{r['corr']:.3f} (α={r['alpha']:.2f})"
            scan_rows.append(row)

    def _r(a: pd.Series, b: pd.Series) -> float:
        return float(a.corr(b)) if a.std() > 0 and b.std() > 0 else float("nan")

    psm_mean = float(panel["cost_psm"].mean()) if "cost_psm" in panel else float("nan")
    cat_sends = panel[catalog_col][panel[catalog_col] > 0] if catalog_col else pd.Series(dtype=float)
    cost_topics = _generic_cost_topics(config, billing, upper_names, commission_name) if not catalog_col else [
        {
            "topic": "Booking date",
            "observation": "Cost is booked on the day of causation, e.g. the whole catalog cost on its send-out day",
            "handling": f"{print_name} in-home shift (k = {shift}) and adstock with max_lag = {CATALOG_MAX_LAG}",
        },
        {
            "topic": "Carry-over",
            "observation": "Effects last days to weeks (about three weeks for the catalog)",
            "handling": "Meridian adstock: geometric, binomial for the catalog",
        },
        {
            "topic": "Saturation",
            "observation": "Mostly near-linear in the observed range (cost-decile curves above)",
            "handling": "Meridian Hill curve per channel; essential for scenarios beyond today's spend",
        },
        {
            "topic": "Scale",
            "observation": (
                f"From ~{fmt_eur(psm_mean)}/day (PSM) to {fmt_eur(float(cat_sends.min()))}–{fmt_eur(float(cat_sends.max()))} per catalog send-out"
                if len(cat_sends) and psm_mean == psm_mean
                else "Spend levels differ by orders of magnitude between channels"
            ),
            "handling": "Meridian scales each channel internally",
        },
        {
            "topic": "Zero vs missing",
            "observation": "No cost row means 0 spend for the catalog, but 'not applicable' for unpaid channels",
            "handling": "Unpaid channels excluded as levers; catalog off-days set to 0",
        },
        {
            "topic": "Billing model",
            "observation": billing or "No channel's cost follows its own clicks or sales",
            "handling": "; ".join(
                x
                for x in (
                    f"{join_names(upper_names)} ROI{'s are upper bounds' if len(upper_names) > 1 else ' is an upper bound'}" if upper_names else "",
                    f"{commission_name} enters Meridian as a control" if commission_name else "",
                )
                if x
            )
            or "Costs enter Meridian as media",
        },
        {
            "topic": "Forecasting",
            "observation": "Spend for the days ahead must be known in advance, i.e. the planned budget",
            "handling": "Spend is the media input, so the model runs on a budget plan",
        },
    ]
    # The model's own features and Meridian table, from the run's settings (the same spec the Model page fits)
    from mmm_studio.core.meridian.spec import MeridianSpecConfig, meridian_roles, meridian_table

    ms = config.model_settings
    spec = MeridianSpecConfig.from_role_config(config, wide=panel)
    m_spend, _, m_controls = meridian_roles(meridian_table(panel, spec), spec)
    calendar_text = {
        "weekdays": "weekday dummies (Monday = reference)",
        "holidays": "holiday (German public holidays)" if config.country == "DE" else f"holiday ({config.country_name} public holidays)",
        "christmas": "christmas (20–31 Dec), half_day (24 and 31 Dec)",
        "black_week": "black_week (Black Friday → Cyber Monday)",
    }
    seasonal_parts = [f"yearly Fourier terms sin_k, cos_k (k = {', '.join(str(k) for k in range(1, ms.n_fourier + 1))})"] if ms.n_fourier else []
    if ms.trend:
        seasonal_parts.append("trend (years since start)")
    feature_rows = []
    if catalog_col:
        feature_rows.append(
            {
                "feature": f"{print_name} in-home spend",
                "columns": f"{slug_map[print_name]}_spend shifted by k = {shift} days",
                "why": f"Aligns the {print_name} lump with its real effect; feeds Meridian's adstock",
            }
        )
    if ms.calendar.groups():
        feature_rows.append(
            {
                "feature": "Calendar controls",
                "columns": ", ".join(calendar_text[g] for g in ms.calendar.groups()),
                "why": "The weekly cycle, holidays, the Christmas dip and Black Friday",
            }
        )
    if seasonal_parts:
        feature_rows.append(
            {
                "feature": "Seasonality and trend",
                "columns": "; ".join(seasonal_parts),
                "why": "Yearly seasonality and growth; lets the baseline extend into a future hold-out",
            }
        )
    if config.columns.controls:
        feature_rows.append(
            {
                "feature": "Your controls",
                "columns": ", ".join(f"{k} ({n})" for c in config.columns.controls for k, n in c.names.items()),
                "why": "Drivers outside marketing, from the upload; a forecast keeps their last value",
            }
        )
    shifted = [f"{s}_spend {spec.shift_for(s)} days" for s in spec.print_slugs if spec.shift_for(s) > 0 and f"{s}_spend" in m_spend]
    roles_rows = [
        {"role": "time", "columns": "daily date, YYYY-MM-DD"},
        {"role": "KPI (kpi_type = revenue)", "columns": "total_sales"},
        {
            "role": "media = media spend",
            "columns": ", ".join(m_spend) + (f" (shifted to the in-home date: {', '.join(shifted)})" if shifted else ""),
        },
        {
            "role": "controls",
            "columns": ", ".join(
                f"{c} (realised commission cost)" if c.endswith("_spend") and c[: -len("_spend")] in spec.commission_slugs else c
                for c in m_controls
            )
            or "none",
        },
    ]

    first, last = ladder[0], ladder[-1]
    vif_max = max(vif.values()) if vif else float("nan")
    vif_word = "moderate" if vif_max < 5 else ("high" if vif_max < 10 else "severe")
    wide_layout = config.columns.layout == "wide"
    target_text = f"the target column **{config.columns.target}**" if wide_layout else "the **daily sum of all sales**"
    zero_text = (
        f" {len(zero_cols)} columns ({', '.join(zero_cols) or 'none'}) are zero on every day because those channels "
        "have no cost data: they stay in the table but **cannot be budget levers**, and their sales remain part of the target."
        if zero_cols or not wide_layout
        else ""
    )
    kpis = [
        KpiItem(
            label="Wide daily table",
            value=f"{len(panel)} days",
            delta=f"{len(cost_rows)} cost columns + total_sales",
        ),
    ]
    if catalog_col:
        kpis.append(KpiItem(label=f"{print_name} in-home shift", value=f"k = {shift} days", delta=f"Run setting · scan best: {best_k_text}", status="neutral"))
    kpis += [
        KpiItem(
            label="Adjusted R² ladder",
            value=f"{first['adj_r2']:.2f} → {last['adj_r2']:.2f}",
            delta="Raw costs → catalog in-home adstock" if catalog_col else f"Raw costs → {last['step'].lstrip('+ ')}",
            status="success" if last["adj_r2"] >= first["adj_r2"] else "neutral",
        ),
        KpiItem(
            label="Max digital VIF",
            value=f"{vif_max:.2f}" if vif else "—",
            delta=f"{max(vif, key=vif.get)}; {vif_word} collinearity" if vif else "",
        ),
    ]
    by_id = {c.id: c for c in charts}

    def placed(*ids: str) -> list[ChartBlock]:
        return [by_id[i] for i in ids if i in by_id]

    blocks: list[Any] = [
        KpisBlock(items=kpis),
        MarkdownBlock(
            title="What the brief asks for",
            body=f"Every channel becomes a column holding its **daily cost**, with {target_text} as the dependent variable.{zero_text}",
        ),
        TableBlock(
            id="prep_cost_columns_table",
            title="Daily cost columns",
            subtitle="Distribution of each channel's daily cost over the modelling panel",
            columns=[
                TableColumn(key="column", label="Column"),
                TableColumn(key="mean", label="Mean", align="right"),
                TableColumn(key="median", label="Median", align="right"),
                TableColumn(key="p95", label="95th pct", align="right"),
                TableColumn(key="max", label="Max", align="right"),
                TableColumn(key="zero_days", label="Zero days", align="right"),
            ],
            rows=cost_rows,
        ),
        *placed("prep_catalog_booking_vs_effect", "prep_catalog_adstock_shift", "prep_shift_scan"),
    ]
    if scan_rows:
        blocks.append(
            TableBlock(
                id="prep_shift_scan_table",
                title="Catalog shift scan: best correlation per shift k and decay family",
                columns=[
                    TableColumn(key="k", label="Shift k", align="right"),
                    TableColumn(key="binomial", label="Binomial", align="right"),
                    TableColumn(key="geometric", label="Geometric", align="right"),
                ],
                rows=scan_rows,
            )
        )
    blocks += [
        *placed("prep_saturation"),
        TableBlock(
            id="prep_cost_considerations",
            title="What should be taken into account when considering the costs?",
            columns=[
                TableColumn(key="topic", label="Topic"),
                TableColumn(key="observation", label="Observation"),
                TableColumn(key="handling", label="How it is handled"),
            ],
            rows=cost_topics,
        ),
        TableBlock(
            id="prep_new_features",
            title="New features (the brief requires at least two)",
            columns=[
                TableColumn(key="feature", label="Feature"),
                TableColumn(key="columns", label="Columns"),
                TableColumn(key="why", label="Why"),
            ],
            rows=feature_rows,
        ),
        *placed("prep_r2_ladder"),
        TableBlock(
            id="prep_ladder_table",
            title="Adjusted R² ladder (plain OLS, in-sample)",
            columns=[
                TableColumn(key="step", label="Feature set"),
                TableColumn(key="r2", label="R²", align="right"),
                TableColumn(key="adj_r2", label="Adj. R²", align="right"),
                TableColumn(key="mae_over_mean", label="In-sample MAE / mean", align="right"),
                TableColumn(key="aic", label="AIC", align="right"),
            ],
            rows=ladder_rows,
        ),
        *placed("prep_channel_importance"),
        TableBlock(
            id="prep_channel_importance_table",
            title="Channel importance (preliminary OLS with HAC errors)",
            columns=[
                TableColumn(key="channel", label="Channel"),
                TableColumn(key="std_effect", label="Std. effect (€/day)", align="right"),
                TableColumn(key="ci", label="95% CI", align="right"),
                TableColumn(key="p_value", label="p-value", align="right"),
                TableColumn(key="contribution_share", label="Contribution share", align="right"),
            ],
            rows=importance_rows,
        ),
        *placed("prep_vif"),
        CalloutBlock(
            tone="warn",
            title="The challenge of using channel costs as influencing factors",
            body=(
                f"1. **Reverse causality:** budgets follow expected demand (monthly digital spend vs sales r = {monthly_corr:.2f}); "
                + (f"{billing}, so part of the cost–sales link runs from sales to cost.\n" if billing else "part of the cost–sales link runs from sales to cost.\n")
                + (
                    f"2. **Correlated inputs:** digital costs rise together in Q4; VIFs ≤ {vif_max:.1f} show moderate collinearity, "
                    if catalog_col
                    else f"2. **Correlated inputs:** costs move together; VIFs ≤ {vif_max:.1f} show {vif_word} collinearity, "
                )
                + "but individual effects stay wide.\n"
                "3. **Timing and shape:** costs are booked on the day of causation while effects are delayed, carry over and saturate.\n"
                "4. **Little independent variation:** spend is never switched off like an experiment; lift tests would calibrate priors.\n"
                + (
                    "5. **Attribution ≠ incrementality:** the model targets total sales, not channel-attributed sales.\n"
                    if not wide_layout
                    else f"5. **Correlation ≠ incrementality:** the model estimates each channel's incremental effect on {config.columns.target}.\n"
                )
                + "6. **Forecast-time availability:** future costs must be planned"
                + (
                    f"; for {join_names(upper_names + ([commission_name] if commission_name else []))} the plan is only an upper limit."
                    if upper_names or commission_name
                    else "."
                )
            ),
        ),
        TableBlock(
            id="prep_meridian_roles",
            title="Meridian input table",
            subtitle=f"{len(panel)} daily rows; downloadable as meridian_input.csv",
            columns=[TableColumn(key="role", label="Meridian role"), TableColumn(key="columns", label="Columns")],
            rows=roles_rows,
        ),
    ]
    if unpaid:
        blocks.append(
            CalloutBlock(
                tone="info",
                title="Not used as inputs",
                body=(
                    f"Clicks, impressions, channel-level sales and the channels without costs ({', '.join(unpaid)}) are not model "
                    "inputs: channel sales add up to the target (leakage), and clicks or impressions are not known at forecast time."
                ),
            )
        )

    return Section(
        id="preparation",
        title="Data Preparation & Features",
        subtitle=(
            "The wide daily table, cost considerations (booking date, carry-over, saturation), new features, channel importance "
            "and the Meridian input table."
            + (
                f" Scans, curves and fits use the {len(fit_panel)} days up to {fit_until:%Y-%m-%d}, before the test window, so "
                "no modelling choice made here sees it."
                if fit_until is not None
                else ""
            )
        ),
        blocks=blocks,
    )


def _generic_cost_topics(
    config: DataRoleConfig, billing: str, upper_names: list[str], commission_name: str | None
) -> list[dict[str, str]]:
    """The cost considerations for data without a print channel (no send-out lump to shift)."""
    ms = config.model_settings
    return [
        {
            "topic": "Booking date",
            "observation": "Cost is booked on the day it is spent; its effect can come later",
            "handling": f"Meridian adstock carries each channel's effect up to max_lag = {ms.max_lag} days",
        },
        {
            "topic": "Carry-over",
            "observation": "Effects can last days to weeks after the spend",
            "handling": "Meridian adstock (geometric by default; binomial can be chosen per channel)",
        },
        {
            "topic": "Saturation",
            "observation": "Returns diminish as daily spend grows",
            "handling": "Meridian Hill curve per channel; essential for scenarios beyond today's spend",
        },
        {
            "topic": "Scale",
            "observation": "Spend levels differ between channels",
            "handling": "Meridian scales each channel internally",
        },
        {
            "topic": "Zero vs missing",
            "observation": (
                "A blank spend cell in the upload means no spend that day (reported on upload)"
                if config.columns.layout == "wide"
                else "No cost row means 0 spend; unpaid channels have no cost at all"
            ),
            "handling": "Days without spend enter as 0",
        },
        {
            "topic": "Billing model",
            "observation": billing or "No channel's cost follows its own clicks or sales",
            "handling": "; ".join(
                x
                for x in (
                    f"{join_names(upper_names)} ROI{'s are upper bounds' if len(upper_names) > 1 else ' is an upper bound'}" if upper_names else "",
                    f"{commission_name} enters Meridian as a control" if commission_name else "",
                )
                if x
            )
            or "Costs enter Meridian as media",
        },
        {
            "topic": "Forecasting",
            "observation": "Spend for the days ahead must be known in advance, i.e. the planned budget",
            "handling": "Spend is the media input, so the model runs on a budget plan",
        },
    ]
