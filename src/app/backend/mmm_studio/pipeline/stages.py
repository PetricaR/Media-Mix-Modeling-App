"""Pipeline stages s01–s16 orchestrating core EDA, preparation, OLS benchmark, and Bayesian Meridian."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import line_figure
from mmm_studio.charts.theme import COPPER, HOLDOUT_SHADE, INK_MUTED, NAVY, SLATE
from mmm_studio.core.schema import DataProfile, DataRoleConfig
from mmm_studio.pipeline.context import MODEL_PANEL_NAME, RunContext, StageSkipped
from mmm_studio.report.facts import compute_case_facts
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

STAGE_DEFINITIONS: list[tuple[str, str, str]] = [
    ("s01_ingest", "Validate & ingest workbook", "analysis"),
    ("s02_quality", "Data quality & coverage audit", "analysis"),
    ("s03_target", "Target deep-dive (STL, ACF, events)", "analysis"),
    ("s04_channels", "Channel sales mix & seasonality", "analysis"),
    ("s05_spend", "Media spend & flighting", "analysis"),
    ("s06_efficiency", "Funnel efficiency & regime breaks", "analysis"),
    ("s07_correlations", "Correlations, lags & VIF", "analysis"),
    ("s08_catalog", "Catalog event study & halo", "analysis"),
    ("s09_readiness", "MMM readiness & naive baselines", "analysis"),
    ("s10_preparation", "Data preparation & features", "analysis"),
    ("s11_benchmark", "OLS benchmark & walk-forward CV", "analysis"),
    ("s12_meridian_fit", "Meridian fit, hold-out & forward hold-out", "meridian"),
    ("s13_drivers_and_optimiser", "ROI, response curves & budget optimiser", "meridian"),
    ("s14_forecast", "Back-test, budget-plan forecast & scenarios", "meridian"),
    ("s15_finalize", "Overview, brief & Meridian HTML reports", "meridian"),
    ("s16_model_selection", "Model-selection grid & prior sensitivity", "meridian"),
]


def _sampling_for(profile: str, purpose: str = "main") -> dict[str, int]:
    """Sampling per run profile. Notebooks: FULL for the final model and forecasts, FAST for selection/forward fits."""
    from mmm_studio.core.meridian.fit import FAST_SAMPLING, FULL_SAMPLING, SMOKE_SAMPLING

    if profile == "smoke":
        return SMOKE_SAMPLING
    if purpose in ("selection", "forward"):
        return FAST_SAMPLING
    return FULL_SAMPLING if profile == "full" else FAST_SAMPLING


def _sampling_label(sampling: dict[str, int]) -> str:
    return f"{sampling['n_chains']} chains × {sampling['n_keep']} draws"


def _run_stage(ctx: RunContext, stage_key: str, fn: Any, critical: bool = False) -> Any:
    ctx.check_cancelled()
    if ctx.on_stage_start:
        ctx.on_stage_start(stage_key)
    try:
        res = fn(ctx)
        if ctx.on_stage_complete:
            ctx.on_stage_complete(stage_key)
        ctx.heartbeat()
        return res
    except StageSkipped as skip:
        if ctx.on_stage_skipped:
            ctx.on_stage_skipped(stage_key, str(skip))
        elif ctx.on_stage_complete:
            ctx.on_stage_complete(stage_key)
        return None
    except InterruptedError:
        raise
    except Exception as exc:
        if ctx.on_stage_error:
            ctx.on_stage_error(stage_key, f"{type(exc).__name__}: {exc}")
        if critical:
            raise
        return None


def _ensure_dataframes(ctx: RunContext) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return `(daily_df, modelling_panel, engineered_panel)` from cache or storage."""
    if "daily_df" not in ctx.cache:
        ctx.cache["daily_df"] = ctx.load_dataframe("daily")
    if "wide_model" not in ctx.cache:
        ctx.cache["wide_model"] = ctx.load_dataframe(MODEL_PANEL_NAME)
    if "panel_df" not in ctx.cache:
        ctx.cache["panel_df"] = ctx.load_dataframe("panel_engineered")
    if ctx.profile is None:
        ctx.profile = DataProfile.model_validate(ctx.load_json("meta/profile.json"))
    if ctx.config is None:
        ctx.config = DataRoleConfig.model_validate(ctx.load_json("meta/config.json"))
    return ctx.cache["daily_df"], ctx.cache["wide_model"], ctx.cache["panel_df"]


def _facts(ctx: RunContext) -> dict[str, Any]:
    if "facts" not in ctx.cache:
        _, _, panel = _ensure_dataframes(ctx)
        ctx.cache["facts"] = compute_case_facts(panel, ctx.config)
    return ctx.cache["facts"]


def _spec_cfg(ctx: RunContext, wide: pd.DataFrame) -> Any:
    from mmm_studio.core.meridian.spec import MeridianSpecConfig

    if "meridian_cfg" not in ctx.cache:
        ctx.cache["meridian_cfg"] = MeridianSpecConfig.from_role_config(ctx.config, index=wide.index)
    return ctx.cache["meridian_cfg"]


def _load_model(ctx: RunContext, name: str = "meridian_final") -> Any:
    from mmm_studio.core.meridian.spec import load_meridian_model

    cache_key = "mmm" if name == "meridian_final" else f"mmm_{name}"
    if ctx.cache.get(cache_key) is None:
        bin_path = ctx.local_work_dir("models") / f"{name}.binpb"
        if not bin_path.exists():
            bin_path.write_bytes(ctx.storage.read_bytes(ctx.key(f"models/{name}.binpb")))
        ctx.cache[cache_key] = load_meridian_model(bin_path)
    return ctx.cache[cache_key]


def _load_optional(ctx: RunContext, cache_key: str, loader: Any) -> Any:
    if ctx.cache.get(cache_key) is not None:
        return ctx.cache[cache_key]
    try:
        ctx.cache[cache_key] = loader()
    except FileNotFoundError:
        ctx.cache[cache_key] = None
    return ctx.cache[cache_key]


def s01_ingest(ctx: RunContext) -> None:
    """Validate the workbook, infer/confirm the schema, and persist the daily panels."""
    from mmm_studio.core.calendar import add_calendar_and_adstock_features
    from mmm_studio.core.tables import build_daily_wide_table
    from mmm_studio.core.workbook import profile_workbook

    if "workbook_bytes" in ctx.cache:
        raw_bytes = ctx.cache["workbook_bytes"]
    else:
        raw_bytes = ctx.storage.read_bytes(ctx.key("input/workbook.xlsx"))

    raw_df, profile = profile_workbook(raw_bytes, filename="workbook.xlsx", role_config=ctx.config)
    ctx.profile = profile
    ctx.config = profile.role_config

    wide_full = build_daily_wide_table(raw_df, config=ctx.config, drop_2025=False)
    wide_model = (
        build_daily_wide_table(raw_df, config=ctx.config, drop_2025=True)
        if ctx.config.drop_trailing_isolated_days
        else wide_full
    )
    panel_df = add_calendar_and_adstock_features(wide_model, config=ctx.config)

    ctx.cache.update(daily_df=raw_df, wide_model=wide_model, panel_df=panel_df)
    ctx.cache.pop("facts", None)
    ctx.save_dataframe("daily", raw_df, also_csv=False)
    ctx.save_dataframe("wide_full", wide_full, also_csv=True)
    ctx.save_dataframe(MODEL_PANEL_NAME, wide_model, also_csv=True)
    ctx.save_dataframe("panel_engineered", panel_df, also_csv=True)
    ctx.save_json("meta/profile.json", ctx.profile.model_dump(mode="json"))
    ctx.save_json("meta/config.json", ctx.config.model_dump(mode="json"))


