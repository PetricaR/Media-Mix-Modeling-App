"""Smoke tests for Meridian specification, smoke MCMC fit, optimiser, and scenario forecast."""

from __future__ import annotations

from pathlib import Path

import pytest

from mmm_studio.core.meridian import (
    SMOKE_SAMPLING,
    MeridianSpecConfig,
    build_drivers_roi_section,
    build_example_plan,
    build_input_data,
    build_meridian_model,
    build_meridian_model_section,
    build_meridian_table,
    build_optimiser_section,
    fast_forecast_from_multipliers,
    fit_meridian,
    meridian_roles,
)
from mmm_studio.core.tables import build_daily_wide_table
from mmm_studio.core.workbook import profile_workbook


def test_meridian_table_and_fast_forecast_helpers(fixture_xlsx_path: Path) -> None:
    raw_df, profile = profile_workbook(fixture_xlsx_path)
    wide = build_daily_wide_table(raw_df, config=profile.role_config, drop_2025=True)

    cfg = MeridianSpecConfig()
    tbl = build_meridian_table(wide, catalog_shift=cfg.catalog_shift, n_fourier=cfg.n_fourier)
    spend_cols, media_names, controls = meridian_roles(tbl, cfg)

    assert len(tbl) == 731
    assert len(spend_cols) == 6
    assert "affiliate_spend" in controls
    assert "catalog" in media_names

    plan = build_example_plan(wide, days=42)
    assert len(plan) == 42
    assert (plan["catalog"] > 0).sum() == 2

    # Mock base_daily to verify fast_forecast_from_multipliers
    import pandas as pd

    base_daily = pd.DataFrame(
        {
            "forecast": 800_000.0,
            "credible_lo": 740_000.0,
            "credible_hi": 860_000.0,
            "prediction_lo": 680_000.0,
            "prediction_hi": 920_000.0,
            "planned_spend": plan.sum(axis=1).to_numpy(),
        },
        index=plan.index,
    )
    scen = fast_forecast_from_multipliers(
        base_daily=base_daily,
        base_plan=plan,
        multipliers={"sea": 1.2, "catalog": 0.0},
        horizon_days=28,
    )
    assert scen["horizon_days"] == 28
    assert len(scen["daily"]) == 28
    assert "data" in scen["figure"]


@pytest.mark.slow
def test_meridian_smoke_fit_and_sections(fixture_xlsx_path: Path) -> None:
    raw_df, profile = profile_workbook(fixture_xlsx_path)
    wide = build_daily_wide_table(raw_df, config=profile.role_config, drop_2025=True)

    cfg = MeridianSpecConfig()
    tbl = build_meridian_table(wide, catalog_shift=cfg.catalog_shift, n_fourier=cfg.n_fourier)
    mmm = fit_meridian(build_meridian_model(build_input_data(tbl, cfg), cfg), seed=0, **SMOKE_SAMPLING)

    mod_sec, metrics_summary = build_meridian_model_section(mmm, wide, cfg)
    assert mod_sec.id == "meridian_model"
    assert "holdout_q4_2024" in metrics_summary

    drv_sec, roi_df, response_curves = build_drivers_roi_section(mmm, wide, cfg)
    assert drv_sec.id == "drivers_roi"
    assert len(roi_df) >= 6

    opt_sec, opt_res = build_optimiser_section(roi_df, response_curves)
    assert opt_sec.id == "optimiser"
    assert opt_res["total_budget"] > 0
