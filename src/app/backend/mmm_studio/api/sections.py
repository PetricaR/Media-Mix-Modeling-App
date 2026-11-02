"""FastAPI routes for fetching rendered Section JSON payloads."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from mmm_studio.api.deps import get_app_storage, get_db
from mmm_studio.db.models import Run
from mmm_studio.jobs.app import sweep_stale_runs
from mmm_studio.storage import StorageBackend

router = APIRouter(prefix="/runs/{run_id}/sections", tags=["sections"])


@router.get("")
def list_sections(
    run_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Return the index of available sections and per-stage progress for a Run."""
    sweep_stale_runs()
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    idx_key = f"runs/{run_id}/sections/_index.json"
    sections = storage.read_json(idx_key) if storage.exists(idx_key) else []
    stages = [
        {
            "key": st.key,
            "label": st.label,
            "queue": st.queue,
            "status": st.status,
            "error": st.error,
        }
        for st in sorted(run.stages, key=lambda x: x.order_idx)
    ]
    return {
        "run_id": run_id,
        "status": run.status,
        "run_status": run.status,
        "sections": sections,
        "stages": stages,
    }


@router.get("/{section_key}")
def get_section(
    run_id: str,
    section_key: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Return a single rendered Section payload (`runs/{run_id}/sections/{section_key}.json`)."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    key = f"runs/{run_id}/sections/{section_key}.json"
    if not storage.exists(key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Section '{section_key}' is not yet available for run {run_id}.",
        )
    return storage.read_json(key)
