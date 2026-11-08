"""FastAPI routes for budget-plan forecasts (`meridian` queue) and interactive what-if scenarios (`interactive` queue)."""

from __future__ import annotations

import io
import logging
import math
import tempfile
import threading
import uuid
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mmm_studio.api.deps import get_app_settings, get_app_storage, get_db
from mmm_studio.core.meridian.forecast import (
    PlanValidationError,
    build_example_plan,
    channel_effect_draws,
    control_effect_draws,
    evaluate_forecast_scenario,
    extend_history,
    forecast_level_sd,
    model_scenario_from_multipliers,
    plan_arrays,
    scenario_payload,
    validate_plan,
)
from mmm_studio.core.meridian.spec import MeridianSpecConfig, channel_label, load_meridian_model
from mmm_studio.core.schema import extract_role_config, slugify_channel
from mmm_studio.db.models import ForecastRun, Run, ScenarioRun
from mmm_studio.jobs.app import (
    QueueUnavailable,
    count_active_queue_jobs,
    defer_task,
    get_queue_position,
    sweep_stale_runs_throttled,
)
from mmm_studio.jobs.registry import default_forecast_keys
from mmm_studio.jobs.tasks import run_forecast_job, run_scenario_job
from mmm_studio.pipeline.context import model_panel_key
from mmm_studio.settings import Settings
from mmm_studio.storage import StorageBackend

logger = logging.getLogger(__name__)
router = APIRouter(tags=["forecasts"])
MAX_PLAN_BYTES = 1024 * 1024

_FORECAST_MODEL_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_FORECAST_CACHE_LOCK = threading.Lock()


def _get_cached_forecast_model(key: str) -> dict[str, Any] | None:
    with _FORECAST_CACHE_LOCK:
        if key in _FORECAST_MODEL_CACHE:
            _FORECAST_MODEL_CACHE.move_to_end(key)
            return _FORECAST_MODEL_CACHE[key]
        return None


def _put_cached_forecast_model(key: str, value: dict[str, Any]) -> None:
    with _FORECAST_CACHE_LOCK:
        _FORECAST_MODEL_CACHE[key] = value
        _FORECAST_MODEL_CACHE.move_to_end(key)
        while len(_FORECAST_MODEL_CACHE) > 2:
            _FORECAST_MODEL_CACHE.popitem(last=False)


class ScenarioCreateRequest(BaseModel):
    name: str = "What-If Scenario"
    multipliers: dict[str, float] = Field(default_factory=dict)
    spend_overrides: dict[str, float] = Field(default_factory=dict)
    horizon_days: int = Field(default=42, ge=1, le=90)
    unlock_controls: bool = False


def _normalise_multipliers(run: Run | None, multipliers: dict[str, float]) -> dict[str, float]:
    """Key scenario multipliers by channel slug; a display name is accepted too.

    An unknown channel or a negative / non-finite multiplier is a 422: silently dropping it would return a
    "no change" scenario that looks like a real result.
    """
    cfg = extract_role_config((run.config if run else None) or {})
    lookup: dict[str, str] = {}
    for name, slug in zip(cfg.paid_channels, cfg.paid_slugs, strict=True):
        lookup[slugify_channel(slug)] = slug
        lookup[slugify_channel(name)] = slug
    normalised: dict[str, float] = {}
    unknown: list[str] = []
    for key, value in multipliers.items():
        slug = lookup.get(slugify_channel(str(key)))
        if slug is None:
            unknown.append(str(key))
            continue
        if not math.isfinite(value) or value < 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Multiplier for {key!r} must be a finite number ≥ 0 (got {value}).",
            )
        normalised[slug] = float(value)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown channel(s) {', '.join(sorted(unknown))}; use one of: {', '.join(sorted(set(lookup.values())))}.",
        )
    return normalised


def _spec_for_run(run: Run | None, wide_df: pd.DataFrame) -> MeridianSpecConfig:
    cfg_obj = extract_role_config((run.config if run else None) or {})
    try:
        return MeridianSpecConfig.from_role_config(cfg_obj, wide=wide_df)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _paid_channels_for_run(run: Run | None, wide_df: pd.DataFrame) -> list[str]:
    return _spec_for_run(run, wide_df).paid_slugs


def _load_wide_df(storage: StorageBackend, run_id: str) -> pd.DataFrame:
    wide_key = model_panel_key(storage, run_id)
    if wide_key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The daily modelling panel is not ready yet for run {run_id}. Wait for ingestion to complete.",
        )
    return pd.read_parquet(io.BytesIO(storage.read_bytes(wide_key)))


