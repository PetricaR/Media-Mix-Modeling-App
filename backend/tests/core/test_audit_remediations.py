"""Regression tests for the audit findings (BUG_REPORT.md): each test states the expected behaviour independently of
the implementation, so a regression fails here even if the code still "runs"."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import numpy as np
import pandas as pd
import pytest
from long_data import long_frame

from mmm_studio.core.meridian.forecast import (
    PlanValidationError,
    plan_time_controls,
    total_interval,
    validate_plan,
)
from mmm_studio.core.meridian.optimise import (
    OptimisationError,
    OptimisationInfeasible,
    feasible_budget_range,
    lift_share,
    solve_constrained_optimisation,
)
from mmm_studio.core.meridian.spec import MeridianSpecConfig, holdout_range, meridian_table
from mmm_studio.core.model_settings import ModelSettings, RoiPrior, run_config_errors
from mmm_studio.core.ols import build_benchmark_section, evaluate_ols_suite
from mmm_studio.core.schema import DataProfile, default_role_config, infer_channel_configs
from mmm_studio.core.workbook import WorkbookValidationError, profile_workbook, validate_upload_bytes
from mmm_studio.errors import public_error
from mmm_studio.report.facts import detect_level_shift
from mmm_studio.report.narrative import fmt_eur


def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()


@pytest.fixture(scope="module")
def raw() -> pd.DataFrame:
    return long_frame()


def _curves() -> dict[str, pd.DataFrame]:
    """Hill-type response curves over 0–200 % of spend, as Meridian's `response_curves` returns them."""
    m = np.round(np.arange(0.0, 2.01, 0.05), 2)
    specs = {"sea": (10e6, 30e6, 8e6), "catalog": (20e6, 25e6, 22e6), "display": (5e6, 4e6, 6e6)}
    out = {}
    for ch, (s0, top, k) in specs.items():
        spend = m * s0
        out[ch] = pd.DataFrame({"multiplier": m, "spend": spend, "mean": top * spend / (spend + k)})
    return out


