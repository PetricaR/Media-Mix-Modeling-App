"""Pipeline stages s01–s16 orchestrating core EDA, preparation, OLS benchmark, and Bayesian Meridian."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import line_figure
from mmm_studio.charts.theme import COPPER, HOLDOUT_SHADE, INK_MUTED, NAVY, SLATE
from mmm_studio.core.model_settings import ModelSettings, sampling_for, settings_changes
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

logger = logging.getLogger(__name__)

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


def _sampling_for(profile: str, purpose: str = "main", settings: ModelSettings | None = None) -> dict[str, int]:
    """Sampling per run profile: FULL for the final model and forecasts, FAST for selection/forward fits."""
    return sampling_for(profile, purpose, settings)


def _settings(ctx: RunContext) -> ModelSettings:
    return ctx.config.model_settings if ctx.config is not None else ModelSettings()


def _with_run_settings(ctx: RunContext, sec: Section) -> Section:
    """Append the run's settings that differ from the defaults to the `meridian_model` section."""
    if ctx.config is None:
        return sec
    changes = settings_changes(
        ctx.config,
        seed=ctx.seed,
        profile=ctx.run_profile,
        holdout_default=ctx.profile.default_holdout_days if ctx.profile is not None else None,
    )
    if not changes:
        sec.blocks.append(
            CalloutBlock(
                tone="info",
                title="Settings used",
                body="Every model setting of this run is at its default. Final fit sampled with "
                f"{_sampling_label(_sampling_for(ctx.run_profile, 'main', _settings(ctx)))}.",
            )
        )
        return sec
    sec.blocks.append(
        TableBlock(
            id="meridian_run_settings",
            title="Settings used: changes from the defaults",
            subtitle=f"{len(changes)} setting{'s differ' if len(changes) != 1 else ' differs'} from the defaults; "
            "results of this run are not comparable one-to-one with a run on the defaults.",
            columns=[
                TableColumn(key="setting", label="Setting"),
                TableColumn(key="value", label="This run"),
                TableColumn(key="default", label="Default"),
            ],
            rows=changes,
        )
    )
    return sec


def _selection_note(ctx: RunContext) -> str:
    ms = _settings(ctx)
    shifts = ", ".join(map(str, ms.selection_shifts))
    grid = (
        f"The selection grid (print shift k ∈ {{{shifts}}} × commission channels as media or control) and the prior / "
        "demand-following sensitivity fits"
    )
    if ms.selection_enabled(ctx.run_profile):
        return f"{grid} run after the main results. They appear here when they finish."
    if ms.model_selection is False:
        return f"{grid} are switched off in this run's settings."
    return f"{grid} run on the **full** profile only, or when they are switched on in the run settings."


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
        from mmm_studio.errors import public_error

        logger.exception("Stage %s failed for run %s", stage_key, ctx.run_id)
        if ctx.on_stage_error:
            ctx.on_stage_error(stage_key, public_error(exc))
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
        from mmm_studio.core.preparation import compute_channel_importance

        _, wide, panel = _ensure_dataframes(ctx)
        facts = compute_case_facts(panel, ctx.config)
        try:
            importance, _ = compute_channel_importance(_fit_panel(ctx, wide), ctx.config)
            positive = importance[(importance["p_value"] < 0.05) & (importance["std_effect"] > 0)]
            facts["important_channels"] = list(positive.sort_values("std_effect", ascending=False).index)
        except Exception:  # noqa: BLE001 - the brief then leaves the sentence out rather than guessing
            facts["important_channels"] = None
        ctx.cache["facts"] = facts
    return ctx.cache["facts"]


def _spec_cfg(ctx: RunContext, wide: pd.DataFrame) -> Any:
    from mmm_studio.core.meridian.spec import MeridianSpecConfig

    if "meridian_cfg" not in ctx.cache:
        ctx.cache["meridian_cfg"] = MeridianSpecConfig.from_role_config(ctx.config, wide=wide)
    return ctx.cache["meridian_cfg"]


def _leak_free(ctx: RunContext) -> bool:
    """Whether this run scores its windows leak-free (`ModelSettings.leak_free_validation`, `core/as_of.py`)."""
    from mmm_studio.core.as_of import leak_free

    return leak_free(ctx.config)


def _fit_panel(ctx: RunContext, wide: pd.DataFrame) -> pd.DataFrame:
    """The days modelling choices may be read from: those before the test window under leak-free validation (the feature
    ladder, channel importance), every day otherwise."""
    if not _leak_free(ctx):
        return wide
    from mmm_studio.core.as_of import scoring_cutoff

    return wide.loc[: scoring_cutoff(pd.DatetimeIndex(wide.index), ctx.config.holdout_days)]


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
    from mmm_studio.core.workbook import INPUT_KEYS, profile_workbook

    stored = next((k for k in INPUT_KEYS if ctx.storage.exists(ctx.key(k))), INPUT_KEYS[0])
    filename = ctx.cache.get("workbook_filename") or stored.split("/")[-1]
    if "workbook_bytes" in ctx.cache:
        raw_bytes = ctx.cache["workbook_bytes"]
    else:
        raw_bytes = ctx.storage.read_bytes(ctx.key(stored))

    raw_df, profile = profile_workbook(raw_bytes, filename=filename, role_config=ctx.config)
    ctx.profile = profile
    ctx.config = profile.role_config

    wide_full = build_daily_wide_table(raw_df, config=ctx.config, drop_2025=False)
    model_index = (
        build_daily_wide_table(raw_df, config=ctx.config, drop_2025=True).index
        if ctx.config.drop_trailing_isolated_days
        else wide_full.index
    )
    if ctx.config.model_settings.weather_controls:
        from mmm_studio.core.as_of import leak_free, scoring_cutoff
        from mmm_studio.core.weather import weather_controls

        # Leak-free: the seasonal normal is fitted on the days before the test window only
        normal_until = scoring_cutoff(pd.DatetimeIndex(model_index), ctx.config.holdout_days) if leak_free(ctx.config) else None
        wide_full = wide_full.join(weather_controls(ctx.config.country, pd.DatetimeIndex(wide_full.index), normal_until=normal_until))
    wide_model = wide_full.loc[model_index] if ctx.config.drop_trailing_isolated_days else wide_full
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
    from mmm_studio.core.eda.target import build_explore_target_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_target_section(panel_df, ctx.config))