def _meridian_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("meridian") is not None


def _load_or_cache_forecast_model(
    run: Run | None,
    run_id: str,
    fc_id: str,
    storage: StorageBackend,
) -> dict[str, Any] | None:
    """Load and cache the fitted Meridian forecast model (`meridian_forecast.binpb`), base plan, and base draws."""
    if not _meridian_installed():
        return None

    if fc_id.startswith("default_"):
        daily_keys, plan_keys = default_forecast_keys(run_id)
    else:
        base = f"runs/{run_id}/forecasts/{fc_id}"
        daily_keys = [f"{base}/daily.parquet"]
        plan_keys = [f"{base}/plan.parquet", f"{base}/plan.csv"]

    d_key = next((k for k in daily_keys if storage.exists(k)), None)
    p_key = next((k for k in plan_keys if storage.exists(k)), None)
    if not d_key or not p_key:
        return None

    own_model = f"runs/{run_id}/forecasts/{fc_id}/meridian_forecast.binpb"
    default_model = f"runs/{run_id}/models/meridian_forecast.binpb"
    m_key = own_model if storage.exists(own_model) else (default_model if storage.exists(default_model) else None)
    if not m_key:
        return None

    version = run.updated_at.isoformat() if run and run.updated_at else ""
    cache_key = f"{run_id}:{m_key}:{version}"
    cached = _get_cached_forecast_model(cache_key)
    if cached is None:
        try:
            wide_df = _load_wide_df(storage, run_id)
            spec_cfg = _spec_for_run(run, wide_df)
            b_plan = _read_plan(storage, p_key)
            b_daily = pd.read_parquet(io.BytesIO(storage.read_bytes(d_key)))
            # When using the default model for a shorter custom forecast, align with the model's full plan window
            model_plan = b_plan
            if m_key == default_model and not fc_id.startswith("default_"):
                def_p_key = next((k for k in default_forecast_keys(run_id)[1] if storage.exists(k)), None)
                if def_p_key:
                    model_plan = _read_plan(storage, def_p_key)
            start = model_plan.index[0].strftime("%Y-%m-%d")
            end = model_plan.index[-1].strftime("%Y-%m-%d")
            ext = extend_history(wide_df, model_plan)
            fc_cfg = replace(spec_cfg, holdout=(start, end)).with_training_priors(ext)
            with tempfile.TemporaryDirectory(prefix=f"fc_fast_{run_id}_") as tmp:
                bin_path = Path(tmp) / "meridian_forecast.binpb"
                bin_path.write_bytes(storage.read_bytes(m_key))
                mmm = load_meridian_model(bin_path)
            cov_key = f"runs/{run_id}/data/forecast_summary.json"
            coverage = None
            fc_summary = storage.read_json(cov_key) if storage.exists(cov_key) else {}
            cov_val = fc_summary.get("backtest_coverage")
            coverage = float(cov_val) if cov_val is not None else None
            cached = {
                "mmm": mmm,
                "wide_df": wide_df,
                "base_daily": b_daily,
                "base_plan": model_plan,
                "fc_cfg": fc_cfg,
                "coverage": coverage,
                "level_sd": forecast_level_sd(fc_summary),
            }
            _put_cached_forecast_model(cache_key, cached)
        except Exception:
            logger.warning("Failed to load cached forecast model for run %s (%s)", run_id, fc_id, exc_info=True)
            return None
    return cached


