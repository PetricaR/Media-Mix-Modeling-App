"""A generic 13-month dataset (run_d1437efd53, 2026-11-07): the example plan follows the current run-rate, a run whose
media spend rivals its sales starts with the total-media-contribution ROI prior, a partial-quarter hold-out is labelled
by its dates, and the Meridian pre-modelling checks describe this data, not the sample workbook."""

from __future__ import annotations

import numpy as np
import pandas as pd
from wide_data import CHANNELS, to_xlsx, wide_frame

from mmm_studio.core.eda.readiness import compute_meridian_pre_modeling_checks
from mmm_studio.core.features import quarter_label
from mmm_studio.core.meridian.forecast import build_example_plan, plan_growth_days, spend_beyond_history
from mmm_studio.core.model_settings import ModelSettings
from mmm_studio.core.schema import DataRoleConfig
from mmm_studio.core.tables import build_daily_wide_table
from mmm_studio.core.workbook import PRIOR_OVER_100_LIMIT, default_prior_over_100, profile_workbook


def _short_history(days: int = 393) -> pd.DataFrame:
    """13 months of daily cost; in the last 28 days one channel doubles, one pauses and one launches."""
    idx = pd.date_range("2022-01-01", periods=days, freq="D")
    last = np.arange(days) >= days - 28
    return pd.DataFrame(
        {
            "cost_steady": 100.0,
            "cost_doubled": np.where(last, 200.0, 100.0),
            "cost_paused": np.where(last, 0.0, 100.0),
            "cost_launched": np.where(last, 50.0, 0.0),
            "total_sales": 1000.0,
        },
        index=idx,
    )


def test_the_example_plan_of_a_short_history_carries_the_recent_run_rate() -> None:
    wide = _short_history()
    plan = build_example_plan(wide, days=42, paid_channels=["steady", "doubled", "paused", "launched"], print_slugs=())
    assert plan.index[0] == pd.Timestamp("2023-01-29")
    # Last year's same-weekday spend × (last 28 days / the same 28 days a year earlier); 13 months used to give 1.0
    assert np.allclose(plan["steady"], 100.0)
    assert np.allclose(plan["doubled"], 200.0)
    assert np.allclose(plan["paused"], 0.0)
    assert np.allclose(plan["launched"], 50.0)  # no spend a year earlier: its recent weekday means
    assert (plan_growth_days(393), plan_growth_days(500), plan_growth_days(732), plan_growth_days(370)) == (28, 135, 365, 5)


def test_spend_above_the_history_is_reported_as_extrapolation() -> None:
    wide = _short_history()
    plan = pd.DataFrame({"steady": [100.0, 150.0], "doubled": [250.0, 250.0], "unknown": [1.0, 1.0]})
    assert spend_beyond_history(wide, plan) == [("steady", 1), ("doubled", 2)]


def test_a_partial_quarter_is_labelled_by_its_dates() -> None:
    assert quarter_label("2024-10-01", "2025-01-01") == "Q4 2024"  # the sample workbook's hold-out
    assert quarter_label("2024-07-01", "2024-09-30") == "Q3 2024"
    assert quarter_label("2023-01-01", "2023-01-28") == "2023-01-01 – 2023-01-28"


def _spend_heavy_xlsx() -> bytes:
    frame = wide_frame(400)
    for col in CHANNELS:
        frame[col] = frame[col] * 3  # media spend now exceeds the revenue
    return to_xlsx({"data": frame})


def test_media_spend_that_rivals_sales_starts_with_the_contribution_prior(fixture_bytes: bytes) -> None:
    data = _spend_heavy_xlsx()
    _, prof = profile_workbook(data, filename="mmm.xlsx")
    assert prof.prior_media_over_100_prob is not None and prof.prior_media_over_100_prob >= PRIOR_OVER_100_LIMIT
    assert prof.role_config.model_settings.roi_prior == "total_media_contribution" == prof.default_roi_prior
    assert any("starts with the total-media-contribution ROI prior" in w for w in prof.warnings)

    # A chosen prior is never replaced: the warning only points at the risk
    chosen = DataRoleConfig(columns=prof.role_config.columns, model_settings=ModelSettings(roi_prior="meridian"))
    _, kept = profile_workbook(data, filename="mmm.xlsx", role_config=chosen)
    assert kept.role_config.model_settings.roi_prior == "meridian"
    assert any("Consider the total-media-contribution ROI prior" in w for w in kept.warnings)

    # The sample workbook (media ≈ 20 % of sales) keeps the Meridian default
    _, sample = profile_workbook(fixture_bytes, filename="sample_data.xlsx")
    assert sample.role_config.model_settings.roi_prior == "meridian" == sample.default_roi_prior
    assert sample.prior_media_over_100_prob is not None and sample.prior_media_over_100_prob < PRIOR_OVER_100_LIMIT
    assert default_prior_over_100([], 1.0) is None


def test_the_pre_modelling_checks_describe_this_data() -> None:
    raw, prof = profile_workbook(to_xlsx({"data": wide_frame(400)}), filename="mmm.xlsx")
    wide = build_daily_wide_table(raw, config=prof.role_config, drop_2025=False)
    rows = compute_meridian_pre_modeling_checks(wide, prof.role_config)["checks"]
    guidance = " ".join(r["guidance"] for r in rows)
    assert "Catalog" not in guidance and "Q4" not in guidance
