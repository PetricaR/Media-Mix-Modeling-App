"""Tests for the OLS benchmark (notebook 04): same hold-outs as Meridian and walk-forward CV."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from mmm_studio.core.ols import OLS2, build_benchmark_section, evaluate_ols_suite
from mmm_studio.core.schema import DataProfile


def test_ols_benchmark_matches_notebook_04(
    profiled_case_study: tuple[pd.DataFrame, DataProfile],
    wide_model: pd.DataFrame,
    golden_data: dict[str, Any],
    notebook_parity: Any,
) -> None:
    _, profile = profiled_case_study
    res = evaluate_ols_suite(wide_model, config=notebook_parity(profile.role_config))
    assert res["windows"]["primary"] == {
        "label": "Q4 2024",
        "train_end": "2024-09-30",
        "test_start": "2024-10-01",
        "test_end": "2025-01-01",
    }
    assert res["windows"]["forward"]["label"] == "Q3 2024"

    gold = golden_data["ols_benchmark_mae_over_mean"]
    by = {(r["window"], r["model"]): r["MAE_over_mean"] for r in res["rows"]}
    for model, ref in gold["q4_2024"].items():
        assert by[("primary", model)] == pytest.approx(ref, abs=0.001)
    for model, ref in gold["q3_2024"].items():
        assert by[("forward", model)] == pytest.approx(ref, abs=0.001)
    assert len(res["cv"]["folds"]) == 8
    for model, ref in gold["walk_forward_mean"].items():
        assert res["cv"]["mean"][model] == pytest.approx(ref, abs=0.001)

    sec = build_benchmark_section(wide_model, meridian_summary=None, config=profile.role_config, ols_res=res)
    assert sec.slug == "benchmark"

    # The app's default adds missing-row controls to OLS-2 too, so the comparison with Meridian stays like-for-like
    default_res = evaluate_ols_suite(wide_model, config=profile.role_config)
    default_by = {(r["window"], r["model"]): r["MAE_over_mean"] for r in default_res["rows"]}
    assert default_by[("primary", OLS2)] != pytest.approx(by[("primary", OLS2)], abs=1e-9)
    kpis = sec.blocks[0].items
    assert kpis[0].value == "pending"  # no Meridian numbers are invented before the fit
    assert any(r.get("model") == OLS2 for b in sec.blocks if b.type == "table" for r in b.rows)
