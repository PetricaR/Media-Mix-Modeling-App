"""Leak-free validation (`ModelSettings.leak_free_validation`, `core/as_of.py`): nothing of a scored window, and nothing
after it, may reach the forecast it is scored on.

Each test perturbs what a planner could not have known the day before a window (its sales, the realised cost of channels
that follow the outcome, the user's controls, the weather, missing-row flags, every later day) and checks that the
model's input or forecast does not move. The conventional (leaky) hold-outs stay reachable with the setting off.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from mmm_studio.core.as_of import as_of_panel, leak_free, outcome_driven_slugs, scoring_cutoff, window_plan
from mmm_studio.core.features import holdout_windows
from mmm_studio.core.schema import DataProfile, DataRoleConfig


def _cfg(profiled_sample: tuple[pd.DataFrame, DataProfile], leak_free_validation: bool = True) -> DataRoleConfig:
    cfg = copy.deepcopy(profiled_sample[1].role_config)
    cfg.model_settings.leak_free_validation = leak_free_validation
    return cfg


def _perturb_window(wide: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """Everything a planner could not know on the day before `start`, changed from `start` on."""
    out = wide.copy()
    later = out.index >= start
    out.loc[later, "total_sales"] *= 1.7
    for col in [c for c in out.columns if c.startswith(("sales_", "clicks_", "impressions_"))]:
        out.loc[later, col] *= 1.7
    for slug in ("affiliate", "sea", "psm"):  # commission and demand-following costs move with the outcome
        out.loc[later, f"cost_{slug}"] *= 2.5
    for col in [c for c in out.columns if c.startswith("missing_")]:
        out.loc[later, col] = 1.0
    out.loc[later, "ctrl_promo"] = 3.0
    out.loc[later, "wx_temp_anomaly"] = 9.0
    return out


@pytest.fixture()
def wide_with_controls(wide_model: pd.DataFrame) -> pd.DataFrame:
    out = wide_model.copy()
    out["ctrl_promo"] = 0.0
    out["wx_temp_anomaly"] = 0.5
    return out


def test_leak_free_is_the_default_and_names_the_outcome_driven_costs(
    profiled_sample: tuple[pd.DataFrame, DataProfile], wide_model: pd.DataFrame
) -> None:
    cfg = _cfg(profiled_sample)
    assert leak_free(cfg) and leak_free(None)
    assert not leak_free(_cfg(profiled_sample, leak_free_validation=False))
    # The commission channel and the click-billed channels whose spend tracks demand
    assert set(outcome_driven_slugs(cfg, wide_model)) == {"affiliate", "sea", "psm"}
    assert scoring_cutoff(pd.DatetimeIndex(wide_model.index), 93) == pd.Timestamp("2024-09-30")


def test_as_of_panel_holds_nothing_from_the_window_or_later(
    profiled_sample: tuple[pd.DataFrame, DataProfile], wide_with_controls: pd.DataFrame
) -> None:
    cfg = _cfg(profiled_sample)
    wide = wide_with_controls
    window = holdout_windows(pd.DatetimeIndex(wide.index), 93)["primary"]
    tr_end, start, end = window
    plan_cols = ["sea", "catalog", "display", "affiliate", "display_network", "social_media", "psm"]
    outcome = outcome_driven_slugs(cfg, wide)

    clean = as_of_panel(wide, window, plan_cols, outcome)
    perturbed = as_of_panel(_perturb_window(wide, start), window, plan_cols, outcome)
    # The planner-set costs of the window are the plan; perturb only what the planner does not set
    pd.testing.assert_frame_equal(clean, perturbed)
    assert clean.index[-1] == end and clean.index[0] == wide.index[0]

    future = clean.loc[start:end]
    assert (future["total_sales"] == wide.loc[:tr_end, "total_sales"].mean()).all()  # unknown: the training mean
    assert (future["ctrl_promo"] == wide.loc[tr_end, "ctrl_promo"]).all()  # last known value
    assert (future["wx_temp_anomaly"] == 0).all()  # normal weather
    assert (future[[c for c in future.columns if c.startswith("missing_")]] == 0).all().all()
    # Planner-set spend is the window's actual spend; outcome-driven spend is planned from history
    assert np.allclose(future["cost_display"], wide.loc[start:end, "cost_display"])
    assert not np.allclose(future["cost_affiliate"], wide.loc[start:end, "cost_affiliate"])
    _, plan = window_plan(wide, window, plan_cols, outcome)
    assert np.allclose(future["cost_sea"], plan["sea"]) and (plan["sea"] > 0).all()


def test_ols_benchmark_forecasts_do_not_see_the_window(
    profiled_sample: tuple[pd.DataFrame, DataProfile], wide_model: pd.DataFrame, reference_config: Any
) -> None:
    from mmm_studio.core.ols import OLS1, OLS2, evaluate_ols_suite

    cfg = _cfg(profiled_sample)
    start = holdout_windows(pd.DatetimeIndex(wide_model.index), cfg.holdout_days)["primary"][1]
    clean = evaluate_ols_suite(wide_model, config=cfg)
    perturbed = evaluate_ols_suite(_perturb_window(wide_model, start), config=cfg)
    for model in (OLS1, OLS2):
        pd.testing.assert_series_equal(clean["predictions"]["primary"][model], perturbed["predictions"]["primary"][model])
    assert clean["leak_free"] and set(clean["estimated_costs"]) == {"affiliate", "sea", "psm"}

    # The conventional benchmark reads the window's realised commission and click costs: its forecast moves with them
    ref = reference_config(profiled_sample[1].role_config)
    ref_clean = evaluate_ols_suite(wide_model, config=ref)["predictions"]["primary"][OLS2]
    ref_perturbed = evaluate_ols_suite(_perturb_window(wide_model, start), config=ref)["predictions"]["primary"][OLS2]
    assert not np.allclose(ref_clean, ref_perturbed)


def test_walk_forward_folds_do_not_see_their_window(
    profiled_sample: tuple[pd.DataFrame, DataProfile], wide_model: pd.DataFrame
) -> None:
    from mmm_studio.core.ols import evaluate_ols_suite

    cfg = _cfg(profiled_sample)
    clean = evaluate_ols_suite(wide_model, config=cfg)
    last_fold = pd.Timestamp(clean["cv"]["folds"][-1])
    perturbed = evaluate_ols_suite(_perturb_window(wide_model, last_fold), config=cfg)
    # Every fold before the last is untouched; the last one changes only through its actual sales (the score)
    for name, scores in clean["cv"]["scores"].items():
        assert scores[:-1] == pytest.approx(perturbed["cv"]["scores"][name][:-1])
    fold_end = last_fold + pd.Timedelta(days=41)
    actual = wide_model.loc[last_fold:fold_end, "total_sales"].sum()
    clean_err, pert_err = clean["cv"]["level_errors"][-1]["error"], perturbed["cv"]["level_errors"][-1]["error"]
    forecast_total = actual / (1 + clean_err)
    assert pert_err == pytest.approx((1.7 * actual - forecast_total) / forecast_total, rel=1e-6)


def test_preparation_choices_use_only_days_before_the_test(
    profiled_sample: tuple[pd.DataFrame, DataProfile], wide_model: pd.DataFrame
) -> None:
    from mmm_studio.core.preparation import build_preparation_section

    cfg = _cfg(profiled_sample)
    start = holdout_windows(pd.DatetimeIndex(wide_model.index), cfg.holdout_days)["primary"][1]

    def figures(wide: pd.DataFrame) -> dict[str, Any]:
        sec = build_preparation_section(wide, cfg)
        ids = ("prep_shift_scan", "prep_r2_ladder", "prep_channel_importance", "prep_catalog_adstock_shift")
        return {b.id: b.figure["data"] for b in sec.blocks if getattr(b, "id", None) in ids}

    clean = figures(wide_model)
    assert len(clean) == 4
    assert clean == figures(_perturb_window(wide_model, start))


def test_naive_baselines_forecast_each_day_from_its_past() -> None:
    from mmm_studio.core.metrics import causal_naive_baselines, in_sample_naive_baselines

    idx = pd.date_range("2024-01-01", periods=70, freq="D")
    y = pd.Series(100.0, index=idx)
    y.iloc[-1] = 800.0  # a spike only the in-sample mean can anticipate
    causal = causal_naive_baselines(y).set_index("model")["MAE"]
    in_sample = in_sample_naive_baselines(y).set_index("model")["MAE"]
    # Causal: every day but the last is forecast exactly; the last is missed by 700
    assert causal["Naive: constant mean"] == pytest.approx(700 / 69)
    assert in_sample["Naive: constant mean"] != pytest.approx(causal["Naive: constant mean"])


def test_weather_normal_is_fitted_before_the_test(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mmm_studio.core import weather as wx

    idx = pd.date_range("2022-01-01", periods=800, freq="D")
    cutoff = idx[600]

    def fetch(shift_after: float) -> Any:
        def _fake(url: str, country: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
            days = pd.date_range(start, end, freq="D")
            doy = days.dayofyear.to_numpy()
            temp = 10 - 9 * np.cos(2 * np.pi * doy / 365.25) + np.where(days > cutoff, shift_after, 0.0)
            return pd.concat(
                [
                    pd.DataFrame({"date": days, "place": name, "temperature_2m_mean": temp, "sunshine_duration": 3600.0 * 5,
                                  "precipitation_sum": 1.0})
                    for name, *_ in wx.LOCATIONS["AT"]
                ],
                ignore_index=True,
            )

        return _fake

    out = {}
    for shift in (0.0, 6.0):
        monkeypatch.setenv("MMM_EXTERNAL_CACHE_DIR", str(tmp_path / f"s{shift}"))
        monkeypatch.setattr(wx, "_fetch", fetch(shift))
        out[shift] = wx.weather_controls("AT", idx, normal_until=cutoff)
    # A warmer test period changes nothing the model trains on
    pd.testing.assert_frame_equal(out[0.0].loc[:cutoff], out[6.0].loc[:cutoff])


def test_selection_grid_fits_never_receive_the_validation_window(
    profiled_sample: tuple[pd.DataFrame, DataProfile],
    wide_with_controls: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mmm_studio.core.meridian import selection
    from mmm_studio.core.meridian.spec import MeridianSpecConfig

    cfg = _cfg(profiled_sample)
    wide_model = wide_with_controls
    spec = MeridianSpecConfig.from_role_config(cfg, wide=wide_model)
    window = holdout_windows(pd.DatetimeIndex(wide_model.index), cfg.holdout_days)["forward"]
    seen: list[pd.DataFrame] = []

    class _Stop(Exception):
        pass

    def capture(data: pd.DataFrame, *args: Any, **kwargs: Any) -> Any:
        seen.append(data)
        raise _Stop

    monkeypatch.setattr(selection, "fit_meridian_isolated", capture)
    for wide in (wide_model, _perturb_window(wide_model, window[1])):
        with pytest.raises(_Stop):
            selection.run_model_selection(wide, spec, tmp_path, sampling={}, validation_window=window,
                                          validation_label="v", shifts=(2,), leak_free=True)
    pd.testing.assert_frame_equal(seen[0], seen[1])
    assert seen[0].index[-1] == window[2]  # nothing after the validation window either


def test_final_model_of_a_leak_free_run_has_no_hold_out(
    profiled_sample: tuple[pd.DataFrame, DataProfile], wide_model: pd.DataFrame
) -> None:
    from mmm_studio.core.meridian.spec import MeridianSpecConfig

    cfg = _cfg(profiled_sample)
    cfg.model_settings.roi_prior = "attributed_roas"
    spec = MeridianSpecConfig.from_role_config(cfg, wide=wide_model)
    final = spec.for_all_days(wide_model)
    assert spec.holdout == ("2024-10-01", "2025-01-01") and final.holdout is None
    # Priors of the final model may use every day; those of the scored spec only the training days
    assert final.roi_prior != spec.roi_prior
    assert set(spec.outcome_driven_slugs) == {"affiliate", "sea", "psm"}