def s02_quality(ctx: RunContext) -> None:
    from mmm_studio.core.calendar import add_calendar_and_adstock_features
    from mmm_studio.core.eda import build_data_quality_section

    daily_df, _, _ = _ensure_dataframes(ctx)
    wide_full = ctx.load_dataframe("wide_full")
    ctx.save_section(build_data_quality_section(daily_df, add_calendar_and_adstock_features(wide_full, config=ctx.config), ctx.profile))


def s03_target(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_target_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_target_section(panel_df))


def s04_channels(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_channels_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_channels_section(panel_df, ctx.config))


def s05_spend(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_spend_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_spend_section(panel_df, ctx.config))


def s06_efficiency(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_efficiency_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_efficiency_section(panel_df, ctx.config))


def s07_correlations(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_correlations_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_correlations_section(panel_df, ctx.config))


def s08_catalog(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_catalog_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_catalog_section(panel_df, ctx.config))


def s09_readiness(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_readiness_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_readiness_section(panel_df, ctx.config))


def s10_preparation(ctx: RunContext) -> None:
    from mmm_studio.core.preparation import build_preparation_section

    _, wide, _ = _ensure_dataframes(ctx)
    ctx.save_section(build_preparation_section(wide, ctx.config))


def _meridian_ready(meridian_metrics: dict[str, Any] | None) -> bool:
    return bool(meridian_metrics and "holdout_q4_2024" in meridian_metrics)


