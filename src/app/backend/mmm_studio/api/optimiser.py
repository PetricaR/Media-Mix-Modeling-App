"""FastAPI routes for interactive budget optimisation (`interactive` queue)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mmm_studio.api.deps import get_app_storage, get_db, require_access_code
from mmm_studio.db.models import OptimisationRun, Run
from mmm_studio.jobs.app import defer_task
from mmm_studio.jobs.tasks import run_optimiser_job
from mmm_studio.storage import StorageBackend

router = APIRouter(tags=["optimisations"])


class OptimisationCreateRequest(BaseModel):
    total_budget: float | None = Field(default=None, gt=0)
    min_multiplier: float = Field(default=0.7, ge=0.0, le=2.0)
    max_multiplier: float = Field(default=1.3, ge=0.1, le=5.0)
    channel_bounds: dict[str, list[float]] | None = None
    include_catalog: bool = True


def _serialize_optimisation(opt: OptimisationRun, storage: StorageBackend) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": opt.id,
        "run_id": opt.run_id,
        "inputs": opt.inputs or {},
        "status": opt.status,
        "summary": opt.summary,
        "error": opt.error,
        "created_at": opt.created_at.isoformat() if opt.created_at else None,
        "updated_at": opt.updated_at.isoformat() if opt.updated_at else None,
    }
    if opt.result_path and storage.exists(opt.result_path):
        out["result"] = storage.read_json(opt.result_path)
    return out


@router.get("/runs/{run_id}/optimisations")
def list_optimisations(
    run_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """List all interactive budget optimisation runs for `run_id`."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    items = (
        db.query(OptimisationRun)
        .filter(OptimisationRun.run_id == run_id)
        .order_by(OptimisationRun.created_at.desc())
        .all()
    )
    return {"optimisations": [_serialize_optimisation(o, storage) for o in items]}


@router.post(
    "/runs/{run_id}/optimisations",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_access_code)],
)
def create_optimisation(
    run_id: str,
    payload: OptimisationCreateRequest,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Enqueue a constrained budget optimisation job on the `interactive` queue."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    opt_id = f"opt_{uuid.uuid4().hex[:10]}"
    opt = OptimisationRun(
        id=opt_id,
        run_id=run_id,
        inputs=payload.model_dump(mode="json"),
        status="queued",
    )
    db.add(opt)
    db.commit()

    job_id = defer_task(
        run_optimiser_job,
        queue="interactive",
        lock=f"interactive:{run_id}",
        queueing_lock=f"optimisation:{opt_id}",
        optimisation_id=opt_id,
    )
    opt.job_id = job_id
    db.commit()
    db.refresh(opt)
    return _serialize_optimisation(opt, storage)


@router.get("/optimisations/{optimisation_id}")
def get_optimisation(
    optimisation_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Fetch a budget optimisation run's status and full allocation result."""
    opt = db.get(OptimisationRun, optimisation_id)
    if not opt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Optimisation {optimisation_id} not found.")
    return _serialize_optimisation(opt, storage)
