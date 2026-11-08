"""Classical OLS benchmark vs Meridian on the same hold-outs."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import bar_figure, line_figure
from mmm_studio.charts.theme import COPPER, INK_MUTED, NAVY, SLATE, STATUS_DANGER, TEAL
from mmm_studio.core.as_of import as_of_panel, leak_free, outcome_driven_slugs
from mmm_studio.core.features import (
    DEFAULT_CATALOG_SHIFT,
    calendar_controls,
    catalog_inhome_adstock,
    holdout_windows,
    quarter_label,
    seasonality_controls,
)
from mmm_studio.core.metrics import regression_metrics
from mmm_studio.core.model_settings import ModelSettings
from mmm_studio.core.schema import DataRoleConfig, slugify_channel
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

TARGET_MAE_RATIO = 0.15
NAIVE = "Naive weekday mean"
OLS1 = "OLS-1 raw costs"
OLS2 = "OLS-2 Meridian inputs"
MERIDIAN = "Meridian MMM"
CV_FOLDS = 8
CV_FOLD_DAYS = 42


def ols_design_sets(panel: pd.DataFrame, config: DataRoleConfig | None = None) -> dict[str, pd.DataFrame]:
    """OLS-1: the raw paid-channel costs. OLS-2: Meridian's inputs (digital costs, catalog in-home adstock, controls).

    OLS-2 follows the run's settings: the print channel's in-home shift, the calendar/seasonality controls and the
    missing-row controls, so OLS and Meridian are compared on the same inputs.
    """
    shift = DEFAULT_CATALOG_SHIFT
    if config is not None and config.paid_channels:
        slug_map = config.slug_map()
        paid_cols = [f"cost_{slug_map[c]}" for c in config.paid_channels if f"cost_{slug_map[c]}" in panel.columns]
        print_cols = [f"cost_{slug_map[c]}" for c in config.print_event_channels if f"cost_{slug_map[c]}" in panel.columns]
        print_cfg = next((c for c in config.channels if c.enabled and c.role == "print_event"), None)
        if print_cfg is not None and print_cfg.in_home_shift_days is not None:
            shift = int(print_cfg.in_home_shift_days)
    else:
        from mmm_studio.core.tables import CANONICAL_PAID_CHANNELS

        paid_cols = [f"cost_{slugify_channel(c)}" for c in CANONICAL_PAID_CHANNELS if f"cost_{slugify_channel(c)}" in panel.columns]
        print_cols = ["cost_catalog"] if "cost_catalog" in panel.columns else []
    idx = pd.DatetimeIndex(panel.index)
    digital = [c for c in paid_cols if c not in print_cols]
    ms = config.model_settings if config is not None else ModelSettings()
    country = config.country if config is not None else "DE"
    controls = calendar_controls(idx, ms.calendar.groups(), country).join(seasonality_controls(idx, ms.n_fourier, ms.trend))
    user = [k for k in (config.control_keys if config is not None else []) if k in panel.columns]
    if user:
        controls = controls.join(panel[user].astype(float))
    if ms.missing_row_controls and config is not None:
        flags = [f"missing_{c.slug}" for c in config.channels if c.enabled and f"missing_{c.slug}" in panel.columns]
        flags = [f for f in flags if float(panel[f].sum()) > 0]
        if flags:
            controls = controls.join(panel[flags].astype(float))
    ols2 = panel[digital].copy()
    if print_cols:
        ols2 = ols2.join(catalog_inhome_adstock(panel[print_cols[0]], shift=shift))
    return {OLS1: panel[paid_cols], OLS2: ols2.join(controls)}


def ols_forecast(
    y: pd.Series,
    x: pd.DataFrame,
    train_end: pd.Timestamp,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
) -> pd.Series:
    """Fit plain OLS on days ≤ `train_end` and predict the test window."""
    import statsmodels.api as sm

    idx = pd.DatetimeIndex(y.index)
    tr = idx <= train_end
    te = (idx >= test_start) & (idx <= test_end)
    fit = sm.OLS(y[tr], sm.add_constant(x[tr], has_constant="add")).fit()
    return pd.Series(np.asarray(fit.predict(sm.add_constant(x[te], has_constant="add"))), index=idx[te])


def weekday_mean_forecast(
    y: pd.Series,
    train_end: pd.Timestamp,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
) -> pd.Series:
    """Weekday profile of the training period."""
    idx = pd.DatetimeIndex(y.index)
    tr = idx <= train_end
    te = (idx >= test_start) & (idx <= test_end)
    prof = y[tr].groupby(idx[tr].dayofweek).mean()
    return pd.Series(prof.reindex(idx[te].dayofweek).to_numpy(), index=idx[te])


def walk_forward_starts(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """8 folds of 42 days with an expanding window; the first fold starts 379 days in (e.g. 2024-01-15 on a 2023–2024 panel)."""
    idx = pd.DatetimeIndex(index)
    first = idx.min() + pd.Timedelta(days=379)
    starts = [first + pd.Timedelta(days=CV_FOLD_DAYS * k) for k in range(CV_FOLDS)]
    return [s for s in starts if s <= idx.max() - pd.Timedelta(days=13)]


def evaluate_ols_suite(
    panel: pd.DataFrame,
    config: DataRoleConfig | None = None,
) -> dict[str, Any]:
    """Score naive, OLS-1 and OLS-2 on the primary and forward hold-outs, plus walk-forward CV.

    Leak-free (the run setting `leak_free_validation`, `core/as_of.py`): each window, hold-out or fold, gets its own
    design matrix built from the panel as of the day before it, so its sales, the realised cost of channels that follow
    the outcome (commission, demand-following), the user's controls, the weather and missing-row flags of the window
    never reach the forecast. Off: one design matrix over the whole panel.
    """
    y = panel["total_sales"]
    idx = pd.DatetimeIndex(panel.index)
    holdout_days = int(config.holdout_days) if config and config.holdout_days > 0 else 93
    windows = holdout_windows(idx, holdout_days)
    x_sets = ols_design_sets(panel, config)
    strict = leak_free(config)
    plan_cols = [str(c).removeprefix("cost_") for c in x_sets[OLS1].columns]
    outcome = outcome_driven_slugs(config, panel) if strict and config is not None else ()

    def forecast(name: str, tr_end: pd.Timestamp, te_start: pd.Timestamp, te_end: pd.Timestamp) -> pd.Series:
        if name == NAIVE:
            return weekday_mean_forecast(y, tr_end, te_start, te_end)
        if not strict:
            return ols_forecast(y, x_sets[name], tr_end, te_start, te_end)
        key = (tr_end, te_start, te_end)
        if key not in as_of_designs:
            ext = as_of_panel(panel, key, plan_cols, outcome)
            as_of_designs[key] = (ext["total_sales"], ols_design_sets(ext, config))
        y_ext, designs = as_of_designs[key]
        return ols_forecast(y_ext, designs[name], tr_end, te_start, te_end)

    as_of_designs: dict[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp], tuple[pd.Series, dict[str, pd.DataFrame]]] = {}
    rows: list[dict[str, Any]] = []
    predictions: dict[str, dict[str, pd.Series]] = {}
    window_meta: dict[str, dict[str, str]] = {}
    for wkey, (tr_end, te_start, te_end) in windows.items():
        label = quarter_label(te_start, te_end)
        window_meta[wkey] = {
            "label": label,
            "train_end": tr_end.strftime("%Y-%m-%d"),
            "test_start": te_start.strftime("%Y-%m-%d"),
            "test_end": te_end.strftime("%Y-%m-%d"),
        }
        preds = {name: forecast(name, tr_end, te_start, te_end) for name in (NAIVE, *x_sets)}
        predictions[wkey] = preds
        for name, p in preds.items():
            rows.append({"window": wkey, "window_label": label, "model": name, **regression_metrics(y.loc[p.index], p)})

    cv_scores: dict[str, list[float]] = {NAIVE: [], **{name: [] for name in x_sets}}
    # Each OLS-2 fold's total error: leak-free evidence of how far a forecast's level drifts over a few weeks
    level_errors: list[dict[str, Any]] = []
    starts = walk_forward_starts(idx)
    for s in starts:
        e = min(s + pd.Timedelta(days=CV_FOLD_DAYS - 1), idx.max())
        tr_end = s - pd.Timedelta(days=1)
        for name in cv_scores:
            p = forecast(name, tr_end, s, e)
            cv_scores[name].append(regression_metrics(y.loc[p.index], p)["MAE_over_mean"])
            if name == OLS2:
                level_errors.append(
                    {
                        "source": f"{OLS2} walk-forward",
                        "start": s.strftime("%Y-%m-%d"),
                        "end": pd.Timestamp(p.index[-1]).strftime("%Y-%m-%d"),
                        "error": (float(y.loc[p.index].sum()) - float(p.sum())) / float(p.sum()),
                    }
                )

    return {
        "windows": window_meta,
        "rows": rows,
        "predictions": predictions,
        "leak_free": strict,
        "estimated_costs": list(outcome),
        "cv": {
            "folds": [s.strftime("%Y-%m-%d") for s in starts],
            "scores": cv_scores,
            "mean": {k: float(np.mean(v)) if v else float("nan") for k, v in cv_scores.items()},
            "worst": {k: float(np.max(v)) if v else float("nan") for k, v in cv_scores.items()},
            "level_errors": level_errors,
        },
    }


def best_ols_row(ols_res: dict[str, Any], window: str = "primary") -> dict[str, Any] | None:
    """The OLS-2 row (Meridian's inputs) of a hold-out window."""
    return next((r for r in ols_res["rows"] if r["window"] == window and r["model"] == OLS2), None)


def _meridian_rows(meridian_summary: dict[str, Any] | None, windows: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    if not meridian_summary:
        return []
    out = []
    for wkey, mkey in (("primary", "holdout_q4_2024"), ("forward", "holdout_q3_2024")):
        m = meridian_summary.get(mkey)
        if wkey in windows and isinstance(m, dict) and "MAE_over_mean" in m:
            out.append({"window": wkey, "window_label": windows[wkey]["label"], "model": MERIDIAN, **m})
    return out


def _pct_or_none(v: float | None) -> float | None:
    return round(100 * v, 1) if v is not None else None


def _pct_text(v: float | None) -> str:
    return f"{100 * v:.1f}%" if v is not None else "–"


def _channel_names(slugs: list[str]) -> list[str]:
    from mmm_studio.core.meridian.spec import channel_label

    return [channel_label(s) for s in slugs]


def _fold_label(fold_start: str) -> str:
    ts = pd.Timestamp(fold_start)
    return f"{ts.day}\u00a0{ts:%b %Y}"  # no-break space: wraps as "15 Jan" / "2024" at most


def build_benchmark_section(
    panel: pd.DataFrame,
    meridian_summary: dict[str, Any] | None = None,
    config: DataRoleConfig | None = None,
    ols_res: dict[str, Any] | None = None,
    meridian_holdout: pd.DataFrame | None = None,
) -> Section:
    """Build the `benchmark` section: same hold-outs, walk-forward CV and the takeaways."""
    res = ols_res or evaluate_ols_suite(panel, config=config)
    windows = res["windows"]
    rows = list(res["rows"]) + _meridian_rows(meridian_summary, windows)
    has_meridian = any(r["model"] == MERIDIAN for r in rows)
    models = [NAIVE, OLS1, OLS2] + ([MERIDIAN] if has_meridian else [])

    def score(wkey: str, model: str) -> float | None:
        r = next((r for r in rows if r["window"] == wkey and r["model"] == model), None)
        return float(r["MAE_over_mean"]) if r else None

    p_label = windows["primary"]["label"]
    f_label = windows.get("forward", {}).get("label")

    # 1. Comparison chart: MAE/mean on both hold-outs vs the 15 % target
    series = [
        {
            "name": f"Hold-out {p_label}",
            "values": [_pct_or_none(score("primary", m)) for m in models],
            "color": NAVY,
            "text": [_pct_text(score("primary", m)) for m in models],
        }
    ]
    if f_label:
        series.append(
            {
                "name": f"Hold-out {f_label}",
                "values": [_pct_or_none(score("forward", m)) for m in models],
                "color": TEAL,
                "text": [_pct_text(score("forward", m)) for m in models],
            }
        )
    fig_cmp = bar_figure(
        categories=models,
        series=series,
        orientation="h",
        xaxis_title="MAE / mean sales on the hold-out (%)",
        vlines=[{"x": 15.0, "color": STATUS_DANGER, "dash": "solid"}],
        height=360,
    )

    # 2. Primary hold-out trajectory
    preds = res["predictions"]["primary"]
    p_idx = preds[OLS2].index
    traj = [{"name": "Actual", "y": panel["total_sales"].reindex(p_idx).round(0).tolist(), "color": SLATE, "width": 1.6}]
    if meridian_holdout is not None and len(meridian_holdout):
        traj.append({"name": MERIDIAN, "y": meridian_holdout["expected"].reindex(p_idx).round(0).tolist(), "color": NAVY, "width": 2.2})
    traj += [
        {"name": OLS2, "y": preds[OLS2].round(0).tolist(), "color": TEAL, "width": 1.8, "dash": "dash"},
        {"name": OLS1, "y": preds[OLS1].round(0).tolist(), "color": COPPER, "width": 1.5, "dash": "dot"},
        {"name": NAIVE, "y": preds[NAIVE].round(0).tolist(), "color": INK_MUTED, "width": 1.2, "dash": "dot"},
    ]
    fig_traj = line_figure(
        x=[d.strftime("%Y-%m-%d") for d in p_idx],
        series=traj,
        yaxis_title="Daily total sales (€)",
        height=380,
    )

    # 3. Walk-forward CV
    cv = res["cv"]
    fig_cv = bar_figure(
        categories=cv["folds"],
        series=[
            {"name": name, "values": [round(100 * v, 1) for v in vals], "color": {NAIVE: INK_MUTED, OLS1: COPPER, OLS2: TEAL}[name]}
            for name, vals in cv["scores"].items()
        ],
        xaxis_title="Fold start (42-day test window, expanding training window)",
        yaxis_title="MAE / mean (%)",
        hlines=[{"y": 15.0, "color": STATUS_DANGER, "dash": "dash", "label": "target ≤ 15 %"}],
        height=360,
    )

    table_rows = []
    for r in rows:
        table_rows.append(
            {
                "holdout": r["window_label"],
                "model": r["model"],
                "mae": fmt_eur(float(r["MAE"])),
                "mae_over_mean": f"{100 * float(r['MAE_over_mean']):.1f}%",
                "rmse": fmt_eur(float(r["RMSE"])),
                "r2": f"{float(r['R2']):.3f}",
                "mape": f"{100 * float(r['MAPE']):.1f}%",
                "passes": "Yes" if float(r["MAE_over_mean"]) <= TARGET_MAE_RATIO else "No",
            }
        )
    table_rows.sort(key=lambda r: (r["holdout"] != p_label, models.index(r["model"])))
    cv_rows = []
    for name, vals in cv["scores"].items():
        row: dict[str, Any] = {"model": name}
        for fold, v in zip(cv["folds"], vals, strict=False):
            row[fold] = f"{100 * v:.1f}%"
        row["mean"] = f"{100 * cv['mean'][name]:.1f}%"
        row["worst"] = f"{100 * cv['worst'][name]:.1f}%"
        cv_rows.append(row)

    ols2_p, ols2_f = score("primary", OLS2), score("forward", OLS2)
    mer_p, mer_f = score("primary", MERIDIAN), score("forward", MERIDIAN)
    naive_vals = [v for v in (score("primary", NAIVE), score("forward", NAIVE)) if v is not None]
    target = 100 * TARGET_MAE_RATIO
    takeaways = []
    if naive_vals:
        verdict = "fail" if min(naive_vals) > TARGET_MAE_RATIO else ("partly meet" if max(naive_vals) > TARGET_MAE_RATIO else "meet")
        takeaways.append(
            f"- **Naive forecasts {verdict} the {target:.0f} % target** ({100 * min(naive_vals):.0f}–{100 * max(naive_vals):.0f} % MAE / mean)."
        )
    cv_mean, cv_worst = cv["mean"], cv["worst"]
    if cv["folds"]:
        ols1_ok = cv_worst[OLS1] <= TARGET_MAE_RATIO
        takeaways.append(
            f"- **Raw costs alone (OLS-1) {'stay within the target' if ols1_ok else 'are unreliable'}:** walk-forward folds reach "
            f"{100 * cv_worst[OLS1]:.0f} % in the worst fold"
            + ("." if ols1_ok else ", because same-day costs miss the print channel's delay and the seasonality.")
        )
        ols2_ok = cv_worst[OLS2] <= TARGET_MAE_RATIO
        takeaways.append(
            f"- **OLS-2 (Meridian's inputs) {'is robust' if ols2_ok else 'misses the target in some folds'}:** "
            f"{100 * cv_mean[OLS2]:.1f} % on average across {len(cv['folds'])} walk-forward folds, {100 * cv_worst[OLS2]:.1f} % "
            "in the worst fold."
        )
    if mer_p is not None and ols2_p is not None:
        fwd = (
            f", and {100 * mer_f:.1f} % vs {100 * ols2_f:.1f} % in {f_label}"
            if mer_f is not None and ols2_f is not None and f_label
            else ""
        )
        gap = mer_p - ols2_p
        if gap <= 0:
            verdict = "is the most accurate model"
        elif gap <= 0.01:
            verdict = "is within one point of the OLS benchmark"
        else:
            verdict = f"is {100 * gap:.1f} points less accurate than the OLS benchmark"
        takeaways.append(
            f"- **Meridian {verdict}:** {100 * mer_p:.1f} % vs {100 * ols2_p:.1f} % for OLS-2 in {p_label}{fwd}. It adds credible "
            "intervals, ROIs, response curves and budget optimisation"
            + (", which is why it is the main model." if gap <= 0.01 else "; its forecast accuracy should be investigated before relying on it.")
        )
    else:
        takeaways.append("- **Meridian** results appear here once the Bayesian fit has finished.")

    kpis = [
        KpiItem(
            label=f"Meridian · {p_label}",
            value=f"{100 * mer_p:.1f}%" if mer_p is not None else "pending",
            delta="MAE / mean (main model)" if mer_p is not None else "Bayesian fit not finished",
            status=("success" if mer_p is not None and mer_p <= TARGET_MAE_RATIO else "neutral"),
        ),
        KpiItem(
            label=f"OLS-2 · {p_label}",
            value=f"{100 * ols2_p:.1f}%" if ols2_p is not None else "—",
            delta="Meridian's inputs, plain OLS",
            status="success" if ols2_p is not None and ols2_p <= TARGET_MAE_RATIO else "warn",
        ),
        KpiItem(
            label=f"OLS-1 · {p_label}",
            value=_pct_text(score("primary", OLS1)),
            delta="Raw costs only",
            status="success" if (score("primary", OLS1) or 1) <= TARGET_MAE_RATIO else "warn",
        ),
        KpiItem(
            label="OLS-2 walk-forward",
            value=f"{100 * cv['mean'][OLS2]:.1f}%",
            delta=f"mean of {len(cv['folds'])} folds · worst {100 * cv['worst'][OLS2]:.1f}%",
            status="success" if cv["mean"][OLS2] <= TARGET_MAE_RATIO else "warn",
        ),
    ]
    blocks: list[Any] = [
        KpisBlock(items=kpis),
        MarkdownBlock(
            title="Benchmark design",
            body=(
                "A transparent sanity check for the Meridian model. Every forecast is scored on the **same hold-outs** Meridian used:\n\n"
                f"- **{NAIVE}:** the weekday profile of the training period.\n"
                f"- **{OLS1}:** the paid-channel costs only (the literal \"sales from budget\" regression).\n"
                + (
                    f"- **{OLS2}:** digital costs, the catalog in-home adstock and all of Meridian's controls.\n"
                    if config is None or config.print_event_channels
                    else f"- **{OLS2}:** the paid-channel costs and all of Meridian's controls.\n"
                )
                + f"- **{MERIDIAN}:** Bayesian regression with adstock, Hill saturation and priors."
                + (
                    "\n\n**Leak-free:** every window, hold-out or walk-forward fold, is forecast as a plan made the day before "
                    "it: the models see no sales, user controls, weather or missing-row flags of the window"
                    + (
                        f", and the costs that follow the outcome ({', '.join(_channel_names(res['estimated_costs']))}) are "
                        "planned from history instead of their realised values."
                        if res.get("estimated_costs")
                        else "."
                    )
                    if res.get("leak_free")
                    else ""
                )
            ),
        ),
        ChartBlock(
            id="bench_model_comparison",
            title=f"Forecast error on two forward hold-outs ({p_label}{f' and {f_label}' if f_label else ''})",
            subtitle="MAE / mean sales; the red line is the brief's 15 % target.",
            figure=fig_cmp,
        ),
        TableBlock(
            id="bench_holdout_table",
            title="Same hold-outs as Meridian",
            subtitle=" · ".join(
                f"{w['label']}: trained to {w['train_end']}, scored {w['test_start']} → {w['test_end']}" for w in windows.values()
            ),
            columns=[
                TableColumn(key="holdout", label="Hold-out"),
                TableColumn(key="model", label="Model"),
                TableColumn(key="mae", label="MAE", align="right"),
                TableColumn(key="mae_over_mean", label="MAE / mean", align="right"),
                TableColumn(key="rmse", label="RMSE", align="right"),
                TableColumn(key="r2", label="R²", align="right"),
                TableColumn(key="mape", label="MAPE", align="right"),
                TableColumn(key="passes", label="≤ 15 %"),
            ],
            rows=table_rows,
        ),
        ChartBlock(
            id="bench_holdout_trajectory",
            title=f"{p_label} hold-out: daily forecasts vs actual sales",
            subtitle="Each model only sees data up to the day before the hold-out.",
            figure=fig_traj,
        ),
        ChartBlock(
            id="bench_walk_forward_cv",
            title=f"Walk-forward cross-validation ({len(cv['folds'])} fold{'s' if len(cv['folds']) != 1 else ''} × {CV_FOLD_DAYS} days)",
            subtitle="Meridian is too slow to refit for every fold, so cross-validation covers the naive and OLS models.",
            figure=fig_cv,
        ),
        TableBlock(
            id="bench_walk_forward_table",
            title="Walk-forward MAE / mean by fold",
            columns=[
                TableColumn(key="model", label="Model"),
                # "15 Jan 2024" rather than ISO dates: a header with spaces can wrap, so the 11 columns fit
                *[TableColumn(key=f, label=_fold_label(f), align="right") for f in cv["folds"]],
                TableColumn(key="mean", label="Mean", align="right"),
                TableColumn(key="worst", label="Worst", align="right"),
            ],
            rows=cv_rows,
        ),
        CalloutBlock(tone="info", title="Takeaways", body="\n".join(t for t in takeaways if t)),
    ]
    return Section(
        id="benchmark",
        title="Benchmark: classical OLS vs Meridian",
        subtitle="Naive, OLS-1 and OLS-2 scored on Meridian's hold-outs, plus walk-forward cross-validation.",
        blocks=blocks,
    )