def build_overview_section(
    wide: pd.DataFrame,
    facts: dict[str, Any],
    ols_res: dict[str, Any],
    meridian_metrics: dict[str, Any] | None = None,
    roi_df: pd.DataFrame | None = None,
    opt_res: dict[str, Any] | None = None,
    fit_df: pd.DataFrame | None = None,
    selection: dict[str, Any] | None = None,
) -> Section:
    """Build the `overview` section: headline accuracy, key findings and the fit trajectory, all computed."""
    from mmm_studio.core.ols import OLS2, best_ols_row

    ols_p = best_ols_row(ols_res, "primary")
    ols_f = best_ols_row(ols_res, "forward")
    windows = ols_res["windows"]
    p_label = windows["primary"]["label"]
    f_label = windows.get("forward", {}).get("label")
    has_mer = _meridian_ready(meridian_metrics)
    if has_mer:
        main = meridian_metrics["holdout_q4_2024"]
        fwd = meridian_metrics.get("holdout_q3_2024")
        model_name = "Meridian MMM"
    else:
        main, fwd, model_name = ols_p, ols_f, f"{OLS2} (Meridian pending)"
    q_pct = 100.0 * float(main["MAE_over_mean"])
    f_pct = 100.0 * float(fwd["MAE_over_mean"]) if fwd else None

    if fit_df is not None and len(fit_df) > 0:
        dates_str = [d.strftime("%Y-%m-%d") for d in fit_df.index]
        ho = fit_df[fit_df["holdout"]] if "holdout" in fit_df.columns else fit_df.iloc[0:0]
        fig_main = line_figure(
            x=dates_str,
            series=[
                {"name": "Actual", "y": fit_df["actual"].round(0).tolist(), "color": SLATE, "width": 1.0},
                {
                    "name": "Meridian (90% interval)",
                    "y": fit_df["expected"].round(0).tolist(),
                    "band_lower": fit_df["expected_lo"].round(0).tolist(),
                    "band_upper": fit_df["expected_hi"].round(0).tolist(),
                    "color": NAVY,
                    "width": 1.6,
                },
                {"name": "Baseline & controls", "y": fit_df["baseline"].round(0).tolist(), "color": INK_MUTED, "dash": "dot", "width": 1.1},
            ],
            yaxis_title="Daily total sales (€)",
            vrects=[{"x0": ho.index[0].strftime("%Y-%m-%d"), "x1": dates_str[-1], "color": HOLDOUT_SHADE, "label": "Hold-out"}]
            if len(ho)
            else [],
        )
        chart_title = f"Daily sales vs Meridian: hold-out MAE / mean = {q_pct:.1f}%"
        chart_sub = "The shaded window is predicted from the budget and the calendar only."
    else:
        cost_cols = [c for c in wide.columns if c.startswith("cost_") and not c.endswith(("_adstock", "_sat", "_roll7"))]
        days = wide["total_sales"].resample("W-SUN").size()
        full_weeks = days.index[days == 7]  # partial first/last weeks would look like collapses
        wk = wide["total_sales"].resample("W-SUN").sum().loc[full_weeks]
        wk_sp = wide[cost_cols].sum(axis=1).resample("W-SUN").sum().loc[full_weeks]
        fig_main = line_figure(
            x=[d.strftime("%Y-%m-%d") for d in wk.index],
            series=[
                {"name": "Weekly sales (€)", "y": wk.round(0).tolist(), "color": NAVY, "width": 2.0},
                {"name": "Weekly paid spend (€)", "y": wk_sp.round(0).tolist(), "color": COPPER, "width": 1.6},
            ],
            yaxis_title="€ per week",
        )
        chart_title = "Weekly sales and paid media spend"
        chart_sub = "The Meridian fit replaces this chart once the Bayesian model has finished."

    halo = facts.get("halo_7d", {})
    halo_top = ", ".join(f"{ch} {v:+.0f}%" for ch, v in facts.get("halo_top", [])[:4])
    ladder = ols_res.get("ladder")

    findings: list[dict[str, str]] = []
    acc_text = f"{model_name}: {q_pct:.1f}% MAE / mean on the {p_label} hold-out (R² {float(main['R2']):.3f})"
    if f_pct is not None and f_label:
        acc_text += f" and {f_pct:.1f}% on the {f_label} forward hold-out"
    if has_mer and ols_p:
        acc_text += f"; the OLS benchmark with the same inputs scores {100 * float(ols_p['MAE_over_mean']):.1f}%."
    findings.append(
        {
            "area": "Forecast accuracy (target ≤ 15 %)",
            "finding": acc_text,
            "implication": "The target is met on both forward hold-outs." if q_pct <= 15 and (f_pct is None or f_pct <= 15)
            else "The target is not met on every hold-out; see the Model and Benchmark pages.",
        }
    )
    if "catalog_lag0_r" in facts:
        findings.append(
            {
                "area": "Catalog: delayed effect and halo",
                "finding": (
                    f"Same-day r = {facts['catalog_lag0_r']:+.2f}, but r = {facts['catalog_best_lag_r']:+.2f} at +{facts['catalog_best_lag']} days. "
                    f"In the 7 days after a send-out total sales are {halo.get('total_sales', float('nan')):+.1f}%"
                    + (f" and channels it feeds rise too ({halo_top})." if halo_top else ".")
                ),
                "implication": (
                    "Model total sales and move catalog spend to its in-home date"
                    + (f": adjusted R² {ladder[-2]['adj_r2']:.2f} → {ladder[-1]['adj_r2']:.2f}." if ladder and len(ladder) >= 4 else ".")
                ),
            }
        )
    if "affiliate_cost_sales_r" in facts:
        aff_media = [r for r in (selection or {}).get("selection", []) if r["affiliate"] == "media" and "affiliate" in r["roi"]]
        aff_text = f"Affiliate cost is a commission on its own sales (r = {facts['affiliate_cost_sales_r']:.2f}"
        if "affiliate_commission_pre_pct" in facts:
            aff_text += (
                f"; {facts['affiliate_commission_pre_pct']:.1f}% of sales before {facts['affiliate_break']}, "
                f"{facts['affiliate_commission_post_pct']:.1f}% after"
            )
        aff_text += ")."
        if aff_media:
            aff_text += f" As a media channel it gets an implausible ROI of ~{np.mean([r['roi']['affiliate'] for r in aff_media]):.0f}."
        findings.append(
            {
                "area": "Reverse causality: Affiliate (and SEA)",
                "finding": aff_text,
                "implication": "Affiliate enters Meridian as a demand-following control, not a lever; SEA's ROI is an upper bound.",
            }
        )
    if roi_df is not None and len(roi_df) > 0:
        media = roi_df.drop(index="All Channels", errors="ignore")
        share = float(roi_df.loc["All Channels", "pct_of_contribution"]) if "All Channels" in roi_df.index else float("nan")
        best = media["roi"].idxmax()
        parts = [f"Media explains {share:.0f}% of sales; highest ROI: {_label(best)} {float(media.loc[best, 'roi']):.2f}×"]
        if "catalog" in media.index:
            parts.append(f"Catalog {float(media.loc['catalog', 'roi']):.2f}× (marginal {float(media.loc['catalog', 'mroi']):.2f}×)")
        impl = "Validate SEA with a geo or hold-out test before moving budget into it."
        if opt_res and opt_res.get("allocations"):
            donor = min(opt_res["allocations"], key=lambda a: a["spend_delta"])
            recip = max(opt_res["allocations"], key=lambda a: a["spend_delta"])
            impl = (
                f"The optimiser moves budget from {donor['label']} to {recip['label']} (+{opt_res['lift_pct']:.1f}% incremental "
                "sales at the same budget) — " + impl[0].lower() + impl[1:]
            )
        findings.append({"area": "Drivers and ROI", "finding": "; ".join(parts) + ".", "implication": impl})
    dq = []
    if facts.get("missing_channel_days"):
        dq.append("missing channel-days: " + ", ".join(f"{k} {v}" for k, v in facts["missing_channel_days"].items()))
    if facts.get("tv_low_days"):
        dq.append(f"{facts['tv_low_days']} TV days with sales < €1")
    if facts.get("trailing_days"):
        dq.append(f"{len(facts['trailing_days'])} isolated day(s) in {facts['trailing_days'][-1][:4]} ({fmt_eur(facts['trailing_sales'])})")
    if "social_spend_ratio" in facts:
        dq.append(
            f"Social Media regime change on {facts['social_break']}: spend ×{facts['social_spend_ratio']:.1f}, "
            f"clicks ×{facts['social_clicks_ratio']:.1f}, attributed sales ×{facts['social_sales_ratio']:.1f}"
        )
    findings.append(
        {
            "area": "Data issues handled",
            "finding": ("; ".join(dq) + ".")[0].upper() + ("; ".join(dq) + ".")[1:] if dq else "No structural data issues found.",
            "implication": "Missing channel-days count as 0 sales and are flagged; the isolated day stays in the models but not in yearly views.",
        }
    )

    kpis = [
        KpiItem(
            label=f"{p_label} hold-out MAE / mean",
            value=f"{q_pct:.1f}%",
            delta=f"Target ≤ 15 % · {model_name}",
            status="success" if q_pct <= 15.0 else "warn",
        ),
        KpiItem(
            label=f"Forward hold-out {f_label or ''}".strip(),
            value=f"{f_pct:.1f}%" if f_pct is not None else "—",
            delta="MAE / mean, trained only on earlier data",
            status="success" if f_pct is not None and f_pct <= 15.0 else "neutral",
        ),
        KpiItem(
            label="Total sales",
            value=fmt_eur(facts["total_sales"]),
            delta=f"Ø {fmt_eur(facts['mean_daily_sales'])}/day over {facts['n_days']} days",
        ),
        KpiItem(
            label="Paid media spend",
            value=fmt_eur(facts["total_spend"]),
            delta=f"{100.0 * facts['total_spend'] / max(facts['total_sales'], 1.0):.1f}% of total sales",
        ),
    ]
    summary_body = (
        f"**Can daily sales be forecast from the marketing budget?** Yes: {model_name} forecasts the {p_label} hold-out with "
        f"**{q_pct:.1f}% MAE / mean**"
        + (f" and the {f_label} forward hold-out with **{f_pct:.1f}%**" if f_pct is not None and f_label else "")
        + " — below the brief's 15 % threshold. "
        + (
            "The main model is a Bayesian regression (Google Meridian) on adstocked, saturated channel spend plus calendar, "
            "seasonality and trend controls; a transparent OLS benchmark confirms the result."
            if has_mer
            else "These are OLS benchmark numbers; the Meridian results replace them when the Bayesian fit finishes."
        )
    )
    blocks: list[Any] = [
        KpisBlock(items=kpis),
        CalloutBlock(tone="success" if q_pct <= 15 else "warn", title="Executive summary", body=summary_body),
        ChartBlock(id="overview_fit_trajectory", title=chart_title, subtitle=chart_sub, figure=fig_main),
        TableBlock(
            id="overview_findings_table",
            title="Key findings and modelling decisions",
            columns=[
                TableColumn(key="area", label="Area"),
                TableColumn(key="finding", label="Finding"),
                TableColumn(key="implication", label="Decision / action"),
            ],
            rows=findings,
        ),
    ]
    return Section(
        id="overview",
        title="Executive overview",
        subtitle=f"{facts['start']} → {facts['end']} · {facts['n_channels']} channels ({facts['n_paid']} with costs) · daily total sales",
        blocks=blocks,
    )


