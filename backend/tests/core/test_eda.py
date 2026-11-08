"""Tests for core EDA modules and golden numerical assertions."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from mmm_studio.core.calendar import add_calendar_and_adstock_features
from mmm_studio.core.eda import (
    build_data_quality_section,
    build_explore_catalog_section,
    build_explore_channels_section,
    build_explore_correlations_section,
    build_explore_efficiency_section,
    build_explore_readiness_section,
    build_explore_spend_section,
    build_explore_target_section,
    compute_halo_uplift_7d,
    compute_vif_table,
)
from mmm_studio.core.schema import DataProfile, slugify_channel


def test_all_eda_sections_build_cleanly(
    profiled_sample: tuple[pd.DataFrame, DataProfile],
    wide_full: pd.DataFrame,
    golden_data: dict[str, Any],
) -> None:
    df_raw, profile = profiled_sample
    wide_feat = add_calendar_and_adstock_features(wide_full, config=profile.role_config)

    s_quality = build_data_quality_section(df_raw, wide_feat, profile)
    assert s_quality.slug == "data_quality"
    assert len(s_quality.blocks) >= 5

    s_target = build_explore_target_section(wide_feat)
    assert s_target.slug == "explore_target"
    assert len(s_target.blocks) >= 8

    s_channels = build_explore_channels_section(wide_feat, profile.role_config)
    assert s_channels.slug == "explore_channels"
    assert len(s_channels.blocks) >= 5

    s_spend = build_explore_spend_section(wide_feat, profile.role_config)
    assert s_spend.slug == "explore_spend"
    assert len(s_spend.blocks) >= 6

    s_efficiency = build_explore_efficiency_section(wide_feat, profile.role_config)
    assert s_efficiency.slug == "explore_efficiency"
    assert len(s_efficiency.blocks) >= 5

    s_corr = build_explore_correlations_section(wide_feat, profile.role_config)
    assert s_corr.slug == "explore_correlations"
    assert len(s_corr.blocks) >= 6

    s_cat = build_explore_catalog_section(wide_feat, profile.role_config)
    assert s_cat.slug == "explore_catalog"
    assert len(s_cat.blocks) >= 7

    s_ready = build_explore_readiness_section(wide_feat)
    assert s_ready.slug == "explore_readiness"
    assert len(s_ready.blocks) >= 4

    # Verify VIF golden assertions
    cost_cols = [
        f"cost_{slugify_channel(ch)}"
        for ch in profile.role_config.paid_channels
        if ch != "Catalog" and f"cost_{slugify_channel(ch)}" in wide_feat.columns
    ]
    vif_dict = compute_vif_table(wide_feat[cost_cols])
    vifs = list(vif_dict.values())
    assert min(vifs) == pytest.approx(golden_data["digital_vif_range"]["min"], abs=0.05)
    assert max(vifs) == pytest.approx(golden_data["digital_vif_range"]["max"], abs=0.05)

    # Verify catalog 7d halo uplift golden assertion
    halo_dict = compute_halo_uplift_7d(wide_feat, profile.role_config)
    assert float(halo_dict["total_sales"]) == pytest.approx(
        golden_data["halo_uplift_7d_pct"]["total_sales"], abs=1.0
    )


def test_eda_numbers_match_golden_numbers(
    profiled_sample: tuple[pd.DataFrame, DataProfile],
    wide_model: pd.DataFrame,
    golden_data: dict[str, Any],
) -> None:
    from mmm_studio.core.eda.stats import catalog_event_study, correlation_robustness
    from mmm_studio.core.metrics import in_sample_naive_baselines
    from mmm_studio.report.facts import compute_case_facts

    _, profile = profiled_sample
    cfg = profile.role_config
    panel = add_calendar_and_adstock_features(wide_model, config=cfg)
    y = panel["total_sales"]
    target = golden_data["target_summary"]
    assert len(panel) == golden_data["calendar_days"]
    assert y.mean() == pytest.approx(target["mean_daily_sales"], abs=1)
    assert y.std() == pytest.approx(target["std_daily_sales"], abs=1)

    naive = {r["model"]: r["MAE_over_mean"] for _, r in in_sample_naive_baselines(y).iterrows()}
    gold_naive = golden_data["naive_baselines_mae_over_mean"]
    assert naive["Naive: constant mean"] == pytest.approx(gold_naive["constant_mean"], abs=0.001)
    assert naive["Naive: weekday mean"] == pytest.approx(gold_naive["weekday_mean"], abs=0.001)
    assert naive["Naive: lag-7 (last week)"] == pytest.approx(gold_naive["lag_7"], abs=0.001)
    assert naive["Naive: lag-1 (yesterday, 1-step)"] == pytest.approx(gold_naive["lag_1"], abs=0.001)

    facts = compute_case_facts(panel, cfg)
    assert facts["yoy_sales_pct"] == pytest.approx(golden_data["yoy_growth"]["sales_growth_2024_vs_2023_pct"], abs=0.05)
    assert facts["yoy_spend_pct"] == pytest.approx(golden_data["yoy_growth"]["spend_growth_2024_vs_2023_pct"], abs=0.05)
    assert facts["paid_share_pct"] == pytest.approx(golden_data["yoy_growth"]["paid_share_attributed_sales_pct"], abs=0.05)
    assert facts["catalog_best_lag"] == 3
    assert facts["catalog_best_lag_r"] == pytest.approx(golden_data["catalog_lag_3_corr"], abs=0.005)
    assert facts["top25_after_sendout"] == 13
    assert facts["missing_channel_days"] == {k: v for k, v in golden_data["missing_channel_days"].items() if k != "total"}
    for ch, ref in golden_data["halo_uplift_7d_pct"].items():
        assert facts["halo_7d"][ch] == pytest.approx(ref, abs=0.6)

    study = catalog_event_study(panel)
    assert study.loc[3, "lift"] == pytest.approx(0.226, abs=0.005)
    robust = correlation_robustness(panel, cfg)
    assert robust.loc["SEA", "weekly"] == pytest.approx(0.981, abs=0.005)
    assert robust.loc["Display", "deseasonalised"] == pytest.approx(0.009, abs=0.005)


def _table(section, block_id: str) -> list[dict[str, Any]]:
    return next(b for b in section.blocks if getattr(b, "id", None) == block_id).rows


def test_eda_tables_match_reference_tables(
    profiled_sample: tuple[pd.DataFrame, DataProfile],
    wide_model: pd.DataFrame,
) -> None:
    """Reference tables (sales lost on missing days, daily sales by year, catalog per year) are reproduced cell for cell."""
    df_raw, profile = profiled_sample
    panel = add_calendar_and_adstock_features(wide_model, config=profile.role_config)

    lost = {r["channel"]: r for r in _table(build_data_quality_section(df_raw, panel, profile), "quality_missing_sales_lost")}
    assert (lost["TV"]["lost"], lost["TV"]["share"]) == ("€610,390", "0.091%")
    assert (lost["Catalog"]["lost"], lost["Owned"]["lost"]) == ("€133,697", "€147,072")

    by_year = {r["stat"]: r for r in _table(build_explore_target_section(panel), "target_profile_by_year")}
    assert (by_year["Total"]["y2023"], by_year["Total"]["y2024"]) == ("€313,430,750", "€358,867,481")
    assert (by_year["Skewness"]["y2023"], by_year["Excess kurtosis"]["all"]) == ("0.81", "0.32")

    s_cat = build_explore_catalog_section(panel, profile.role_config)
    per_year = {r["year"]: r for r in _table(s_cat, "catalog_by_year")}
    assert (per_year[2023]["cost_per_copy"], per_year[2024]["cost_per_copy"]) == ("€0.677", "€0.788")
    evidence = next(b for b in s_cat.blocks if getattr(b, "title", "") == "Sales peaks follow the catalog").body
    assert "**13 of the 25**" in evidence and "€698.7k/day" in evidence and "€1.08M/day" in evidence


def test_eda_figures_match_reference_headlines(
    profiled_sample: tuple[pd.DataFrame, DataProfile],
    wide_model: pd.DataFrame,
) -> None:
    """Figure headlines carry the sample workbook's reference facts."""
    _, profile = profiled_sample
    cfg = profile.role_config
    panel = add_calendar_and_adstock_features(wide_model, config=cfg)

    def block(section, block_id: str):
        return next(b for b in section.blocks if getattr(b, "id", None) == block_id)

    gap = block(build_explore_catalog_section(panel, cfg), "catalog_natural_experiment")
    assert gap.title == "Without a catalog, September 2023 fell to €698.7k/day — the lowest month of the period"

    s_corr = build_explore_correlations_section(panel, cfg)
    assert "mean r = 0.72" in block(s_corr, "corr_channel_sales").title
    cross = block(s_corr, "corr_cost_vs_other_channel_sales").figure["data"][0]
    row = cross["z"][cross["y"].index("Catalog cost (lag 3)")]
    assert round(row[cross["x"].index("Catalog")], 2) == 0.60

    weekday = block(build_explore_spend_section(panel, cfg), "spend_weekday_index").subtitle
    assert "Social Media spends 1.7× its average on Mondays" in weekday and "Affiliate spends 1.7× its average on Sundays" in weekday


