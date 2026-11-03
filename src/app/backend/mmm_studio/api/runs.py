"""FastAPI routes for Run lifecycle: upload (draft), confirm roles (patch), start, poll, cancel, activate, delete."""

from __future__ import annotations

import hashlib
import platform
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mmm_studio import __version__
from mmm_studio.api.deps import get_app_settings, get_app_storage, get_db, require_access_code
from mmm_studio.core.model_settings import MAX_SEED, RUN_PROFILES, settings_changes, settings_defaults
from mmm_studio.core.schema import DataRoleConfig, extract_role_config
from mmm_studio.core.workbook import WorkbookValidationError, profile_workbook
from mmm_studio.db.models import ForecastRun, Run, utcnow
from mmm_studio.jobs.app import (
    cancel_procrastinate_job,
    count_active_queue_jobs,
    defer_task,
    get_queue_position,
    sweep_stale_runs,
)
from mmm_studio.jobs.tasks import ensure_stage_rows, run_analysis_job, run_meridian_job
from mmm_studio.settings import Settings
from mmm_studio.storage import StorageBackend

router = APIRouter(prefix="/runs", tags=["runs"])


class RunPatchRequest(BaseModel):
    name: str | None = None
    profile: str | None = None
    # Validated as a DataRoleConfig (roles + model settings); an invalid value is a 422
    role_config: DataRoleConfig | None = None
    skip_meridian: bool | None = None
    seed: int | None = Field(default=None, ge=0, le=MAX_SEED)


class RunStartRequest(BaseModel):
    profile: str | None = None
    skip_meridian: bool = False
    role_config: DataRoleConfig | None = None
    seed: int = Field(default=0, ge=0, le=MAX_SEED)


def _capture_versions(settings: Settings) -> dict[str, str]:
    import numpy as np
    import pandas as pd

    return {
        "mmm_studio": __version__,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "git_sha": settings.git_sha,
    }


def _normalised_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return the stored config with its role config re-validated, so roles show what the pipeline will use."""
    config = dict(config or {})
    if isinstance(config.get("role_config"), dict):
        config["role_config"] = extract_role_config(config).model_dump(mode="json")
    return config


def _settings_changes(run: Run) -> list[dict[str, str]]:
    """The run's settings that differ from the notebook defaults (empty for a run on the defaults)."""
    config = run.config or {}
    if not isinstance(config.get("role_config"), dict):
        return []
    return settings_changes(extract_role_config(config), seed=int(config.get("seed", 0)), profile=run.profile)


def _store_role_config(run: Run, storage: StorageBackend, role_config: DataRoleConfig, cfg: dict[str, Any]) -> None:
    rc_dump = role_config.model_dump(mode="json")
    cfg["role_config"] = rc_dump
    schema_dict = dict(run.schema_json or {})
    schema_dict["role_config"] = rc_dump
    run.schema_json = schema_dict
    storage.write_json(f"runs/{run.id}/meta/profile.json", schema_dict)