def _label(slug: str) -> str:
    from mmm_studio.core.meridian.spec import CHANNEL_DISPLAY_NAMES

    return CHANNEL_DISPLAY_NAMES.get(slug, slug)


def build_reports_section(ctx: RunContext) -> Section:
    """Build the `reports` section indexing Meridian HTML reports and downloadable run artifacts."""
    prefix = ctx.prefix.rstrip("/") + "/"
    all_keys = ctx.storage.list_keys(prefix)
    html_reports: list[dict[str, Any]] = []
    data_files: list[dict[str, Any]] = []

    report_meta = {
        "meridian_summary.html": (
            "Meridian model results summary",
            "Google Meridian's report: model fit, ROI, contribution and response curves.",
        ),
        "meridian_health_card.html": (
            "Meridian model health card",
            "Diagnostics: convergence (R-hat), prior vs posterior shift, baseline share and ROI plausibility.",
        ),
        "meridian_optimization.html": (
            "Meridian budget optimisation",
            "Same-budget re-allocation: current vs optimised spend and incremental sales by channel.",
        ),
    }

    for k in all_keys:
        rel = k[len(prefix) :] if k.startswith(prefix) else k
        fname = Path(rel).name
        if rel.startswith("reports/") and fname.endswith(".html"):
            title, desc = report_meta.get(fname, (fname, "Standalone HTML report"))
            html_reports.append({"filename": fname, "artifact_name": rel, "title": title, "description": desc})
        elif rel.startswith(("data/", "models/", "meta/")) and not fname.endswith((".tmp", ".wide_input.parquet")):
            data_files.append({"artifact_name": rel, "filename": fname, "category": rel.split("/")[0].upper()})

    rows = [
        {"name": r["title"], "path": r["artifact_name"], "type": "HTML report", "description": r["description"]}
        for r in html_reports
    ] + [
        {
            "name": d["filename"],
            "path": d["artifact_name"],
            "type": d["category"].title(),
            "description": f"{Path(d['filename']).suffix.lstrip('.').upper()} file produced by this run",
        }
        for d in data_files
    ]
    blocks = [
        KpisBlock(
            items=[
                KpiItem(
                    label="Meridian HTML reports",
                    value=str(len(html_reports)),
                    delta="Available after the Meridian fit" if not html_reports else "Open them below",
                    status="success" if len(html_reports) >= 3 else "neutral",
                ),
                KpiItem(label="Data & model files", value=str(len(data_files)), delta="CSV, Parquet, JSON and .binpb"),
            ]
        ),
        TableBlock(
            id="reports_artifact_inventory",
            title="Generated reports and downloads",
            subtitle="Every file is produced from the uploaded workbook during this run.",
            columns=[
                TableColumn(key="name", label="Report / file"),
                TableColumn(key="type", label="Type"),
                TableColumn(key="path", label="Path"),
                TableColumn(key="description", label="Description"),
            ],
            rows=rows,
        ),
    ]
    return Section(
        id="reports",
        title="Reports & downloads",
        subtitle="Google Meridian's HTML reports and every dataset, model and metric file of the run.",
        blocks=blocks,
        metadata={"html_reports": html_reports, "data_files": data_files},
    )