def s04_channels(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_channels_section

    _, _, panel_df = _ensure_dataframes(ctx)
    if not ctx.config.has_channel_sales:
        raise StageSkipped("The data has no sales per channel, so there is no channel-sales landscape; Spend describes the channels.")
    ctx.save_section(build_explore_channels_section(panel_df, ctx.config))


def s05_spend(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_spend_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_spend_section(panel_df, ctx.config))


def s06_efficiency(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_efficiency_section

    _, _, panel_df = _ensure_dataframes(ctx)
    if not ctx.config.has_channel_sales and not any(c.has_impressions or c.has_clicks for c in ctx.config.channels if c.enabled):
        raise StageSkipped("The data has no sales, impressions or clicks per channel, which CPC, CPM, CTR and ROAS need.")
    ctx.save_section(build_explore_efficiency_section(panel_df, ctx.config))


def s07_correlations(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_correlations_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_correlations_section(panel_df, ctx.config))


def s08_catalog(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_catalog_section

    _, _, panel_df = _ensure_dataframes(ctx)
    if not ctx.config.print_event_channels:
        raise StageSkipped("No channel is a print event (a catalogue or mailing drop), so there is no send-out to study.")
    ctx.save_section(build_explore_catalog_section(panel_df, ctx.config))


def s09_readiness(ctx: RunContext) -> None:
    from mmm_studio.core.eda import build_explore_readiness_section

    _, _, panel_df = _ensure_dataframes(ctx)
    ctx.save_section(build_explore_readiness_section(panel_df, ctx.config))


def s10_preparation(ctx: RunContext) -> None:
    from mmm_studio.core.preparation import build_preparation_section

    _, wide, _ = _ensure_dataframes(ctx)
    ctx.save_section(build_preparation_section(wide, ctx.config))


@dataclass
class ChannelNarrative:
    """Display names the narratives need: channels whose cost follows demand (upper-bound ROI), commission controls,
    the print-event channel, and whether missing-row controls are on."""

    upper: list[str] = field(default_factory=list)
    commission: list[str] = field(default_factory=list)
    print_name: str | None = None
    missing_controls: bool = False


def _narrative(ctx: RunContext, wide: pd.DataFrame) -> ChannelNarrative:
    from mmm_studio.core.meridian.spec import channel_label

    cfg = _spec_cfg(ctx, wide)
    return ChannelNarrative(
        upper=[channel_label(c) for c in cfg.upper_bound_slugs],
        commission=[channel_label(c) for c in cfg.commission_slugs],
        print_name=channel_label(cfg.print_slugs[0]) if cfg.print_slugs else None,
        missing_controls=bool(_settings(ctx).missing_row_controls),
    )


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
    names: ChannelNarrative | None = None,
    forecast_summary: dict[str, Any] | None = None,
) -> Section:
    """Build the `overview` section: headline accuracy, key findings and the fit trajectory, all computed.

    Every conclusion (target met or not, which model is more accurate, which channels need an experiment) is derived
    from the numbers it quotes and from the run's channel roles in `names`.
    """
    names = names or ChannelNarrative()
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
        chart_sub = (
            "The shaded window is forecast as a budget plan made the day before it, by a refit on the earlier days only."
            if has_mer and meridian_metrics.get("leak_free")
            else "The shaded window is predicted from the budget and the calendar only."
        )
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

    pt_pct = forecast_summary.get("plan_time_mae_pct") if has_mer and forecast_summary else None
    ols_pct = 100 * float(ols_p["MAE_over_mean"]) if has_mer and ols_p else None
    holdouts_meet = all(v <= 15.0 for v in (q_pct, f_pct) if v is not None)
    meets_target = holdouts_meet and (pt_pct is None or pt_pct <= 15.0)

    findings: list[dict[str, str]] = []
    acc_text = f"{model_name}: {q_pct:.1f}% MAE / mean on the {p_label} hold-out (R² {float(main['R2']):.3f})"
    if f_pct is not None and f_label:
        acc_text += f" and {f_pct:.1f}% on the {f_label} forward hold-out"
    if pt_pct is not None:
        acc_text += f"; {pt_pct:.1f}% in the back-test from a plan ({', '.join(names.commission)} estimated, not realised)"
    if ols_pct is not None:
        acc_text += f"; the OLS benchmark with the same inputs scores {ols_pct:.1f}%"
    findings.append(
        {
            "area": "Forecast accuracy (target ≤ 15 %)",
            "finding": acc_text + ".",
            "implication": "The target is met on every hold-out and back-test."
            if meets_target
            else (
                "The hold-outs meet the target only with the realised commission; a forecast from a plan alone misses it."
                if holdouts_meet
                else "The target is not met on every hold-out; see the Model, Benchmark and Forecast pages."
            ),
        }
    )
    print_name = names.print_name or facts.get("catalog")
    if "catalog_lag0_r" in facts and print_name:
        findings.append(
            {
                "area": f"{print_name}: delayed effect and halo",
                "finding": (
                    f"Same-day r = {facts['catalog_lag0_r']:+.2f}, but r = {facts['catalog_best_lag_r']:+.2f} at +{facts['catalog_best_lag']} days. "
                    f"In the 7 days after a send-out total sales are {halo.get('total_sales', float('nan')):+.1f}%"
                    + (f" and channels it feeds rise too ({halo_top})." if halo_top else ".")
                ),
                "implication": (
                    f"Model total sales and move {print_name} spend to its in-home date"
                    + (f": adjusted R² {ladder[-2]['adj_r2']:.2f} → {ladder[-1]['adj_r2']:.2f}." if ladder and len(ladder) >= 4 else ".")
                ),
            }
        )
    if "affiliate_cost_sales_r" in facts and names.commission:
        commission = names.commission[0]
        commission_slugs = (selection or {}).get("commission_channels") or []
        media_rois = [
            r["roi"][c] for r in (selection or {}).get("selection", []) if r["affiliate"] == "media" for c in commission_slugs if c in r["roi"]
        ]
        aff_text = f"{commission} cost is a commission on its own sales (r = {facts['affiliate_cost_sales_r']:.2f}"
        if "affiliate_commission_pre_pct" in facts:
            aff_text += (
                f"; {facts['affiliate_commission_pre_pct']:.1f}% of sales before {facts['affiliate_break']}, "
                f"{facts['affiliate_commission_post_pct']:.1f}% after"
            )
        aff_text += ")."
        if media_rois:
            aff_text += f" As a media channel it gets an implausible ROI of ~{np.mean(media_rois):.0f}."
        upper_txt = f"; {', '.join(names.upper)} ROI{'s are upper bounds' if len(names.upper) > 1 else ' is an upper bound'}" if names.upper else ""
        findings.append(
            {
                "area": f"Reverse causality: {commission}" + (f" (and {', '.join(names.upper)})" if names.upper else ""),
                "finding": aff_text,
                "implication": f"{commission} enters Meridian as a demand-following control, not a lever{upper_txt}.",
            }
        )
    if roi_df is not None and len(roi_df) > 0:
        media = roi_df.drop(index="All Channels", errors="ignore")
        share = float(roi_df.loc["All Channels", "pct_of_contribution"]) if "All Channels" in roi_df.index else float("nan")
        best = media["roi"].idxmax()
        parts = [f"Media explains {share:.0f}% of modelled sales; highest ROI: {_label(best)} {float(media.loc[best, 'roi']):.2f}×"]
        print_slug = next((c for c in media.index if _label(c) == print_name), None)
        if print_slug is not None:
            parts.append(f"{print_name} {float(media.loc[print_slug, 'roi']):.2f}× (marginal {float(media.loc[print_slug, 'mroi']):.2f}×)")
        impl = (
            f"Validate {', '.join(names.upper)} with a geo or hold-out test before moving budget into {'it' if len(names.upper) == 1 else 'them'}."
            if names.upper
            else "Validate the largest budget moves with a geo or hold-out test first."
        )
        if opt_res and opt_res.get("allocations"):
            donor = min(opt_res["allocations"], key=lambda a: a["spend_delta"])
            recip = max(opt_res["allocations"], key=lambda a: a["spend_delta"])
            fixed = opt_res.get("upper_bound_channels") or []
            note = f" with {', '.join(_label(c) for c in fixed)} held at current spend" if fixed else ""
            impl = (
                f"The optimiser moves budget from {donor['label']} to {recip['label']}{note} ({opt_res['lift_pct']:+.1f}% incremental "
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
            f"{facts.get('social_channel', 'Social Media')} regime change on {facts['social_break']}: spend ×{facts['social_spend_ratio']:.1f}, "
            f"clicks ×{facts['social_clicks_ratio']:.1f}, attributed sales ×{facts['social_sales_ratio']:.1f}"
        )
    findings.append(
        {
            "area": "Data issues handled",
            "finding": ("; ".join(dq) + ".")[0].upper() + ("; ".join(dq) + ".")[1:] if dq else "No structural data issues found.",
            "implication": (
                "Days with missing channel rows get a control in the models, so they are not read as sales drops"
                if names.missing_controls
                else "Missing channel-days count as 0 sales (missing-row controls off)"
            )
            + "; an isolated trailing day stays in the models but not in yearly views.",
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
    hold_txt = f"the {p_label} hold-out with **{q_pct:.1f}% MAE / mean**" + (
        f" and the {f_label} forward hold-out with **{f_pct:.1f}%**" if f_pct is not None and f_label else ""
    )
    commission_txt = ", ".join(names.commission)
    pt_txt = (
        f"from a budget plan alone (with {commission_txt} estimated instead of its realised cost) the back-test scores "
        f"**{pt_pct:.1f}%**"
        if pt_pct is not None
        else ""
    )
    if meets_target:
        verdict = f"Yes: {model_name} forecasts {hold_txt}, within the brief's 15 % target." + (
            f" {pt_txt[0].upper()}{pt_txt[1:]}." if pt_txt else ""
        )
    elif holdouts_meet:
        verdict = (
            f"Only with the realised {commission_txt} cost: {model_name} forecasts {hold_txt}, within the brief's 15 % "
            f"target, but {pt_txt}, outside it."
        )
    else:
        verdict = f"Not reliably: {model_name} forecasts {hold_txt}; at least one hold-out misses the brief's 15 % target." + (
            f" {pt_txt[0].upper()}{pt_txt[1:]}." if pt_txt else ""
        )
    if has_mer:
        comparison = ""
        if ols_pct is not None:
            if q_pct <= ols_pct:
                comparison = f" It is at least as accurate as the transparent OLS benchmark ({ols_pct:.1f}%)."
            else:
                comparison = f" The transparent OLS benchmark with the same inputs is more accurate ({ols_pct:.1f}%)."
        method = (
            "The main model is a Bayesian regression (Google Meridian) on adstocked, saturated channel spend plus calendar, "
            "seasonality and trend controls." + comparison
        )
    else:
        method = "These are OLS benchmark numbers; the Meridian results replace them when the Bayesian fit finishes."
    summary_body = f"**Can daily sales be forecast from the marketing budget?** {verdict} {method}"
    blocks: list[Any] = [
        KpisBlock(items=kpis),
        CalloutBlock(tone="success" if meets_target else "warn", title="Executive summary", body=summary_body),
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
    from mmm_studio.core.meridian.spec import channel_label

    return channel_label(slug)


def build_reports_section(ctx: RunContext) -> Section:
    """Build the `reports` section indexing Meridian HTML reports and downloadable run artifacts."""
    prefix = ctx.prefix.rstrip("/") + "/"
    all_keys = ctx.storage.list_keys(prefix)
    html_reports: list[dict[str, Any]] = []
    data_files: list[dict[str, Any]] = []

    report_meta = {
        "meridian_eda.html": (
            "Meridian exploratory data analysis",
            "Pre-modeling diagnostics: treatment variation, correlation, VIF and spend share.",
        ),
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
    names: ChannelNarrative | None = None,
) -> Section:
    """Build the `brief` section: the executive brief questions answered with this run's numbers.

    Sentences about a channel appear only when the run has it; nothing falls back to a fixed value.
    """
    names = names or ChannelNarrative()
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

    print_name = names.print_name or facts.get("catalog")
    commission = ", ".join(names.commission)
    upper = ", ".join(names.upper)
    q1_parts = []
    if "catalog_lag0_r" in facts and print_name:
        q1_parts.append(
            f"{print_name} works with a delay: same-day r = {facts['catalog_lag0_r']:+.2f}, +{facts['catalog_best_lag']} days "
            f"r = {facts['catalog_best_lag_r']:+.2f}; {facts.get('top25_after_sendout', 0)} of the 25 best days fall within a week "
            "after a send-out."
        )
        if halo:
            q1_parts.append(
                f"It creates a halo: total sales are {halo.get('total_sales', float('nan')):+.1f}% in the 7 days after a send-out"
                + (" and " + ", ".join(f"{c} {v:+.0f}%" for c, v in facts.get("halo_top", [])[:4]) if facts.get("halo_top") else "")
                + f", so attributed ROAS understates {print_name}."
            )
    following = []
    if names.upper:
        rs = facts.get("cost_clicks_r", {})
        r_txt = ", ".join(f"{n} {rs[n]:.2f}" for n in names.upper if n in rs and rs[n] == rs[n])
        following.append(f"{upper} cost follows {('its' if len(names.upper) == 1 else 'their')} clicks" + (f" (r = {r_txt})" if r_txt else ""))
    if names.commission and "affiliate_cost_sales_r" in facts:
        following.append(f"{commission} cost follows its own sales (r = {facts['affiliate_cost_sales_r']:.2f})")
    if following:
        q1_parts.append("Costs follow demand: " + "; ".join(following) + ".")
    if "yoy_sales_pct" in facts:
        q1_parts.append(
            f"The baseline is strong: unpaid channels carry {unpaid_share:.0f}% of attributed sales; sales grew "
            f"{facts['yoy_sales_pct']:+.1f}% vs spend {facts['yoy_spend_pct']:+.1f}%."
        )
    q1 = " ".join(f"({i}) {t}" for i, t in enumerate(q1_parts, start=1)) or "No channel interaction stands out in this workbook."
    missing = ", ".join(f"{k} {v}" for k, v in facts.get("missing_channel_days", {}).items())
    breaks = []
    if "social_break" in facts:
        breaks.append(f"{facts.get('social_channel', 'Social Media')} from {facts['social_break']}")
    if "affiliate_break" in facts and names.commission:
        breaks.append(f"{names.commission[0]} commission from {facts['affiliate_break']}")
    q2 = (
        f"Long, shuffled rows that need pivoting; missing channel-days ({missing or 'none'}); "
        + (
            f"costs booked on the day of causation ({print_name} {fmt_eur(facts['catalog_cost_min'])}–{fmt_eur(facts['catalog_cost_max'])} "
            "on one day, effect over weeks); "
            if print_name and "catalog_cost_min" in facts
            else ""
        )
        + "very different scales; correlated and demand-following costs"
        + (f"; regime changes ({', '.join(breaks)})" if breaks else "")
        + f"; only {len(facts.get('years', []))} full years; no data on promotions, prices, weather or competitors."
    )
    q3 = (
        "Channel-attributed sales (they add up to the target: leakage); same-day clicks and impressions (outcomes, unknown at "
        f"forecast time); the channels without costs ({', '.join(facts.get('unpaid_channels', [])) or 'none'}) as budget levers"
        + (f"; and realised {commission} cost as a lever (a commission on sales, so it enters only as a control)." if commission else ".")
    )
    important = facts.get("important_channels")
    importance_txt = (
        f"Most important channels (preliminary OLS with HAC errors, p < 0.05): {', '.join(important)}; the others cannot be "
        "told apart from zero."
        if important
        else "No channel's preliminary OLS effect is distinguishable from zero (HAC errors, p < 0.05)."
        if important is not None
        else ""
    )
    t2 = (
        "Costs: booking date vs effect date"
        + (f" ({print_name} in-home shift)" if print_name else "")
        + ", carry-over, saturation, scale, zero vs missing, billing models. "
        + importance_txt
        + " New features: "
        + (f"{print_name} in-home spend, " if print_name else "")
        + "calendar controls (weekday, holidays, Christmas, Black Week), yearly Fourier terms and trend. Challenge: reverse "
        "causality and correlated, always-on budgets."
    )
    if mer_p:
        t3 = (
            "A Bayesian regression (Google Meridian) of daily total sales on adstocked, Hill-saturated channel spend plus controls; "
            f"trained to the day before the {p_label} hold-out. MAE / mean: {pct(mer_p)} on {p_label}"
            + (f" and {pct(mer_f)} on {f_label}" if f_label and mer_f else "")
            + f"; second metric R² = {float(mer_p['R2']):.3f}."
        )
    else:
        t3 = (
            f"A Bayesian regression (Google Meridian) is the main model; its results appear when the fit finishes. OLS-2 benchmark: "
            f"{pct(ols_p)} on {p_label}" + (f", {pct(ols_f)} on {f_label}" if f_label else "") + "."
        )
    if has_mer and ols_p:
        t3 += f" The OLS benchmark with the same inputs ({OLS2}) scores {pct(ols_p)}" + (f" and {pct(ols_f)}" if ols_f else "") + "."
    if roi_df is not None and len(roi_df):
        media = roi_df.drop(index="All Channels", errors="ignore").sort_values("roi", ascending=False)
        t3 += " ROI: " + ", ".join(f"{_label(c)} {float(r['roi']):.2f}×" for c, r in media.iterrows())
        t3 += f" ({upper} {'is an upper bound' if len(names.upper) == 1 else 'are upper bounds'})." if names.upper else "."
    t4 = (
        "Marketing submits a daily budget plan (CSV, one cost column per paid channel); the plan's days are appended as hold-out "
        "rows, Meridian is refit, and the app returns a daily forecast with prediction intervals plus instant what-if scenarios."
    )
    if forecast_summary:
        t4 += (
            f" Example plan {forecast_summary['start']} → {forecast_summary['end']}: {fmt_eur(forecast_summary['sales'])} forecast "
            f"sales for {fmt_eur(forecast_summary['spend'])} spend; leak-free back-test {forecast_summary['backtest_mae_pct']:.1f}% MAE / mean"
            + (
                f" ({forecast_summary['plan_time_mae_pct']:.1f}% with {commission} estimated from history, as a plan would)."
                if forecast_summary.get("plan_time_mae_pct") is not None and commission
                else "."
            )
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
    if roi_df is not None:
        from mmm_studio.core.meridian.spec import channel_label

        for c in roi_df.drop(index="All Channels", errors="ignore").index:
            if channel_label(c) in names.upper:
                exp_rows.append(
                    {
                        "channel": channel_label(c),
                        "risk": f"ROI {float(roi_df.loc[c, 'roi']):.2f}× and {float(roi_df.loc[c, 'pct_of_contribution']):.0f}% of modelled sales; cost follows demand (upper bound).",
                        "experiment": "Geo hold-out or matched-market test",
                        "prior_update": f"Centre {channel_label(c)}'s ROI prior on the measured lift with a tighter σ.",
                    }
                )
    if print_name and print_name in facts.get("spend_share", {}):
        print_slug = next((c for c in (roi_df.index if roi_df is not None else []) if _label(c) == print_name), None)
        cat_roi = f"{float(roi_df.loc[print_slug, 'roi']):.2f}×" if print_slug is not None else "pending (Meridian fit)"
        exp_rows.append(
            {
                "channel": print_name,
                "risk": f"{facts['spend_share'][print_name]:.1f}% of paid spend with revenue ROI {cat_roi}; attributed ROAS "
                f"{roas.get(print_name, float('nan')):.2f} misses the halo.",
                "experiment": "Postal-code hold-out group per edition",
                "prior_update": f"Calibrate the {print_name} ROI and its carry-over.",
            }
        )
    if "social_spend_ratio" in facts:
        exp_rows.append(
            {
                "channel": facts.get("social_channel", "Social Media"),
                "risk": f"Regime change on {facts['social_break']} (spend ×{facts['social_spend_ratio']:.1f}, attributed sales ×{facts['social_sales_ratio']:.1f}); only a few months of new-regime data.",
                "experiment": "Conversion-lift / scale-back test",
                "prior_update": "Re-estimate the new regime's saturation once enough data exists.",
            }
        )
    next_targets = [*names.upper, *([print_name] if print_name else [])]

    # The verdict uses every window the run has (hold-out, forward hold-out, back-test from a plan) of one model: Meridian's
    # once it exists (its forward hold-out may be switched off), the OLS-2 benchmark's before that
    prim, fwd = (mer_p, mer_f) if mer_p else (ols_p, ols_f)
    plan_time = (forecast_summary or {}).get("plan_time_mae_pct") if mer_p else None
    checks = [100 * float(m["MAE_over_mean"]) for m in (prim, fwd) if m] + ([plan_time] if plan_time is not None else [])
    target_met = bool(checks) and all(v <= 15.0 for v in checks)
    kpis = [
        KpiItem(
            label="Brief target (≤ 15 % MAE / mean)",
            value="Met" if target_met else "Not met",
            delta=f"{p_label}: {pct(prim)}"
            + ("" if mer_p else " (OLS-2)")
            + (f" · from a plan: {plan_time:.1f}%" if plan_time is not None else ""),
            status="success" if target_met else "warn",
        ),
        KpiItem(
            label=f"Forward hold-out {f_label or ''}".strip(),
            value=pct(fwd) if fwd or not mer_p else "—",
            delta=("MAE / mean" + ("" if mer_p else " (OLS-2)")) if fwd or not mer_p else "Meridian's forward refit is off in this run",
            status="success" if fwd and float(fwd["MAE_over_mean"]) <= 0.15 else "neutral",
        ),
        KpiItem(label="Second metric (R²)", value=f"{float(prim['R2']):.3f}" if prim else "—", delta=f"{p_label} hold-out"),
        KpiItem(
            label="Next step",
            value="Lift tests",
            delta=f"Calibrate the {' and '.join(next_targets)} ROIs" if next_targets else "Calibrate the largest ROIs",
            status="warn",
        ),
    ]
    blocks: list[Any] = [
        KpisBlock(items=kpis),
        TableBlock(
            id="brief_qa_summary_table",
            title="Answers to the executive brief",
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
                (
                    f"- The ROIs of demand-following channels ({', '.join(x for x in (upper, commission + ', hence a control' if commission else '') if x)}) are not causal.\n"
                    if upper or commission
                    else ""
                )
                + f"- Only {len(facts.get('years', []))} full year{'s' if len(facts.get('years', [])) != 1 else ''}, so peak-season effects are the most uncertain.\n"
                + "- Drivers missing from the data (promotions, prices, weather, competitors) cap the achievable accuracy.\n"
                + (
                    f"- For {', '.join(x for x in (upper, commission) if x)} a budget plan is only an upper limit: their realised cost depends on demand."
                    if upper or commission
                    else ""
                )
            ),
        )
    )
    return Section(
        id="brief",
        title="Answers to the brief",
        subtitle="The executive brief questions answered with this run's results, plus the production and calibration roadmap.",
        blocks=blocks,
    )


def s11_benchmark(ctx: RunContext) -> None:
    """OLS benchmark plus draft overview, brief and reports before Meridian finishes."""
    from mmm_studio.core.meridian.spec import meridian_table
    from mmm_studio.core.ols import build_benchmark_section, evaluate_ols_suite

    _, wide, _ = _ensure_dataframes(ctx)
    from mmm_studio.core.preparation import compute_preparation_ols_ladder

    ols_res = evaluate_ols_suite(wide, config=ctx.config)
    ols_res["ladder"] = compute_preparation_ols_ladder(_fit_panel(ctx, wide), ctx.config)
    ctx.cache["ols_res"] = ols_res
    ctx.save_section(build_benchmark_section(wide, config=ctx.config, ols_res=ols_res))

    spec_cfg = _spec_cfg(ctx, wide)
    mer_tbl = meridian_table(wide, spec_cfg)
    ctx.save_dataframe("meridian_input", mer_tbl.set_index("time"), also_csv=True)

    if ctx.storage.exists(ctx.key("meta/meridian_metrics.json")):
        # Meridian results of this run already exist: rebuild the final summaries, never the drafts
        _refresh_summary_sections(ctx)
        return
    facts = _facts(ctx)
    names = _narrative(ctx, wide)
    ctx.save_section(build_overview_section(wide, facts, ols_res, names=names))
    ctx.save_section(build_brief_section(wide, facts, ols_res, names=names))
    ctx.save_section(build_reports_section(ctx))


def _fit_kwargs(ctx: RunContext) -> dict[str, Any]:
    return {
        "spawn_child": ctx.spawn_meridian_child,
        "should_cancel": ctx.is_cancelled,
        "on_heartbeat": ctx.on_heartbeat,
        "timeout_seconds": ctx.fit_timeout_seconds,
    }


def _s12_leak_free(ctx: RunContext) -> None:
    """Leak-free validation, then the final model (`ModelSettings.leak_free_validation`).

    The validation and test windows are each forecast as a budget plan made the day before them (`forecast_window`: a
    refit on the days before the window, its sales absent from the input, outcome-driven costs planned from history), so
    their scores are genuine forecasts; nothing of a window reaches the model scored on it. Only then is the final model
    (ROIs, optimiser, reports) fitted on every day. The fit table shows the final model in-sample and, on the test days,
    the test forecast; s14 reuses both windows as its back-tests instead of refitting them.
    """
    from mmm_studio.core.features import holdout_windows, quarter_label
    from mmm_studio.core.meridian.analysis import build_meridian_model_section, extract_fit_table
    from mmm_studio.core.meridian.fit import fit_meridian_isolated
    from mmm_studio.core.meridian.forecast import _prediction_draws, level_uncertainty, window_level_error
    from mmm_studio.core.meridian.selection import forecast_window
    from mmm_studio.core.meridian.spec import channel_label
    from mmm_studio.core.ols import evaluate_ols_suite

    _, wide, _ = _ensure_dataframes(ctx)
    spec_cfg = _spec_cfg(ctx, wide)
    ms = _settings(ctx)
    fit_kwargs = _fit_kwargs(ctx)
    windows = holdout_windows(pd.DatetimeIndex(wide.index), ctx.config.holdout_days)
    outcome = spec_cfg.outcome_driven_slugs
    estimated = [channel_label(c) for c in outcome]
    # How far a forecast's level drifts, from leak-free windows that end before the scored one (OLS-2 walk-forward folds)
    ols_res = ctx.cache.get("ols_res") or evaluate_ols_suite(wide, config=ctx.config)
    level_windows = list(ols_res["cv"].get("level_errors", []))

    def level_sd(start: pd.Timestamp) -> tuple[float, dict[str, Any] | None]:
        lvl = level_uncertainty(level_windows, before=start)
        return (lvl["level_sd"] if lvl else 0.0), lvl

    # 1. Validation window, forecast from the days before it
    fwd_metrics, fwd_label = None, None
    if "forward" in windows and ms.forward_holdout:
        w = windows["forward"]
        val = forecast_window(
            wide,
            spec_cfg,
            w,
            _sampling_for(ctx.run_profile, "forward", ms),
            ctx.seed,
            outcome_driven=outcome,
            level_sd=level_sd(w[1])[0],
            **fit_kwargs,
        )
        fwd_metrics, fwd_label = val.metrics, quarter_label(w[1], w[2])
        ctx.save_dataframe("forecast_validation_daily", val.daily, also_csv=True)
        ctx.save_dataframe("meridian_forward_holdout", val.holdout_table(), also_csv=True)
        level_windows.append(
            {
                "source": "Meridian forward hold-out",
                "start": w[1].strftime("%Y-%m-%d"),
                "end": w[2].strftime("%Y-%m-%d"),
                "error": window_level_error(val.daily["actual"], val.daily["forecast"]),
            }
        )
        ctx.heartbeat()

    # 2. Test window (the hold-out), forecast from the days before it
    w = windows["primary"]
    test_level_sd, test_level = level_sd(w[1])
    test = forecast_window(
        wide,
        spec_cfg,
        w,
        _sampling_for(ctx.run_profile, "main", ms),
        ctx.seed,
        outcome_driven=outcome,
        level_sd=test_level_sd,
        **fit_kwargs,
    )
    ctx.save_dataframe("forecast_backtest_daily", test.daily, also_csv=True)
    ctx.save_json(
        "data/backtest_meta.json",
        {
            "leak_free": True,
            "train_end": w[0].strftime("%Y-%m-%d"),
            "total_lo": test.total_pi[0],
            "total_hi": test.total_pi[1],
            "ar1": test.ar1,
            "level": test_level,
            "estimated_costs": list(outcome),
        },
    )
    ctx.heartbeat()

    # 3. The final model on every day, now that its windows are scored
    work_dir = ctx.local_work_dir("models")
    bin_path = work_dir / "meridian_final.binpb"
    mmm = fit_meridian_isolated(
        wide, spec_cfg.for_all_days(wide), bin_path, sampling=_sampling_for(ctx.run_profile, "main", ms), seed=ctx.seed, **fit_kwargs
    )
    ctx.cache["mmm"] = mmm
    ctx.storage.write_bytes(ctx.key("models/meridian_final.binpb"), bin_path.read_bytes())

    fit_df = extract_fit_table(mmm, spec_cfg)  # `holdout` marks the test days
    _, pred_draws = _prediction_draws(mmm, seed=ctx.seed)
    fit_df["prediction_lo"] = np.quantile(pred_draws, 0.05, axis=0)
    fit_df["prediction_hi"] = np.quantile(pred_draws, 0.95, axis=0)
    fit_df["in_sample_expected"] = fit_df["expected"]
    ho = test.holdout_table()
    for col in ("actual", "expected", "expected_lo", "expected_hi", "prediction_lo", "prediction_hi"):
        fit_df.loc[ho.index, col] = ho[col].to_numpy()
    ctx.cache["fit_df"] = fit_df
    ctx.save_dataframe("meridian_fit_daily", fit_df, also_csv=True)
    ctx.save_dataframe("meridian_holdout_forecast", fit_df[fit_df["holdout"]][["actual", "expected", "expected_lo", "expected_hi"]], also_csv=True)

    sec, metrics = build_meridian_model_section(
        mmm,
        wide,
        spec_cfg,
        forward_metrics=fwd_metrics,
        forward_label=fwd_label,
        selection_note=_selection_note(ctx),
        fit_df=fit_df,
        leak_free=True,
        estimated_costs=estimated,
    )
    ctx.save_section(_with_run_settings(ctx, sec))
    ctx.cache["meridian_metrics"] = metrics
    ctx.save_json("meta/meridian_metrics.json", metrics)


def s12_meridian_fit(ctx: RunContext) -> None:
    """Fit the final Meridian model, the forward hold-out refit, and build the `meridian_model` section.

    Under leak-free validation (the default) the windows are scored first and the final model is fitted on every day
    (`_s12_leak_free`); otherwise the final model holds the test window out of its likelihood.
    """
    if _leak_free(ctx):
        _s12_leak_free(ctx)
        return
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
        sampling=_sampling_for(ctx.run_profile, "main", _settings(ctx)),
        seed=ctx.seed,
        spawn_child=ctx.spawn_meridian_child,
        should_cancel=ctx.is_cancelled,
        on_heartbeat=ctx.on_heartbeat,
        timeout_seconds=ctx.fit_timeout_seconds,
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
    sec, metrics = build_meridian_model_section(mmm, wide, spec_cfg, selection_note=_selection_note(ctx))
    ctx.save_section(_with_run_settings(ctx, sec))
    ctx.cache["meridian_metrics"] = metrics
    ctx.save_json("meta/meridian_metrics.json", metrics)
    ctx.heartbeat()

    windows = holdout_windows(pd.DatetimeIndex(wide.index), ctx.config.holdout_days if ctx.config else 93)
    if "forward" in windows and _settings(ctx).forward_holdout:
        fwd_metrics, fwd_df = fit_forward_holdout(
            wide,
            spec_cfg,
            windows["forward"],
            work_dir / "meridian_forward.binpb",
            sampling=_sampling_for(ctx.run_profile, "forward", _settings(ctx)),
            seed=ctx.seed,
            spawn_child=ctx.spawn_meridian_child,
            should_cancel=ctx.is_cancelled,
            on_heartbeat=ctx.on_heartbeat,
            timeout_seconds=ctx.fit_timeout_seconds,
        )
        label = quarter_label(windows["forward"][1], windows["forward"][2])
        ctx.save_dataframe("meridian_forward_holdout", fwd_df, also_csv=True)
        sec, metrics = build_meridian_model_section(
            mmm, wide, spec_cfg, forward_metrics=fwd_metrics, forward_label=label, selection_note=_selection_note(ctx)
        )
        ctx.save_section(_with_run_settings(ctx, sec))
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
    fixed_alloc_df = None
    opt_error = None
    upper = [c for c in spec_cfg.upper_bound_slugs if c in spec_cfg.media_slugs]
    try:
        opt_obj, opt_alloc_df = run_meridian_budget_optimizer(mmm)
        ctx.save_dataframe("meridian_optimised_budget", opt_alloc_df, also_csv=True)
        if upper and len(upper) < len(spec_cfg.media_slugs):
            fixed_obj, fixed_alloc_df = run_meridian_budget_optimizer(mmm, fixed_channels=upper)
            ctx.save_dataframe("meridian_optimised_budget_fixed", fixed_alloc_df, also_csv=True)
            opt_obj = fixed_obj  # Meridian's HTML report shows the recommendation the app headlines
    except Exception as exc:  # noqa: BLE001 - fall back to the response-curve optimiser
        from mmm_studio.errors import public_error

        logger.warning("Meridian BudgetOptimizer failed for run %s", ctx.run_id, exc_info=True)
        opt_error = public_error(exc)

    opt_sec, opt_res = build_optimiser_section(
        roi_df, response_curves, opt_alloc_df=opt_alloc_df, native_error=opt_error, fixed_alloc_df=fixed_alloc_df, upper_bound=upper
    )
    ctx.cache["opt_res"] = opt_res
    ctx.save_json("data/default_optimisation.json", opt_res)
    ctx.save_section(opt_sec)

    rep_dir = ctx.local_work_dir("reports")
    html_paths = generate_meridian_html_reports(mmm, rep_dir, opt_result=opt_obj)
    for _, hp in html_paths.items():
        if hp.exists():
            ctx.storage.write_bytes(ctx.key(f"reports/{hp.name}"), hp.read_bytes(), content_type="text/html; charset=utf-8")


def _level_windows(ctx: RunContext, wide: pd.DataFrame) -> list[dict[str, Any]]:
    """Leak-free out-of-sample windows of this run with their total error ((actual − forecast) / forecast): the OLS-2
    walk-forward refits (same inputs as Meridian) and Meridian's own hold-out and forward hold-out."""
    from mmm_studio.core.meridian.forecast import window_level_error
    from mmm_studio.core.ols import evaluate_ols_suite

    ols_res = ctx.cache.get("ols_res") or evaluate_ols_suite(wide, config=ctx.config)
    windows = list(ols_res["cv"].get("level_errors", []))
    fit_df = _load_optional(ctx, "fit_df", lambda: ctx.load_dataframe("meridian_fit_daily"))
    sources = [("Meridian hold-out", fit_df[fit_df["holdout"]] if fit_df is not None and "holdout" in fit_df else None)]
    if ctx.has_dataframe("meridian_forward_holdout"):
        sources.append(("Meridian forward hold-out", ctx.load_dataframe("meridian_forward_holdout")))
    for source, df in sources:
        if df is not None and len(df):
            windows.append(
                {
                    "source": source,
                    "start": pd.Timestamp(df.index[0]).strftime("%Y-%m-%d"),
                    "end": pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d"),
                    "error": window_level_error(df["actual"], df["expected"]),
                }
            )
    return windows


@dataclass
class WindowForecast:
    """A past window forecast as a plan made the day before it (see `_forecast_window`)."""

    forecast: Any  # core.meridian.forecast.PlanForecast
    daily: pd.DataFrame
    plan_time_daily: pd.DataFrame | None
    plan_time_total: tuple[float, float] | None
    level: dict[str, Any] | None


def _forecast_window(
    ctx: RunContext,
    wide: pd.DataFrame,
    spec_cfg: Any,
    window: tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp],
    level_windows: list[dict[str, Any]],
    sampling: dict[str, int],
    plan_cols: list[str],
    fit_kwargs: dict[str, Any],
) -> WindowForecast:
    """Forecast a past window exactly as a budget-plan forecast made the day before it, and score it on its actuals.

    The model is refit on the days before the window only (priors from those days), the window's sales are unknown to it
    (the training mean stands in, as for a future plan) and its actual spend is the plan. When a cost is a control (a
    commission on sales), `plan_time_daily` repeats the forecast with that cost as it could have been planned, because
    the realised commission leaks the outcome. The level uncertainty only learns from windows that end before this one.
    """
    from mmm_studio.core.meridian.forecast import (
        evaluate_forecast_scenario,
        forecast_budget_plan,
        level_uncertainty,
        plan_time_controls,
    )

    tr_end, start, end = window
    level = level_uncertainty(level_windows, before=start)
    level_sd = level["level_sd"] if level else 0.0
    history = wide.loc[:tr_end]
    plan = pd.DataFrame({c: wide.loc[start:end, f"cost_{c}"] for c in plan_cols})
    plan.index.name = "date"
    fc = forecast_budget_plan(history, plan, cfg=spec_cfg, sampling=sampling, seed=ctx.seed, level_sd=level_sd, **fit_kwargs)
    actual = wide.loc[start:end, "total_sales"].to_numpy()
    daily = fc.daily
    daily["actual"] = actual
    pt_daily, pt_total = None, None
    if any(c in plan.columns for c in spec_cfg.as_controls):
        pt_plan = plan_time_controls(history, plan, fc.cfg)
        pt_daily, pt_total = evaluate_forecast_scenario(
            fc.mmm, history, daily, pt_plan, fc.cfg, seed=ctx.seed, with_controls=True, level_sd=level_sd
        )
        pt_daily["actual"] = actual
    return WindowForecast(forecast=fc, daily=daily, plan_time_daily=pt_daily, plan_time_total=pt_total, level=level)


def s14_forecast(ctx: RunContext) -> None:
    """Leak-free back-test (also from a plan), the example budget-plan forecast and its scenarios."""
    from mmm_studio.core.features import holdout_windows, quarter_label
    from mmm_studio.core.meridian.forecast import (
        build_example_plan,
        build_forecast_scenarios_section,
        channel_effect_draws,
        control_effect_draws,
        evaluate_forecast_scenario,
        forecast_budget_plan,
        interval_coverage,
        level_uncertainty,
        scenario_payload,
        total_check,
    )
    from mmm_studio.core.meridian.spec import channel_label, save_meridian_model
    from mmm_studio.core.schema import slugify_channel

    _, wide, _ = _ensure_dataframes(ctx)
    spec_cfg = _spec_cfg(ctx, wide)
    sampling = _sampling_for(ctx.run_profile, "main", _settings(ctx))
    plan_cols = [c for c in spec_cfg.paid_slugs if f"cost_{c}" in wide.columns]
    fit_kwargs = {
        "spawn_child": ctx.spawn_meridian_child,
        "should_cancel": ctx.is_cancelled,
        "on_heartbeat": ctx.on_heartbeat,
        "timeout_seconds": ctx.fit_timeout_seconds,
    }

    # 0. How far a forecast's level drifts: leak-free out-of-sample windows of this run (the back-test only learns from
    # windows that end before it starts)
    level_windows = _level_windows(ctx, wide)
    windows = holdout_windows(pd.DatetimeIndex(wide.index), ctx.config.holdout_days)
    tr_end, te_start, te_end = windows["primary"]
    lvl_fc = level_uncertainty(level_windows)
    fc_level = lvl_fc["level_sd"] if lvl_fc else 0.0

    estimated: list[str] | None = None
    if _leak_free(ctx):
        # 1. Leak-free: s12 already forecast the test and validation windows as plans made the day before them (their
        # outcome-driven costs planned from history), so they are the back-tests; no realised-cost variant exists
        bt_daily = ctx.load_dataframe("forecast_backtest_daily")
        meta = ctx.load_json("data/backtest_meta.json")
        bt_total_pi = (meta["total_lo"], meta["total_hi"])
        pt_daily, pt_total, lvl_bt = None, None, meta.get("level")
        estimated = [channel_label(c) for c in meta.get("estimated_costs", [])]
    else:
        # 1. Back-test (the test window): the hold-out forecast as a plan made the day before it
        test = _forecast_window(ctx, wide, spec_cfg, windows["primary"], level_windows, sampling, plan_cols, fit_kwargs)
        bt_daily, pt_daily, pt_total, lvl_bt = test.daily, test.plan_time_daily, test.plan_time_total, test.level
        bt_total_pi = (test.forecast.total_lo, test.forecast.total_hi)
        ctx.save_dataframe("forecast_backtest_daily", bt_daily, also_csv=True)
        if pt_daily is not None:
            ctx.save_dataframe("forecast_backtest_plan_time_daily", pt_daily, also_csv=True)
        ctx.heartbeat()

    # 1b. The validation window (as long as the test, right before it) forecast by the same procedure from the days
    # before it, so validation, test and the plan forecast are one rolling-origin evaluation. Gated, like the forward
    # hold-out refit, by the "forward hold-out" setting, and sampled like it (an extra fit: at most the Quick profile's
    # draws). Leak-free runs have it from s12.
    if "forward" in windows and _settings(ctx).forward_holdout and not _leak_free(ctx):
        val = _forecast_window(
            ctx, wide, spec_cfg, windows["forward"], level_windows, _sampling_for(ctx.run_profile, "forward", _settings(ctx)), plan_cols, fit_kwargs
        )
        ctx.save_dataframe("forecast_validation_daily", val.daily, also_csv=True)
        if val.plan_time_daily is not None:
            ctx.save_dataframe("forecast_validation_plan_time_daily", val.plan_time_daily, also_csv=True)
        ctx.heartbeat()

    # 2. Example plan: starts the day after the history, 42 days, two print send-outs
    plan_df = build_example_plan(wide, days=42, paid_channels=list(spec_cfg.paid_channels), print_slugs=spec_cfg.print_slugs)
    fc = forecast_budget_plan(wide, plan_df, cfg=spec_cfg, sampling=sampling, seed=ctx.seed, level_sd=fc_level, **fit_kwargs)
    fc_daily, fc_mmm, fc_cfg = fc.daily, fc.mmm, fc.cfg
    fc_bin = save_meridian_model(fc_mmm, ctx.local_work_dir("models") / "meridian_forecast.binpb")
    ctx.storage.write_bytes(ctx.key("models/meridian_forecast.binpb"), fc_bin.read_bytes())

    # 3. Scenarios on the same window, evaluated with the fitted model (no refit)
    scenarios: dict[str, pd.DataFrame] = {"Plan as submitted": plan_df}
    for slug in [c for c in spec_cfg.print_slugs if c in plan_df.columns]:
        if (plan_df[slug] > 0).any():
            last = plan_df.index[plan_df[slug] > 0][-1]
            alt = plan_df.copy()
            alt.loc[last, slug] = 0.0
            scenarios[f"Skip the {last:%d %b} {channel_label(slug)} send-out"] = alt
            break
    upper = [c for c in spec_cfg.upper_bound_slugs if c in plan_df.columns]
    if "display" in plan_df.columns and "sea" in plan_df.columns:
        alt = plan_df.copy()
        moved = 0.2 * alt["display"]
        alt["display"] -= moved
        alt["sea"] += moved
        scenarios["Move 20 % of Display to SEA" + (" (upper-bound ROI)" if "sea" in upper else "")] = alt
    digital = [c for c in plan_df.columns if c not in spec_cfg.print_slugs and c not in spec_cfg.as_controls]
    if digital:
        alt = plan_df.copy()
        alt[digital] = alt[digital] * 1.10
        scenarios["+10 % on all digital channels"] = alt

    base_sales = float(fc_daily["forecast"].sum())
    base_spend = float(plan_df.to_numpy().sum())
    scen_rows = []
    for name, p in scenarios.items():
        d = (
            fc_daily
            if name == "Plan as submitted"
            else evaluate_forecast_scenario(fc_mmm, wide, fc_daily, p, fc_cfg, seed=ctx.seed, level_sd=fc_level)[0]
        )
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
    pt_mae = (
        float(np.mean(np.abs(pt_daily["actual"] - pt_daily["forecast"])) / pt_daily["actual"].mean()) if pt_daily is not None else None
    )
    coverage = interval_coverage(pt_daily if pt_daily is not None else bt_daily)
    head_total = total_check(pt_daily, pt_total) if pt_daily is not None else total_check(bt_daily, bt_total_pi)
    ctx.cache["forecast_summary"] = {
        "start": plan_df.index[0].strftime("%Y-%m-%d"),
        "end": plan_df.index[-1].strftime("%Y-%m-%d"),
        "sales": base_sales,
        "spend": base_spend,
        "sales_total_lo": fc.total_lo,
        "sales_total_hi": fc.total_hi,
        "backtest_mae_pct": 100.0 * bt_mae,
        "plan_time_mae_pct": 100.0 * pt_mae if pt_mae is not None else None,
        "backtest_coverage": coverage,
        "backtest_label": bt_label,
        # The back-test's total against the 90 % interval of its total (the same method as the plan's total interval)
        "backtest_total_check": head_total,
        # Level uncertainty of every forecast of this run (custom plans and scenarios read it from here) and of the
        # back-test (windows before it only); None when there were too few windows to estimate it
        "level_uncertainty": lvl_fc,
        "backtest_level_uncertainty": lvl_bt,
        "noise_ar1": fc.ar1,
    }
    ctx.save_json("data/forecast_summary.json", ctx.cache["forecast_summary"])

    # Pre-compute the default forecast result.json with full posterior media & control draws while fc_mmm is in memory
    try:
        base_draws, effect_channels = channel_effect_draws(fc_mmm, wide, plan_df, fc_cfg)
        base_ctrl = control_effect_draws(fc_mmm, wide, plan_df, fc_cfg)
    except Exception:
        logger.warning("Could not extract posterior draws for default forecast of run %s", ctx.run_id, exc_info=True)
        base_draws, effect_channels, base_ctrl = None, None, None

    roi_map: dict[str, float] = {}
    if ctx.has_dataframe("meridian_roi"):
        roi_df = ctx.load_dataframe("meridian_roi").drop(index="All Channels", errors="ignore")
        roi_map = {slugify_channel(str(c)): float(v) for c, v in roi_df["roi"].items()}

    default_payload = scenario_payload(
        fc_daily,
        plan_df,
        fc_daily,
        plan_df,
        "Meridian refit (pipeline example plan)",
        effect_channels=effect_channels,
        total_pi=(fc.total_lo, fc.total_hi) if fc.total_lo is not None and fc.total_hi is not None else None,
        coverage=coverage,
        base_media_draws=base_draws,
        alt_media_draws=base_draws,
        base_control_draws=base_ctrl,
        alt_control_draws=base_ctrl,
        roi_map=roi_map,
    )
    ctx.save_json(f"forecasts/default_{ctx.run_id}/result.json", default_payload)
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
            plan_time_df=pt_daily,
            control_names=[channel_label(c) for c in spec_cfg.as_controls],
            forecast_total_pi=(fc.total_lo, fc.total_hi),
            print_slugs=spec_cfg.print_slugs,
            backtest_total_pi=bt_total_pi,
            plan_time_total_pi=pt_total,
            level_info=lvl_fc,
            backtest_level_info=lvl_bt,
            noise_ar1=fc.ar1,
            estimated_costs=estimated,
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
        ols_res["ladder"] = compute_preparation_ols_ladder(_fit_panel(ctx, wide), ctx.config)
    ctx.cache["ols_res"] = ols_res
    meridian_metrics = _load_optional(ctx, "meridian_metrics", lambda: ctx.load_json("meta/meridian_metrics.json"))
    roi_df = _load_optional(ctx, "roi_df", lambda: ctx.load_dataframe("meridian_roi"))
    fit_df = _load_optional(ctx, "fit_df", lambda: ctx.load_dataframe("meridian_fit_daily"))
    opt_res = _load_optional(ctx, "opt_res", lambda: ctx.load_json("data/default_optimisation.json"))
    fc_sum = _load_optional(ctx, "forecast_summary", lambda: ctx.load_json("data/forecast_summary.json"))
    selection = _load_optional(ctx, "selection", lambda: ctx.load_json("data/model_selection.json"))
    holdout = fit_df[fit_df["holdout"]] if fit_df is not None and "holdout" in fit_df else None

    facts = _facts(ctx)
    names = _narrative(ctx, wide)
    ctx.save_section(
        build_benchmark_section(wide, meridian_summary=meridian_metrics, config=ctx.config, ols_res=ols_res, meridian_holdout=holdout)
    )
    ctx.save_section(
        build_overview_section(
            wide, facts, ols_res, meridian_metrics, roi_df, opt_res, fit_df, selection, names=names, forecast_summary=fc_sum
        )
    )
    ctx.save_section(build_brief_section(wide, facts, ols_res, meridian_metrics, roi_df, opt_res, fc_sum, names=names))
    ctx.save_section(build_reports_section(ctx))


def s15_finalize(ctx: RunContext) -> None:
    """Refresh `benchmark`, `overview`, `reports` and `brief` with the Meridian results."""
    _refresh_summary_sections(ctx)


def s16_model_selection(ctx: RunContext) -> None:
    """Model selection: print shift × commission-channel role grid, then prior / demand-following sensitivity (fast fits,
    full profile), all scored on the validation window before the hold-out."""
    from mmm_studio.core.features import holdout_windows, quarter_label
    from mmm_studio.core.meridian.analysis import build_meridian_model_section
    from mmm_studio.core.meridian.selection import run_model_selection
    from mmm_studio.core.model_settings import MIN_TRAINING_DAYS

    ms = _settings(ctx)
    if not ms.selection_enabled(ctx.run_profile):
        raise StageSkipped(
            "Switched off in the run settings."
            if ms.model_selection is False
            else "Runs on the full profile only, or when switched on in the run settings."
        )
    _, wide, _ = _ensure_dataframes(ctx)
    spec_cfg = _spec_cfg(ctx, wide)
    windows = holdout_windows(pd.DatetimeIndex(wide.index), ctx.config.holdout_days)
    if "forward" not in windows:
        raise StageSkipped(
            f"No validation window: one as long as the {ctx.config.holdout_days}-day hold-out, right before it, would leave "
            f"fewer than {MIN_TRAINING_DAYS} training days. Shorten the hold-out or upload a longer history."
        )
    result = run_model_selection(
        wide,
        spec_cfg,
        ctx.local_work_dir("models") / "selection",
        sampling=_sampling_for(ctx.run_profile, "selection", ms),
        validation_window=windows["forward"],
        validation_label=quarter_label(windows["forward"][1], windows["forward"][2]),
        shifts=tuple(ms.selection_shifts),
        seed=ctx.seed,
        spawn_child=ctx.spawn_meridian_child,
        should_cancel=ctx.is_cancelled,
        on_heartbeat=ctx.on_heartbeat,
        timeout_seconds=ctx.fit_timeout_seconds,
        leak_free=_leak_free(ctx),
    )
    ctx.cache["selection"] = result
    ctx.save_json("data/model_selection.json", result)

    mmm = _load_model(ctx)
    fwd_metrics, fwd_label = None, None
    metrics = _load_optional(ctx, "meridian_metrics", lambda: ctx.load_json("meta/meridian_metrics.json"))
    if metrics and "holdout_q3_2024" in metrics:
        fwd_metrics = metrics["holdout_q3_2024"]
        fwd_label = metrics.get("forward_label") or quarter_label(windows["forward"][1], windows["forward"][2])
    roi_df = _load_optional(ctx, "roi_df", lambda: ctx.load_dataframe("meridian_roi"))
    leak_free_kw: dict[str, Any] = {}
    if _leak_free(ctx):
        from mmm_studio.core.meridian.spec import channel_label

        leak_free_kw = {
            "fit_df": _load_optional(ctx, "fit_df", lambda: ctx.load_dataframe("meridian_fit_daily")),
            "leak_free": True,
            "estimated_costs": [channel_label(c) for c in spec_cfg.outcome_driven_slugs],
        }
    sec, _ = build_meridian_model_section(
        mmm, wide, spec_cfg, forward_metrics=fwd_metrics, forward_label=fwd_label, selection=result, final_roi=roi_df, **leak_free_kw
    )
    ctx.save_section(_with_run_settings(ctx, sec))
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