def _roi(curves: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame({ch: {"spend": float(c.loc[c.multiplier == 1.0, "spend"].iloc[0])} for ch, c in curves.items()}).T


# --- Data validation (DATA-01, DATA-04, DATA-05, SEC-03) -----------------------------------------------------------


def test_conflicting_duplicates_are_rejected_and_exact_duplicates_removed(raw: pd.DataFrame) -> None:
    exact = pd.concat([raw, raw.iloc[:20]], ignore_index=True)
    df, prof = profile_workbook(_xlsx(exact), filename="x.xlsx")
    _, base = profile_workbook(_xlsx(raw), filename="x.xlsx")
    assert prof.duplicate_rows_count == 20 and any("duplicate" in w for w in prof.warnings)
    assert prof.total_sales_sum == pytest.approx(base.total_sales_sum)  # nothing double counted

    conflicting = raw.copy()
    extra = conflicting.iloc[[0]].copy()
    extra["costs"] = extra["costs"].fillna(0) + 100.0
    with pytest.raises(WorkbookValidationError, match="share a date and channel"):
        profile_workbook(_xlsx(pd.concat([conflicting, extra], ignore_index=True)), filename="x.xlsx")


def test_negative_costs_are_rejected_and_negative_sales_warned(raw: pd.DataFrame) -> None:
    neg_cost = raw.copy()
    neg_cost.loc[neg_cost.index[neg_cost["costs"].fillna(0) > 0][:2], "costs"] *= -1
    with pytest.raises(WorkbookValidationError, match="negative cost"):
        profile_workbook(_xlsx(neg_cost), filename="x.xlsx")

    neg_sales = raw.copy()
    neg_sales.loc[neg_sales.index[:3], "sales_by_channel"] = -5.0
    _, prof = profile_workbook(_xlsx(neg_sales), filename="x.xlsx")
    assert prof.negative_values_count == 3 and any("negative sales" in w for w in prof.warnings)


def test_too_short_history_is_rejected(raw: pd.DataFrame) -> None:
    short = raw[pd.to_datetime(raw["date"]) < pd.Timestamp("2023-09-01")]
    with pytest.raises(WorkbookValidationError, match="at least"):
        profile_workbook(_xlsx(short), filename="x.xlsx")


def test_zip_bombs_are_checked_on_the_actual_bytes() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(300):
            zf.writestr(f"f{i}.xml", "x")
    with pytest.raises(WorkbookValidationError, match="too many entries"):
        validate_upload_bytes("bomb.xlsx", buf.getvalue())


def test_role_inference_does_not_turn_sparse_digital_spend_into_a_print_event() -> None:
    dates = pd.date_range("2023-01-01", periods=400, freq="D")
    rows = [{"date": d, "channel": "Search", "sales_by_channel": 10.0, "costs_by_channel": 5.0, "clicks": 1.0} for d in dates]
    rows += [{"date": d, "channel": "Launch Promo", "sales_by_channel": 1.0, "costs_by_channel": 100.0 if i > 360 else 0.0, "clicks": 0.0} for i, d in enumerate(dates)]
    rows += [{"date": d, "channel": "Katalog", "sales_by_channel": 1.0, "costs_by_channel": 500.0 if i % 30 == 0 else 0.0, "clicks": 0.0} for i, d in enumerate(dates)]
    from mmm_studio.core.schema import detect_column_mapping

    df = pd.DataFrame(rows)
    roles = {c.name: c for c in infer_channel_configs(df, detect_column_mapping(list(df.columns)))}
    assert roles["Launch Promo"].role == "paid" and "Print event" in (roles["Launch Promo"].rationale or "")
    assert roles["Katalog"].role == "print_event"


# --- Hold-out and run settings (STAT-02, MER-01, API-01 validation) ------------------------------------------------


def test_holdout_always_comes_from_the_data_and_never_falls_back() -> None:
    idx = pd.date_range("2022-01-01", periods=500, freq="D")
    assert holdout_range(idx, 93) == ((idx[-93]).strftime("%Y-%m-%d"), idx[-1].strftime("%Y-%m-%d"))
    with pytest.raises(ValueError, match="training days"):
        holdout_range(pd.date_range("2022-01-01", periods=390, freq="D"), 200)
    rc = default_role_config()
    with pytest.raises(ValueError, match="dates"):
        MeridianSpecConfig.from_role_config(rc)


def test_run_config_errors_explain_what_cannot_run() -> None:
    rc = default_role_config()
    assert run_config_errors(rc, 732) == []
    rc.holdout_days = 365
    assert any("training days" in e for e in run_config_errors(rc, 600))
    rc = default_role_config()
    rc.model_settings = ModelSettings(roi_prior="custom", custom_roi_priors={"affiliate": RoiPrior(median=2, sigma=0.5)})
    assert any("not media channels" in e for e in run_config_errors(rc, 732))


def test_attributed_roas_priors_use_training_days_only(wide_model: pd.DataFrame) -> None:
    rc = default_role_config()
    rc.model_settings = ModelSettings(roi_prior="attributed_roas", roas_prior_sigma=0.7)
    cfg = MeridianSpecConfig.from_role_config(rc, wide=wide_model)
    train = wide_model.loc[: pd.Timestamp(cfg.holdout[0]) - pd.Timedelta(days=1)]
    assert cfg.roi_prior["sea"][0] == pytest.approx(np.log(train["sales_sea"].sum() / train["cost_sea"].sum()))


# --- Narratives (STAT-01, TXT-01, TXT-02) --------------------------------------------------------------------------


def _ols_and_facts(profiled_sample: tuple[pd.DataFrame, DataProfile], wide_model: pd.DataFrame) -> tuple[Any, Any]:
    from mmm_studio.core.calendar import add_calendar_and_adstock_features
    from mmm_studio.report.facts import compute_case_facts

    _, prof = profiled_sample
    panel = add_calendar_and_adstock_features(wide_model, config=prof.role_config)
    return evaluate_ols_suite(wide_model, config=prof.role_config), compute_case_facts(panel, prof.role_config)


def test_executive_summary_follows_the_numbers(profiled_sample: Any, wide_model: pd.DataFrame) -> None:
    from mmm_studio.pipeline.stages import ChannelNarrative, build_overview_section

    ols_res, facts = _ols_and_facts(profiled_sample, wide_model)
    names = ChannelNarrative(upper=["SEA"], commission=["Affiliate"], print_name="Catalog")
    bad = {"MAE_over_mean": 0.22, "R2": 0.4, "MAE": 1, "RMSE": 1, "MAPE": 0.2, "bias": 0}
    sec = build_overview_section(wide_model, facts, ols_res, {"holdout_q4_2024": bad, "holdout_q3_2024": bad}, names=names)
    body = next(b for b in sec.blocks if getattr(b, "title", "") == "Executive summary").body
    assert "Not reliably" in body and "below the brief" not in body
    assert "OLS benchmark with the same inputs is more accurate" in body

    good = {**bad, "MAE_over_mean": 0.08, "R2": 0.9}
    sec = build_overview_section(
        wide_model, facts, ols_res, {"holdout_q4_2024": good, "holdout_q3_2024": good}, names=names,
        forecast_summary={"plan_time_mae_pct": 18.0},
    )
    body = next(b for b in sec.blocks if getattr(b, "title", "") == "Executive summary").body
    # Hold-outs meet the target, the back-test from a plan does not: the verdict says which
    assert body.startswith("**Can daily sales be forecast from the marketing budget?** Only with the realised Affiliate cost")
    assert "**18.0%**, outside it" in body


def test_draft_brief_has_no_invented_roi_and_computed_channel_importance(profiled_sample: Any, wide_model: pd.DataFrame) -> None:
    from mmm_studio.pipeline.stages import ChannelNarrative, build_brief_section

    ols_res, facts = _ols_and_facts(profiled_sample, wide_model)
    facts["important_channels"] = ["Affiliate", "SEA"]
    sec = build_brief_section(wide_model, facts, ols_res, names=ChannelNarrative(upper=["SEA"], commission=["Affiliate"], print_name="Catalog"))
    text = str([b.model_dump() for b in sec.blocks])
    assert "≈ 1" not in text and "pending (Meridian fit)" in text
    assert "Affiliate, SEA; the others" in text


def test_benchmark_takeaways_say_when_meridian_is_worse(profiled_sample: Any, wide_model: pd.DataFrame) -> None:
    _, prof = profiled_sample
    ols_res, _ = _ols_and_facts(profiled_sample, wide_model)
    worse = {"holdout_q4_2024": {"MAE_over_mean": 0.20, "R2": 0.5, "MAE": 1, "RMSE": 1, "MAPE": 0.2, "bias": 0}}
    sec = build_benchmark_section(wide_model, meridian_summary=worse, config=prof.role_config, ols_res=ols_res)
    text = str([b.model_dump() for b in sec.blocks])
    assert "points less accurate than the OLS benchmark" in text and "is close to" not in text


# --- Optimiser (STAT-03, STAT-05, STAT-09) -------------------------------------------------------------------------


def test_infeasible_budgets_raise_instead_of_relaxing_bounds() -> None:
    curves = _curves()
    roi = _roi(curves)
    hist = float(roi["spend"].sum())
    lo, hi = feasible_budget_range(roi, curves, default_bounds=(0.7, 1.3))
    assert (lo, hi) == pytest.approx((0.7 * hist, 1.3 * hist))
    with pytest.raises(OptimisationInfeasible):
        solve_constrained_optimisation(roi, curves, total_budget=0.5 * hist, default_bounds=(0.7, 1.3))


def test_fixed_catalog_stays_fixed_and_every_allocation_respects_its_bounds() -> None:
    curves = _curves()
    roi = _roi(curves)
    hist = float(roi["spend"].sum())
    res = solve_constrained_optimisation(roi, curves, total_budget=1.1 * hist, include_catalog=False, default_bounds=(0.7, 1.3))
    cat = next(a for a in res["allocations"] if a["channel"] == "catalog")
    assert cat["optimised_spend"] == pytest.approx(cat["current_spend"], abs=1.0)
    for a in res["allocations"]:
        assert a["min_bound"] - 1e-3 <= a["multiplier"] <= a["max_bound"] + 1e-3
    assert sum(a["optimised_spend"] for a in res["allocations"]) == pytest.approx(1.1 * hist, rel=1e-6)
    with pytest.raises(OptimisationInfeasible):  # the other channels cannot absorb 1.5× with catalog fixed
        solve_constrained_optimisation(roi, curves, total_budget=1.5 * hist, include_catalog=False, default_bounds=(0.7, 1.3))


def test_bounds_beyond_the_response_curves_are_refused() -> None:
    curves = _curves()
    with pytest.raises(OptimisationError, match="beyond its response curve"):
        solve_constrained_optimisation(_roi(curves), curves, default_bounds=(0.5, 3.0))


def test_lift_share_attributes_the_lift_to_channels() -> None:
    res = {"lift_eur": 100.0, "allocations": [{"channel": "sea", "inc_sales_delta": 107.0}, {"channel": "display", "inc_sales_delta": -7.0}]}
    assert lift_share(res, ["sea"]) == pytest.approx(1.07)


# --- Forecast inputs and intervals (DATA-02, STAT-04, STAT-12) -----------------------------------------------------


def test_plans_with_unknown_or_missing_channel_columns_are_rejected() -> None:
    dates = pd.date_range("2025-01-02", periods=7, freq="D")
    channels = ["sea", "catalog", "display", "affiliate"]
    ok = pd.DataFrame({"date": dates, **{c: 1.0 for c in channels}})
    assert list(validate_plan(ok, last_history_date=pd.Timestamp("2025-01-01"), paid_channels=channels).columns) == channels
    with pytest.raises(PlanValidationError, match="Unknown or misspelled"):
        validate_plan(ok.rename(columns={"catalog": "catalg"}), last_history_date=pd.Timestamp("2025-01-01"), paid_channels=channels)
    with pytest.raises(PlanValidationError, match="Missing channel"):
        validate_plan(ok.drop(columns=["affiliate"]), last_history_date=pd.Timestamp("2025-01-01"), paid_channels=channels)


def test_plan_time_controls_replace_the_commission_with_history_only_values(wide_model: pd.DataFrame) -> None:
    rc = default_role_config()
    cfg = MeridianSpecConfig.from_role_config(rc, wide=wide_model)
    history = wide_model.loc[:"2024-09-30"]
    window = wide_model.loc["2024-10-01":"2024-10-14"]
    plan = pd.DataFrame({c: window[f"cost_{c}"] for c in cfg.paid_slugs}, index=window.index)
    pt = plan_time_controls(history, plan, cfg)
    assert not np.allclose(pt["affiliate"], plan["affiliate"])  # realised commission is not used
    pd.testing.assert_frame_equal(pt.drop(columns=["affiliate"]), plan.drop(columns=["affiliate"]))  # media unchanged
    # Only history counts: a different realised commission gives the same estimate
    other = plan_time_controls(history, plan.assign(affiliate=plan["affiliate"] * 3.0), cfg)
    np.testing.assert_allclose(other["affiliate"], pt["affiliate"])
    # Recent run-rate × last year's seasonal ratio
    recent = history["cost_affiliate"].iloc[-56:]
    ratio = history.loc["2023-10-01":"2023-10-14", "cost_affiliate"].mean() / history.loc[
        recent.index[0] - pd.DateOffset(years=1) : recent.index[-1] - pd.DateOffset(years=1), "cost_affiliate"
    ].mean()
    assert pt["affiliate"].mean() == pytest.approx(recent.mean() * ratio, rel=0.02)


def test_total_interval_is_a_quantile_of_sums_not_a_sum_of_quantiles() -> None:
    rng = np.random.default_rng(0)
    draws = 100.0 + rng.normal(size=(4000, 42)) * 10.0
    idx = pd.date_range("2025-01-02", periods=42, freq="D")
    lo, hi = total_interval(draws, idx, idx)
    naive_lo = float(np.quantile(draws, 0.05, axis=0).sum())
    assert (hi - lo) < 0.25 * (float(np.quantile(draws, 0.95, axis=0).sum()) - naive_lo)


def test_missing_row_controls_only_for_channels_with_gaps_in_training(wide_model: pd.DataFrame) -> None:
    rc = default_role_config()
    cfg = MeridianSpecConfig.from_role_config(rc, wide=wide_model)
    assert set(cfg.missing_controls) == {"catalog", "owned", "tv"}
    table = meridian_table(wide_model, cfg)
    assert {"missing_catalog", "missing_owned", "missing_tv"} <= set(table.columns)


# --- Formatting, errors and break detection (UI-02, SEC-04, AR-01) -------------------------------------------------


def test_number_formatting_boundaries() -> None:
    assert fmt_eur(999_999.6) == "€1.00M"
    assert fmt_eur(float("nan")) == "—"


def test_public_errors_hide_paths() -> None:
    msg = public_error(FileNotFoundError("No such file: '/app/.venv/lib/x.binpb'"))
    assert "/app" not in msg and "<path>" in msg


def test_regime_breaks_are_detected_from_the_data(wide_model: pd.DataFrame) -> None:
    social = detect_level_shift(wide_model["cost_social_media"])
    assert social is not None and social[0] == pd.Timestamp("2024-08-01")
    rate = wide_model["cost_affiliate"] / wide_model["sales_affiliate"].where(wide_model["sales_affiliate"] > 0)
    commission = detect_level_shift(rate)
    assert commission is not None and commission[0] == pd.Timestamp("2024-02-01")
    flat = pd.Series(1.0, index=wide_model.index)
    assert detect_level_shift(flat) is None


def test_a_worker_is_started_when_the_execution_list_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.cloud import run_v2

    from mmm_studio.jobs import cloud_run
    from mmm_studio.settings import get_settings

    monkeypatch.setenv("MMM_WAKE_WORKERS", "cloudrun")
    monkeypatch.setenv("MMM_GCP_PROJECT_ID", "test-project")
    get_settings.cache_clear()
    monkeypatch.setattr(cloud_run, "_LAST_WAKE_AT", {})
    started: list[str] = []

    def no_listing(queue: str) -> bool:
        raise PermissionError("run.executions.list denied")

    class Jobs:
        def run_job(self, name: str) -> None:
            started.append(name)

    monkeypatch.setattr(cloud_run, "_worker_active", no_listing)
    monkeypatch.setattr(run_v2, "JobsClient", Jobs)
    try:
        cloud_run.wake_worker("meridian")
        expected = cloud_run.job_resource_name("meridian")
    finally:
        get_settings.cache_clear()  # no cached test settings for later tests
    assert started == [expected]


def test_scipy_is_in_core_api_dependencies() -> None:
    """BUG-NEW-01: The slim API container runs solver helpers that import scipy; it must be a core dependency."""
    import tomllib
    from pathlib import Path

    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    core_deps = data["project"]["dependencies"]
    assert any(dep.startswith("scipy") for dep in core_deps)


def test_forecast_get_never_writes_or_downloads_binpb_without_meridian(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """BUG-NEW-02: In the slim API container (no meridian), _enrich_forecast_result must not download .binpb or write on GET."""
    from mmm_studio.api import forecasts as fc_api
    from mmm_studio.db.models import ForecastRun
    from mmm_studio.storage import LocalStorage

    storage = LocalStorage(tmp_path)
    run_id = "run_slim_test"
    fc_id = f"default_{run_id}"
    dates = pd.date_range("2025-01-02", periods=7, freq="D")
    daily_df = pd.DataFrame(
        {"forecast": [1000.0] * 7, "prediction_lo": [800.0] * 7, "prediction_hi": [1200.0] * 7, "planned_spend": [200.0] * 7},
        index=dates,
    )
    plan_df = pd.DataFrame({"sea": [100.0] * 7, "display": [100.0] * 7}, index=dates)

    import io

    d_buf = io.BytesIO()
    daily_df.to_parquet(d_buf)
    storage.write_bytes(f"runs/{run_id}/data/forecast_daily.parquet", d_buf.getvalue())

    p_buf = io.BytesIO()
    plan_df.to_parquet(p_buf)
    storage.write_bytes(f"runs/{run_id}/data/forecast_plan.parquet", p_buf.getvalue())
    storage.write_bytes(f"runs/{run_id}/models/meridian_forecast.binpb", b"fake-35mb-binary")

    monkeypatch.setattr(fc_api, "_meridian_installed", lambda: False)

    read_keys: list[str] = []
    orig_read_bytes = storage.read_bytes

    def spy_read_bytes(key: str) -> bytes:
        read_keys.append(key)
        return orig_read_bytes(key)

    monkeypatch.setattr(storage, "read_bytes", spy_read_bytes)

    write_calls: list[str] = []
    monkeypatch.setattr(storage, "write_json", lambda k, v: write_calls.append(k))

    fc_row = ForecastRun(
        id=fc_id,
        run_id=run_id,
        name="Example plan",
        status="succeeded",
        plan_path=f"runs/{run_id}/data/forecast_plan.parquet",
        result_path=f"runs/{run_id}/forecasts/{fc_id}/result.json",
    )
    legacy_result = {"horizon_days": 7, "method": "Meridian refit"}
    enriched = fc_api._enrich_forecast_result(fc_row, legacy_result, storage, db=None)

    assert "variable_contributions" in enriched
    assert not any(k.endswith(".binpb") for k in read_keys), "Slim API container must not download .binpb when meridian is absent"
    assert write_calls == [], "GET forecast enrichment must never write to storage (API-06)"


def test_worker_reload_priority_and_legacy_model_settings_fallback(wide_model: pd.DataFrame) -> None:
    """BUG-NEW-03: Hot-reload must reload model_settings before schema, and MeridianSpecConfig must tolerate legacy ModelSettings."""
    from types import SimpleNamespace

    from mmm_studio.jobs import tasks

    priority = list(tasks._RELOAD_PRIORITY)
    assert priority.index("mmm_studio.core.model_settings") < priority.index("mmm_studio.core.schema")

    rc = default_role_config()
    legacy_ms = SimpleNamespace(
        **{
            k: v
            for k, v in rc.model_settings.__dict__.items()
            if k not in ("total_media_contribution_mean", "total_media_contribution_sd")
        }
    )
    legacy_rc = SimpleNamespace(
        channels=rc.channels,
        holdout_days=rc.holdout_days,
        model_settings=legacy_ms,
    )
    cfg = MeridianSpecConfig.from_role_config(legacy_rc, wide=wide_model)  # type: ignore[arg-type]
    assert cfg.total_media_contribution_mean == pytest.approx(0.4)
    assert cfg.total_media_contribution_sd == pytest.approx(0.2)


def test_custom_short_horizon_scenario_slices_plan_and_keeps_custom_controls(
    monkeypatch: pytest.MonkeyPatch,
    wide_model: pd.DataFrame,
) -> None:
    """BUG-NEW-04: Evaluating a scenario on a 5-day custom plan padded to a 42-day model must slice planned_spend and pass controls."""
    from mmm_studio.core.meridian import forecast as fc_mod

    rc = default_role_config()
    cfg = MeridianSpecConfig.from_role_config(rc, wide=wide_model)
    dates_42 = pd.date_range("2025-01-02", periods=42, freq="D", name="date")
    dates_5 = dates_42[:5]
    padded_plan = pd.DataFrame({c: 1000.0 for c in cfg.paid_slugs}, index=dates_42)
    base_daily_5 = pd.DataFrame(
        {
            "forecast": [100_000.0] * 5,
            "credible_lo": [90_000.0] * 5,
            "credible_hi": [110_000.0] * 5,
            "prediction_lo": [85_000.0] * 5,
            "prediction_hi": [115_000.0] * 5,
            "planned_spend": [7_000.0] * 5,
        },
        index=dates_5,
    )

    seen_with_controls: list[bool] = []
    orig_eval = fc_mod.evaluate_forecast_scenario

    def fake_prediction_draws(mmm: Any, new_data: Any = None, seed: int = 0, **_: Any) -> tuple[np.ndarray, np.ndarray]:
        n_time = len(wide_model) + len(dates_42)
        has_ctrl = getattr(new_data, "controls", None) is not None
        seen_with_controls.append(has_ctrl)
        val = 105_000.0 if has_ctrl else 80_000.0
        draws = np.full((10, n_time), val, dtype=float)
        return draws, draws

    monkeypatch.setattr(fc_mod, "_prediction_draws", fake_prediction_draws)
    monkeypatch.setattr(
        fc_mod,
        "channel_effect_draws",
        lambda mmm, h, p, c: (np.full((10, len(p), len(c.media_slugs)), 1000.0), list(c.media_slugs)),
    )
    monkeypatch.setattr(fc_mod, "control_effect_draws", lambda mmm, h, p, c: {})

    res = fc_mod.model_scenario_from_multipliers(
        mmm=object(),
        history=wide_model,
        base_daily=base_daily_5,
        base_plan=padded_plan,
        cfg=cfg,
        multipliers={"sea": 1.1},
        horizon_days=5,
        unlock_controls=False,
    )
    assert seen_with_controls == [True], "model_scenario_from_multipliers must pass with_controls=True so custom plan controls are preserved"
    assert res["horizon_days"] == 5
    assert res["delta_sales"] > 0
    assert orig_eval is not None


def test_aks_active_time_steps_trims_constant_forecast_tail(wide_model: pd.DataFrame) -> None:
    """BUG-NEW-05: Automatic knot selection must ignore synthetic constant-KPI forecast tails from extend_history."""
    from types import SimpleNamespace

    from mmm_studio.core.features import holdout_windows
    from mmm_studio.core.meridian.forecast import extend_history
    from mmm_studio.core.meridian.spec import _active_aks_time_steps, _TrimmedAKSData

    tr_end, te_start, te_end = holdout_windows(pd.DatetimeIndex(wide_model.index), 93)["primary"]
    history_bt = wide_model.loc[:tr_end]
    plan_bt = pd.DataFrame({"sea": wide_model.loc[te_start:te_end, "cost_sea"]})
    ext_bt = extend_history(history_bt, plan_bt)

    def make_dummy(y: np.ndarray) -> Any:
        scaled = ((y - np.mean(y)) / np.std(y))[np.newaxis, :]
        return SimpleNamespace(
            time=np.arange(len(y)),
            geo=np.array(["national"]),
            scaled_centered_kpi=scaled,
            media_channel=[0] * 6,
            rf_channel=None,
            organic_media_channel=None,
            organic_rf_channel=None,
            non_media_channel=None,
            control_variable=[0] * 14,
        )

    d_full = make_dummy(wide_model["total_sales"].to_numpy(dtype=float))
    assert _active_aks_time_steps(d_full) == len(wide_model)

    d_ext = make_dummy(ext_bt["total_sales"].to_numpy(dtype=float))
    n_active = _active_aks_time_steps(d_ext)
    assert n_active == len(history_bt)
    trimmed = _TrimmedAKSData(d_ext, n_active)
    assert len(trimmed.time) == len(history_bt)
    assert trimmed.scaled_centered_kpi.shape == (1, len(history_bt))
    assert np.isclose(float(np.mean(trimmed.scaled_centered_kpi)), 0.0, atol=1e-6)
    assert np.isclose(float(np.std(trimmed.scaled_centered_kpi)), 1.0, atol=1e-6)


@pytest.mark.slow
def test_aks_guard_handles_extend_history_and_collinear_tails(wide_model: pd.DataFrame) -> None:
    """BUG-NEW-05: Meridian AKS with baseline='auto' must not crash with empty max() on extend_history or collinear tails."""
    from types import SimpleNamespace

    from meridian.model import knots as knots_mod

    from mmm_studio.core.features import holdout_windows
    from mmm_studio.core.meridian.forecast import extend_history
    from mmm_studio.core.meridian.spec import (
        _install_meridian_aks_guard,
        build_input_data,
        build_meridian_model,
    )

    _install_meridian_aks_guard()

    rc = default_role_config()
    rc.model_settings.baseline = "auto"
    tr_end, te_start, te_end = holdout_windows(pd.DatetimeIndex(wide_model.index), rc.holdout_days)["primary"]
    history_bt = wide_model.loc[:tr_end]
    plan_cols = [c for c in ("sea", "catalog", "display", "affiliate", "display_network", "social_media", "psm") if f"cost_{c}" in wide_model.columns]
    plan_bt = pd.DataFrame({c: wide_model.loc[te_start:te_end, f"cost_{c}"] for c in plan_cols})
    ext_bt = extend_history(history_bt, plan_bt)

    def make_dummy(y: np.ndarray) -> Any:
        scaled = ((y - np.mean(y)) / np.std(y))[np.newaxis, :]
        return SimpleNamespace(
            time=np.arange(len(y)),
            geo=np.array(["national"]),
            scaled_centered_kpi=scaled,
            media_channel=[0] * 6,
            rf_channel=None,
            organic_media_channel=None,
            organic_rf_channel=None,
            non_media_channel=None,
            control_variable=[0] * 14,
        )

    # 1. Float64 constant-tail KPI (exact sigma0sq == 0.0 in unguarded AKS)
    res_ext = knots_mod.AKS(make_dummy(ext_bt["total_sales"].to_numpy(dtype=np.float64))).automatic_knot_selection()
    assert res_ext.knots.size >= 2
    assert int(res_ext.knots.max()) == len(history_bt) - 1

    # 2. Collinear last 3 time steps without a constant tail (sigma0sq == 0.0 on active prefix)
    y_col = history_bt["total_sales"].to_numpy(dtype=np.float64).copy()
    y_col[-3:] = [500_000.0, 510_000.0, 520_000.0]
    res_col = knots_mod.AKS(make_dummy(y_col)).automatic_knot_selection()
    assert res_col.knots.size >= 2
    assert int(res_col.knots.max()) == len(history_bt) - 1

    # 3. Full Meridian model built on extend_history with baseline='auto'
    fc_cfg = MeridianSpecConfig.from_role_config(rc, wide=ext_bt)
    mmm = build_meridian_model(build_input_data(meridian_table(ext_bt, fc_cfg), fc_cfg), fc_cfg)
    ki = mmm.knot_info
    assert int(ki.knot_locations[-1]) == len(history_bt) - 1
    assert np.allclose(ki.weights[-1, len(history_bt) :], 1.0)