def build_brief_section(
    wide: pd.DataFrame,
    facts: dict[str, Any],
    ols_res: dict[str, Any],
    meridian_metrics: dict[str, Any] | None = None,
    roi_df: pd.DataFrame | None = None,
    opt_res: dict[str, Any] | None = None,
    forecast_summary: dict[str, Any] | None = None,
) -> Section:
    """Build the `brief` section: the case-study questions answered with this run's numbers."""
    from mmm_studio.core.ols import OLS2, best_ols_row

    windows = ols_res["windows"]
    p_label = windows["primary"]["label"]
    f_label = windows.get("forward", {}).get("label")
    ols_p = best_ols_row(ols_res, "primary")
    ols_f = best_ols_row(ols_res, "forward")
    has_mer = _meridian_ready(meridian_metrics)
    mer_p = meridian_metrics["holdout_q4_2024"] if has_mer else None
    mer_f = (meridian_metrics or {}).get("holdout_q3_2024")
    halo = facts.get("halo_7d", {})
    share = facts.get("sales_share", {})
    unpaid_share = sum(share.get(c, 0.0) for c in facts.get("unpaid_channels", []))
    roas = facts.get("attributed_roas", {})

    def pct(m: dict[str, Any] | None) -> str:
        return f"{100 * float(m['MAE_over_mean']):.1f}%" if m else "pending"

    q1 = (
        f"(1) The catalog is the strongest single driver but works with a delay: same-day r = {facts.get('catalog_lag0_r', float('nan')):+.2f}, "
        f"+{facts.get('catalog_best_lag', 0)} days r = {facts.get('catalog_best_lag_r', float('nan')):+.2f}; "
        f"{facts.get('top25_after_sendout', 0)} of the 25 best days fall within a week after a send-out. "
        f"(2) It creates a halo: total sales are {halo.get('total_sales', float('nan')):+.1f}% in the 7 days after a send-out and "
        + ", ".join(f"{c} {v:+.0f}%" for c, v in facts.get("halo_top", [])[:4])
        + ", so attributed ROAS understates the catalog. "
        f"(3) SEA and Affiliate costs follow demand (r(cost, clicks) = {facts.get('sea_cost_clicks_r', float('nan')):.2f}; "
        f"r(cost, own sales) = {facts.get('affiliate_cost_sales_r', float('nan')):.2f}). "
        f"(4) The baseline is strong: unpaid channels carry {unpaid_share:.0f}% of attributed sales, sales grew "
        f"{facts.get('yoy_sales_pct', float('nan')):+.1f}% vs spend {facts.get('yoy_spend_pct', float('nan')):+.1f}%."
    )
    missing = ", ".join(f"{k} {v}" for k, v in facts.get("missing_channel_days", {}).items())
    q2 = (
        f"Long, shuffled rows that need pivoting; missing channel-days ({missing or 'none'}); costs booked on the day of causation "
        f"(catalog {fmt_eur(facts.get('catalog_cost_min', 0))}–{fmt_eur(facts.get('catalog_cost_max', 0))} on one day, effect over weeks); "
        "very different scales; correlated and demand-following costs; regime changes"
        + (f" (Social Media from {facts['social_break']}, Affiliate commission from {facts.get('affiliate_break', '—')})" if "social_break" in facts else "")
        + f"; only {len(facts.get('years', []))} full years; no data on promotions, prices, weather or competitors."
    )
    q3 = (
        "Channel-attributed sales (they add up to the target: leakage); same-day clicks and impressions (outcomes, unknown at "
        f"forecast time); the channels without costs ({', '.join(facts.get('unpaid_channels', []))}) as budget levers; and "
        "realised Affiliate cost as a lever (it is a commission on sales, so it enters only as a control)."
    )
    t2 = (
        "Costs: booking date vs effect date (catalog in-home shift), carry-over, saturation, scale, zero vs missing, billing models. "
        "Most important channels (preliminary OLS with HAC errors): Affiliate, SEA and Catalog; the others cannot be told apart "
        "from zero. New features: catalog in-home spend, calendar controls (weekday, holidays, Christmas, Black Week), yearly "
        "Fourier terms and trend. Challenge: reverse causality and correlated, always-on budgets."
    )
    t3 = (
        "A Bayesian regression (Google Meridian) of daily total sales on adstocked, Hill-saturated channel spend plus controls; "
        f"trained to the day before the {p_label} hold-out. MAE / mean: {pct(mer_p)} on {p_label}"
        + (f" and {pct(mer_f)} on {f_label}" if f_label else "")
        + f"; second metric R² = {float(mer_p['R2']):.3f}." if mer_p else
        f"A Bayesian regression (Google Meridian) is the main model; its results appear when the fit finishes. OLS-2 benchmark: "
        f"{pct(ols_p)} on {p_label}" + (f", {pct(ols_f)} on {f_label}" if f_label else "") + "."
    )
    if has_mer and ols_p:
        t3 += f" The OLS benchmark with the same inputs ({OLS2}) scores {pct(ols_p)}" + (f" and {pct(ols_f)}" if ols_f else "") + "."
    if roi_df is not None and len(roi_df):
        media = roi_df.drop(index="All Channels", errors="ignore").sort_values("roi", ascending=False)
        t3 += " ROI: " + ", ".join(f"{_label(c)} {float(r['roi']):.2f}×" for c, r in media.iterrows()) + " (SEA is an upper bound)."
    t4 = (
        "Marketing submits a daily budget plan (CSV, one cost column per paid channel); the plan's days are appended as hold-out "
        "rows, Meridian is refit, and the app returns a daily forecast with 90 % prediction intervals plus instant what-if scenarios."
    )
    if forecast_summary:
        t4 += (
            f" Example plan {forecast_summary['start']} → {forecast_summary['end']}: {fmt_eur(forecast_summary['sales'])} forecast "
            f"sales for {fmt_eur(forecast_summary['spend'])} spend; leak-free back-test {forecast_summary['backtest_mae_pct']:.1f}% MAE / mean."
        )

    qa_rows = [
        {"task": "1 · Data understanding", "question": "What conclusions follow from the channel interactions and the data?", "answer": q1},
        {"task": "1 · Data understanding", "question": "What obstacles can come up in further processing?", "answer": q2},
        {"task": "1 · Data understanding", "question": "Which influencing factors cannot be used?", "answer": q3},
        {"task": "2 · Data preparation", "question": "Costs, important channels, new features, and the challenge of costs as inputs", "answer": t2},
        {"task": "3 · Modelling", "question": "Approach, features, validation, MAE and a second metric", "answer": t3},
        {"task": "4 · Forecasting", "question": "How is a new budget plan turned into a sales forecast?", "answer": t4},
    ]

    mlops_rows = [
        {"layer": "Data ingestion", "gcp_service": "Cloud Storage + BigQuery", "design": "Land the daily channel feed; validated wide table and German calendar as BigQuery views with data-quality assertions (missing channel-days, commission-rate drift)."},
        {"layer": "Orchestration & retraining", "gcp_service": "Cloud Run Jobs / Vertex AI Pipelines + Cloud Scheduler", "design": "Weekly refit: validation, OLS drift benchmark, then the Meridian MCMC fit on a CPU or GPU job; weekly refits re-anchor the level offset seen on the hold-out."},
        {"layer": "Registry & lineage", "gcp_service": "Vertex AI Model Registry + Cloud Storage", "design": "Version the .binpb model, R-hat, hold-out MAE / mean (gate ≤ 15 %) and the HTML health card before a run becomes active."},
        {"layer": "Serving & scenarios", "gcp_service": "Cloud Run (this app) + Cloud SQL", "design": "Planners upload budget plans, inspect diagnostics and run what-if scenarios and constrained optimisations on queued workers."},
        {"layer": "Monitoring & calibration", "gcp_service": "Cloud Monitoring + Looker Studio", "design": "Track forecast bias and spend regime shifts; re-anchor ROI priors when geo or conversion-lift experiments conclude."},
    ]
    exp_rows = []
    if roi_df is not None and "sea" in roi_df.index:
        exp_rows.append(
            {
                "channel": "SEA",
                "risk": f"ROI {float(roi_df.loc['sea', 'roi']):.2f}× and {float(roi_df.loc['sea', 'pct_of_contribution']):.0f}% of sales; cost follows search demand (upper bound).",
                "experiment": "Geo hold-out or matched-market test",
                "prior_update": "Centre SEA's ROI prior on the measured lift with a tighter σ.",
            }
        )
    if "catalog" in facts:
        cat_roi = f"{float(roi_df.loc['catalog', 'roi']):.2f}×" if roi_df is not None and "catalog" in roi_df.index else "≈ 1"
        exp_rows.append(
            {
                "channel": facts["catalog"],
                "risk": f"{facts['spend_share'].get(facts['catalog'], 0):.1f}% of spend with revenue ROI {cat_roi}; attributed ROAS {roas.get(facts['catalog'], float('nan')):.2f} misses the halo.",
                "experiment": "Postal-code hold-out group per edition",
                "prior_update": "Calibrate the catalog ROI and its 2–3-week carry-over.",
            }
        )
    if "social_spend_ratio" in facts:
        exp_rows.append(
            {
                "channel": "Social Media",
                "risk": f"Regime change on {facts['social_break']} (spend ×{facts['social_spend_ratio']:.1f}, attributed sales ×{facts['social_sales_ratio']:.1f}); only a few months of new-regime data.",
                "experiment": "Conversion-lift / scale-back test",
                "prior_update": "Re-estimate the new regime's saturation once enough data exists.",
            }
        )

    kpis = [
        KpiItem(
            label="Brief target (≤ 15 % MAE / mean)",
            value="Met" if (mer_p or ols_p) and float((mer_p or ols_p)["MAE_over_mean"]) <= 0.15 else "Not met",
            delta=f"{p_label}: {pct(mer_p or ols_p)}" + ("" if mer_p else " (OLS-2)"),
            status="success" if (mer_p or ols_p) and float((mer_p or ols_p)["MAE_over_mean"]) <= 0.15 else "warn",
        ),
        KpiItem(
            label=f"Forward hold-out {f_label or ''}".strip(),
            value=pct(mer_f or ols_f),
            delta="MAE / mean" + ("" if mer_f else " (OLS-2)"),
            status="success" if (mer_f or ols_f) and float((mer_f or ols_f)["MAE_over_mean"]) <= 0.15 else "neutral",
        ),
        KpiItem(label="Second metric (R²)", value=f"{float((mer_p or ols_p)['R2']):.3f}" if (mer_p or ols_p) else "—", delta=f"{p_label} hold-out"),
        KpiItem(label="Next step", value="Lift tests", delta="Calibrate SEA and catalog ROIs", status="warn"),
    ]
    blocks: list[Any] = [
        KpisBlock(items=kpis),
        TableBlock(
            id="brief_qa_summary_table",
            title="Answers to the case-study brief",
            subtitle="Each answer quotes this run's numbers.",
            columns=[
                TableColumn(key="task", label="Brief section"),
                TableColumn(key="question", label="Question"),
                TableColumn(key="answer", label="Answer"),
            ],
            rows=qa_rows,
        ),
        TableBlock(
            id="brief_gcp_mlops_table",
            title="Running it in production on Google Cloud",
            columns=[
                TableColumn(key="layer", label="Layer"),
                TableColumn(key="gcp_service", label="Google Cloud"),
                TableColumn(key="design", label="Design"),
            ],
            rows=mlops_rows,
        ),
    ]
    if exp_rows:
        blocks.append(
            TableBlock(
                id="brief_experiments_roadmap_table",
                title="Limitations and the calibration roadmap",
                subtitle="An MMM is observational; experiments turn its ROIs into causal estimates.",
                columns=[
                    TableColumn(key="channel", label="Channel"),
                    TableColumn(key="risk", label="Limitation"),
                    TableColumn(key="experiment", label="Experiment"),
                    TableColumn(key="prior_update", label="Prior update"),
                ],
                rows=exp_rows,
            )
        )
    blocks.append(
        MarkdownBlock(
            title="Limits of this analysis",
            body=(
                "- The ROIs of demand-following channels (SEA; Affiliate, hence a control) are not causal.\n"
                f"- Only {len(facts.get('years', []))} full years: two Q4s and two Black Fridays, so peak-season effects are the most uncertain.\n"
                "- Missing drivers (promotions, prices, weekly product worlds, weather, competitors) cap the achievable accuracy.\n"
                "- For SEA and Affiliate a budget plan is only an upper limit: their realised cost depends on demand."
            ),
        )
    )
    return Section(
        id="brief",
        title="Answers to the brief",
        subtitle="The case-study questions answered with this run's results, plus the production and calibration roadmap.",
        blocks=blocks,
    )


