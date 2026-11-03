"""FastAPI routes for budget-plan forecasts (`meridian` queue) and interactive what-if scenarios (`interactive` queue)."""

from __future__ import annotations

import io
import math
import uuid
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mmm_studio.api.deps import get_app_settings, get_app_storage, get_db
from mmm_studio.core.meridian.forecast import (
    PlanValidationError,
    build_example_plan,
    plan_arrays,
    validate_plan,
)
from mmm_studio.core.meridian.spec import MeridianSpecConfig, channel_label
from mmm_studio.core.schema import extract_role_config, slugify_channel
from mmm_studio.db.models import ForecastRun, Run, ScenarioRun
from mmm_studio.jobs.app import (
    QueueUnavailable,
    count_active_queue_jobs,
    defer_task,
    get_queue_position,
    sweep_stale_runs_throttled,
)
from mmm_studio.jobs.tasks import run_forecast_job, run_scenario_job
from mmm_studio.pipeline.context import model_panel_key
from mmm_studio.settings import Settings
from mmm_studio.storage import StorageBackend

router = APIRouter(tags=["forecasts"])
MAX_PLAN_BYTES = 1024 * 1024


class ScenarioCreateRequest(BaseModel):
    name: str = "What-If Scenario"
    multipliers: dict[str, float] = Field(default_factory=dict)
    horizon_days: int = Field(default=42, ge=1, le=90)


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


def _serialize_forecast(fc: ForecastRun, storage: StorageBackend, include_result: bool = False) -> dict[str, Any]:
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
    """Daily actual sales and paid-channel spend of the modelling panel, shown before a forecast for context."""
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
    }


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


@router.post("/runs/{run_id}/forecasts", status_code=status.HTTP_202_ACCEPTED)
def create_forecast(
    run_id: str,
    file: UploadFile = File(...),
    name: str = Form("Custom Budget Plan Forecast"),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Validate an uploaded future budget plan CSV and enqueue `run_forecast_job` on the `meridian` queue."""
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

    try:
        active_meridian = count_active_queue_jobs("meridian")
    except QueueUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The job queue is unavailable; try again shortly.") from exc
    if active_meridian >= settings.max_meridian_queued:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Meridian queue depth limit reached ({active_meridian}/{settings.max_meridian_queued}).",
        )

    forecast_id = f"fc_{uuid.uuid4().hex[:10]}"
    plan_key = f"runs/{run_id}/forecasts/{forecast_id}/plan.csv"
    clean_csv = clean_df.reset_index().to_csv(index=False).encode("utf-8")
    storage.write_bytes(plan_key, clean_csv)

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
    return _serialize_forecast(fc, storage, include_result=False)


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
    return _serialize_forecast(fc, storage, include_result=True)


@router.post("/forecasts/{forecast_id}/scenarios", status_code=status.HTTP_202_ACCEPTED)
def create_scenario(
    forecast_id: str,
    payload: ScenarioCreateRequest,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Enqueue an interactive what-if multiplier scenario on the `interactive` queue."""
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

    scenario_id = f"sc_{uuid.uuid4().hex[:10]}"
    sc = ScenarioRun(
        id=scenario_id,
        forecast_id=forecast_id,
        run_id=fc.run_id,
        name=payload.name.strip() or "What-If Scenario",
        inputs={
            "multipliers": multipliers,
            "horizon_days": payload.horizon_days,
            # Lets the interactive worker's cache tell a re-run's model apart from the previous one
            "cache_version": run.updated_at.isoformat() if run and run.updated_at else "",
        },
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
