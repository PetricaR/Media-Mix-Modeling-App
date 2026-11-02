"""Tests for core preparation module and OLS R2 ladder."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from mmm_studio.core.calendar import add_calendar_and_adstock_features
from mmm_studio.core.preparation import build_preparation_section, compute_preparation_ols_ladder
from mmm_studio.core.schema import DataProfile


def test_preparation_ols_ladder_and_section(
    profiled_case_study: tuple[pd.DataFrame, DataProfile],
    wide_2023_2024: pd.DataFrame,
    golden_data: dict[str, Any],
) -> None:
    _, profile = profiled_case_study
    wide_feat = add_calendar_and_adstock_features(wide_2023_2024, config=profile.role_config)

    ladder = compute_preparation_ols_ladder(wide_feat)
    assert len(ladder) == 4
    r2_vals = [float(r["adj_r2"]) for r in ladder]
    assert r2_vals[0] == pytest.approx(golden_data["ols_in_sample_adj_r2_ladder"]["raw_costs"], abs=0.03)
    assert r2_vals[3] == pytest.approx(
        golden_data["ols_in_sample_adj_r2_ladder"]["catalog_in_home_adstock"], abs=0.03
    )
    assert r2_vals[3] > r2_vals[0]

    sec = build_preparation_section(wide_2023_2024, profile.role_config)
    assert sec.slug == "preparation"
    assert len(sec.blocks) >= 8