def s11_benchmark(ctx: RunContext) -> None:
    """OLS benchmark (notebook 04) plus draft overview, brief and reports before Meridian finishes."""
    from mmm_studio.core.meridian.spec import build_meridian_table
    from mmm_studio.core.ols import build_benchmark_section, evaluate_ols_suite

    _, wide, _ = _ensure_dataframes(ctx)
    from mmm_studio.core.preparation import compute_preparation_ols_ladder

    ols_res = evaluate_ols_suite(wide, config=ctx.config)
    ols_res["ladder"] = compute_preparation_ols_ladder(wide, ctx.config)
    ctx.cache["ols_res"] = ols_res
    ctx.save_section(build_benchmark_section(wide, config=ctx.config, ols_res=ols_res))

    spec_cfg = _spec_cfg(ctx, wide)
    mer_tbl = build_meridian_table(wide, catalog_shift=spec_cfg.catalog_shift, n_fourier=spec_cfg.n_fourier, paid_channels=spec_cfg.paid_channels)
    ctx.save_dataframe("meridian_input", mer_tbl.set_index("time"), also_csv=True)

    facts = _facts(ctx)
    ctx.save_section(build_overview_section(wide, facts, ols_res))
    ctx.save_section(build_brief_section(wide, facts, ols_res))
    ctx.save_section(build_reports_section(ctx))


def s12_meridian_fit(ctx: RunContext) -> None:
    """Fit the final Meridian model, the forward hold-out refit, and build the `meridian_model` section."""
    from mmm_studio.core.features import holdout_windows, quarter_label
    from mmm_studio.core.meridian.analysis import build_meridian_model_section, extract_fit_table
    from mmm_studio.core.meridian.fit import fit_meridian_isolated
    from mmm_studio.core.meridian.forecast import _prediction_draws
    from mmm_studio.core.meridian.selection import fit_forward_holdout

    _, wide, _ = _ensure_dataframes(ctx)
    spec_cfg = _spec_cfg(ctx, wide)
    work_dir = ctx.local_work_dir("models")
    bin_path = work_dir / "meridian_final.binpb"
    mmm = fit_meridian_isolated(
        wide,
        spec_cfg,
        bin_path,
        sampling=_sampling_for(ctx.run_profile, "main"),
        seed=ctx.seed,
        spawn_child=ctx.spawn_meridian_child,
        should_cancel=ctx.is_cancelled,
        on_heartbeat=ctx.on_heartbeat,
    )
    ctx.cache["mmm"] = mmm
    ctx.storage.write_bytes(ctx.key("models/meridian_final.binpb"), bin_path.read_bytes())

    fit_df = extract_fit_table(mmm, spec_cfg)
    _, pred_draws = _prediction_draws(mmm, seed=ctx.seed)
    fit_df["prediction_lo"] = np.quantile(pred_draws, 0.05, axis=0)
    fit_df["prediction_hi"] = np.quantile(pred_draws, 0.95, axis=0)
    ctx.cache["fit_df"] = fit_df
    ctx.save_dataframe("meridian_fit_daily", fit_df, also_csv=True)
    ctx.save_dataframe("meridian_holdout_forecast", fit_df[fit_df["holdout"]][["actual", "expected", "expected_lo", "expected_hi"]], also_csv=True)

    # Interim section so results appear while the forward refit runs
    sec, metrics = build_meridian_model_section(mmm, wide, spec_cfg, selection_note=None)
    ctx.save_section(sec)
    ctx.cache["meridian_metrics"] = metrics
    ctx.save_json("meta/meridian_metrics.json", metrics)
    ctx.heartbeat()

    windows = holdout_windows(pd.DatetimeIndex(wide.index), ctx.config.holdout_days if ctx.config else 93)
    if "forward" in windows:
        fwd_metrics, fwd_df = fit_forward_holdout(
            wide,
            spec_cfg,
            windows["forward"],
            work_dir / "meridian_forward.binpb",
            sampling=_sampling_for(ctx.run_profile, "forward"),
            seed=ctx.seed,
            spawn_child=ctx.spawn_meridian_child,
            should_cancel=ctx.is_cancelled,
            on_heartbeat=ctx.on_heartbeat,
        )
        label = quarter_label(windows["forward"][1], windows["forward"][2])
        ctx.save_dataframe("meridian_forward_holdout", fwd_df, also_csv=True)
        sec, metrics = build_meridian_model_section(mmm, wide, spec_cfg, forward_metrics=fwd_metrics, forward_label=label)
        ctx.save_section(sec)
        ctx.cache["meridian_metrics"] = metrics
        ctx.save_json("meta/meridian_metrics.json", metrics)


