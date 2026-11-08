"""Regression tests for the cross-tab consistency audit (2026-11-06).

Every page names the same demand-following channels and the same in-home shift as the model, describes the model it
fits, never mixes two models in one verdict and never assumes a number it does not have. Each test states the expected
behaviour, independently of the current output.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from mmm_studio.core.calendar import add_calendar_and_adstock_features
from mmm_studio.core.meridian.spec import (
    MeridianSpecConfig,
    demand_following_slugs,
    meridian_roles,
    meridian_table,
)
from mmm_studio.core.schema import DataProfile, DataRoleConfig


def _cfg(profiled_sample: tuple[pd.DataFrame, DataProfile], **channel_changes: dict[str, Any]) -> DataRoleConfig:
    """The sample workbook's role config with per-channel changes, e.g. `sea={"demand_following": False}`."""
    cfg = copy.deepcopy(profiled_sample[1].role_config)
    for ch in cfg.channels:
        for key, value in channel_changes.get(ch.slug, {}).items():
            setattr(ch, key, value)
    return cfg


def _table(sec: Any, block_id: str) -> list[dict[str, Any]]:
    return next(b for b in sec.blocks if getattr(b, "id", None) == block_id).rows


def _kpi(sec: Any, label_start: str) -> Any:
    return next(k for b in sec.blocks if b.type == "kpis" for k in b.items if k.label.startswith(label_start))


def test_preparation_meridian_table_is_the_table_the_model_fits(profiled_sample: Any, wide_model: pd.DataFrame) -> None:
    from mmm_studio.core.preparation import build_preparation_section

    cfg = _cfg(profiled_sample)
    roles = {r["role"]: r["columns"] for r in _table(build_preparation_section(wide_model, cfg), "prep_meridian_roles")}
    spec = MeridianSpecConfig.from_role_config(cfg, wide=wide_model)
    spend, _, controls = meridian_roles(meridian_table(wide_model, spec), spec)

    assert roles["media = media spend"].split(" (")[0].split(", ") == spend
    assert "affiliate_spend" not in roles["media = media spend"]  # a commission is a control, not media
    listed = [c.split(" (")[0] for c in roles["controls"].split(", ")]
    assert listed == controls and "affiliate_spend" in listed and "missing_tv" in listed


def test_demand_following_override_is_one_definition_for_model_narratives_and_optimiser(
    profiled_sample: Any, wide_model: pd.DataFrame, tmp_path: Any
) -> None:
    from mmm_studio.api.optimiser import _upper_bound_slugs
    from mmm_studio.report.facts import billing_links
    from mmm_studio.storage import LocalStorage

    assert demand_following_slugs(_cfg(profiled_sample), wide_model) == ("psm", "sea")  # inferred: r(cost, clicks) ≥ 0.9

    cfg = _cfg(profiled_sample, sea={"demand_following": False})  # the user unticks SEA; inference still says yes
    assert demand_following_slugs(cfg, wide_model) == ("psm",)
    assert MeridianSpecConfig.from_role_config(cfg, wide=wide_model).upper_bound_slugs == ("psm",)
    assert [n for n, _ in billing_links(wide_model, cfg)["upper"]] == ["PSM"]

    storage = LocalStorage(tmp_path)
    run = SimpleNamespace(id="r_df", config={"role_config": cfg.model_dump(mode="json")})
    media = ["sea", "catalog", "display", "display_network", "social_media", "psm"]
    assert _upper_bound_slugs(storage, run, media) == ["psm"]  # never `demand_following or inferred_demand_following`
    storage.write_json("runs/r_df/data/default_optimisation.json", {"upper_bound_channels": ["psm"]})
    assert _upper_bound_slugs(storage, run, media) == ["psm"]


def test_eda_narratives_name_every_demand_following_channel(profiled_sample: Any, wide_model: pd.DataFrame) -> None:
    from mmm_studio.core.eda import build_explore_readiness_section
    from mmm_studio.core.preparation import build_preparation_section

    cfg = _cfg(profiled_sample)
    panel = add_calendar_and_adstock_features(wide_model, config=cfg)
    row = next(r for r in _table(build_explore_readiness_section(panel, cfg), "readiness_implications_table") if r["finding"] == "Costs that follow demand")
    assert "PSM and SEA ROIs as upper bounds" in row["action"] and "Affiliate as a control" in row["action"]

    billing = next(r for r in _table(build_preparation_section(wide_model, cfg), "prep_cost_considerations") if r["topic"] == "Billing model")
    assert billing["observation"].startswith("PSM and SEA are billed per click")
    assert billing["handling"].startswith("PSM and SEA ROIs are upper bounds")


