"""Posterior diagnostics, accuracy evaluation, ROI/adstock/response extraction, and section builders."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mmm_studio.charts.builders import _hex_to_rgba, bar_figure, line_figure, small_multiples_figure
from mmm_studio.charts.theme import (
    COPPER,
    HOLDOUT_SHADE,
    INK_MUTED,
    NAVY,
    SLATE,
    TEAL,
    channel_color,
)
from mmm_studio.core.meridian.spec import MeridianSpecConfig, channel_label, meridian_table
from mmm_studio.core.metrics import regression_metrics
from mmm_studio.report.narrative import fmt_eur, fmt_eur_signed
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


def _flatten_draws(values: Any) -> np.ndarray:
    arr = np.asarray(values)
    return arr.reshape(-1, *arr.shape[2:])


def extract_fit_table(mmm: Any, cfg: MeridianSpecConfig) -> pd.DataFrame:
    """Daily actual vs posterior expected sales (mean and 90% CI), plus baseline and holdout flag."""
    from meridian.analysis import analyzer

    ds = analyzer.Analyzer(mmm).expected_vs_actual_data(aggregate_geos=True)
    df = ds.to_dataframe().reset_index()
    wide = df.pivot(index="time", columns="metric", values="expected")
    out = pd.DataFrame(
        {
            "actual": df.groupby("time")["actual"].first(),
            "expected": wide["mean"],
            "expected_lo": wide["ci_lo"],
            "expected_hi": wide["ci_hi"],
            "baseline": df[df.metric == "mean"].set_index("time")["baseline"],
        }
    )
    out.index = pd.to_datetime(out.index)
    out["holdout"] = False
    if cfg.holdout:
        out.loc[cfg.holdout[0] : cfg.holdout[1], "holdout"] = True
    return out


def extract_roi_table(mmm: Any) -> pd.DataFrame:
    """Per-channel spend, incremental sales, contribution %, ROI and marginal ROI with 90% CIs.

    Also how well the data identifies each ROI: `roi_prior_lo/hi` (the prior's 90 % interval), `ci_width_ratio`
    (posterior / prior 90 % interval width on the log scale; near 1 means the ROI is mostly the prior) and `p_roi_below_1`
    (posterior probability that the channel returns less than it costs).
    """
    from meridian import constants
    from meridian.analysis import analyzer

    an = analyzer.Analyzer(mmm)
    ds = an.summary_metrics()
    cols: dict[str, pd.Series] = {}
    for var in [
        "spend",
        "pct_of_spend",
        "incremental_outcome",
        "pct_of_contribution",
        "roi",
        "mroi",
    ]:
        da = ds[var]
        prior = None
        if "distribution" in da.dims:
            if constants.PRIOR in list(da["distribution"].values):
                prior = da.sel(distribution=constants.PRIOR)
            da = da.sel(distribution=constants.POSTERIOR)
        if "metric" in da.dims:
            cols[var] = da.sel(metric="mean").to_series()
            cols[f"{var}_lo"] = da.sel(metric="ci_lo").to_series()
            cols[f"{var}_hi"] = da.sel(metric="ci_hi").to_series()
            if var == "roi" and prior is not None:
                cols["roi_prior_lo"] = prior.sel(metric="ci_lo").to_series()
                cols["roi_prior_hi"] = prior.sel(metric="ci_hi").to_series()
        else:
            cols[var] = da.to_series()
    df = pd.DataFrame(cols)
    df.index.name = "channel"
    if "roi_prior_lo" in df:
        # ROIs are positive and LogNormal a priori, so interval widths are compared on the log scale: a posterior that
        # moved far into the prior's tail (SEA) is informed by the data even if its interval is not narrower in euros
        def log_width(lo: pd.Series, hi: pd.Series) -> pd.Series:
            ok = (lo > 0) & (hi > lo)
            return np.log(hi.where(ok) / lo.where(ok))

        df["ci_width_ratio"] = log_width(df["roi_lo"], df["roi_hi"]) / log_width(df["roi_prior_lo"], df["roi_prior_hi"])
    roi_draws = _flatten_draws(an.roi())
    channels = list(mmm.input_data.media_channel.values)
    df["p_roi_below_1"] = pd.Series({ch: float((roi_draws[:, i] < 1.0).mean()) for i, ch in enumerate(channels)})
    return df


# A posterior 90 % interval at least this wide relative to the prior's means the data barely informed the ROI
WEAK_IDENTIFICATION_RATIO = 0.7


def weakly_identified(roi_df: pd.DataFrame) -> list[str]:
    """Media channels whose ROI interval is still ≥ 70 % as wide as the prior's: their ROI is mostly the prior."""
    if "ci_width_ratio" not in roi_df:
        return []
    media = roi_df.drop(index="All Channels", errors="ignore")
    return [str(c) for c, r in media["ci_width_ratio"].items() if pd.notna(r) and float(r) >= WEAK_IDENTIFICATION_RATIO]


def extract_rhat_summary(mmm: Any) -> pd.DataFrame:
    """Extract parameter-group R-hat convergence summary table."""
    from meridian.analysis import analyzer

    rhat = analyzer.Analyzer(mmm).rhat_summary()
    cols = [c for c in ["param", "n_params", "avg_r_hat", "max_r_hat", "percent_bad_r_hat"] if c in rhat.columns]
    return rhat[cols].copy()


def extract_health_review(mmm: Any) -> dict[str, Any]:
    """Run Meridian's `ModelReviewer` and `Analyzer` quality checks (`post-modeling/health-checks`, `health-score`).

    Returns the unified Model Health Score (0–100), Bayesian PPP, posterior negative baseline probability,
    prior probability that total media contribution exceeds 100 % of observed outcome (`post-modeling/baseline`),
    model-level check rows, and per-channel calibration scores and recommendations (`post-modeling/channel-recommendation`).
    """
    from meridian import constants
    from meridian.analysis import analyzer
    from meridian.analysis.review import results as review_results
    from meridian.analysis.review import reviewer

    from mmm_studio.core.meridian.spec import suppress_meridian_warnings

    with suppress_meridian_warnings():
        an = analyzer.Analyzer(mmm)
        summary = reviewer.ModelReviewer(
            model_context=mmm.model_context,
            inference_data=mmm.inference_data,
        ).run()
        neg_base_prob = float(an.negative_baseline_probability(use_posterior=True))
        prior_contrib_exceeds_100: float | None = None
        if hasattr(mmm.inference_data, "groups") and constants.PRIOR in mmm.inference_data.groups():
            total_outcome = float(np.sum(np.asarray(mmm.kpi)))
            if total_outcome > 0:
                prior_inc = np.asarray(an.incremental_outcome(use_posterior=False))
                total_prior_contrib = np.sum(prior_inc, axis=-1) / total_outcome
                prior_contrib_exceeds_100 = float(np.mean(total_prior_contrib > 1.0))

    check_labels = {
        "ConvergenceCheckResult": ("Convergence (R-hat)", "Max R-hat < 1.20 across all parameter groups"),
        "BaselineCheckResult": ("Baseline negativity", "P(aggregate baseline < 0) < 0.20 PASS · 0.20–0.80 REVIEW · > 0.80 FAIL"),
        "BayesianPPPCheckResult": ("Bayesian posterior predictive p-value", "PPP ≥ 0.05 PASS (observed total outcome consistent with posterior)"),
        "GoodnessOfFitCheckResult": ("Goodness of fit (R² / MAPE / wMAPE)", "R² > 0 PASS · ≤ 0 REVIEW"),
        "PriorPosteriorShiftCheckResult": ("Prior–posterior ROI shift", "Bootstrap test on mean/median/quartiles (data updated prior)"),
        "ROIConsistencyCheckResult": ("ROI prior consistency", "Posterior mean ROI within 1st–99th percentile of prior"),
    }
    bayesian_ppp: float | None = None
    no_shift_channels: set[str] = set()
    implausible_map: dict[str, review_results.ImplausibleROIChannelResult] = {}
    high_var_map: dict[str, review_results.HighVarianceChannelResult] = {}
    bias_map: dict[str, review_results.PotentialBiasChannelResult] = {}

    model_checks: list[dict[str, str]] = []
    for res in summary.results:
        cls_name = res.__class__.__name__
        if isinstance(res, review_results.BayesianPPPCheckResult):
            bayesian_ppp = float(res.bayesian_ppp)
        elif isinstance(res, review_results.PriorPosteriorShiftCheckResult):
            no_shift_channels = {str(c) for c in res.no_shift_channels}
        elif isinstance(res, review_results.ImplausibleROICheckResult):
            implausible_map = {str(cr.channel_name): cr for cr in res.channel_results}
        elif isinstance(res, review_results.HighVarianceCheckResult):
            high_var_map = {str(cr.channel_name): cr for cr in res.channel_results}
        elif isinstance(res, review_results.PotentialBiasCheckResult):
            bias_map = {str(cr.channel_name): cr for cr in res.channel_results}

        if cls_name in check_labels:
            label, threshold = check_labels[cls_name]
            model_checks.append(
                {
                    "check": label,
                    "threshold": threshold,
                    "status": res.case.status.name,
                    "recommendation": str(res.recommendation),
                }
            )

    if prior_contrib_exceeds_100 is not None:
        model_checks.append(
            {
                "check": "Prior P(total media contribution > 100% outcome)",
                "threshold": "< 0.20 PASS · ≥ 0.50 REVIEW (Meridian baseline check)",
                "status": "REVIEW" if prior_contrib_exceeds_100 >= 0.50 else "PASS",
                "recommendation": (
                    f"Prior probability that total treatment contribution exceeds 100 % of observed sales is "
                    f"{prior_contrib_exceeds_100:.2f}. When high, consider the Total Media Contribution ROI prior."
                ),
            }
        )

    calib_scores = {str(k): float(v) for k, v in summary.channel_calibration_scores.items()}
    recommended_set = {str(c) for c in summary.channels_recommended_for_calibration}
    channel_rows: list[dict[str, Any]] = []
    for ch in list(mmm.input_data.media_channel.values):
        slug = str(ch)
        score = calib_scores.get(slug, 100.0)
        imp = implausible_map.get(slug)
        hv = high_var_map.get(slug)
        pb = bias_map.get(slug)
        if imp is not None and imp.case == review_results.ImplausibleROIChannelCases.ROI_HIGH:
            imp_str = f"Review: High ROI ({imp.spend_weighted_roi:.2f} spend-wtd)"
        elif imp is not None and imp.case == review_results.ImplausibleROIChannelCases.ROI_LOW:
            imp_str = f"Review: Low ROI ({imp.spend_weighted_roi:.2f} spend-wtd)"
        else:
            imp_str = "Pass"

        if hv is not None and hv.case == review_results.HighVarianceChannelCases.HIGH_VARIANCE:
            hv_str = f"Review: Wide CI ({hv.relative_width_ratio:.2f}× prior)"
        elif hv is not None:
            hv_str = f"Pass ({hv.relative_width_ratio:.2f}× prior)"
        else:
            hv_str = "Pass"

        if pb is not None and pb.case == review_results.PotentialBiasChannelCases.LOW_CORRELATION:
            pb_str = f"Review: Low control |r| ({pb.max_abs_correlation:.2f} < 0.10)"
        elif pb is not None:
            pb_str = f"Pass (max |r| = {pb.max_abs_correlation:.2f})"
        else:
            pb_str = "Pass"

        channel_rows.append(
            {
                "channel": channel_label(slug),
                "slug": slug,
                "calibration_score": round(score, 1),
                "prior_posterior_shift": "No shift (weak signal)" if slug in no_shift_channels else "Shifted (data learned)",
                "implausible_roi": imp_str,
                "high_variance": hv_str,
                "hv_ratio": round(float(hv.relative_width_ratio), 3) if hv is not None else None,
                "potential_bias": pb_str,
                "priority": "Prioritize experiment (< 67.5)" if slug in recommended_set else "Sufficient (≥ 67.5)",
            }
        )

    return {
        "health_score": round(float(summary.health_score), 1),
        "overall_status": summary.overall_status.name,
        "summary_message": str(summary.summary_message),
        "calibration_score": round(float(summary.calibration_score), 1),
        "channels_recommended_for_calibration": [str(c) for c in summary.channels_recommended_for_calibration],
        "posterior_negative_baseline_prob": round(neg_base_prob, 4),
        "prior_contrib_exceeds_100_prob": round(prior_contrib_exceeds_100, 4) if prior_contrib_exceeds_100 is not None else None,
        "bayesian_ppp": round(bayesian_ppp, 4) if bayesian_ppp is not None else None,
        "checks": model_checks,
        "channel_calibration": channel_rows,
    }


def extract_adstock_curves(mmm: Any) -> pd.DataFrame:
    """Extract integer-day posterior adstock decay curves with 90% CIs per channel."""
    from meridian.analysis import analyzer

    ad = analyzer.Analyzer(mmm).adstock_decay()
    ad = ad[(ad.distribution == "posterior") & ad.is_int_time_unit].copy()
    return ad[["channel", "time_units", "mean", "ci_lo", "ci_hi"]].reset_index(drop=True)


def _band_traces(lo: Any, hi: Any, color: str, decimals: int) -> list[dict[str, Any]]:
    """Two traces that shade a 90 % credible band (upper edge, then the lower edge filled up to it)."""
    fill = _hex_to_rgba(color, 0.18)
    edge = {"color": "rgba(0,0,0,0)", "width": 0}
    return [
        {"name": "90% CI upper", "y": [round(float(v), decimals) for v in hi], "line": edge, "hoverinfo": "skip", "showlegend": False},
        {"name": "90% CI", "y": [round(float(v), decimals) for v in lo], "line": edge, "fill": "tonexty", "fillcolor": fill, "hoverinfo": "skip"},
    ]


def extract_response_curves(
    mmm: Any,
    spend_multipliers: list[float] | None = None,
) -> dict[str, pd.DataFrame]:
    """Extract Hill saturation response curves over 0..200% spend multipliers per channel."""
    from meridian.analysis import analyzer

    mults = spend_multipliers or list(np.round(np.arange(0.0, 2.01, 0.05), 2))
    rc = analyzer.Analyzer(mmm).response_curves(spend_multipliers=mults)
    channels = list(mmm.input_data.media_channel.values)
    curves: dict[str, pd.DataFrame] = {}
    for ch in channels:
        sp = np.asarray(rc["spend"].sel(channel=ch).values, dtype=float)
        mean = np.asarray(rc["incremental_outcome"].sel(channel=ch, metric="mean").values, dtype=float)
        lo = np.asarray(rc["incremental_outcome"].sel(channel=ch, metric="ci_lo").values, dtype=float)
        hi = np.asarray(rc["incremental_outcome"].sel(channel=ch, metric="ci_hi").values, dtype=float)
        curves[ch] = pd.DataFrame(
            {
                "multiplier": mults,
                "spend": sp,
                "mean": mean,
                "ci_lo": lo,
                "ci_hi": hi,
            }
        )
    return curves


def extract_weekly_contributions(mmm: Any, fit_df: pd.DataFrame) -> pd.DataFrame:
    """Compute weekly decomposed sales (Baseline & controls + each media channel + Actual)."""
    from meridian.analysis import analyzer

    an = analyzer.Analyzer(mmm)
    inc = _flatten_draws(an.incremental_outcome(aggregate_times=False)).mean(axis=0)
    channels = list(mmm.input_data.media_channel.values)
    times = pd.to_datetime(mmm.input_data.time.values)
    contrib = pd.DataFrame(inc, index=times, columns=channels)
    contrib["baseline"] = fit_df["baseline"].reindex(times).to_numpy()
    contrib["actual"] = fit_df["actual"].reindex(times).to_numpy()
    weekly = contrib.resample("W-SUN").sum()
    if len(weekly) > 4:
        weekly = weekly.iloc[1:-1]
    return weekly


def compute_catalog_whatif(
    mmm: Any,
    wide_df: pd.DataFrame,
    cfg: MeridianSpecConfig,
    skip_date: str | None = None,
) -> dict[str, Any]:
    """Simulate skipping one send-out of the first print-event channel in the hold-out window.

    By default the middle send-out of the hold-out window is skipped (e.g. 14 Nov 2024 in the sample workbook). Without a
    print-event media channel the result is empty.
    """
    from meridian.analysis import analyzer
    from meridian.analysis.tensors import DataTensors

    an = analyzer.Analyzer(mmm)
    media = np.asarray(mmm.input_data.media.values, dtype=float)
    times = pd.to_datetime(mmm.input_data.media_time.values)
    channels = list(mmm.input_data.media_channel.values)
    slug = next((s for s in cfg.print_slugs if s in channels and f"cost_{s}" in wide_df.columns), None)
    if slug is None:
        return {"skip_date": None, "channel": None, "cost_saved": 0.0, "sales_lost": 0.0, "ratio": 0.0, "df": pd.DataFrame()}

    cat_idx = channels.index(slug)
    cost_col = f"cost_{slug}"
    cat_days = wide_df.index[wide_df[cost_col] > 0]
    if skip_date is None and len(cat_days) > 0:
        in_window = cat_days
        if cfg.holdout:
            in_window = cat_days[(cat_days >= pd.Timestamp(cfg.holdout[0])) & (cat_days <= pd.Timestamp(cfg.holdout[1]))]
        pool = in_window if len(in_window) else cat_days[-3:]
        skip_date = pool[len(pool) // 2].strftime("%Y-%m-%d")
    if skip_date is None:
        return {"skip_date": None, "channel": slug, "cost_saved": 0.0, "sales_lost": 0.0, "ratio": 0.0, "df": pd.DataFrame()}
    skip_ts = pd.Timestamp(skip_date)

    in_home_ts = skip_ts + pd.Timedelta(days=cfg.shift_for(slug))
    scenario = media.copy()
    scenario[:, times == in_home_ts, cat_idx] = 0.0

    base = _flatten_draws(an.expected_outcome(aggregate_times=False)).mean(axis=0).reshape(-1)
    alt = (
        _flatten_draws(an.expected_outcome(new_data=DataTensors(media=scenario), aggregate_times=False))
        .mean(axis=0)
        .reshape(-1)
    )
    time_idx = pd.to_datetime(mmm.input_data.time.values)
    whatif_df = pd.DataFrame({"forecast": base, "without_sendout": alt}, index=time_idx)
    win_start = (skip_ts - pd.Timedelta(days=4)).strftime("%Y-%m-%d")
    win_end = (skip_ts + pd.Timedelta(days=31)).strftime("%Y-%m-%d")
    win_df = whatif_df.loc[win_start:win_end].copy()

    sales_lost = float((whatif_df["forecast"] - whatif_df["without_sendout"]).loc[skip_date:].sum())
    cost_saved = float(wide_df.loc[skip_ts, cost_col]) if skip_ts in wide_df.index else 0.0
    ratio = sales_lost / max(cost_saved, 1.0)
    return {
        "skip_date": skip_date,
        "channel": slug,
        "cost_saved": cost_saved,
        "sales_lost": sales_lost,
        "ratio": ratio,
        "df": win_df,
    }


def generate_meridian_html_reports(
    mmm: Any,
    output_dir: Path | str,
    opt_result: Any = None,
) -> dict[str, Path]:
    """Generate the standalone Meridian HTML reports (`summary`, `health_card`, `eda`, `optimization`).

    Each file is passed through `polish_meridian_report` (fonts, chart sizing, channel colours, text fixes).
    """
    from meridian.analysis import summarizer
    from meridian.analysis.review import reviewer
    from meridian.model.eda import meridian_eda

    from mmm_studio.core.meridian.spec import suppress_meridian_warnings
    from mmm_studio.report.meridian_html import polish_meridian_report

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    with suppress_meridian_warnings():
        # MeridianEDA assigns into a slice of its own frame; the report is unaffected.
        warnings.simplefilter("ignore", pd.errors.SettingWithCopyWarning)
        # 1. Model results summary HTML. Its contribution-over-time chart asks Meridian for daily summary metrics, and
        # those include effectiveness = incremental outcome / media units per day. On a day without spend the adstock
        # carry-over still gives an outcome, so every draw is ±inf and np.quantile warns "invalid value encountered in
        # subtract". The chart reads only the incremental outcome; the report shows no daily effectiveness.
        summary_file = "meridian_summary.html"
        with np.errstate(invalid="ignore"):
            summarizer.Summarizer(mmm).output_model_results_summary(summary_file, str(out))
        paths["summary"] = out / summary_file

        # 2. Model health card HTML
        health_file = "meridian_health_card.html"
        health = reviewer.ModelReviewer(
            model_context=mmm.model_context,
            inference_data=mmm.inference_data,
        ).run()
        health.output_model_health_card(health_file, str(out))
        paths["health_card"] = out / health_file

        # 3. Exploratory data analysis HTML (MeridianEDA)
        eda_file = "meridian_eda.html"
        meridian_eda.MeridianEDA(mmm).generate_and_save_report(eda_file, str(out))
        paths["eda"] = out / eda_file

        # 4. Budget optimization HTML
        if opt_result is not None:
            opt_file = "meridian_optimization.html"
            opt_result.output_optimization_summary(opt_file, str(out), currency="€")
            paths["optimization"] = out / opt_file

    interval_days = int(mmm.input_data.time_coordinates.interval_days)
    for path in paths.values():
        html = path.read_text(encoding="utf-8")
        path.write_text(polish_meridian_report(html, currency="€", interval_days=interval_days), encoding="utf-8")
    return paths


def _residual_caption(by_dow: list[float], by_dss: list[float], shift: int) -> str:
    """What the residual panels show, read from their numbers."""
    same = max(sum(1 for v in by_dow if v < 0), sum(1 for v in by_dow if v > 0))
    text = (
        f"The hold-out error has the same sign on {same} of 7 weekdays: a level offset, not a missed weekday pattern."
        if same >= 6
        else f"The hold-out error changes sign across weekdays (same sign on {same} of 7): no common level offset."
    )
    early = [(k, v) for k, v in enumerate(by_dss[:7]) if v == v]
    if early:
        k, v = max(early, key=lambda t: abs(t[1]))
        text += (
            f" In the week after a send-out the largest mean error (actual − model) is {fmt_eur_signed(v)} on day {k} "
            f"(the model shifts print spend by {shift} day{'s' if shift != 1 else ''})."
        )
    return text


def _sampling_text(mmm: Any) -> str:
    """'4 chains × 1000 draws' of a fitted model's posterior."""
    try:
        post = mmm.inference_data.posterior
        return f"{int(post.sizes['chain'])} chains × {int(post.sizes['draw'])} draws"
    except Exception:  # noqa: BLE001 - only the wording depends on it
        return "full sampling"


def _unstable_roi_note(selection: dict[str, Any], final_roi: pd.DataFrame | None) -> str:
    """Channels whose final-spec fast-fit ROI falls outside the final model's 90 % interval."""
    if final_roi is None or not selection.get("sensitivity"):
        return ""
    fast = selection["sensitivity"][0]["roi"]
    off = []
    for slug, v in fast.items():
        if slug in final_roi.index and {"roi", "roi_lo", "roi_hi"} <= set(final_roi.columns):
            r = final_roi.loc[slug]
            if not float(r["roi_lo"]) <= float(v) <= float(r["roi_hi"]):
                off.append(
                    f"{channel_label(slug)} {float(v):.2f}× here vs {float(r['roi']):.2f}× "
                    f"[{float(r['roi_lo']):.2f}, {float(r['roi_hi']):.2f}] in the final model"
                )
    if not off:
        return ""
    return (
        " On the shorter training window the final specification gives ROIs outside the final model's 90 % interval ("
        + "; ".join(off)
        + "): these channels' split of the sales is not stable across fits."
    )


def build_meridian_model_section(
    mmm: Any,
    wide_df: pd.DataFrame,
    cfg: MeridianSpecConfig,
    forward_metrics: dict[str, float] | None = None,
    forward_label: str | None = None,
    selection: dict[str, Any] | None = None,
    selection_note: str | None = None,
    final_roi: pd.DataFrame | None = None,
    fit_df: pd.DataFrame | None = None,
    leak_free: bool = False,
    estimated_costs: list[str] | None = None,
) -> tuple[Section, dict[str, Any]]:
    """Build the `meridian_model` section and return `(section, metrics_summary_dict)`.

    `fit_df` is the daily fit table to show (default: `extract_fit_table(mmm, cfg)`). With `leak_free` it is the final
    model's in-sample fit (`in_sample_expected` on every day) whose hold-out rows hold the leak-free test forecast (a
    refit on the days before it, the window as a plan; `estimated_costs` names the channels whose cost it planned from
    history), and `forward_metrics` come from the same procedure on the validation window.

    `final_roi` (the final model's ROI table) lets the selection tables say where their fast fits disagree with it.

    `forward_metrics` are the scores of the forward hold-out refit and `selection` the result of
    `run_model_selection`. Neither is invented when missing: the section says what is pending.
    """
    from mmm_studio.core.features import quarter_label
    from mmm_studio.core.meridian.selection import selection_table_rows, sensitivity_table_rows
    from mmm_studio.core.meridian.spec import meridian_roles

    fit_df = fit_df if fit_df is not None else extract_fit_table(mmm, cfg)
    rhat_df = extract_rhat_summary(mmm)
    health_review = extract_health_review(mmm)
    max_rhat = float(rhat_df["max_r_hat"].max()) if len(rhat_df) > 0 else float("nan")

    train_df = fit_df[~fit_df["holdout"]]
    test_df = fit_df[fit_df["holdout"]]
    if len(test_df) == 0 or len(train_df) == 0:
        # Training metrics are never reported as hold-out metrics
        raise ValueError("The Meridian model has no hold-out days, so its forecast accuracy cannot be measured.")
    m_train = regression_metrics(train_df["actual"].to_numpy(), train_df["expected"].to_numpy())
    m_test = regression_metrics(test_df["actual"].to_numpy(), test_df["expected"].to_numpy())
    q4_label = quarter_label(test_df.index[0], test_df.index[-1])
    train_span = f"{train_df.index[0]:%Y-%m-%d} .. {train_df.index[-1]:%Y-%m-%d}"
    test_span = f"{test_df.index[0]:%Y-%m-%d} .. {test_df.index[-1]:%Y-%m-%d}"
    realised_controls = [] if leak_free else [channel_label(c) for c in cfg.as_controls]
    estimated_txt = ", ".join(estimated_costs or [])

    metrics_summary: dict[str, Any] = {
        "train": m_train,
        "holdout_q4_2024": m_test,
        "holdout_label": q4_label,
        "holdout_days": int(len(test_df)),
        "max_rhat": max_rhat,
        "q4_mae_over_mean": float(m_test["MAE_over_mean"]),
        "q4_r2": float(m_test["R2"]),
        "q4_rmse": float(m_test["RMSE"]),
        "q4_mape": float(m_test["MAPE"]),
        "health_score": health_review["health_score"],
        "overall_health_status": health_review["overall_status"],
        "posterior_negative_baseline_prob": health_review["posterior_negative_baseline_prob"],
        "prior_contrib_exceeds_100_prob": health_review["prior_contrib_exceeds_100_prob"],
        "bayesian_ppp": health_review["bayesian_ppp"],
        "calibration_score": health_review["calibration_score"],
        "channels_recommended_for_calibration": health_review["channels_recommended_for_calibration"],
        "health_review": health_review,
        "leak_free": bool(leak_free),
    }
    if forward_metrics:
        metrics_summary["holdout_q3_2024"] = forward_metrics
        metrics_summary["forward_label"] = forward_label
        metrics_summary["q3_mae_over_mean"] = float(forward_metrics["MAE_over_mean"])
        metrics_summary["q3_r2"] = float(forward_metrics["R2"])

    dates_str = [d.strftime("%Y-%m-%d") for d in fit_df.index]
    vrects = []
    if len(test_df) > 0:
        vrects.append(
            {
                "x0": test_df.index[0].strftime("%Y-%m-%d"),
                "x1": test_df.index[-1].strftime("%Y-%m-%d"),
                "color": HOLDOUT_SHADE,
                "label": "Hold-out: forecast from a plan (refit on earlier days)" if leak_free else "Hold-out: forecast from budget",
            }
        )
    fig_full_fit = line_figure(
        x=dates_str,
        series=[
            {"name": "Actual", "y": fit_df["actual"].round(0).tolist(), "color": SLATE, "width": 1.0},
            {
                "name": "Model (90% interval)",
                "y": fit_df["expected"].round(0).tolist(),
                "band_lower": fit_df["expected_lo"].round(0).tolist(),
                "band_upper": fit_df["expected_hi"].round(0).tolist(),
                "color": NAVY,
                "width": 1.6,
            },
            {"name": "Baseline & controls", "y": fit_df["baseline"].round(0).tolist(), "color": INK_MUTED, "dash": "dot", "width": 1.2},
        ],
        yaxis_title="Daily total sales (€)",
        vrects=vrects,
    )

    q4_df = test_df
    cat_vlines = []
    if "catalog_sendout" in wide_df.columns:
        q4_cat = wide_df.loc[q4_df.index[0] : q4_df.index[-1]]
        for d in q4_cat.index[q4_cat["catalog_sendout"] == 1]:
            cat_vlines.append({"x": d.strftime("%Y-%m-%d"), "color": COPPER, "dash": "dot", "label": "Catalog"})
    fig_q4 = line_figure(
        x=[d.strftime("%Y-%m-%d") for d in q4_df.index],
        series=[
            {"name": "Actual", "y": q4_df["actual"].round(0).tolist(), "color": SLATE, "width": 1.6, "mode": "lines+markers", "marker": {"size": 4}},
            {
                "name": "Forecast (90% interval)",
                "y": q4_df["expected"].round(0).tolist(),
                "band_lower": q4_df["expected_lo"].round(0).tolist(),
                "band_upper": q4_df["expected_hi"].round(0).tolist(),
                "color": TEAL,
                "width": 2.0,
            },
        ],
        yaxis_title="Daily total sales (€)",
        vlines=cat_vlines,
    )

    resid = fit_df["actual"] - fit_df["expected"]
    q4_resid = resid.loc[q4_df.index]
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_dow = [round(float(q4_resid[q4_resid.index.dayofweek == d].mean()), 0) for d in range(7)]
    dss = wide_df["days_since_catalog"].reindex(fit_df.index).clip(upper=21)
    dss_days = list(range(0, 22))
    by_dss = [round(float(resid[dss == k].mean()), 0) if (dss == k).any() else 0.0 for k in dss_days]
    fig_resid = small_multiples_figure(
        panels=[
            {
                "title": "Hold-out: mean error by weekday (€)",
                "x": dow_names,
                "xaxis_title": "Weekday",
                "traces": [{"name": "Mean residual", "y": by_dow, "kind": "bar", "color": SLATE}],
            },
            {
                "title": "All days: mean error by days since catalog send-out (€)",
                "x": dss_days,
                "xaxis_title": "Days since send-out (21 = 21 or more)",
                "traces": [{"name": "Mean residual", "y": by_dss, "kind": "bar", "color": COPPER}],
            },
        ],
        ncols=2,
        height_per_row=300,
    )

    def _acc_row(window: str, m: dict[str, float]) -> dict[str, Any]:
        return {
            "window": window,
            "mae": fmt_eur(m["MAE"]),
            "mae_pct": f"{100.0 * m['MAE_over_mean']:.1f}%",
            "rmse": fmt_eur(m["RMSE"]),
            "r2": f"{m['R2']:.3f}",
            "mape": f"{100.0 * m['MAPE']:.1f}%",
            "bias": fmt_eur(m["bias"]),
            "passes_15pct": "Yes" if m["MAE_over_mean"] <= 0.15 else "No",
        }

    if leak_free:
        ho_note = "leak-free forecast from a plan, refit on earlier days"
        train_label = f"Train, in-sample ({train_span}; final model fitted on every day)"
    else:
        ho_note = "forecast, realised control costs" if realised_controls else "forecast"
        train_label = f"Train ({train_span})"
    acc_rows = [_acc_row(train_label, m_train), _acc_row(f"Hold-out {q4_label} ({test_span}, {ho_note})", m_test)]
    if forward_metrics:
        fwd_note = "leak-free forecast from a plan, refit on earlier days" if leak_free else "trained only on earlier data"
        acc_rows.append(_acc_row(f"Forward hold-out {forward_label} ({fwd_note})", forward_metrics))
    if leak_free and "in_sample_expected" in fit_df:
        # The final model's in-sample fit on every day (it has seen every day, so this is not a forecast score)
        m_all = regression_metrics(fit_df["actual"].to_numpy(), fit_df["in_sample_expected"].to_numpy())
        acc_rows.append(_acc_row(f"All days, in-sample ({len(fit_df)} days, final model)", m_all))
    else:
        # Meridian's predictive_accuracy "All Data" row: every day, training and hold-out together
        m_all = regression_metrics(fit_df["actual"].to_numpy(), fit_df["expected"].to_numpy())
        acc_rows.append(_acc_row(f"All days ({len(fit_df)} days, training + hold-out)", m_all))

    rhat_rows = [
        {
            "param": str(r["param"]),
            "n_params": int(r["n_params"]),
            "avg_r_hat": f"{float(r['avg_r_hat']):.3f}",
            "max_r_hat": f"{float(r['max_r_hat']):.3f}",
            "pct_bad": f"{float(r['percent_bad_r_hat']):.1f}%",
        }
        for _, r in rhat_df.iterrows()
    ]

    table = meridian_table(wide_df, cfg)
    _, media, controls = meridian_roles(table, cfg)
    media_names = [channel_label(c) for c in media]
    control_names = [cfg.control_names.get(c, c.replace("_spend", " cost")) for c in controls]
    # The specification text follows the run's settings
    if cfg.enable_aks:
        baseline_line = "sales_t = μ_t                                     baseline, knots chosen automatically"
    elif cfg.knots and cfg.knots > 1:
        baseline_line = f"sales_t = μ_t                                     time-varying baseline ({cfg.knots} knots)"
    else:
        baseline_line = "sales_t = μ                                       intercept (national model)"
    kinds = [k for k, on in (("calendar", bool(cfg.calendar)), ("seasonality", cfg.n_fourier > 0), ("trend", cfg.trend)) if on]
    if cfg.as_controls:
        kinds.append("commission cost")
    if cfg.user_controls:
        kinds.append("your controls (" + ", ".join(cfg.control_names.get(k, k) for k in cfg.user_controls) + ")")
    control_kinds = ", ".join(kinds) or "none"
    media_term = "Adstock_c( Hill_c(spend_c) )" if cfg.hill_before_adstock else "Hill_c( Adstock_c(spend_c) )"
    media_notes = []
    for slug in media:
        name = channel_label(slug)
        bits = []
        if slug in cfg.print_slugs and cfg.catalog_shift > 0:
            bits.append(f"shifted {cfg.catalog_shift} days to its in-home date")
        if cfg.adstock_decay.get(slug, "geometric") != "geometric":
            bits.append(f"{cfg.adstock_decay[slug]} adstock")
        if cfg.saturation.get(slug) == "none":
            bits.append("no saturation")
        if bits:
            media_notes.append(f"{name}: {', '.join(bits)}")
    no_hill = [channel_label(c) for c in media if cfg.saturation.get(c) == "none"]
    saturation_text = "**Hill** models diminishing returns" + (f" (not for {', '.join(no_hill)})" if no_hill else "")
    prior_text = {
        "roi": "**ROI priors** regularise correlated channels"
        + (" (custom or centred on attributed ROAS)" if cfg.roi_prior else ""),
        "mroi": "**marginal-ROI priors** regularise correlated channels",
        "contribution": "**contribution priors** regularise correlated channels",
        "coefficient": "**coefficient priors** regularise correlated channels",
    }.get(cfg.media_prior_type, "priors regularise correlated channels")
    bias_pct = 100.0 * m_test["bias"] / max(float(test_df["actual"].mean()), 1.0) if len(test_df) else 0.0

    blocks: list[Any] = [
        KpisBlock(
            items=[
                KpiItem(
                    label=f"{q4_label} hold-out MAE / mean",
                    value=f"{100.0 * m_test['MAE_over_mean']:.1f}%",
                    delta="Target ≤ 15 %" + (" · met" if m_test["MAE_over_mean"] <= 0.15 else " · missed"),
                    status="success" if m_test["MAE_over_mean"] <= 0.15 else "warn",
                ),
                KpiItem(
                    label=f"{q4_label} hold-out R² / RMSE",
                    value=f"{m_test['R2']:.3f}",
                    delta=f"RMSE {fmt_eur(m_test['RMSE'])} · train R² {m_train['R2']:.3f}",
                    status="success" if m_test["R2"] >= 0.7 else "warn",
                ),
                KpiItem(
                    label=f"Forward hold-out {forward_label or ''} MAE / mean".replace("  ", " "),
                    value=f"{100.0 * forward_metrics['MAE_over_mean']:.1f}%" if forward_metrics else "pending",
                    delta=(
                        ("Leak-free: refit on earlier days, from a plan" if leak_free else "Trained only on earlier data")
                        if forward_metrics
                        else "Refit not available for this run"
                    ),
                    status=("success" if forward_metrics and forward_metrics["MAE_over_mean"] <= 0.15 else "neutral"),
                ),
                KpiItem(
                    label="Max R-hat",
                    value=f"{max_rhat:.3f}",
                    delta="Converged (threshold 1.2)" if max_rhat < 1.2 else "Not converged (threshold 1.2)",
                    status="success" if max_rhat < 1.2 else "danger",
                ),
                KpiItem(
                    label="Meridian Health Score",
                    value=f"{health_review['health_score']:.1f} / 100",
                    delta=(
                        f"Status: {health_review['overall_status']} · "
                        f"P(baseline < 0) = {100.0 * health_review['posterior_negative_baseline_prob']:.0f}%"
                    ),
                    status=(
                        "success"
                        if health_review["overall_status"] == "PASS" and health_review["health_score"] >= 75.0
                        else ("danger" if health_review["overall_status"] == "FAIL" else "warn")
                    ),
                ),
            ]
        ),
        MarkdownBlock(
            title="Approach: a Bayesian regression (Google Meridian)",
            body=(
                "```\n"
                f"{baseline_line}\n"
                f"        + Σ_k γ_k · control_k,t                   {control_kinds}\n"
                f"        + Σ_c β_c · {media_term}   paid media channels\n"
                "        + ε_t\n"
                "```\n\n"
                f"- **Media ({len(media_names)}):** {', '.join(media_names)}"
                + "".join(f" ({note})" for note in media_notes)
                + ".\n"
                f"- **Controls ({len(controls)}):** {', '.join(control_names) or 'none'}.\n"
                "- **Adstock** carries spend over to later days (up to "
                f"{cfg.max_lag} days), {saturation_text}, and {prior_text}.\n"
                + (
                    f"- **Validation (leak-free):** the {q4_label} hold-out ({len(test_df)} days) is forecast by a refit on the "
                    "days before it, as a budget plan made the day before: its sales are not in the model's input at all "
                    "(not even in the KPI scaling), the media plan is the window's actual spend"
                    + (f" except {estimated_txt}, whose cost follows the outcome and is planned from history" if estimated_txt else "")
                    + ", your controls stay at their last value and the weather at normal"
                    + (
                        f"; the {forward_label} validation window is forecast the same way from the days before it"
                        if forward_metrics and forward_label
                        else ""
                    )
                    + ". Once both windows are scored, the final model (ROIs, optimiser, reports) is fitted on every day."
                    if leak_free
                    else f"- **Validation:** the {q4_label} hold-out ({len(test_df)} days) is hidden from the likelihood: its sales are "
                    "predicted from the channel spend and the calendar of those days."
                )
                + (
                    f" The controls {', '.join(realised_controls)} enter with their realised daily cost; a budget plan only "
                    "knows planned amounts, so the plan-time back-test on the Forecast page is the stricter test."
                    if realised_controls
                    else ""
                )
                + (
                    " Automatic knot selection reads the whole sales series, hold-out included, to place the baseline's knots."
                    if cfg.enable_aks and not leak_free
                    else ""
                )
            ),
        ),
        ChartBlock(
            id="meridian_fit_full_period",
            title=f"Meridian fit: hold-out MAE / mean = {100.0 * m_test['MAE_over_mean']:.1f}% (target ≤ 15 %)",
            subtitle=(
                "Daily actual sales vs posterior mean and 90 % credible interval: the final model's in-sample fit, and in the "
                "shaded hold-out the leak-free forecast of a refit on the days before it."
                if leak_free
                else "Daily actual sales vs posterior mean and 90 % credible interval; the shaded window is the hold-out."
            ),
            figure=fig_full_fit,
        ),
        ChartBlock(
            id="meridian_holdout_q4",
            title=f"{q4_label} hold-out: daily forecast from the budget vs actual sales",
            subtitle="Dotted lines mark catalog send-outs.",
            figure=fig_q4,
            caption=(
                f"Average bias on the hold-out: {fmt_eur(m_test['bias'])} per day ({bias_pct:+.1f} % of actual). "
                + ("Most of the error is a level offset; refitting regularly re-anchors the level." if abs(bias_pct) >= 3 else "")
            ),
        ),
        TableBlock(
            id="meridian_accuracy_table",
            title="Accuracy: training period vs hold-outs",
            subtitle="MAE / mean is the brief's KPI and equals Meridian's wMAPE; bias = mean(forecast − actual).",
            columns=[
                TableColumn(key="window", label="Window"),
                TableColumn(key="mae", label="MAE", align="right"),
                TableColumn(key="mae_pct", label="MAE / mean", align="right"),
                TableColumn(key="rmse", label="RMSE", align="right"),
                TableColumn(key="r2", label="R²", align="right"),
                TableColumn(key="mape", label="MAPE", align="right"),
                TableColumn(key="bias", label="Bias", align="right"),
                TableColumn(key="passes_15pct", label="≤ 15 %"),
            ],
            rows=acc_rows,
        ),
        ChartBlock(
            id="meridian_residuals",
            title="Residual checks (actual − model)",
            subtitle="Left: hold-out error by weekday. Right: error on all days by days since the last catalog send-out.",
            figure=fig_resid,
            caption=_residual_caption(by_dow, by_dss, cfg.catalog_shift),
        ),
    ]

    if selection and selection.get("selection"):
        sel_rows, sel_channels = selection_table_rows(selection, final_shift=cfg.catalog_shift)
        best = min(selection["selection"], key=lambda r: r["test_mae_over_mean"])
        val_window = selection.get("validation_window") or []
        fit_end = (
            f"up to {(pd.Timestamp(val_window[0]) - pd.Timedelta(days=1)):%Y-%m-%d}" if val_window else "before the validation window"
        )
        not_final = (
            f" Their ROIs are not the final model's (Drivers page: trained to "
            f"{(fit_df.index[-1] if leak_free else train_df.index[-1]):%Y-%m-%d}, "
            f"{_sampling_text(mmm)})."
        )
        blocks.append(
            TableBlock(
                id="meridian_selection_table",
                title="Model selection: catalog shift k × commission channels as media or control",
                subtitle=(
                    f"Fast fits ({selection['sampling']['n_chains']} chains × {selection['sampling']['n_keep']} draws) trained {fit_end} and "
                    f"scored on the {selection.get('validation_label', 'validation')} window"
                    + (" as a forecast from a plan (its sales and outcome-driven costs unseen)" if selection.get("leak_free") else "")
                    + f", so the {q4_label} hold-out stays unseen. "
                    f"Lowest error: k = {best['catalog_shift']}, commission channels as {best['affiliate']}; the final model uses "
                    f"k = {cfg.catalog_shift}." + not_final
                ),
                columns=[
                    TableColumn(key="catalog_shift", label="Print shift k", align="right"),
                    TableColumn(key="affiliate", label="Commission channels"),
                    TableColumn(key="test_mae_pct", label="Validation MAE / mean", align="right"),
                    TableColumn(key="train_mae_pct", label="Train MAE / mean", align="right"),
                    TableColumn(key="test_r2", label="Validation R²", align="right"),
                    *[TableColumn(key=f"roi_{c}", label=f"ROI {channel_label(c)}", align="right") for c in sel_channels],
                ],
                rows=sel_rows,
                caption="A commission channel as media gets an implausible ROI: its cost is paid on its own sales (reverse causality).",
            )
        )
        sens_rows, sens_channels = sensitivity_table_rows(selection)
        if sens_rows:
            blocks.append(
                TableBlock(
                    id="meridian_sensitivity_table",
                    title="Sensitivity: ROI priors and the demand-following channels",
                    subtitle=(
                        "Informative priors centred on attributed ROAS, LogNormal(log ROAS, 0.5), and the channels whose cost "
                        f"follows demand entered as controls; fast fits trained {fit_end} and scored on the "
                        f"{selection.get('validation_label', 'validation')} window." + not_final
                    ),
                    columns=[
                        TableColumn(key="variant", label="Variant"),
                        TableColumn(key="test_mae_pct", label="Validation MAE / mean", align="right"),
                        *[TableColumn(key=f"roi_{c}", label=f"ROI {channel_label(c)}", align="right") for c in sens_channels],
                        TableColumn(key="media_share", label="Media share of sales", align="right"),
                    ],
                    rows=sens_rows,
                    caption=(
                        "If priors leave the big budget lines unchanged, the data dominates them; channels that move toward their "
                        "priors are only weakly identified. Demand-following ROIs stay upper bounds until an experiment checks them."
                        + _unstable_roi_note(selection, final_roi)
                    ),
                )
            )
    else:
        blocks.append(
            CalloutBlock(
                tone="info",
                title="Model selection grid and prior sensitivity",
                body=selection_note
                or (
                    "The selection grid (print shift k × commission channels as media or control) and the prior / demand-following "
                    "sensitivity fits run after the main results on the **full** profile. They appear here when they finish."
                ),
            )
        )

    blocks.append(
        TableBlock(
            id="meridian_health_checks_table",
            title="Meridian post-modeling quality checks (ModelReviewer)",
            subtitle=(
                f"Overall Model Health Score: {health_review['health_score']:.1f}/100 ({health_review['overall_status']}). "
                f"{health_review['summary_message']}"
            ),
            columns=[
                TableColumn(key="check", label="Quality Check"),
                TableColumn(key="threshold", label="Meridian Threshold"),
                TableColumn(key="status", label="Status"),
                TableColumn(key="recommendation", label="Diagnostic Finding & Recommendation"),
            ],
            rows=health_review["checks"],
        )
    )

    blocks.append(
        TableBlock(
            id="meridian_rhat_table",
            title="Convergence (R-hat by parameter group)",
            subtitle="R-hat below 1.2 means the MCMC chains agree.",
            columns=[
                TableColumn(key="param", label="Parameter"),
                TableColumn(key="n_params", label="# Parameters", align="right"),
                TableColumn(key="avg_r_hat", label="Mean R-hat", align="right"),
                TableColumn(key="max_r_hat", label="Max R-hat", align="right"),
                TableColumn(key="pct_bad", label="% bad (> 1.2)", align="right"),
            ],
            rows=rhat_rows,
        )
    )

    sec = Section(
        id="meridian_model",
        title="Meridian MMM: specification, fit and validation",
        subtitle=f"Bayesian regression of daily total sales on media spend and controls, validated on the {q4_label} hold-out.",
        blocks=blocks,
    )
    return sec, metrics_summary


def _calibration_caption(rows: list[dict[str, Any]], weak: set[str]) -> str | None:
    """Why Meridian's table can pass channels the "mostly prior" flag catches: its width check is spend-weighted."""
    passed = [r for r in rows if r.get("slug") in weak and not str(r.get("high_variance", "")).startswith("Review")]
    if not passed:
        return None
    widths = ", ".join(f"{r['channel']} {r['hv_ratio']:.2f}×" if r.get("hv_ratio") is not None else str(r["channel"]) for r in passed)
    return (
        "Meridian's high-variance check multiplies each channel's interval width (relative to the prior) by its share of "
        "spend and flags it only above 1.0, so small channels pass even when the data barely narrowed their ROI "
        f"(interval width vs the prior's: {widths}); “Shifted” means the posterior moved, not that it became precise. The “mostly prior” flag above "
        "is not weighted by spend."
    )


def build_drivers_roi_section(
    mmm: Any,
    wide_df: pd.DataFrame,
    cfg: MeridianSpecConfig,
) -> tuple[Section, pd.DataFrame, dict[str, pd.DataFrame]]:
    """Build the `drivers_roi` section and return `(section, roi_df, response_curves)`."""
    fit_df = extract_fit_table(mmm, cfg)
    roi_df = extract_roi_table(mmm)
    health_review = extract_health_review(mmm)
    adstock_df = extract_adstock_curves(mmm)
    response_curves = extract_response_curves(mmm)
    weekly_contrib = extract_weekly_contributions(mmm, fit_df)
    whatif_res = compute_catalog_whatif(mmm, wide_df, cfg)

    media_rt = roi_df.drop(index="All Channels", errors="ignore").sort_values("roi", ascending=True)
    all_ch_row = roi_df.loc["All Channels"] if "All Channels" in roi_df.index else None
    media_share_pct = float(all_ch_row["pct_of_contribution"]) if all_ch_row is not None else float("nan")

    # 1. Revenue ROI chart (`03_roi`)
    ch_labels = [channel_label(c) for c in media_rt.index]
    roi_means = [round(float(v), 2) for v in media_rt["roi"]]
    roi_lo = [round(float(v), 2) for v in media_rt["roi_lo"]]
    roi_hi = [round(float(v), 2) for v in media_rt["roi_hi"]]
    err_plus = [round(hi - m, 2) for m, hi in zip(roi_means, roi_hi, strict=False)]
    err_minus = [round(m - lo, 2) for m, lo in zip(roi_means, roi_lo, strict=False)]

    fig_roi = bar_figure(
        categories=ch_labels,
        series=[
            {
                "name": "Revenue ROI (90% CI)",
                "values": roi_means,
                "colors": [channel_color(channel_label(c), idx) for idx, c in enumerate(media_rt.index)],
                "error_y": {"type": "data", "symmetric": False, "array": err_plus, "arrayminus": err_minus},
            }
        ],
        orientation="h",
        title="Revenue ROI by Paid Media Channel (Posterior Mean & 90% Credible Interval)",
        subtitle="Incremental sales (€) per €1 spent; vertical dashed line marks revenue break-even (ROI = 1.0)",
        xaxis_title="Incremental Sales per €1 Spend (ROI)",
        vlines=[{"x": 1.0, "color": SLATE, "dash": "dash", "annotation": "Break-even (1.0x)"}],
    )

    # 2. Weekly sales decomposition (`03_contribution_weekly`)
    wk_dates = [d.strftime("%Y-%m-%d") for d in weekly_contrib.index]
    order_ch = [c for c in media_rt.sort_values("pct_of_contribution", ascending=False).index if c in weekly_contrib.columns]
    base_wk_m = (weekly_contrib["baseline"].clip(lower=0.0) / 1e6).round(3)
    actual_wk_m = (weekly_contrib["actual"] / 1e6).round(3)
    media_wk_m = {c: (weekly_contrib[c].clip(lower=0.0) / 1e6).round(3) for c in order_ch}
    total_mod_wk_m = (base_wk_m + sum(media_wk_m.values(), pd.Series(0.0, index=weekly_contrib.index))).clip(lower=1e-6)

    decomp_vrects: list[dict[str, Any]] = []
    decomp_vlines: list[dict[str, Any]] = []
    if cfg.holdout and len(weekly_contrib) > 0:
        ho_start_ts = pd.Timestamp(cfg.holdout[0])
        ho_wks = weekly_contrib.index[weekly_contrib.index >= ho_start_ts]
        if len(ho_wks) > 0:
            ho_x0 = ho_wks[0].strftime("%Y-%m-%d")
            decomp_vrects.append(
                {
                    "x0": ho_x0,
                    "x1": weekly_contrib.index[-1].strftime("%Y-%m-%d"),
                    "color": HOLDOUT_SHADE,
                    "label": "Hold-out",
                }
            )
            decomp_vlines.append({"x": ho_x0, "color": SLATE, "width": 1.0, "dash": "dot"})

    decomp_series: list[dict[str, Any]] = [
        {
            "name": "Baseline & Controls",
            "y": base_wk_m.tolist(),
            "customdata": (base_wk_m / total_mod_wk_m * 100.0).round(1).tolist(),
            "color": "#B8C2D0",
            "fillcolor": "rgba(213, 220, 230, 0.88)",
            "width": 0.6,
            "stackgroup": "one",
            "legendrank": 10,
            "hovertemplate": "<b>Baseline & Controls</b>: €%{y:.2f}M (%{customdata:.1f}%)<extra></extra>",
        }
    ]
    share_series: list[dict[str, Any]] = [
        {
            "name": "Baseline & Controls",
            "y": base_wk_m.tolist(),
            "customdata": base_wk_m.tolist(),
            "color": "#B8C2D0",
            "fillcolor": "rgba(213, 220, 230, 0.88)",
            "width": 0.6,
            "stackgroup": "one",
            "groupnorm": "percent",
            "legendrank": 10,
            "hovertemplate": "<b>Baseline & Controls</b>: %{y:.1f}% (€%{customdata:.2f}M)<extra></extra>",
        }
    ]
    for idx, c in enumerate(order_ch):
        label = channel_label(c)
        col = channel_color(label, idx)
        ch_m = media_wk_m[c]
        ch_pct = (ch_m / total_mod_wk_m * 100.0).round(1)
        decomp_series.append(
            {
                "name": label,
                "y": ch_m.tolist(),
                "customdata": ch_pct.tolist(),
                "color": col,
                "fillcolor": _hex_to_rgba(col, 0.90),
                "width": 0.35,
                "stackgroup": "one",
                "legendrank": 20 + idx,
                "hovertemplate": f"<b>{label}</b>: €%{{y:.2f}}M (%{{customdata:.1f}}%)<extra></extra>",
            }
        )
        share_series.append(
            {
                "name": label,
                "y": ch_m.tolist(),
                "customdata": ch_m.tolist(),
                "color": col,
                "fillcolor": _hex_to_rgba(col, 0.90),
                "width": 0.35,
                "stackgroup": "one",
                "groupnorm": "percent",
                "legendrank": 20 + idx,
                "hovertemplate": f"<b>{label}</b>: %{{y:.1f}}% (€%{{customdata:.2f}}M)<extra></extra>",
            }
        )
    decomp_series.extend(
        [
            {
                "name": "Actual Halo",
                "y": actual_wk_m.tolist(),
                "color": "rgba(255,255,255,0.80)",
                "width": 3.4,
                "showlegend": False,
                "hoverinfo": "skip",
            },
            {
                "name": "Actual Weekly Sales",
                "y": actual_wk_m.tolist(),
                "color": "#0F1828",
                "width": 1.85,
                "legendrank": 100,
                "hovertemplate": "<b>Actual Weekly Sales</b>: €%{y:.2f}M<extra></extra>",
            },
        ]
    )

    decomp_subtitle = (
        f"Stacked posterior mean contributions across full weeks of {weekly_contrib.index[0]:%b %Y}–"
        f"{weekly_contrib.index[-1]:%b %Y} vs observed weekly total sales"
        if len(weekly_contrib)
        else "Stacked posterior mean contributions vs observed weekly total sales"
    )
    decomp_xaxis = {
        "type": "date",
        "showgrid": False,
        "tickformat": "%b %Y",
        "hoverformat": "Week ending %d %b %Y",
    }
    decomp_margin = {"l": 56, "r": 24, "t": 58, "b": 48}

    # Monthly stacked bar variant (eliminates weekly bar subpixel moiré while showing crisp monthly totals)
    monthly_contrib = weekly_contrib.resample("MS").sum() if len(weekly_contrib) else weekly_contrib
    mo_dates = [d.strftime("%Y-%m-%d") for d in monthly_contrib.index]
    base_mo_m = (monthly_contrib["baseline"].clip(lower=0.0) / 1e6).round(3) if len(monthly_contrib) else pd.Series(dtype=float)
    actual_mo_m = (monthly_contrib["actual"] / 1e6).round(3) if len(monthly_contrib) else pd.Series(dtype=float)
    media_mo_m = {c: (monthly_contrib[c].clip(lower=0.0) / 1e6).round(3) for c in order_ch} if len(monthly_contrib) else {}
    total_mod_mo_m = (
        (base_mo_m + sum(media_mo_m.values(), pd.Series(0.0, index=monthly_contrib.index))).clip(lower=1e-6)
        if len(monthly_contrib)
        else pd.Series(dtype=float)
    )
    mo_series: list[dict[str, Any]] = [
        {
            "name": "Baseline & Controls",
            "values": base_mo_m.tolist(),
            "customdata": (base_mo_m / total_mod_mo_m * 100.0).round(1).tolist() if len(monthly_contrib) else [],
            "color": "#CED6E2",
            "marker_line": {"color": "#FFFFFF", "width": 0.5},
            "legendrank": 10,
            "hovertemplate": "<b>Baseline & Controls</b>: €%{y:.2f}M (%{customdata:.1f}%)<extra></extra>",
        }
    ]
    for idx, c in enumerate(order_ch):
        label = channel_label(c)
        col = channel_color(label, idx)
        ch_mo = media_mo_m[c]
        mo_series.append(
            {
                "name": label,
                "values": ch_mo.tolist(),
                "customdata": (ch_mo / total_mod_mo_m * 100.0).round(1).tolist(),
                "color": col,
                "marker_line": {"color": "#FFFFFF", "width": 0.5},
                "legendrank": 20 + idx,
                "hovertemplate": f"<b>{label}</b>: €%{{y:.2f}}M (%{{customdata:.1f}}%)<extra></extra>",
            }
        )
    fig_decomp_monthly = bar_figure(
        categories=mo_dates,
        series=mo_series,
        barmode="stack",
        bargap=0.16,
        height=430,
        margin=decomp_margin,
        yaxis_title="Monthly Sales (€ Millions)",
        xaxis={"type": "date", "showgrid": False, "tickformat": "%b %Y", "hoverformat": "%b %Y"},
        yaxis={"tickprefix": "€", "ticksuffix": "M", "rangemode": "tozero"},
        legend={"traceorder": "normal"},
        vrects=decomp_vrects,
        vlines=decomp_vlines,
        extra_traces=[
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Actual Monthly Sales",
                "x": mo_dates,
                "y": actual_mo_m.tolist(),
                "line": {"color": "#0F1828", "width": 2.0},
                "marker": {"color": "#0F1828", "size": 5, "line": {"color": "#FFFFFF", "width": 1}},
                "legendrank": 100,
                "hovertemplate": "<b>Actual Monthly Sales</b>: €%{y:.2f}M<extra></extra>",
            }
        ],
    )
    fig_decomp_share = line_figure(
        x=wk_dates,
        series=share_series,
        height=430,
        margin=decomp_margin,
        yaxis_title="Share of Modelled Sales (%)",
        xaxis=decomp_xaxis,
        yaxis={"ticksuffix": "%", "range": [0, 100]},
        legend={"traceorder": "normal"},
        vrects=decomp_vrects,
        vlines=decomp_vlines,
    )

    fig_decomp = line_figure(
        x=wk_dates,
        series=decomp_series,
        title="Weekly Sales Decomposition: Baseline & Controls vs Incremental Media Contributions (€M)",
        subtitle=decomp_subtitle,
        yaxis_title="Weekly Sales (€ Millions)",
        height=430,
        margin=decomp_margin,
        xaxis=decomp_xaxis,
        yaxis={"tickprefix": "€", "ticksuffix": "M", "rangemode": "tozero"},
        legend={"traceorder": "normal"},
        vrects=decomp_vrects,
        vlines=decomp_vlines,
    )
    fig_decomp["variants"] = {
        "Weekly (€M)": {"data": fig_decomp["data"], "layout": fig_decomp["layout"]},
        "Monthly (€M)": {"data": fig_decomp_monthly["data"], "layout": fig_decomp_monthly["layout"]},
        "Share (%)": {"data": fig_decomp_share["data"], "layout": fig_decomp_share["layout"]},
    }

    # 3. Adstock decay curves (`03_adstock`)
    ad_panels = []
    for idx, c in enumerate(media_rt.sort_values("spend", ascending=False).index):
        sub = adstock_df[adstock_df["channel"] == c]
        label = channel_label(c)
        col = channel_color(label, idx)
        if len(sub) > 0:
            ad_panels.append(
                {
                    "title": f"{label}",
                    "x": [int(t) for t in sub["time_units"]],
                    "traces": [
                        *_band_traces(sub["ci_lo"], sub["ci_hi"], col, 3),
                        {
                            "name": "Remaining Effect",
                            "y": [round(float(v), 3) for v in sub["mean"]],
                            "color": col,
                        },
                    ],
                }
            )
    fig_adstock = small_multiples_figure(
        panels=ad_panels,
        ncols=3,
        title="Posterior Adstock Carry-Over Curves by Channel (Days After Spend / In-Home Date)",
        subtitle="Share of initial media impulse remaining n days after exposure (posterior mean, 90 % CI)",
        height_per_row=240,
    )

    # 4. Hill saturation response curves (`03_response_curves`)
    rc_panels = []
    for idx, c in enumerate(media_rt.sort_values("spend", ascending=False).index):
        if c in response_curves:
            rc_df = response_curves[c]
            label = channel_label(c)
            col = channel_color(label, idx)
            rc_panels.append(
                {
                    "title": f"{label} (0–200% Spend)",
                    "x": [round(float(s) / 1e6, 2) for s in rc_df["spend"]],
                    "traces": [
                        *_band_traces(rc_df["ci_lo"] / 1e6, rc_df["ci_hi"] / 1e6, col, 2),
                        {
                            "name": "Incremental Sales (€M)",
                            "y": [round(float(m) / 1e6, 2) for m in rc_df["mean"]],
                            "color": col,
                        },
                        *(
                            [
                                {
                                    "name": "Actual spend",
                                    "x": [round(float(rc_df.loc[cur, "spend"]) / 1e6, 2)],
                                    "y": [round(float(rc_df.loc[cur, "mean"]) / 1e6, 2)],
                                    "mode": "markers",
                                    "marker": {"color": col, "size": 9, "line": {"color": "#FFFFFF", "width": 2}},
                                }
                                for cur in [int((rc_df["multiplier"] - 1.0).abs().idxmin())]
                            ]
                            if "multiplier" in rc_df
                            else []
                        ),
                    ],
                }
            )
    fig_rc = small_multiples_figure(
        panels=rc_panels,
        ncols=3,
        title="Hill Saturation Response Curves: Incremental Sales (€M) vs Period Spend (€M, 0–200% of Actual)",
        subtitle="Posterior mean incremental sales with a 90 % credible band; the dot marks the actual spend",
        height_per_row=250,
    )

    # 5. Print-event skip what-if (`03_whatif_catalog`); only charted when the run has a print-event channel
    win_df = whatif_res["df"] if len(whatif_res["df"]) else pd.DataFrame({"forecast": [], "without_sendout": []})
    fig_whatif = line_figure(
        x=[d.strftime("%Y-%m-%d") for d in win_df.index],
        series=[
            {
                "name": "Forecast with Planned Budget",
                "y": win_df["forecast"].round(0).tolist(),
                "color": NAVY,
                "width": 2.2,
            },
            {
                "name": f"Forecast Skipping the {whatif_res['skip_date']} Send-Out",
                "y": win_df["without_sendout"].round(0).tolist(),
                "color": COPPER,
                "dash": "dash",
                "width": 2.2,
            },
        ],
        title=(
            f"Budget What-If Simulation: Skipping the {whatif_res['skip_date']} {channel_label(whatif_res['channel'] or '')} Send-Out "
            f"Costs {fmt_eur(whatif_res['sales_lost'])} in Sales"
        ),
        subtitle=(
            f"Production cost saved: {fmt_eur(whatif_res['cost_saved'])} | "
            f"Forecast sales lost over following weeks: {fmt_eur(whatif_res['sales_lost'])} "
            f"(revenue payback ratio = {whatif_res['ratio']:.2f}x)"
        ),
        yaxis_title="Daily Sales Forecast (€)",
    )

    weak = set(weakly_identified(roi_df))
    upper = [c for c in cfg.upper_bound_slugs if c in media_rt.index]
    roi_rows = []
    for c, r in media_rt.sort_values("spend", ascending=False).iterrows():
        notes = []
        if c in upper:
            notes.append("upper bound")
        if c in weak:
            notes.append("mostly prior")
        roi_rows.append(
            {
                "channel": channel_label(c),
                "spend": fmt_eur(float(r["spend"])),
                "pct_spend": f"{float(r['pct_of_spend']):.1f}%",
                "inc_sales": fmt_eur(float(r["incremental_outcome"])),
                "pct_contrib": f"{float(r['pct_of_contribution']):.1f}%",
                "roi": round(float(r["roi"]), 2),
                "roi_ci": f"[{float(r['roi_lo']):.2f}, {float(r['roi_hi']):.2f}]",
                "p_loss": f"{100.0 * float(r['p_roi_below_1']):.0f}%" if pd.notna(r.get("p_roi_below_1")) else "–",
                "mroi": round(float(r["mroi"]), 2),
                "evidence": ", ".join(notes) or "data",
            }
        )

    by_roi = media_rt.sort_values("roi", ascending=False)
    top = next((c for c in by_roi.index if c not in weak), by_roi.index[0])
    top_upper = top in upper
    print_slug = whatif_res.get("channel")
    flattest = media_rt["mroi"].idxmin() if len(media_rt) else None
    skip_label = pd.Timestamp(whatif_res["skip_date"]).strftime("%d %b %Y") if whatif_res.get("skip_date") else None

    kpis = [
        KpiItem(
            label=f"Highest ROI: {channel_label(top)}" + (" (upper bound)" if top_upper else ""),
            value=f"{float(media_rt.loc[top, 'roi']):.2f}×",
            delta=f"90 % interval {float(media_rt.loc[top, 'roi_lo']):.2f}–{float(media_rt.loc[top, 'roi_hi']):.2f}×; "
            f"{float(media_rt.loc[top, 'pct_of_contribution']):.1f}% of modelled sales",
            status="warn" if top_upper else "success",
        ),
    ]
    if print_slug and print_slug in media_rt.index:
        kpis.append(
            KpiItem(
                label=f"{channel_label(print_slug)} revenue ROI",
                value=f"{float(media_rt.loc[print_slug, 'roi']):.2f}×",
                delta=f"Marginal ROI {float(media_rt.loc[print_slug, 'mroi']):.2f}×",
                status="neutral",
            )
        )
    kpis.append(
        KpiItem(
            label="Media share of modelled sales",
            value=f"{media_share_pct:.1f}%",
            delta=f"Remaining {100.0 - media_share_pct:.1f}% is baseline & controls",
            status="neutral",
        )
    )
    if skip_label:
        kpis.append(
            KpiItem(
                label=f"Skipping one {channel_label(print_slug)} send-out",
                value=f"−{fmt_eur(whatif_res['sales_lost'])} sales",
                delta=f"saves {fmt_eur(whatif_res['cost_saved'])} cost (ratio {whatif_res['ratio']:.2f})",
                status="neutral",
            )
        )
    else:
        kpis.append(
            KpiItem(
                label="ROIs mostly from the prior",
                value=str(len(weak)),
                delta="of the media channels" if weak else "every ROI is informed by the data",
                status="warn" if weak else "success",
            )
        )

    callouts: list[Any] = []
    if upper:
        names = ", ".join(channel_label(c) for c in upper)
        share = sum(float(media_rt.loc[c, "pct_of_contribution"]) for c in upper)
        callouts.append(
            CalloutBlock(
                tone="warn",
                title=f"Upper-bound ROI: {names}",
                body=(
                    f"{names} {'is' if len(upper) == 1 else 'are'} billed per click and the cost follows {('its' if len(upper) == 1 else 'their')} "
                    f"own clicks, so spend rises whenever demand rises ({share:.1f}% of modelled sales). The model cannot separate "
                    "that demand from the media effect: the ROI is an upper bound. A geo or hold-out experiment should confirm the "
                    "lift before budget moves into these channels."
                    + (
                        f" {channel_label(flattest)} has the lowest marginal ROI ({float(media_rt.loc[flattest, 'mroi']):.2f}×)."
                        if flattest is not None
                        else ""
                    )
                ),
            )
        )
    if weak:
        names = ", ".join(channel_label(c) for c in weak)
        callouts.append(
            CalloutBlock(
                tone="info",
                title=f"ROI mostly from the prior: {names}",
                body=(
                    f"The posterior 90 % interval of {names} is still at least {100 * WEAK_IDENTIFICATION_RATIO:.0f} % as wide as "
                    "the prior's (as a ratio of high to low), so the data say little about these ROIs; even a channel without any "
                    "effect gets a positive ROI under a LogNormal prior. Treat them as unknown until an experiment measures them."
                ),
            )
        )

    blocks = [
        KpisBlock(items=kpis),
        *callouts,
        ChartBlock(
            id="drivers_roi_chart",
            title="Revenue ROI by channel (posterior mean, 90% credible interval)",
            subtitle=f"Incremental sales per € spent across the {len(media_rt)} media channels; 1.0 is revenue break-even.",
            figure=fig_roi,
        ),
        TableBlock(
            id="drivers_roi_table",
            title="What drives sales: spend, incremental sales, ROI and marginal ROI",
            subtitle=(
                f"Posterior means and 90% credible intervals over {wide_df.index[0]:%Y-%m-%d} .. {wide_df.index[-1]:%Y-%m-%d}"
                + (
                    f"; shares of the media budget, without the control{'s' if len(cfg.as_controls) > 1 else ''} "
                    f"{', '.join(channel_label(c) for c in cfg.as_controls)}"
                    if cfg.as_controls
                    else ""
                )
            ),
            columns=[
                TableColumn(key="channel", label="Channel"),
                TableColumn(key="spend", label="Spend", align="right"),
                TableColumn(key="pct_spend", label="Share of media spend", align="right"),
                TableColumn(key="inc_sales", label="Incremental sales", align="right"),
                TableColumn(key="pct_contrib", label="Share of sales", align="right"),
                TableColumn(key="roi", label="ROI", align="right"),
                TableColumn(key="roi_ci", label="ROI 90% interval", align="right"),
                TableColumn(key="p_loss", label="P(ROI < 1)", align="right"),
                TableColumn(key="mroi", label="Marginal ROI", align="right"),
                TableColumn(key="evidence", label="Evidence"),
            ],
            rows=roi_rows,
            caption="Evidence: “upper bound” = cost follows demand; “mostly prior” = the data barely narrowed the prior.",
        ),
        TableBlock(
            id="drivers_calibration_table",
            title="Meridian channel calibration score & incrementality experiment priority",
            subtitle=(
                "Composite calibration score (0–100, model average "
                f"{health_review['calibration_score']:.1f}): channels scoring below 67.5 are recommended by Meridian "
                "for geo/hold-out incrementality calibration due to implausible spend-weighted ROI, wide posterior CIs, "
                "or low control correlation (< 0.10)."
            ),
            columns=[
                TableColumn(key="channel", label="Channel"),
                TableColumn(key="calibration_score", label="Calibration Score", align="right"),
                TableColumn(key="prior_posterior_shift", label="Prior–Posterior Shift"),
                TableColumn(key="implausible_roi", label="Implausible ROI Check"),
                TableColumn(key="high_variance", label="High Variance Check"),
                TableColumn(key="potential_bias", label="Potential Bias Check"),
                TableColumn(key="priority", label="Experiment Priority"),
            ],
            rows=health_review["channel_calibration"],
            caption=_calibration_caption(health_review["channel_calibration"], weak),
        ),
        ChartBlock(
            id="drivers_weekly_decomp",
            title="Weekly sales decomposed: baseline & controls vs media channels (€M)",
            subtitle="Stacked posterior-mean contributions over full weeks; the line is actual weekly sales.",
            figure=fig_decomp,
            height=430,
            caption=(
                f"Paid media accounts for {media_share_pct:.1f}% of modelled sales across {len(weekly_contrib)} full weeks "
                f"(baseline & controls: {max(0.0, 100.0 - media_share_pct):.1f}%). "
                "Channels are stacked from largest to smallest contribution above the baseline."
                if media_share_pct == media_share_pct and len(weekly_contrib)
                else None
            ),
        ),
        ChartBlock(
            id="drivers_adstock_curves",
            title="Carry-over: share of the effect remaining n days after spend",
            subtitle="Posterior mean with a 90 % credible band by channel (print events: days after the in-home date).",
            figure=fig_adstock,
        ),
        ChartBlock(
            id="drivers_response_curves",
            title="Response curves: incremental sales vs total spend (0–200 % of actual)",
            subtitle="Incremental sales (€M) vs the channel's total spend over the period (€M), with a 90 % credible band; the dot is the actual spend.",
            figure=fig_rc,
        ),
    ]
    if skip_label:
        blocks.append(
            ChartBlock(
                id="drivers_whatif_catalog",
                title=f"What-if: skipping the {skip_label} {channel_label(print_slug)} send-out",
                subtitle=(
                    f"Forecast sales lost over the following weeks: {fmt_eur(whatif_res['sales_lost'])} vs "
                    f"{fmt_eur(whatif_res['cost_saved'])} cost saved (ratio {whatif_res['ratio']:.2f})."
                ),
                figure=fig_whatif,
            )
        )

    sec = Section(
        id="drivers_roi",
        title="Drivers & ROI: contributions, carry-over and response curves",
        subtitle="What each channel adds to sales, what it returns per euro, how long its effect lasts and where it saturates.",
        blocks=blocks,
    )
    return sec, roi_df, response_curves
