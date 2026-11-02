"""Smoke tests for Meridian specification, smoke MCMC fit, optimiser, and scenario forecast."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
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
    wide = build_daily_wide_table(raw_df, config=profile.role_config, drop_2025=False)

    cfg = MeridianSpecConfig.from_role_config(profile.role_config, index=wide.index)
    assert cfg.holdout == ("2024-10-01", "2025-01-01")  # Q4 2024 through 1 Jan 2025, 93 days (notebook 03)
    tbl = build_meridian_table(wide, catalog_shift=cfg.catalog_shift, n_fourier=cfg.n_fourier)
    spend_cols, media_names, controls = meridian_roles(tbl, cfg)

    assert len(tbl) == 732
    assert len(spend_cols) == 6
    assert "affiliate_spend" in controls
    assert "catalog" in media_names

    plan = build_example_plan(wide, days=42)
    assert len(plan) == 42
    assert plan.index[0] == pd.Timestamp("2025-01-02")
    assert list(plan.index[plan["catalog"] > 0].strftime("%Y-%m-%d")) == ["2025-01-09", "2025-01-30"]
    assert plan.to_numpy().sum() == pytest.approx(8_008_549, abs=5)  # data/input/budget_plan_2025-01.csv

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
        mroi_map={"sea": 4.5, "catalog": 0.8},
    )
    assert scen["horizon_days"] == 28
    assert len(scen["daily"]) == 28
    assert "data" in scen["figure"]
    assert scen["delta_spend"] < 0 and "delta_sales_pct" in scen and scen["channel_summary"]


@pytest.mark.slow
def test_meridian_smoke_fit_and_sections(fixture_xlsx_path: Path) -> None:
    raw_df, profile = profile_workbook(fixture_xlsx_path)
    wide = build_daily_wide_table(raw_df, config=profile.role_config, drop_2025=False)

    cfg = MeridianSpecConfig.from_role_config(profile.role_config, index=wide.index)
    tbl = build_meridian_table(wide, catalog_shift=cfg.catalog_shift, n_fourier=cfg.n_fourier)
    mmm = fit_meridian(build_meridian_model(build_input_data(tbl, cfg), cfg), seed=0, **SMOKE_SAMPLING)

    mod_sec, metrics_summary = build_meridian_model_section(mmm, wide, cfg)
    assert mod_sec.id == "meridian_model"
    assert "holdout_q4_2024" in metrics_summary
    assert metrics_summary["holdout_days"] == 93
    assert "holdout_q3_2024" not in metrics_summary  # never invented without the forward refit

    drv_sec, roi_df, response_curves = build_drivers_roi_section(mmm, wide, cfg)
    assert drv_sec.id == "drivers_roi"
    assert len(roi_df) >= 6

    opt_sec, opt_res = build_optimiser_section(roi_df, response_curves)
    assert opt_sec.id == "optimiser"
    assert opt_res["total_budget"] > 0


def test_response_curve_optimiser_moves_budget_to_the_higher_marginal_return() -> None:
    """Regression: on euro-scale spends SLSQP used to see a zero gradient and return the starting allocation."""
    import numpy as np

    from mmm_studio.core.meridian.optimise import solve_constrained_optimisation

    spend = {"sea": 38.5e6, "display": 29.8e6}
    roi = pd.DataFrame({"spend": spend, "roi": {"sea": 8.0, "display": 0.7}, "mroi": {"sea": 4.5, "display": 0.2}})
    mult = np.round(np.arange(0, 2.01, 0.05), 2)
    curves = {
        ch: pd.DataFrame({"multiplier": mult, "spend": mult * sp, "mean": roi.loc[ch, "roi"] * sp * np.sqrt(mult)})
        for ch, sp in spend.items()
    }
    res = solve_constrained_optimisation(roi, curves, default_bounds=(0.7, 1.3))
    alloc = {a["channel"]: a["multiplier"] for a in res["allocations"]}
    # Display falls to its 0.7 floor and the freed budget goes to SEA (higher marginal return everywhere)
    assert alloc["display"] == pytest.approx(0.7, abs=0.005)
    assert alloc["sea"] == pytest.approx(1 + 0.3 * spend["display"] / spend["sea"], abs=0.005)
    assert res["uplift_pct"] > 1.0
    assert sum(a["optimised_spend"] for a in res["allocations"]) == pytest.approx(sum(spend.values()), rel=1e-6)