def _enrich_forecast_result(
    fc: ForecastRun,
    result: dict[str, Any],
    storage: StorageBackend,
    db: Session | None = None,
) -> dict[str, Any]:
    """Backfill `variable_contributions` and enriched `channel_summary` on legacy stored forecast `result.json`."""
    var_contribs = result.get("variable_contributions")
    has_media_ci = bool(
        var_contribs
        and result.get("decomposition_version") == 2
        and any(v.get("category") == "media" and v.get("base_ci_lo") is not None for v in var_contribs)
    )
    own_model = f"runs/{fc.run_id}/forecasts/{fc.id}/meridian_forecast.binpb"
    default_model = f"runs/{fc.run_id}/models/meridian_forecast.binpb"
    can_load_binpb = _meridian_installed() and (storage.exists(own_model) or storage.exists(default_model))
    if var_contribs and (has_media_ci or not can_load_binpb):
        return result

    try:
        run = db.get(Run, fc.run_id) if db is not None else None
        if fc.id.startswith("default_"):
            daily_keys, plan_keys = default_forecast_keys(fc.run_id)
        else:
            base = f"runs/{fc.run_id}/forecasts/{fc.id}"
            daily_keys = [f"{base}/daily.parquet"]
            plan_keys = [f"{base}/plan.parquet", f"{base}/plan.csv"]
        if fc.plan_path and fc.plan_path not in plan_keys:
            plan_keys.append(fc.plan_path)

        d_key = next((k for k in daily_keys if storage.exists(k)), None)
        p_key = next((k for k in plan_keys if storage.exists(k)), None)
        if not d_key or not p_key:
            return result
        fc_daily = pd.read_parquet(io.BytesIO(storage.read_bytes(d_key)))
        fc_plan = _read_plan(storage, p_key).iloc[: len(fc_daily)]

        base_draws = None
        effect_channels = None
        base_ctrl = None
        if can_load_binpb:
            cached = _load_or_cache_forecast_model(run, fc.run_id, fc.id, storage)
            if cached is not None:
                model_plan: pd.DataFrame = cached["base_plan"].copy()
                n_days = len(fc_plan)
                for col in fc_plan.columns:
                    if col in model_plan.columns:
                        model_plan.iloc[:n_days, model_plan.columns.get_loc(col)] = fc_plan[col].to_numpy(dtype=float)
                draws_full, effect_channels = channel_effect_draws(
                    cached["mmm"], cached["wide_df"], model_plan, cached["fc_cfg"]
                )
                base_draws = draws_full[:, :n_days, :]
                base_ctrl = control_effect_draws(
                    cached["mmm"], cached["wide_df"], model_plan, cached["fc_cfg"]
                )

        roi_map: dict[str, float] = {}
        roi_key = f"runs/{fc.run_id}/data/meridian_roi.parquet"
        if storage.exists(roi_key):
            roi_df = pd.read_parquet(io.BytesIO(storage.read_bytes(roi_key))).drop(index="All Channels", errors="ignore")
            roi_map = {slugify_channel(str(c)): float(v) for c, v in roi_df["roi"].items()}

        pi_lo = result.get("prediction_total_lo")
        pi_hi = result.get("prediction_total_hi")
        total_pi = (float(pi_lo), float(pi_hi)) if pi_lo is not None and pi_hi is not None else None
        cov = result.get("backtest_coverage")
        return scenario_payload(
            fc_daily,
            fc_plan,
            fc_daily,
            fc_plan,
            str(result.get("method") or "Meridian refit"),
            effect_channels=effect_channels,
            total_pi=total_pi,
            coverage=float(cov) if cov is not None else None,
            base_media_draws=base_draws,
            alt_media_draws=base_draws,
            base_control_draws=base_ctrl,
            alt_control_draws=base_ctrl,
            roi_map=roi_map,
        )
    except Exception:
        logger.warning("Failed to enrich forecast result for %s", fc.id, exc_info=True)
        return result


def _serialize_forecast(
    fc: ForecastRun,
    storage: StorageBackend,
    include_result: bool = False,
    db: Session | None = None,
) -> dict[str, Any]:
    q_pos = get_queue_position(fc.job_id, "meridian") if fc.job_id else None
    out: dict[str, Any] = {
        "id": fc.id,
        "run_id": fc.run_id,
        "name": fc.name,
        "status": fc.status,
        "queue_position": q_pos,
        "summary": fc.summary,
        "error": fc.error,
        "created_at": fc.created_at.isoformat() if fc.created_at else None,
        "updated_at": fc.updated_at.isoformat() if fc.updated_at else None,
    }
    if include_result and fc.result_path and storage.exists(fc.result_path):
        result = storage.read_json(fc.result_path)
        if "plan_daily" not in result and fc.plan_path and storage.exists(fc.plan_path):
            # Results written before the planner chart showed spend carry no daily plan: read it from the plan file
            plan = _read_plan(storage, fc.plan_path).iloc[: int(result.get("horizon_days") or 0) or None]
            result["plan_daily"] = result["scenario_plan_daily"] = plan_arrays(plan)
        result = _enrich_forecast_result(fc, result, storage, db=db)
        out["result"] = result
    return out


def _read_plan(storage: StorageBackend, key: str) -> pd.DataFrame:
    """A stored plan: the pipeline's parquet (date index) or an upload's cleaned CSV (`date` column)."""
    raw = io.BytesIO(storage.read_bytes(key))
    if key.endswith(".parquet"):
        return pd.read_parquet(raw)
    return pd.read_csv(raw, parse_dates=["date"]).set_index("date")