def test_pages_use_the_runs_in_home_shift(profiled_sample: Any, wide_model: pd.DataFrame, wide_full: pd.DataFrame) -> None:
    from mmm_studio.core.eda import build_data_quality_section
    from mmm_studio.core.preparation import build_preparation_section, compute_preparation_ols_ladder

    raw, profile = profiled_sample
    cfg3 = _cfg(profiled_sample, catalog={"in_home_shift_days": 3})
    assert cfg3.print_shift == 3

    prep = build_preparation_section(wide_model, cfg3)
    assert _kpi(prep, "Catalog in-home shift").value == "k = 3 days"
    assert any("shifted by k = 3 days" in r["columns"] for r in _table(prep, "prep_new_features"))
    assert "catalog_spend 3 days" in _table(prep, "prep_meridian_roles")[2]["columns"]
    # The ladder's in-home step and the channel importance use the same shift as the models
    k2 = compute_preparation_ols_ladder(wide_model, _cfg(profiled_sample))[-1]["adj_r2"]
    assert compute_preparation_ols_ladder(wide_model, cfg3)[-1]["adj_r2"] != pytest.approx(k2, abs=1e-6)

    quality = build_data_quality_section(
        raw, add_calendar_and_adstock_features(wide_full, config=cfg3), profile.model_copy(update={"role_config": cfg3})
    )
    print_row = next(r for r in _table(quality, "quality_anomaly_inventory") if r["issue"] == "Catalog episodic cost booking")
    assert "(+3 days" in print_row["treatment"]


def test_brief_and_overview_never_mix_meridian_and_ols_windows(profiled_sample: Any, wide_model: pd.DataFrame) -> None:
    from mmm_studio.core.ols import OLS2, evaluate_ols_suite
    from mmm_studio.pipeline.stages import ChannelNarrative, build_brief_section, build_overview_section
    from mmm_studio.report.facts import compute_case_facts

    cfg = _cfg(profiled_sample)
    ols_res = copy.deepcopy(evaluate_ols_suite(wide_model, config=cfg))
    for r in ols_res["rows"]:
        if r["window"] == "forward" and r["model"] == OLS2:
            r["MAE_over_mean"] = 0.30  # OLS misses the target on the forward window
    facts = compute_case_facts(add_calendar_and_adstock_features(wide_model, config=cfg), cfg)
    good = {"MAE_over_mean": 0.08, "R2": 0.9, "MAE": 1, "RMSE": 1, "MAPE": 0.08, "bias": 0}
    mer = {"holdout_q4_2024": good}  # Meridian's forward refit switched off
    names = ChannelNarrative(upper=["PSM", "SEA"], commission=["Affiliate"], print_name="Catalog")

    brief = build_brief_section(wide_model, facts, ols_res, mer, names=names)
    assert _kpi(brief, "Brief target").value == "Met"
    assert _kpi(brief, "Forward hold-out").value == "—"
    overview = build_overview_section(wide_model, facts, ols_res, mer, names=names)
    summary = next(b for b in overview.blocks if getattr(b, "title", "") == "Executive summary").body
    assert "Yes:" in summary  # the two pages give the same verdict


def test_decomposition_without_draws_uses_average_roi_and_assumes_nothing() -> None:
    from mmm_studio.core.meridian.forecast import BASELINE_LABEL, scenario_payload

    dates = pd.date_range("2025-01-02", periods=4, freq="D", name="date")
    plan = pd.DataFrame({"sea": [100.0] * 4, "display": [50.0] * 4, "affiliate": [10.0] * 4}, index=dates)
    base = pd.DataFrame({"forecast": 1000.0, "prediction_lo": 900.0, "prediction_hi": 1100.0}, index=dates)

    res = scenario_payload(base, plan, base, plan, "test", roi_map={"sea": 8.0, "display": 0.6})
    rows = {v["key"]: v for v in res["variable_contributions"]}
    assert rows["sea"]["base_contribution"] == pytest.approx(400 * 8.0) and rows["sea"]["base_roi"] == 8.0  # the ROI, not the mROI
    assert rows["display"]["base_contribution"] == pytest.approx(200 * 0.6)
    assert rows["affiliate"]["category"] != "media" and rows["affiliate"]["base_contribution"] == 0
    assert rows["baseline"]["label"] == BASELINE_LABEL
    assert sum(v["base_contribution"] for v in rows.values()) == pytest.approx(4000, abs=2)

    # A media channel without an ROI gets no assumed one: the baseline row says its sales are not split out
    draws = np.full((1, 4, 1), 5.0)
    res = scenario_payload(base, plan, base, plan, "test", effect_draws=draws, effect_channels=["display"], roi_map={"sea": 8.0})
    rows = {v["key"]: v for v in res["variable_contributions"]}
    assert rows["display"]["base_contribution"] == 0 and "Display (no ROI)" in rows["baseline"]["label"]