def s13_drivers_and_optimiser(ctx: RunContext) -> None:
    """ROI, adstock, response curves, the budget optimiser and Meridian's HTML reports."""
    from mmm_studio.core.meridian.analysis import build_drivers_roi_section, generate_meridian_html_reports
    from mmm_studio.core.meridian.optimise import build_optimiser_section, run_meridian_budget_optimizer

    _, wide, _ = _ensure_dataframes(ctx)
    mmm = _load_model(ctx)
    spec_cfg = _spec_cfg(ctx, wide)
    drv_sec, roi_df, response_curves = build_drivers_roi_section(mmm, wide, spec_cfg)
    ctx.cache["roi_df"] = roi_df
    ctx.save_dataframe("meridian_roi", roi_df, also_csv=True)
    ctx.save_json("data/response_curves.json", {ch: df.to_dict(orient="records") for ch, df in response_curves.items()})
    ctx.save_section(drv_sec)

    opt_obj = None
    opt_alloc_df = None
    opt_error = None
    try:
        opt_obj, opt_alloc_df = run_meridian_budget_optimizer(mmm)
        ctx.save_dataframe("meridian_optimised_budget", opt_alloc_df, also_csv=True)
    except Exception as exc:  # noqa: BLE001 - fall back to the response-curve optimiser
        opt_error = f"{type(exc).__name__}: {exc}"

    opt_sec, opt_res = build_optimiser_section(roi_df, response_curves, opt_alloc_df=opt_alloc_df, native_error=opt_error)
    ctx.cache["opt_res"] = opt_res
    ctx.save_json("data/default_optimisation.json", opt_res)
    ctx.save_section(opt_sec)

    rep_dir = ctx.local_work_dir("reports")
    html_paths = generate_meridian_html_reports(mmm, rep_dir, opt_result=opt_obj)
    for _, hp in html_paths.items():
        if hp.exists():
            ctx.storage.write_bytes(ctx.key(f"reports/{hp.name}"), hp.read_bytes(), content_type="text/html; charset=utf-8")


def s14_forecast(ctx: RunContext) -> None:
    """Leak-free back-test, the example budget-plan forecast and the four scenarios of notebook 05."""
    from mmm_studio.core.features import holdout_windows, quarter_label
    from mmm_studio.core.meridian.forecast import (
        build_example_plan,
        build_forecast_scenarios_section,
        evaluate_forecast_scenario,
        forecast_budget_plan,
    )
    from mmm_studio.core.meridian.spec import save_meridian_model

    _, wide, _ = _ensure_dataframes(ctx)
    spec_cfg = _spec_cfg(ctx, wide)
    sampling = _sampling_for(ctx.run_profile, "main")
    plan_cols = [c for c in spec_cfg.paid_slugs if f"cost_{c}" in wide.columns]

    # 1. Back-test: history up to the day before the hold-out, the hold-out's actual spend as the plan
    tr_end, te_start, te_end = holdout_windows(pd.DatetimeIndex(wide.index), ctx.config.holdout_days)["primary"]
    plan_bt = pd.DataFrame({c: wide.loc[te_start:te_end, f"cost_{c}"] for c in plan_cols})
    plan_bt.index.name = "date"
    bt_daily, _, _ = forecast_budget_plan(wide.loc[:tr_end], plan_bt, cfg=spec_cfg, sampling=sampling, seed=ctx.seed)
    bt_daily["actual"] = wide.loc[te_start:te_end, "total_sales"].to_numpy()
    ctx.save_dataframe("forecast_backtest_daily", bt_daily, also_csv=True)
    ctx.heartbeat()

    # 2. Example plan: starts the day after the history, 42 days, two catalog send-outs
    plan_df = build_example_plan(wide, days=42, paid_channels=list(spec_cfg.paid_channels))
    fc_daily, fc_mmm, fc_cfg = forecast_budget_plan(wide, plan_df, cfg=spec_cfg, sampling=sampling, seed=ctx.seed)
    fc_bin = save_meridian_model(fc_mmm, ctx.local_work_dir("models") / "meridian_forecast.binpb")
    ctx.storage.write_bytes(ctx.key("models/meridian_forecast.binpb"), fc_bin.read_bytes())

    # 3. Scenarios on the same window, evaluated with the fitted model (no refit)
    scenarios: dict[str, pd.DataFrame] = {"Plan as submitted": plan_df}
    if "catalog" in plan_df.columns and (plan_df["catalog"] > 0).any():
        last = plan_df.index[plan_df["catalog"] > 0][-1]
        alt = plan_df.copy()
        alt.loc[last, "catalog"] = 0.0
        scenarios[f"Skip the {last:%d %b} catalog"] = alt
    if "display" in plan_df.columns and "sea" in plan_df.columns:
        alt = plan_df.copy()
        moved = 0.2 * alt["display"]
        alt["display"] -= moved
        alt["sea"] += moved
        scenarios["Move 20 % of Display to SEA"] = alt
    digital = [c for c in plan_df.columns if c != "catalog" and c not in spec_cfg.as_controls]
    if digital:
        alt = plan_df.copy()
        alt[digital] = alt[digital] * 1.10
        scenarios["+10 % on all digital channels"] = alt

    base_sales = float(fc_daily["forecast"].sum())
    base_spend = float(plan_df.to_numpy().sum())
    scen_rows = []
    for name, p in scenarios.items():
        d = fc_daily if name == "Plan as submitted" else evaluate_forecast_scenario(fc_mmm, wide, fc_daily, p, fc_cfg, seed=ctx.seed)
        s_sales, s_spend = float(d["forecast"].sum()), float(p.to_numpy().sum())
        ds, dsp = s_sales - base_sales, s_spend - base_spend
        scen_rows.append(
            {"scenario": name, "sales": s_sales, "spend": s_spend, "delta_sales": ds, "delta_spend": dsp, "marginal_roas": ds / dsp if abs(dsp) > 1.0 else np.nan}
        )
    scenarios_df = pd.DataFrame(scen_rows)

    ctx.save_dataframe("forecast_plan", plan_df, also_csv=True)
    ctx.save_dataframe("forecast_daily", fc_daily, also_csv=True)
    ctx.save_dataframe("forecast_scenarios", scenarios_df, also_csv=True)
    bt_label = quarter_label(te_start, te_end)
    bt_mae = float(np.mean(np.abs(bt_daily["actual"] - bt_daily["forecast"])) / bt_daily["actual"].mean())
    ctx.cache["forecast_summary"] = {
        "start": plan_df.index[0].strftime("%Y-%m-%d"),
        "end": plan_df.index[-1].strftime("%Y-%m-%d"),
        "sales": base_sales,
        "spend": base_spend,
        "backtest_mae_pct": 100.0 * bt_mae,
    }
    ctx.save_json("data/forecast_summary.json", ctx.cache["forecast_summary"])
    ctx.save_section(
        build_forecast_scenarios_section(
            wide_df=wide,
            backtest_df=bt_daily,
            forecast_daily=fc_daily,
            plan_df=plan_df,
            scenarios_df=scenarios_df,
            backtest_label=bt_label,
            backtest_train_end=tr_end.strftime("%Y-%m-%d"),
            sampling_label=_sampling_label(sampling),
        )
    )