def _serialize_scenario(sc: ScenarioRun, storage: StorageBackend) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": sc.id,
        "forecast_id": sc.forecast_id,
        "run_id": sc.run_id,
        "name": sc.name,
        "inputs": sc.inputs or {},
        "status": sc.status,
        "summary": sc.summary,
        "error": sc.error,
        "created_at": sc.created_at.isoformat() if sc.created_at else None,
        "updated_at": sc.updated_at.isoformat() if sc.updated_at else None,
    }
    if sc.result_path and storage.exists(sc.result_path):
        out["result"] = storage.read_json(sc.result_path)
    return out


@router.get("/runs/{run_id}/plan-template")
def get_plan_template(
    run_id: str,
    days: int = Query(default=42, ge=1, le=90),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> Response:
    """Download a pre-populated future budget plan CSV starting the day after the historical window."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    wide_df = _load_wide_df(storage, run_id)
    spec = _spec_for_run(run, wide_df)
    plan_df = build_example_plan(wide_df, days=days, paid_channels=spec.paid_slugs, print_slugs=spec.print_slugs)
    plan_df = plan_df.round(2)
    csv_bytes = plan_df.reset_index().to_csv(index=False).encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="plan_template_{days}d.csv"'},
    )


@router.get("/runs/{run_id}/history")
def get_history(
    run_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Daily actual sales and paid-channel spend of the modelling panel, shown before a forecast for context, plus the
    validation and test windows forecast like a plan (`backtest`, empty until the forecast stage ran)."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    wide_df = _load_wide_df(storage, run_id)
    slugs = [s for s in _paid_channels_for_run(run, wide_df) if f"cost_{s}" in wide_df.columns]

    def values(col: str) -> list[float | None]:
        return [None if pd.isna(v) else round(float(v), 0) for v in wide_df[col]]

    return {
        "run_id": run_id,
        "dates": [d.strftime("%Y-%m-%d") for d in pd.DatetimeIndex(wide_df.index)],
        "sales": values("total_sales"),
        "spend": {s: values(f"cost_{s}") for s in slugs},
        "channels": [{"slug": s, "channel": channel_label(s)} for s in slugs],
        "backtest": _backtest_windows(storage, run_id, pd.DatetimeIndex(wide_df.index)),
    }


def _backtest_windows(storage: StorageBackend, run_id: str, panel: pd.DatetimeIndex) -> list[dict[str, Any]]:
    """The validation and test windows forecast exactly like a budget plan, as a rolling-origin evaluation.

    Each window was forecast by a model refit on every day before it (`train_start`..`train_end`), with the window's
    sales unknown to it and its actual spend as the plan (`_forecast_window` in the pipeline); with a commission control
    the plan-time variant is used (`plan_time`), because a realised commission leaks the sales it was paid on. The test
    window is the last `holdout_days` days of the panel and validation an equally long window right before it
    (`features.holdout_windows`), so training, validation, test and the plan forecast tile the timeline without
    overlap. A window the run has not produced is left out; a
    stored split that does not tile the panel is an error, never redrawn.
    """
    from mmm_studio.core.features import quarter_label
    from mmm_studio.core.metrics import regression_metrics

    def read(name: str) -> pd.DataFrame | None:
        key = f"runs/{run_id}/data/{name}.parquet"
        return pd.read_parquet(io.BytesIO(storage.read_bytes(key))) if storage.exists(key) else None

    # A leak-free run (`ModelSettings.leak_free_validation`) forecast both windows with the costs that follow the outcome
    # planned from history already, so its plain back-tests are the plan-time ones
    meta_key = f"runs/{run_id}/data/backtest_meta.json"
    leak_free = bool(storage.exists(meta_key) and storage.read_json(meta_key).get("leak_free"))
    windows: list[dict[str, Any]] = []
    for window_id, stem in (("validation", "forecast_validation"), ("test", "forecast_backtest")):
        plan_time = read(f"{stem}_plan_time_daily")
        df = plan_time if plan_time is not None else read(f"{stem}_daily")
        if df is None or df.empty:
            continue
        df = df.sort_index()
        idx = pd.DatetimeIndex(df.index)
        mae_over_mean = regression_metrics(df["actual"], df["forecast"])["MAE_over_mean"]

        windows.append(
            {
                "id": window_id,
                "label": quarter_label(idx[0], idx[-1]),
                "start": idx[0].strftime("%Y-%m-%d"),
                "end": idx[-1].strftime("%Y-%m-%d"),
                "train_start": panel[0].strftime("%Y-%m-%d"),
                "train_end": (idx[0] - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                "plan_time": plan_time is not None or leak_free,
                "dates": [d.strftime("%Y-%m-%d") for d in idx],
                "forecast": [None if pd.isna(v) else round(float(v), 0) for v in df["forecast"]],
                "lo": [None if pd.isna(v) else round(float(v), 0) for v in df["prediction_lo"]],
                "hi": [None if pd.isna(v) else round(float(v), 0) for v in df["prediction_hi"]],
                "mae_over_mean": float(mae_over_mean) if math.isfinite(mae_over_mean) else None,
            }
        )

    # The split must tile the panel: consecutive daily windows, validation ending the day before the test and the test
    # ending on the panel's last day (the plan forecast starts the day after it)
    problems = []
    for w in windows:
        days = pd.DatetimeIndex(w["dates"])
        if len(days) != (days[-1] - days[0]).days + 1 or days[0] < panel[0] or days[-1] > panel[-1]:
            problems.append(f"the {w['id']} window is not a run of consecutive days inside the data")
    by_id = {w["id"]: w for w in windows}
    if "test" in by_id and by_id["test"]["end"] != panel[-1].strftime("%Y-%m-%d"):
        problems.append("the test window does not end on the last day of the data")
    if "validation" in by_id and "test" in by_id:
        if pd.Timestamp(by_id["validation"]["end"]) + pd.Timedelta(days=1) != pd.Timestamp(by_id["test"]["start"]):
            problems.append("the validation window does not end the day before the test window")
    if problems:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This run's validation/test split is inconsistent ({'; '.join(problems)}). Rerun the model.",
        )
    return windows


@router.get("/runs/{run_id}/forecasts")
def list_forecasts(
    run_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """List all budget-plan forecasts for a Run (including the default 42-day plan if ready)."""
    sweep_stale_runs_throttled()
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    items = (
        db.query(ForecastRun)
        .filter(ForecastRun.run_id == run_id)
        .order_by(ForecastRun.created_at.desc())
        .all()
    )
    return {
        "run_id": run_id,
        "forecasts": [_serialize_forecast(f, storage, include_result=False) for f in items],
    }


def _try_fast_forecast_evaluation(
    run: Run,
    wide_df: pd.DataFrame,
    clean_df: pd.DataFrame,
    forecast_id: str,
    storage: StorageBackend,
    db: Session,
) -> tuple[str, dict[str, Any]] | None:
    """Evaluate `clean_df` directly on an already-fitted forecast model if its horizon covers `len(clean_df)` days."""
    run_id = run.id
    n_days = len(clean_df)
    default_daily_keys, default_plan_keys = default_forecast_keys(run_id)
    candidates: list[tuple[str, str, str]] = []

    default_daily = next((k for k in default_daily_keys if storage.exists(k)), None)
    default_plan = next((k for k in default_plan_keys if storage.exists(k)), None)
    default_model = f"runs/{run_id}/models/meridian_forecast.binpb"
    if default_daily and default_plan and storage.exists(default_model):
        candidates.append((default_model, default_daily, default_plan))

    prior_fcs = (
        db.query(ForecastRun)
        .filter(ForecastRun.run_id == run_id, ForecastRun.status == "succeeded")
        .order_by(ForecastRun.created_at.desc())
        .all()
    )
    for pfc in prior_fcs:
        if pfc.id.startswith("default_"):
            continue
        m_key = f"runs/{run_id}/forecasts/{pfc.id}/meridian_forecast.binpb"
        d_key = f"runs/{run_id}/forecasts/{pfc.id}/daily.parquet"
        p_key = f"runs/{run_id}/forecasts/{pfc.id}/plan.parquet"
        if storage.exists(m_key) and storage.exists(d_key) and storage.exists(p_key):
            candidates.append((m_key, d_key, p_key))

    chosen: tuple[str, str, str, pd.DataFrame] | None = None
    for m_key, d_key, p_key in candidates:
        try:
            b_plan = _read_plan(storage, p_key)
        except Exception:
            continue
        if len(b_plan) >= n_days and pd.Timestamp(b_plan.index[0]) == pd.Timestamp(clean_df.index[0]):
            chosen = (m_key, d_key, p_key, b_plan)
            break

    if chosen is None:
        return None

    m_key, d_key, p_key, b_plan = chosen
    version = run.updated_at.isoformat() if run.updated_at else ""
    cache_key = f"{run_id}:{m_key}:{version}"
    cached = _get_cached_forecast_model(cache_key)
    if cached is None:
        try:
            spec_cfg = _spec_for_run(run, wide_df)
            b_daily = pd.read_parquet(io.BytesIO(storage.read_bytes(d_key)))
            start = b_plan.index[0].strftime("%Y-%m-%d")
            end = b_plan.index[-1].strftime("%Y-%m-%d")
            ext = extend_history(wide_df, b_plan)
            fc_cfg = replace(spec_cfg, holdout=(start, end)).with_training_priors(ext)
            with tempfile.TemporaryDirectory(prefix=f"fc_fast_{run_id}_") as tmp:
                bin_path = Path(tmp) / "meridian_forecast.binpb"
                bin_path.write_bytes(storage.read_bytes(m_key))
                mmm = load_meridian_model(bin_path)
            cov_key = f"runs/{run_id}/data/forecast_summary.json"
            coverage = None
            fc_summary = storage.read_json(cov_key) if storage.exists(cov_key) else {}
            cov_val = fc_summary.get("backtest_coverage")
            coverage = float(cov_val) if cov_val is not None else None
            cached = {
                "mmm": mmm,
                "wide_df": wide_df,
                "base_daily": b_daily,
                "base_plan": b_plan,
                "fc_cfg": fc_cfg,
                "coverage": coverage,
                "level_sd": forecast_level_sd(fc_summary),
            }
            _put_cached_forecast_model(cache_key, cached)
        except Exception:
            logger.warning("Fast forecast evaluation fallback to queue for run %s", run_id, exc_info=True)
            return None

    try:
        seed = int((run.config or {}).get("seed", 0))
        base_plan: pd.DataFrame = cached["base_plan"]
        base_daily: pd.DataFrame = cached["base_daily"]
        padded = base_plan.copy()
        for col in clean_df.columns:
            if col in padded.columns:
                padded.iloc[:n_days, padded.columns.get_loc(col)] = clean_df[col].to_numpy(dtype=float)

        daily_full, (total_lo, total_hi) = evaluate_forecast_scenario(
            cached["mmm"],
            wide_df,
            base_daily,
            padded,
            cached["fc_cfg"],
            seed=seed,
            with_controls=True,
            horizon_days=n_days,
            level_sd=cached.get("level_sd", 0.0),
        )
        fc_daily = daily_full.iloc[:n_days].copy()
        draws_full, effect_channels = channel_effect_draws(cached["mmm"], wide_df, padded, cached["fc_cfg"])
        base_draws = draws_full[:, :n_days, :]
        base_ctrl = control_effect_draws(cached["mmm"], wide_df, padded, cached["fc_cfg"])

        base = f"runs/{run_id}/forecasts/{forecast_id}"
        for frame_name, frame in (("daily", fc_daily), ("plan", clean_df)):
            buf = io.BytesIO()
            frame.to_parquet(buf, index=True)
            storage.write_bytes(f"{base}/{frame_name}.parquet", buf.getvalue())
        storage.write_bytes(
            f"{base}/forecast.csv",
            fc_daily.round(0).to_csv().encode("utf-8"),
            content_type="text/csv; charset=utf-8",
        )

        res_payload = scenario_payload(
            fc_daily,
            clean_df,
            fc_daily,
            clean_df,
            "Meridian posterior (fitted forecast model)",
            effect_channels=effect_channels,
            total_pi=(total_lo, total_hi),
            coverage=cached["coverage"],
            base_media_draws=base_draws,
            alt_media_draws=base_draws,
            base_control_draws=base_ctrl,
            alt_control_draws=base_ctrl,
        )
        result_key = f"{base}/result.json"
        storage.write_json(result_key, res_payload)
        summary = {
            "horizon_days": n_days,
            "start_date": clean_df.index[0].strftime("%Y-%m-%d"),
            "end_date": clean_df.index[-1].strftime("%Y-%m-%d"),
            "total_forecast_sales": float(fc_daily["forecast"].sum()),
            "total_planned_spend": float(fc_daily["planned_spend"].sum()),
            "prediction_total_lo": total_lo,
            "prediction_total_hi": total_hi,
        }
        return result_key, summary
    except Exception:
        logger.warning("Fast forecast evaluation failed for %s; falling back to queue", forecast_id, exc_info=True)
        return None


@router.post("/runs/{run_id}/forecasts", status_code=status.HTTP_202_ACCEPTED)
def create_forecast(
    run_id: str,
    file: UploadFile = File(...),
    name: str = Form("Custom Budget Plan Forecast"),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Validate an uploaded future budget plan CSV and evaluate immediately on the fitted forecast model or enqueue `run_forecast_job`."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")
    if run.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run {run_id} is {run.status}; forecast a plan once the run has finished.",
        )

    wide_df = _load_wide_df(storage, run_id)
    last_history_date = pd.Timestamp(wide_df.index.max())
    paid_channels = _paid_channels_for_run(run, wide_df)

    raw_bytes = file.file.read(MAX_PLAN_BYTES + 1)
    if len(raw_bytes) > MAX_PLAN_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The plan file is larger than 1 MB.")
    try:
        raw_df = pd.read_csv(io.BytesIO(raw_bytes))
        clean_df = validate_plan(
            raw_df,
            last_history_date=last_history_date,
            paid_channels=paid_channels,
        )
    except PlanValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The plan could not be read as a CSV file with a date column and one cost column per paid channel.",
        ) from exc

    forecast_id = f"fc_{uuid.uuid4().hex[:10]}"
    plan_key = f"runs/{run_id}/forecasts/{forecast_id}/plan.csv"
    clean_csv = clean_df.reset_index().to_csv(index=False).encode("utf-8")
    storage.write_bytes(plan_key, clean_csv)

    fast_res = _try_fast_forecast_evaluation(run, wide_df, clean_df, forecast_id, storage, db)
    if fast_res is not None:
        result_key, summary = fast_res
        fc = ForecastRun(
            id=forecast_id,
            run_id=run_id,
            name=name.strip() or "Custom Budget Plan Forecast",
            status="succeeded",
            plan_path=plan_key,
            result_path=result_key,
            summary=summary,
        )
        db.add(fc)
        db.commit()
        db.refresh(fc)
        return _serialize_forecast(fc, storage, include_result=True, db=db)

    try:
        active_meridian = count_active_queue_jobs("meridian")
    except QueueUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The job queue is unavailable; try again shortly.") from exc
    if active_meridian >= settings.max_meridian_queued:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Meridian queue depth limit reached ({active_meridian}/{settings.max_meridian_queued}).",
        )

    fc = ForecastRun(
        id=forecast_id,
        run_id=run_id,
        name=name.strip() or "Custom Budget Plan Forecast",
        status="queued",
        plan_path=plan_key,
    )
    db.add(fc)
    db.commit()

    job_id = defer_task(
        run_forecast_job,
        queue="meridian",
        lock="meridian_singleton",
        queueing_lock=f"forecast:{forecast_id}",
        forecast_id=forecast_id,
    )
    fc.job_id = job_id
    db.commit()
    db.refresh(fc)
    return _serialize_forecast(fc, storage, include_result=False, db=db)


@router.get("/forecasts/{forecast_id}")
def get_forecast(
    forecast_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Fetch a budget-plan forecast's status and full result payload."""
    sweep_stale_runs_throttled()
    fc = db.get(ForecastRun, forecast_id)
    if not fc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Forecast {forecast_id} not found.")
    return _serialize_forecast(fc, storage, include_result=True, db=db)


def _try_fast_scenario_evaluation(
    fc: ForecastRun,
    run: Run | None,
    multipliers: dict[str, float],
    horizon_days: int,
    unlock_controls: bool,
    scenario_id: str,
    storage: StorageBackend,
    spend_overrides: dict[str, float] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Evaluate a multiplier scenario immediately in-process when the fitted `.binpb` forecast model is available."""
    own_model = f"runs/{fc.run_id}/forecasts/{fc.id}/meridian_forecast.binpb"
    default_model = f"runs/{fc.run_id}/models/meridian_forecast.binpb"
    if not (storage.exists(own_model) or storage.exists(default_model)):
        return None
    cached = _load_or_cache_forecast_model(run, fc.run_id, fc.id, storage)
    if cached is None or cached.get("mmm") is None:
        return None
    try:
        cfg_obj = extract_role_config((run.config if run else None) or {})
        spec_cfg: MeridianSpecConfig = cached["fc_cfg"]
        eff_mults = dict(multipliers)
        eff_overrides = dict(spend_overrides or {})
        if not unlock_controls:
            for locked in (*cfg_obj.commission_channels, *spec_cfg.as_controls):
                eff_mults.pop(locked, None)
                eff_overrides.pop(locked, None)

        if fc.id.startswith("default_"):
            daily_keys, plan_keys = default_forecast_keys(fc.run_id)
        else:
            base = f"runs/{fc.run_id}/forecasts/{fc.id}"
            daily_keys = [f"{base}/daily.parquet"]
            plan_keys = [f"{base}/plan.parquet", f"{base}/plan.csv"]
        d_key = next((k for k in daily_keys if storage.exists(k)), None)
        p_key = next((k for k in plan_keys if storage.exists(k)), None)
        if not d_key or not p_key:
            return None
        fc_daily = pd.read_parquet(io.BytesIO(storage.read_bytes(d_key)))
        fc_plan = _read_plan(storage, p_key)
        model_plan: pd.DataFrame = cached["base_plan"].copy()
        n_fc = len(fc_plan)
        for col in fc_plan.columns:
            if col in model_plan.columns:
                model_plan.iloc[:n_fc, model_plan.columns.get_loc(col)] = fc_plan[col].to_numpy(dtype=float)

        res = model_scenario_from_multipliers(
            cached["mmm"],
            cached["wide_df"],
            fc_daily,
            model_plan,
            spec_cfg,
            multipliers=eff_mults,
            horizon_days=min(horizon_days, n_fc),
            coverage=cached.get("coverage"),
            unlock_controls=unlock_controls,
            level_sd=cached.get("level_sd", 0.0),
            spend_overrides=eff_overrides,
        )
        result_key = f"runs/{fc.run_id}/scenarios/{scenario_id}/result.json"
        storage.write_json(result_key, res)
        summary = {
            "method": res["method"],
            "base_total_sales": res["base_total_sales"],
            "scenario_total_sales": res["scenario_total_sales"],
            "delta_sales": res["delta_sales"],
            "delta_spend": res["delta_spend"],
            "marginal_roas": res["marginal_roas"],
        }
        return result_key, summary
    except Exception:
        logger.warning("Fast scenario evaluation failed for %s; falling back to queue", scenario_id, exc_info=True)
        return None


@router.post("/forecasts/{forecast_id}/scenarios", status_code=status.HTTP_202_ACCEPTED)
def create_scenario(
    forecast_id: str,
    payload: ScenarioCreateRequest,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Evaluate a what-if multiplier scenario immediately on the cached forecast model or enqueue on `interactive`."""
    fc = db.get(ForecastRun, forecast_id)
    if not fc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Forecast {forecast_id} not found.")
    if fc.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Forecast {forecast_id} is {fc.status}; scenarios can run once it has succeeded.",
        )
    run = db.get(Run, fc.run_id)
    multipliers = _normalise_multipliers(run, payload.multipliers)
    spend_overrides = _normalise_multipliers(run, payload.spend_overrides)

    scenario_id = f"sc_{uuid.uuid4().hex[:10]}"
    inputs_dict = {
        "multipliers": multipliers,
        "spend_overrides": spend_overrides,
        "horizon_days": payload.horizon_days,
        "unlock_controls": payload.unlock_controls,
        # Lets the interactive worker's cache tell a re-run's model apart from the previous one
        "cache_version": run.updated_at.isoformat() if run and run.updated_at else "",
    }

    fast_sc = _try_fast_scenario_evaluation(
        fc,
        run,
        multipliers,
        payload.horizon_days,
        payload.unlock_controls,
        scenario_id,
        storage,
        spend_overrides=spend_overrides,
    )
    if fast_sc is not None:
        result_key, summary = fast_sc
        sc = ScenarioRun(
            id=scenario_id,
            forecast_id=forecast_id,
            run_id=fc.run_id,
            name=payload.name.strip() or "What-If Scenario",
            inputs=inputs_dict,
            status="succeeded",
            result_path=result_key,
            summary=summary,
        )
        db.add(sc)
        db.commit()
        db.refresh(sc)
        return _serialize_scenario(sc, storage)

    sc = ScenarioRun(
        id=scenario_id,
        forecast_id=forecast_id,
        run_id=fc.run_id,
        name=payload.name.strip() or "What-If Scenario",
        inputs=inputs_dict,
        status="queued",
    )
    db.add(sc)
    db.commit()

    job_id = defer_task(
        run_scenario_job,
        queue="interactive",
        lock=f"interactive:{fc.run_id}",
        queueing_lock=f"scenario:{scenario_id}",
        scenario_id=scenario_id,
    )
    sc.job_id = job_id
    db.commit()
    db.refresh(sc)
    return _serialize_scenario(sc, storage)


@router.get("/scenarios/{scenario_id}")
def get_scenario(
    scenario_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Fetch a what-if scenario's status and full result payload."""
    sc = db.get(ScenarioRun, scenario_id)
    if not sc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scenario {scenario_id} not found.")
    return _serialize_scenario(sc, storage)