def test_eda_reproduces_remaining_reference_tables(
    profiled_sample: tuple[pd.DataFrame, DataProfile],
    wide_model: pd.DataFrame,
) -> None:
    """Quartiles, weekday spend in €, spend per year, metric ranges and quarterly KPIs."""
    df_raw, profile = profiled_sample
    cfg = profile.role_config
    panel = add_calendar_and_adstock_features(wide_model, config=cfg)

    by_year = {r["stat"]: r for r in _table(build_explore_target_section(panel), "target_profile_by_year")}
    assert (by_year["25th percentile"]["all"], by_year["75th percentile"]["all"]) == ("€782,628", "€1,032,651")

    s_spend = build_explore_spend_section(panel, cfg)
    monday = _table(s_spend, "spend_weekday_eur")[0]
    assert (monday["sea"], monday["social_media"]) == ("€46,388", "€18,066")
    catalog = next(r for r in _table(s_spend, "spend_by_year") if r["channel"] == "Catalog")
    assert (catalog["spend_prev"], catalog["spend_last"], catalog["cv_active"]) == ("€16,846,137", "€21,914,149", "0.11")

    ranges = {(r["channel"], r["metric"]): r for r in _table(build_data_quality_section(df_raw, panel, profile), "quality_metric_ranges")}
    assert (ranges[("SEA", "Sales")]["median"], ranges[("Catalog", "Impressions")]["count"]) == ("250,181", 728)

    quarterly = {r["series"]: r for r in _table(build_explore_efficiency_section(panel, cfg), "efficiency_quarterly")}
    assert quarterly["CPM (€) · Affiliate"]["2024Q4"] == "100.496"
    assert quarterly["Attributed ROAS · SEA"]["2023Q1"] == "6.482"


def test_meridian_pre_modeling_checks(
    profiled_sample: tuple[pd.DataFrame, DataProfile],
    wide_model: pd.DataFrame,
) -> None:
    """Meridian pre-modeling checks compute data-to-parameter ratio, VIF, pairwise correlation, and IQR-inlier variation."""
    from mmm_studio.core.eda import compute_meridian_pre_modeling_checks

    _, profile = profiled_sample
    cfg = profile.role_config
    panel = add_calendar_and_adstock_features(wide_model, config=cfg)

    pre = compute_meridian_pre_modeling_checks(panel, cfg)
    assert pre["n_times"] >= 700
    assert pre["n_parameters"] >= 20
    assert pre["data_to_parameter_ratio_train"] > 5.0
    assert pre["full_knots_ratio"] < 1.0
    assert pre["kpi_scaled_std_no_outliers"] > 0.0
    assert len(pre["checks"]) == 6

    s_ready = build_explore_readiness_section(panel, cfg)
    rows = _table(s_ready, "readiness_meridian_eda_checks")
    assert len(rows) == 6
    assert "Data-to-parameter ratio" in rows[0]["check"]
    assert rows[0]["status"] == "PASS"
