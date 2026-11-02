"""Unit and golden tests for wide table construction and calendar features."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from mmm_studio.core.calendar import add_calendar_and_adstock_features
from mmm_studio.core.metrics import in_sample_naive_baselines, naive_baselines


def test_wide_table_dimensions_and_target(
    wide_full: pd.DataFrame,
    wide_2023_2024: pd.DataFrame,
    golden_data: dict[str, Any],
) -> None:
    assert len(wide_full) == 732
    assert len(wide_2023_2024) == 731
    assert wide_2023_2024.index.min() == pd.Timestamp("2023-01-01")
    assert wide_2023_2024.index.max() == pd.Timestamp("2024-12-31")

    assert wide_full["total_sales"].mean() == pytest.approx(
        golden_data["target_summary"]["mean_daily_sales"], rel=1e-3
    )
    assert wide_full["total_sales"].min() == pytest.approx(
        golden_data["target_summary"]["min_daily_sales"], rel=1e-3
    )
    assert wide_full["total_sales"].max() == pytest.approx(
        golden_data["target_summary"]["max_daily_sales"], rel=1e-3
    )

    assert int(wide_full["missing_tv"].sum()) == 38
    assert int(wide_full["missing_catalog"].sum()) == 4
    assert int(wide_full["missing_owned"].sum()) == 2
    assert int(wide_full["catalog_sendout"].sum()) == 28


def test_calendar_and_naive_baselines(
    wide_full: pd.DataFrame,
    golden_data: dict[str, Any],
) -> None:
    panel = add_calendar_and_adstock_features(wide_full)
    assert "cost_catalog_adstock" in panel.columns
    assert "is_christmas_window" in panel.columns
    assert "is_black_week" in panel.columns
    assert "sin_1" in panel.columns

    # In-sample naive baselines (01b_eda_mmm_deep_dive)
    nb_in = in_sample_naive_baselines(wide_full["total_sales"])
    by_model = nb_in.set_index("model")["MAE_over_mean"].to_dict()
    assert by_model["Naive: constant mean"] == pytest.approx(
        golden_data["naive_baselines_mae_over_mean"]["constant_mean"], abs=0.003
    )
    assert by_model["Naive: weekday mean"] == pytest.approx(
        golden_data["naive_baselines_mae_over_mean"]["weekday_mean"], abs=0.003
    )
    assert by_model["Naive: lag-7 (last week)"] == pytest.approx(
        golden_data["naive_baselines_mae_over_mean"]["lag_7"], abs=0.003
    )
    assert by_model["Naive: lag-1 (yesterday, 1-step)"] == pytest.approx(
        golden_data["naive_baselines_mae_over_mean"]["lag_1"], abs=0.003
    )

    # Holdout naive baselines (04_benchmark_ols Q4 2024)
    w = wide_full.loc["2023-01-01":"2024-12-31"]
    train = w.loc[:"2024-09-30", "total_sales"]
    test = w.loc["2024-10-01":"2024-12-31", "total_sales"]
    nb_hold = naive_baselines(train, test, pd.DatetimeIndex(train.index), pd.DatetimeIndex(test.index))
    hold_map = nb_hold.set_index("model")["MAE_over_mean"].to_dict()
    assert hold_map["Naive: constant mean"] == pytest.approx(0.350, abs=0.003)
    assert hold_map["Naive: lag-7 (last week)"] == pytest.approx(0.272, abs=0.003)
