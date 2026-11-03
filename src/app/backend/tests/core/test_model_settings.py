"""Run settings: the defaults reproduce notebook 03's specification, and every changed setting reaches Meridian."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from mmm_studio.core.meridian.spec import MeridianSpecConfig, build_meridian_table, meridian_table
from mmm_studio.core.model_settings import (
    FAST_SAMPLING,
    FULL_SAMPLING,
    SMOKE_SAMPLING,
    ModelSettings,
    sampling_for,
    settings_changes,
    settings_defaults,
)
from mmm_studio.core.schema import DataProfile, DataRoleConfig


def _custom_config(profile: DataProfile) -> DataRoleConfig:
    rc = profile.role_config.model_copy(deep=True)
    rc.holdout_days = 60
    rc.model_settings = ModelSettings.model_validate(
        {
            "calendar": {"weekdays": False},
            "n_fourier": 1,
            "trend": False,
            "max_lag": 14,
            "baseline": "knots",
            "n_knots": 6,
            "hill_before_adstock": True,
            "roi_prior": "custom",
            "custom_roi_priors": {"sea": {"median": 2.0, "sigma": 0.4}},
            "forward_holdout": False,
            "model_selection": True,
            "selection_shifts": [2, 4],
        }
    )
    for ch in rc.channels:
        if ch.slug == "catalog":
            ch.in_home_shift_days = 3
            ch.adstock_shape = "geometric"
        if ch.slug == "sea":
            ch.saturation = "none"
        if ch.slug == "psm":
            ch.role = "unpaid"
    return rc


def test_defaults_reproduce_the_notebook_specification(
    profiled_case_study: tuple[pd.DataFrame, DataProfile], wide_model: pd.DataFrame
) -> None:
    _, profile = profiled_case_study
    cfg = MeridianSpecConfig.from_role_config(profile.role_config, wide=wide_model)
    assert cfg.catalog_shift == 2
    assert cfg.as_controls == ("affiliate",)
    assert cfg.max_lag == 21
    assert cfg.adstock_decay == {"catalog": "binomial"}
    assert cfg.holdout == ("2024-10-01", "2025-01-01")
    assert cfg.n_fourier == 2 and cfg.trend
    assert cfg.roi_prior is None and cfg.media_prior_type == "roi"
    assert cfg.knots is None and not cfg.enable_aks and not cfg.hill_before_adstock
    assert cfg.saturation == {} and cfg.print_slugs == ("catalog",)

    # The settings-driven table is the table the notebooks (and earlier app versions) built
    legacy = build_meridian_table(wide_model, catalog_shift=2, n_fourier=2, paid_channels=cfg.paid_channels)
    pd.testing.assert_frame_equal(meridian_table(wide_model, cfg), legacy)
    assert settings_changes(profile.role_config) == []
    assert sampling_for("full") == FULL_SAMPLING
    assert sampling_for("quick") == FAST_SAMPLING and sampling_for("full", "selection") == FAST_SAMPLING


def test_changed_settings_reach_the_meridian_inputs(
    profiled_case_study: tuple[pd.DataFrame, DataProfile], wide_model: pd.DataFrame
) -> None:
    _, profile = profiled_case_study
    rc = _custom_config(profile)
    cfg = MeridianSpecConfig.from_role_config(rc, wide=wide_model)
    assert cfg.catalog_shift == 3
    assert cfg.adstock_decay == {}
    assert cfg.saturation == {"sea": "none"}
    assert cfg.max_lag == 14 and cfg.knots == 6 and cfg.hill_before_adstock
    assert cfg.roi_prior == {"sea": (pytest.approx(math.log(2.0)), 0.4)}
    assert "psm" not in cfg.paid_slugs
    assert cfg.holdout == ("2024-11-03", "2025-01-01")

    table = meridian_table(wide_model, cfg)
    assert not any(c.startswith("dow_") for c in table.columns)
    assert {"holiday", "christmas", "black_week", "sin_1", "cos_1"} <= set(table.columns)
    assert "sin_2" not in table.columns and "trend" not in table.columns
    shifted = wide_model["cost_catalog"].shift(3, fill_value=0.0).to_numpy()
    np.testing.assert_allclose(table["catalog_spend"].to_numpy(), shifted)


def test_roas_prior_preset_is_centred_on_attributed_roas(
    profiled_case_study: tuple[pd.DataFrame, DataProfile], wide_model: pd.DataFrame
) -> None:
    _, profile = profiled_case_study
    rc = profile.role_config.model_copy(deep=True)
    rc.model_settings = ModelSettings(roi_prior="attributed_roas", roas_prior_sigma=0.7)
    cfg = MeridianSpecConfig.from_role_config(rc, wide=wide_model)
    assert cfg.roi_prior is not None and "affiliate" not in cfg.roi_prior
    roas = wide_model["sales_sea"].sum() / wide_model["cost_sea"].sum()
    assert cfg.roi_prior["sea"] == (pytest.approx(math.log(roas)), 0.7)
    # Other prior types have no ROI prior to set
    rc.model_settings = ModelSettings(roi_prior="attributed_roas", media_prior_type="mroi")
    assert MeridianSpecConfig.from_role_config(rc, wide=wide_model).roi_prior is None


def test_custom_sampling_and_its_limits() -> None:
    ms = ModelSettings.model_validate({"sampling": {"n_chains": 3, "n_adapt": 400, "n_burnin": 100, "n_keep": 1500, "n_prior": 100}})
    assert sampling_for("custom", "main", ms) == {"n_chains": 3, "n_adapt": 400, "n_burnin": 100, "n_keep": 1500, "n_prior": 100}
    # The selection and forward fits never take more than the FAST draws
    assert sampling_for("custom", "forward", ms) == {"n_chains": 2, "n_adapt": 300, "n_burnin": 100, "n_keep": 200, "n_prior": 100}
    assert sampling_for("smoke", "main", ms) == SMOKE_SAMPLING
    for bad in ({"sampling": {"n_chains": 8}}, {"sampling": {"n_keep": 5000}}, {"max_lag": 0}, {"selection_shifts": [9]}, {"selection_shifts": []}):
        with pytest.raises(ValidationError):
            ModelSettings.model_validate(bad)
    with pytest.raises(ValidationError):
        DataRoleConfig(holdout_days=5)


def test_settings_changes_lists_every_difference(profiled_case_study: tuple[pd.DataFrame, DataProfile]) -> None:
    _, profile = profiled_case_study
    rc = _custom_config(profile)
    changes = {r["setting"]: (r["value"], r["default"]) for r in settings_changes(rc, seed=7, profile="quick")}
    assert changes["Hold-out length"] == ("60 days", "93 days")
    assert changes["Random seed"] == ("7", "0")
    assert changes["Calendar controls"] == ("Holidays, Christmas, Black Week", "Weekdays, holidays, Christmas, Black Week")
    assert changes["Role · PSM"] == ("Unpaid / organic", "Paid media")
    assert changes["Adstock · Catalog"] == ("Geometric", "Binomial")
    assert changes["In-home shift · Catalog"] == ("3 days", "2 days")
    assert changes["Saturation · SEA"] == ("None", "Hill")
    assert changes["Baseline"] == ("Time-varying, 6 knots", "Constant (1 knot)")
    assert changes["ROI prior · SEA"] == ("median 2.00, σ 0.4", "median 1.22, σ 0.9")
    assert changes["Model-selection grid"] == ("On", "Off")
    assert changes["Selection catalog shifts"] == ("2, 4", "1, 2, 3")
    assert "Sampling draws" not in changes


def test_settings_defaults_payload() -> None:
    d = settings_defaults()
    assert d["model_settings"] == ModelSettings().model_dump(mode="json")
    assert d["limits"]["sampling.n_chains"] == {"min": 1, "max": 4}
    assert d["limits"]["holdout_days"] == {"min": 14, "max": 365}
    assert d["meridian_roi_prior"]["median"] == pytest.approx(math.exp(0.2))
    assert d["profiles"]["full"] == FULL_SAMPLING


@pytest.mark.slow  # builds (does not fit) a Meridian model, which imports TensorFlow
def test_changed_settings_reach_meridians_model_spec(
    profiled_case_study: tuple[pd.DataFrame, DataProfile], wide_model: pd.DataFrame
) -> None:
    from mmm_studio.core.meridian.spec import build_input_data, build_meridian_model

    _, profile = profiled_case_study
    rc = _custom_config(profile)
    cfg = MeridianSpecConfig.from_role_config(rc, wide=wide_model)
    mmm = build_meridian_model(build_input_data(meridian_table(wide_model, cfg), cfg), cfg)
    spec = mmm.model_spec
    assert spec.max_lag == 14 and spec.knots == 6 and spec.hill_before_adstock
    assert spec.saturation_spec == {"sea": "none"}
    assert spec.adstock_decay_spec == "geometric"
    channels = list(mmm.input_data.media_channel.values)
    loc = np.asarray(spec.prior.roi_m.loc)
    scale = np.asarray(spec.prior.roi_m.scale)
    assert loc[channels.index("sea")] == pytest.approx(math.log(2.0))
    assert scale[channels.index("sea")] == pytest.approx(0.4)
    assert loc[channels.index("display")] == pytest.approx(0.2) and scale[channels.index("display")] == pytest.approx(0.9)

    rc.model_settings = ModelSettings(media_prior_type="contribution", baseline="constant")
    cfg = MeridianSpecConfig.from_role_config(rc, wide=wide_model)
    spec = build_meridian_model(build_input_data(meridian_table(wide_model, cfg), cfg), cfg).model_spec
    assert spec.effective_media_prior_type == "contribution" and spec.knots is None