def test_backtest_total_is_checked_against_its_own_interval() -> None:
    from mmm_studio.core.meridian.forecast import interval_coverage, total_check

    dates = pd.date_range("2024-10-01", periods=10, freq="D")
    df = pd.DataFrame({"actual": 110.0, "forecast": 100.0, "prediction_lo": 95.0, "prediction_hi": 105.0}, index=dates)
    out = total_check(df, (970.0, 1030.0))
    assert out == {"days": 10, "error_pct": pytest.approx(10.0), "half_width_pct": pytest.approx(3.0), "inside": False}
    assert total_check(df, (1000.0, 1200.0))["inside"] is True
    assert total_check(df, None) is None
    assert interval_coverage(df) == 0.0 and interval_coverage(df, shift=10.0) == 1.0  # the level offset alone


def test_residual_caption_reads_the_weekday_signs() -> None:
    from mmm_studio.core.meridian.analysis import _residual_caption

    assert "a level offset" in _residual_caption([-5, -3, -2, -8, -1, -4, -6], [0.0] * 22, 2)
    split = _residual_caption([-5, 3, -2, 8, -1, 4, -6], [0.0] * 22, 2)
    assert "no common level offset" in split and "a level offset, not" not in split


def test_selection_tables_show_the_largest_demand_following_channel(profiled_sample: Any, wide_model: pd.DataFrame) -> None:
    from mmm_studio.core.meridian.selection import key_channels

    spec = MeridianSpecConfig.from_role_config(_cfg(profiled_sample), wide=wide_model)
    assert key_channels(spec, wide_model) == ["sea", "catalog", "affiliate"]  # SEA's spend dwarfs PSM's


def test_fast_fit_rois_outside_the_final_interval_are_called_out() -> None:
    from mmm_studio.core.meridian.analysis import _unstable_roi_note

    final = pd.DataFrame({"roi": [8.53, 2.48], "roi_lo": [6.64, 0.31], "roi_hi": [11.29, 8.32]}, index=["sea", "psm"])
    note = _unstable_roi_note({"sensitivity": [{"roi": {"sea": 7.04, "psm": 31.09}}]}, final)
    assert "PSM 31.09×" in note and "SEA" not in note
    assert _unstable_roi_note({"sensitivity": [{"roi": {"sea": 7.04}}]}, final) == ""


def test_meridian_calibration_table_explains_its_spend_weighting() -> None:
    from mmm_studio.core.meridian.analysis import _calibration_caption

    rows = [
        {"slug": "psm", "channel": "PSM", "high_variance": "Pass (1.07× prior)", "hv_ratio": 1.07},
        {"slug": "sea", "channel": "SEA", "high_variance": "Pass (0.19× prior)", "hv_ratio": 0.19},
    ]
    caption = _calibration_caption(rows, {"psm"})
    assert caption is not None and "share of spend" in caption and "PSM 1.07×" in caption and "SEA" not in caption
    assert _calibration_caption(rows, set()) is None


# --- Calibrated intervals of forecast totals (2026-11-06) -------------------------------------------------------------


