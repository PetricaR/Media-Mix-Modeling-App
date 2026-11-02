"""Tests for core OLS benchmark suite."""

from __future__ import annotations

import pandas as pd

from mmm_studio.core.calendar import add_calendar_and_adstock_features
from mmm_studio.core.ols import build_benchmark_section, evaluate_ols_suite
from mmm_studio.core.schema import DataProfile


def test_ols_benchmark_suite_and_section(
    profiled_case_study: tuple[pd.DataFrame, DataProfile],
    wide_2023_2024: pd.DataFrame,
) -> None:
    _, profile = profiled_case_study
    wide_feat = add_calendar_and_adstock_features(wide_2023_2024, config=profile.role_config)

    ols_results = evaluate_ols_suite(wide_feat)
    spec_rows = ols_results["spec_rows"]
    assert len(spec_rows) == 5
    best_ols_mae = min(float(r["q4_mae_over_mean"]) for r in spec_rows)
    assert best_ols_mae < 0.12

    sec = build_benchmark_section(wide_2023_2024, meridian_summary=None)
    assert sec.slug == "benchmark"
    assert len(sec.blocks) >= 7
