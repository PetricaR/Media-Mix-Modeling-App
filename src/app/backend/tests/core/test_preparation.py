"""Tests for the data-preparation analyses of notebook 02."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from mmm_studio.core.preparation import (
    build_preparation_section,
    compute_catalog_shift_scan,
    compute_channel_importance,
    compute_digital_vif,
    compute_preparation_ols_ladder,
)
from mmm_studio.core.schema import DataProfile


def test_preparation_matches_notebook_02(
    profiled_case_study: tuple[pd.DataFrame, DataProfile],
    wide_model: pd.DataFrame,
    golden_data: dict[str, Any],
) -> None:
    _, profile = profiled_case_study
    cfg = profile.role_config
    ladder = compute_preparation_ols_ladder(wide_model, cfg)
    gold = golden_data["ols_in_sample_adj_r2_ladder"]
    assert [round(r["adj_r2"], 2) for r in ladder] == [
        gold["raw_costs"],
        gold["plus_calendar"],
        gold["plus_seasonality_trend"],
        gold["catalog_in_home_adstock"],
    ]

    scan = compute_catalog_shift_scan(wide_model, cfg)
    best_k = int(scan.loc[scan["corr"].idxmax(), "k"])
    assert best_k in golden_data["preparation"]["best_catalog_shift_k"]

    _, r2 = compute_channel_importance(wide_model, cfg)
    assert r2 == pytest.approx(golden_data["preparation"]["importance_ols_r2"], abs=0.001)
    vif = compute_digital_vif(wide_model, cfg)
    assert max(vif.values()) == pytest.approx(golden_data["preparation"]["max_digital_vif"], abs=0.01)

    sec = build_preparation_section(wide_model, cfg)
    assert sec.slug == "preparation"
    assert sum(1 for b in sec.blocks if b.type == "chart") >= 6