def test_predictive_draws_keep_the_mean_and_the_daily_spread() -> None:
    from mmm_studio.core.meridian.forecast import predictive_from_expected

    expected = np.full((4000, 60), 1000.0)
    noise_sd = np.full((4000, 1), 100.0)
    old = predictive_from_expected(expected, noise_sd, seed=3)
    rng = np.random.default_rng(3)
    assert np.allclose(old, expected + rng.normal(size=expected.shape) * noise_sd)  # defaults = independent days

    new = predictive_from_expected(expected, noise_sd, seed=3, level_sd=0.08, ar1=0.4)
    assert new.mean() == pytest.approx(1000.0, rel=0.01)  # the forecast itself does not move
    resid = new - expected * (1.0 + (new - expected).mean(axis=1, keepdims=True) / 1000.0)
    lag1 = np.mean([np.corrcoef(r[:-1], r[1:])[0, 1] for r in resid[:500]])
    assert lag1 == pytest.approx(0.4, abs=0.08)
    # A 60-day total: independent 10 % daily noise gives ~1.3 % of the total (10 % / √60), the level offset ~8 %
    assert old.sum(axis=1).std() / 60_000 == pytest.approx(0.1 / np.sqrt(60), rel=0.1)
    assert new.sum(axis=1).std() / 60_000 == pytest.approx(0.08, abs=0.01)


def test_level_uncertainty_is_the_rms_of_earlier_out_of_sample_windows() -> None:
    from mmm_studio.core.meridian.forecast import MIN_LEVEL_WINDOWS, level_uncertainty, residual_ar1

    windows = [
        {"source": "A", "start": "2024-01-01", "end": "2024-02-11", "error": 0.06},
        {"source": "A", "start": "2024-03-01", "end": "2024-04-11", "error": -0.08},
        {"source": "B", "start": "2024-07-01", "end": "2024-09-30", "error": 0.10},
        {"source": "B", "start": "2024-10-01", "end": "2025-01-01", "error": -0.30},
    ]
    every = level_uncertainty(windows)
    assert every["level_sd"] == pytest.approx(np.sqrt((0.06**2 + 0.08**2 + 0.10**2 + 0.30**2) / 4))
    assert every["n_windows"] == 4 and every["sources"] == ["A", "B"]
    # A back-test from 2024-10-01 must not learn from its own window
    before = level_uncertainty(windows, before=pd.Timestamp("2024-10-01"))
    assert before["n_windows"] == 3 and before["level_sd"] == pytest.approx(np.sqrt((0.06**2 + 0.08**2 + 0.10**2) / 3))
    assert level_uncertainty(windows[: MIN_LEVEL_WINDOWS - 1]) is None  # too little evidence: not estimated

    x = np.sin(np.arange(200) / 3.0)
    assert residual_ar1(x, np.zeros(200)) > 0.9 and residual_ar1(np.ones(5), np.ones(5)) == 0.0


def test_benchmark_walk_forward_records_each_folds_total_error(profiled_sample: Any, wide_model: pd.DataFrame) -> None:
    from mmm_studio.core.ols import CV_FOLD_DAYS, OLS2, evaluate_ols_suite, ols_design_sets, ols_forecast

    cfg = _cfg(profiled_sample)
    res = evaluate_ols_suite(wide_model, config=cfg)
    errs = res["cv"]["level_errors"]
    assert len(errs) == len(res["cv"]["folds"]) == 8
    first = pd.Timestamp(errs[0]["start"])
    fold = (first - pd.Timedelta(days=1), first, first + pd.Timedelta(days=CV_FOLD_DAYS - 1))
    # Leak-free (the default): the fold's design is the panel as of the day before it
    from mmm_studio.core.as_of import as_of_panel, outcome_driven_slugs

    plan_cols = [c.removeprefix("cost_") for c in ols_design_sets(wide_model, cfg)["OLS-1 raw costs"].columns]
    ext = as_of_panel(wide_model, fold, plan_cols, outcome_driven_slugs(cfg, wide_model))
    p = ols_forecast(ext["total_sales"], ols_design_sets(ext, cfg)[OLS2], *fold)
    actual = float(wide_model.loc[p.index, "total_sales"].sum())
    assert errs[0]["error"] == pytest.approx((actual - float(p.sum())) / float(p.sum()))


def test_forecast_section_explains_the_width_of_a_totals_interval() -> None:
    from mmm_studio.core.meridian.forecast import _totals_method_text

    lvl = {"level_sd": 0.079, "n_windows": 10, "sources": ["Meridian hold-out", "OLS-2 Meridian inputs walk-forward"]}
    text = _totals_method_text(lvl, {"level_sd": 0.087, "n_windows": 7}, 0.38)
    assert "7.9 %" in text and "10 leak-free" in text and "lag-1 r = 0.38" in text and "7 windows that end before it" in text
    assert "not calibrated" in _totals_method_text(None, None, 0.38)