def _refresh_summary_sections(ctx: RunContext) -> None:
    """Rebuild benchmark, overview, brief and reports from whatever results exist."""
    from mmm_studio.core.ols import build_benchmark_section, evaluate_ols_suite
    from mmm_studio.core.preparation import compute_preparation_ols_ladder

    _, wide, _ = _ensure_dataframes(ctx)
    ols_res = ctx.cache.get("ols_res")
    if ols_res is None:
        ols_res = evaluate_ols_suite(wide, config=ctx.config)
        ols_res["ladder"] = compute_preparation_ols_ladder(wide, ctx.config)
    ctx.cache["ols_res"] = ols_res
    meridian_metrics = _load_optional(ctx, "meridian_metrics", lambda: ctx.load_json("meta/meridian_metrics.json"))
    roi_df = _load_optional(ctx, "roi_df", lambda: ctx.load_dataframe("meridian_roi"))
    fit_df = _load_optional(ctx, "fit_df", lambda: ctx.load_dataframe("meridian_fit_daily"))
    opt_res = _load_optional(ctx, "opt_res", lambda: ctx.load_json("data/default_optimisation.json"))
    fc_sum = _load_optional(ctx, "forecast_summary", lambda: ctx.load_json("data/forecast_summary.json"))
    selection = _load_optional(ctx, "selection", lambda: ctx.load_json("data/model_selection.json"))
    holdout = fit_df[fit_df["holdout"]] if fit_df is not None and "holdout" in fit_df else None

    facts = _facts(ctx)
    ctx.save_section(
        build_benchmark_section(wide, meridian_summary=meridian_metrics, config=ctx.config, ols_res=ols_res, meridian_holdout=holdout)
    )
    ctx.save_section(build_overview_section(wide, facts, ols_res, meridian_metrics, roi_df, opt_res, fit_df, selection))
    ctx.save_section(build_brief_section(wide, facts, ols_res, meridian_metrics, roi_df, opt_res, fc_sum))
    ctx.save_section(build_reports_section(ctx))


def s15_finalize(ctx: RunContext) -> None:
    """Refresh `benchmark`, `overview`, `reports` and `brief` with the Meridian results."""
    _refresh_summary_sections(ctx)


def s16_model_selection(ctx: RunContext) -> None:
    """Notebook 03 §1: catalog shift × Affiliate role grid, then prior / SEA sensitivity (fast fits, full profile)."""
    from mmm_studio.core.features import holdout_windows, quarter_label
    from mmm_studio.core.meridian.analysis import build_meridian_model_section
    from mmm_studio.core.meridian.selection import run_model_selection

    if ctx.run_profile != "full":
        raise StageSkipped("Runs on the full profile only (9 extra fits).")
    _, wide, _ = _ensure_dataframes(ctx)
    spec_cfg = _spec_cfg(ctx, wide)
    result = run_model_selection(
        wide,
        spec_cfg,
        ctx.local_work_dir("models") / "selection",
        sampling=_sampling_for(ctx.run_profile, "selection"),
        seed=ctx.seed,
        spawn_child=ctx.spawn_meridian_child,
        should_cancel=ctx.is_cancelled,
        on_heartbeat=ctx.on_heartbeat,
    )
    ctx.cache["selection"] = result
    ctx.save_json("data/model_selection.json", result)

    mmm = _load_model(ctx)
    fwd_metrics, fwd_label = None, None
    metrics = _load_optional(ctx, "meridian_metrics", lambda: ctx.load_json("meta/meridian_metrics.json"))
    if metrics and "holdout_q3_2024" in metrics:
        fwd_metrics = metrics["holdout_q3_2024"]
        windows = holdout_windows(pd.DatetimeIndex(wide.index), ctx.config.holdout_days)
        fwd_label = metrics.get("forward_label") or (quarter_label(windows["forward"][1], windows["forward"][2]) if "forward" in windows else None)
    sec, _ = build_meridian_model_section(mmm, wide, spec_cfg, forward_metrics=fwd_metrics, forward_label=fwd_label, selection=result)
    ctx.save_section(sec)
    _refresh_summary_sections(ctx)


def run_analysis_stages(ctx: RunContext) -> None:
    """Execute all fast analysis stages (`s01_ingest` .. `s11_benchmark`)."""
    _run_stage(ctx, "s01_ingest", s01_ingest, critical=True)
    _run_stage(ctx, "s02_quality", s02_quality)
    _run_stage(ctx, "s03_target", s03_target)
    _run_stage(ctx, "s04_channels", s04_channels)
    _run_stage(ctx, "s05_spend", s05_spend)
    _run_stage(ctx, "s06_efficiency", s06_efficiency)
    _run_stage(ctx, "s07_correlations", s07_correlations)
    _run_stage(ctx, "s08_catalog", s08_catalog)
    _run_stage(ctx, "s09_readiness", s09_readiness)
    _run_stage(ctx, "s10_preparation", s10_preparation)
    _run_stage(ctx, "s11_benchmark", s11_benchmark)


def run_meridian_stages(ctx: RunContext) -> None:
    """Execute all Bayesian Meridian stages (`s12_meridian_fit` .. `s16_model_selection`)."""
    _run_stage(ctx, "s12_meridian_fit", s12_meridian_fit, critical=True)
    _run_stage(ctx, "s13_drivers_and_optimiser", s13_drivers_and_optimiser)
    _run_stage(ctx, "s14_forecast", s14_forecast)
    _run_stage(ctx, "s15_finalize", s15_finalize)
    _run_stage(ctx, "s16_model_selection", s16_model_selection)


def run_full_pipeline(
    ctx: RunContext,
    skip_meridian: bool = False,
) -> dict[str, Any]:
    """Run the complete MMM Studio pipeline (analysis stages + optional Meridian stages)."""
    t0 = time.monotonic()
    run_analysis_stages(ctx)
    if not skip_meridian:
        run_meridian_stages(ctx)
    elapsed = round(time.monotonic() - t0, 2)
    index_key = ctx.key("sections/_index.json")
    sections = ctx.storage.read_json(index_key) if ctx.storage.exists(index_key) else []
    summary = {
        "run_id": ctx.run_id,
        "run_profile": ctx.run_profile,
        "skip_meridian": skip_meridian,
        "elapsed_seconds": elapsed,
        "n_sections": len(sections),
        "sections": [s["id"] for s in sections],
    }
    ctx.save_json("meta/summary.json", summary)
    return summary
