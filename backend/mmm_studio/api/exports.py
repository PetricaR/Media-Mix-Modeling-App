"""FastAPI routes for listing and streaming run artifacts (Meridian HTML reports, Parquet tables, model binaries)."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from mmm_studio.api.deps import get_app_storage, get_db
from mmm_studio.db.models import Run
from mmm_studio.storage import StorageBackend

router = APIRouter(prefix="/runs/{run_id}/artifacts", tags=["artifacts"])

_MIME_OVERRIDES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".csv": "text/csv; charset=utf-8",
    ".parquet": "application/octet-stream",
    ".binpb": "application/octet-stream",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".md": "text/markdown; charset=utf-8",
}


def _categorise_artifact(rel_path: str) -> str:
    if rel_path.startswith("reports/"):
        return "report"
    if rel_path.startswith("data/"):
        return "data"
    if rel_path.startswith("models/"):
        return "model"
    if rel_path.startswith("sections/"):
        return "section"
    if rel_path.startswith("input/"):
        return "input"
    return "meta"


@router.get("")
def list_artifacts(
    run_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """List all downloadable artifacts for a Run."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    prefix = f"runs/{run_id}/"
    keys = storage.list_keys(prefix)
    items = []
    for k in keys:
        rel = k[len(prefix) :] if k.startswith(prefix) else k
        if rel.endswith((".tmp", ".wide_input.parquet", ".fit_error.txt")):
            continue
        items.append(
            {
                "path": rel,
                "category": _categorise_artifact(rel),
                "filename": Path(rel).name,
                "url": f"/api/v1/runs/{run_id}/artifacts/{rel}",
            }
        )
    return {"run_id": run_id, "artifacts": items}


@router.get("/{artifact_path:path}")
def get_artifact(
    run_id: str,
    artifact_path: str,
    download: bool = Query(default=False),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> Response:
    """Stream a single artifact file (HTML report, Parquet table, model binary, etc.)."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    clean_rel = artifact_path.lstrip("/")
    if ".." in Path(clean_rel).parts:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact path.")

    key = f"runs/{run_id}/{clean_rel}"
    if not storage.exists(key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact '{clean_rel}' not found for run {run_id}.",
        )

    raw_bytes = storage.read_bytes(key)
    suffix = Path(clean_rel).suffix.lower()
    if suffix == ".html" and clean_rel.startswith("reports/"):
        # Reports written before the polish step existed are polished on the way out (a no-op for newer ones)
        from mmm_studio.report.meridian_html import polish_meridian_report

        raw_bytes = polish_meridian_report(raw_bytes.decode("utf-8")).encode("utf-8")
    media_type = _MIME_OVERRIDES.get(suffix) or mimetypes.guess_type(clean_rel)[0] or "application/octet-stream"

    headers: dict[str, str] = {}
    if suffix == ".html" and not download:
        headers["Content-Security-Policy"] = (
            "default-src 'self' https: data: blob: 'unsafe-inline' 'unsafe-eval';"
        )
    elif download or suffix in (".parquet", ".binpb", ".xlsx", ".csv"):
        filename = Path(clean_rel).name
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    return Response(content=raw_bytes, media_type=media_type, headers=headers)