def serialize_run(run: Run) -> dict[str, Any]:
    """Serialize a Run ORM instance (including ordered StageProgress rows and queue position)."""
    q_pos = get_queue_position(run.meridian_job_id, "meridian") if run.meridian_job_id else None
    stages_out = [
        {
            "key": st.key,
            "label": st.label,
            "queue": st.queue,
            "status": st.status,
            "order_idx": st.order_idx,
            "started_at": st.started_at.isoformat() if st.started_at else None,
            "finished_at": st.finished_at.isoformat() if st.finished_at else None,
            "error": st.error,
        }
        for st in sorted(run.stages, key=lambda x: x.order_idx)
    ]
    return {
        "id": run.id,
        "name": run.name,
        "filename": run.filename,
        "sha256": run.sha256,
        "profile": run.profile,
        "status": run.status,
        "is_active": run.is_active,
        "config": _normalised_config(run.config),
        "settings_changes": _settings_changes(run),
        "schema": run.schema_json or {},
        "summary": run.summary,
        "versions": run.versions or {},
        "analysis_job_id": run.analysis_job_id,
        "meridian_job_id": run.meridian_job_id,
        "queue_position": q_pos,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "stages": stages_out,
    }


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_access_code)])
async def create_run(
    file: UploadFile = File(...),
    name: str = Form("Case Study Run"),
    profile: str = Form("quick"),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Step 1 of the Import flow: upload an `.xlsx` workbook, infer data roles, and create a `draft` Run."""
    filename = file.filename or "workbook.xlsx"
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Excel (.xlsx) workbooks are supported.",
        )

    raw_bytes = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Workbook exceeds the {settings.max_upload_mb} MB upload limit.",
        )
    if len(raw_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded workbook is empty.",
        )

    try:
        _, data_profile = profile_workbook(raw_bytes, filename=filename)
    except WorkbookValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse Excel workbook: {exc}",
        ) from exc

    if profile not in RUN_PROFILES:
        profile = "quick"

    run_id = f"run_{uuid.uuid4().hex[:10]}"
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # Persist input workbook and inferred profile to storage
    storage.write_bytes(f"runs/{run_id}/input/workbook.xlsx", raw_bytes)
    profile_dict = data_profile.model_dump(mode="json")
    storage.write_json(f"runs/{run_id}/meta/profile.json", profile_dict)

    has_any = db.query(Run).first() is not None
    run = Run(
        id=run_id,
        name=name.strip() or "Case Study Run",
        filename=filename,
        sha256=sha256,
        profile=profile,
        status="draft",
        is_active=not has_any,
        schema_json=profile_dict,
        config={
            "role_config": data_profile.role_config.model_dump(mode="json"),
            "seed": 0,
            "skip_meridian": False,
        },
        versions=_capture_versions(settings),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return serialize_run(run)


@router.patch("/{run_id}", dependencies=[Depends(require_access_code)])
def patch_run(
    run_id: str,
    payload: RunPatchRequest,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Step 2 of the Import flow: update run name, sampling profile, or channel role assignments before starting."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    if payload.name is not None:
        run.name = payload.name.strip() or run.name
    if payload.profile is not None:
        if payload.profile not in RUN_PROFILES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"profile must be one of {', '.join(RUN_PROFILES)}.",
            )
        run.profile = payload.profile

    cfg = dict(run.config or {})
    if payload.role_config is not None:
        _store_role_config(run, storage, payload.role_config, cfg)
    if payload.skip_meridian is not None:
        cfg["skip_meridian"] = bool(payload.skip_meridian)
    if payload.seed is not None:
        cfg["seed"] = int(payload.seed)
    run.config = cfg
    run.updated_at = utcnow()
    db.commit()
    db.refresh(run)
    return serialize_run(run)


@router.post("/{run_id}/start", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_access_code)])
def start_run(
    run_id: str,
    payload: RunStartRequest | None = None,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Step 3 of the Import flow: enqueue `analysis` and `meridian` jobs for a draft Run."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    if run.status in ("queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run {run_id} is already {run.status}; cancel it before starting it again.",
        )

    cfg = dict(run.config or {})
    skip_meridian = bool(cfg.get("skip_meridian", False))

    if payload is not None:
        if payload.profile in RUN_PROFILES:
            run.profile = payload.profile
        skip_meridian = bool(payload.skip_meridian)
        cfg["skip_meridian"] = skip_meridian
        cfg["seed"] = int(payload.seed)
        if payload.role_config is not None:
            _store_role_config(run, storage, payload.role_config, cfg)

    if not skip_meridian:
        active_meridian = count_active_queue_jobs("meridian")
        if active_meridian >= settings.max_meridian_queued:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Meridian queue depth limit reached ({active_meridian}/{settings.max_meridian_queued} active/queued jobs). "
                    "Please wait for a slot to free up or cancel a queued run."
                ),
            )

    rerun = run.status != "draft"
    if rerun:
        # A re-run starts from a clean slate: results of the previous run (sections, data, models, reports) and the
        # example forecast built from them are removed; the uploaded workbook and its profile are kept.
        for sub in ("sections", "data", "models", "reports"):
            storage.delete_prefix(f"runs/{run_id}/{sub}")
        for meta in ("summary.json", "meridian_metrics.json"):
            storage.delete_prefix(f"runs/{run_id}/meta/{meta}")
        default_fc = db.get(ForecastRun, f"default_{run_id}")
        if default_fc is not None:
            db.delete(default_fc)
    run.config = cfg
    run.status = "queued"
    run.error = None
    run.summary = None
    run.heartbeat_at = utcnow()
    db.commit()

    ensure_stage_rows(run_id, skip_meridian=skip_meridian, reset=rerun)

    analysis_job_id = defer_task(
        run_analysis_job,
        queue="analysis",
        lock=f"analysis:{run_id}",
        queueing_lock=f"analysis:{run_id}",
        run_id=run_id,
        skip_meridian=skip_meridian,
    )
    meridian_job_id: int | None = None
    if not skip_meridian:
        meridian_job_id = defer_task(
            run_meridian_job,
            queue="meridian",
            lock="meridian_singleton",
            queueing_lock=f"meridian:{run_id}",
            run_id=run_id,
        )

    run.analysis_job_id = analysis_job_id
    run.meridian_job_id = meridian_job_id
    db.commit()
    db.refresh(run)
    return serialize_run(run)


@router.get("/settings/defaults")
def get_settings_defaults() -> dict[str, Any]:
    """Defaults (the notebooks' values), limits and fixed reference values for the run settings form."""
    return settings_defaults()


@router.get("")
def list_runs(db: Session = Depends(get_db)) -> dict[str, Any]:
    """List all runs ordered newest-first, sweeping any stale heartbeats."""
    sweep_stale_runs()
    runs = db.query(Run).order_by(Run.created_at.desc()).all()
    active_run = next((r for r in runs if r.is_active), runs[0] if runs else None)
    return {
        "runs": [serialize_run(r) for r in runs],
        "active_run_id": active_run.id if active_run else None,
    }


@router.get("/{run_id}")
def get_run(
    run_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Fetch a single run's metadata, per-stage progress, and Meridian queue position."""
    sweep_stale_runs()
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")
    out = serialize_run(run)
    summary = out.get("summary")
    # Runs finished before the summary stored the hold-out window labels get them from their Meridian metrics
    if isinstance(summary, dict) and "holdout_q4_2024" in summary and "holdout_label" not in summary:
        key = f"runs/{run_id}/meta/meridian_metrics.json"
        if storage.exists(key):
            metrics = storage.read_json(key)
            out["summary"] = {**summary, "holdout_label": metrics.get("holdout_label"), "forward_label": metrics.get("forward_label")}
    return out


@router.post("/{run_id}/cancel", dependencies=[Depends(require_access_code)])
def cancel_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Cancel a queued or running Run and abort its Procrastinate jobs."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    if run.status not in ("queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run {run_id} is {run.status}; only queued or running runs can be cancelled.",
        )
    cancel_procrastinate_job(run.analysis_job_id, abort=True)
    cancel_procrastinate_job(run.meridian_job_id, abort=True)

    run.status = "cancelled"
    run.updated_at = utcnow()
    for st in run.stages:
        if st.status in ("pending", "running"):
            st.status = "cancelled"
            st.finished_at = utcnow()
    db.commit()
    db.refresh(run)
    return serialize_run(run)


@router.post("/{run_id}/activate", dependencies=[Depends(require_access_code)])
def activate_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Set `run_id` as the active run across the workspace."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    db.query(Run).update({Run.is_active: False})
    run.is_active = True
    run.updated_at = utcnow()
    db.commit()
    db.refresh(run)
    return serialize_run(run)


@router.delete("/{run_id}", dependencies=[Depends(require_access_code)])
def delete_run(
    run_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Cancel any running jobs, remove stored artifacts, and delete the Run record."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    cancel_procrastinate_job(run.analysis_job_id, abort=True)
    cancel_procrastinate_job(run.meridian_job_id, abort=True)
    storage.delete_prefix(f"runs/{run_id}")

    was_active = run.is_active
    db.delete(run)
    db.commit()

    if was_active:
        newest = db.query(Run).order_by(Run.created_at.desc()).first()
        if newest:
            newest.is_active = True
            db.commit()

    return {"deleted": run_id}
