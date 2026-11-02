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
    profiled_case_study: tuple[pd.DataFrame, DataProfile],
    wide_full: pd.DataFrame,
    golden_data: dict[str, Any],
) -> None:
    df_raw, profile = profiled_case_study
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
