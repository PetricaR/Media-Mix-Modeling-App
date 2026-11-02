"""FastAPI routes for budget-plan forecasts (`meridian` queue) and interactive what-if scenarios (`interactive` queue)."""

from __future__ import annotations

import io
import uuid
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mmm_studio.api.deps import get_app_settings, get_app_storage, get_db, require_access_code
from mmm_studio.core.meridian.forecast import (
    PlanValidationError,
    build_example_plan,
    scenario_payload,
    validate_plan,
)
from mmm_studio.core.meridian.spec import MeridianSpecConfig
from mmm_studio.core.schema import extract_role_config
from mmm_studio.db.models import ForecastRun, Run, ScenarioRun
from mmm_studio.jobs.app import count_active_queue_jobs, defer_task, get_queue_position, sweep_stale_runs
from mmm_studio.jobs.tasks import default_forecast_keys, run_forecast_job, run_scenario_job
from mmm_studio.pipeline.context import model_panel_key
from mmm_studio.settings import Settings
from mmm_studio.storage import StorageBackend

router = APIRouter(tags=["forecasts"])


class ScenarioCreateRequest(BaseModel):
    name: str = "What-If Scenario"
    multipliers: dict[str, float] = Field(default_factory=dict)
    horizon_days: int = Field(default=42, ge=1, le=90)


def _paid_channels_for_run(run: Run | None, wide_df: pd.DataFrame) -> list[str]:
    cfg_dict = (run.config if run else None) or {}
    cfg_obj = extract_role_config(cfg_dict)
    spec_cfg = MeridianSpecConfig.from_role_config(cfg_obj, index=wide_df.index)
    return spec_cfg.paid_slugs


def _load_wide_df(storage: StorageBackend, run_id: str) -> pd.DataFrame:
    wide_key = model_panel_key(storage, run_id)
    if wide_key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The daily modelling panel is not ready yet for run {run_id}. Wait for ingestion to complete.",
        )
    return pd.read_parquet(io.BytesIO(storage.read_bytes(wide_key)))


def _ensure_default_forecast(db: Session, storage: StorageBackend, run_id: str) -> ForecastRun | None:
    """Expose the run's example-plan forecast (stage s14) as the `default_{run_id}` ForecastRun once it exists.

    Nothing is created before the Meridian forecast has run, so the page never shows a placeholder as a result.
    A row created by an older version (without `source = pipeline`) is rebuilt from the real data.
    """
    default_id = f"default_{run_id}"
    fc = db.get(ForecastRun, default_id)
    if fc is not None and (fc.summary or {}).get("source") == "pipeline":
        return fc

    daily_keys, plan_keys = default_forecast_keys(run_id)
    daily_key = next((k for k in daily_keys if storage.exists(k)), None)
    plan_key = next((k for k in plan_keys if storage.exists(k)), None)
    if daily_key is None or plan_key is None:
        if fc is not None:
            db.delete(fc)
            db.commit()
        return None

    base_daily = pd.read_parquet(io.BytesIO(storage.read_bytes(daily_key)))
    base_plan = pd.read_parquet(io.BytesIO(storage.read_bytes(plan_key)))
    res_payload = scenario_payload(base_daily, base_plan, base_daily, base_plan, "Meridian refit (pipeline example plan)")
    result_key = f"runs/{run_id}/forecasts/{default_id}/result.json"
    storage.write_json(result_key, res_payload)
    summary = {
        "source": "pipeline",
        "horizon_days": len(base_plan),
        "start_date": base_plan.index[0].strftime("%Y-%m-%d"),
        "end_date": base_plan.index[-1].strftime("%Y-%m-%d"),
        "total_forecast_sales": float(base_daily["forecast"].sum()),
        "total_planned_spend": float(base_plan.to_numpy().sum()),
    }
    name = f"Example plan {summary['start_date']} → {summary['end_date']} ({len(base_plan)} days)"
    if fc is None:
        fc = ForecastRun(id=default_id, run_id=run_id, name=name, status="succeeded", plan_path=plan_key, result_path=result_key, summary=summary)
        db.add(fc)
    else:
        fc.name, fc.status, fc.plan_path, fc.result_path, fc.summary, fc.error = name, "succeeded", plan_key, result_key, summary, None
    db.commit()
    db.refresh(fc)
    return fc


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
        out["result"] = storage.read_json(fc.result_path)
    return out


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
    paid_channels = _paid_channels_for_run(run, wide_df)
    plan_df = build_example_plan(wide_df, days=days, paid_channels=paid_channels)
    plan_df = plan_df.round(2)
    csv_bytes = plan_df.reset_index().to_csv(index=False).encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="plan_template_{days}d.csv"'},
    )


@router.get("/runs/{run_id}/forecasts")
def list_forecasts(
    run_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """List all budget-plan forecasts for a Run (including the default 42-day plan if ready)."""
    sweep_stale_runs()
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    _ensure_default_forecast(db, storage, run_id)
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


@router.post(
    "/runs/{run_id}/forecasts",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_access_code)],
)
async def create_forecast(
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

    wide_df = _load_wide_df(storage, run_id)
    last_history_date = pd.Timestamp(wide_df.index.max())
    paid_channels = _paid_channels_for_run(run, wide_df)

    raw_bytes = await file.read()
    try:
        raw_df = pd.read_csv(io.BytesIO(raw_bytes))
        clean_df = validate_plan(
            raw_df,
            last_history_date=last_history_date,
            paid_channels=paid_channels,
        )
    except PlanValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid CSV file: {exc}") from exc

    active_meridian = count_active_queue_jobs("meridian")
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
    sweep_stale_runs()
    if forecast_id.startswith("default_"):
        run_id = forecast_id[len("default_") :]
        _ensure_default_forecast(db, storage, run_id)

    fc = db.get(ForecastRun, forecast_id)
    if not fc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Forecast {forecast_id} not found.")
    return _serialize_forecast(fc, storage, include_result=True)


@router.post(
    "/forecasts/{forecast_id}/scenarios",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_access_code)],
)
def create_scenario(
    forecast_id: str,
    payload: ScenarioCreateRequest,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Enqueue an interactive what-if multiplier scenario on the `interactive` queue."""
    if forecast_id.startswith("default_"):
        run_id = forecast_id[len("default_") :]
        _ensure_default_forecast(db, storage, run_id)

    fc = db.get(ForecastRun, forecast_id)
    if not fc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Forecast {forecast_id} not found.")
    if fc.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Forecast {forecast_id} is {fc.status}; scenarios can run once it has succeeded.",
        )
    run = db.get(Run, fc.run_id)

    scenario_id = f"sc_{uuid.uuid4().hex[:10]}"
    sc = ScenarioRun(
        id=scenario_id,
        forecast_id=forecast_id,
        run_id=fc.run_id,
        name=payload.name.strip() or "What-If Scenario",
        inputs={
            "multipliers": payload.multipliers,
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
