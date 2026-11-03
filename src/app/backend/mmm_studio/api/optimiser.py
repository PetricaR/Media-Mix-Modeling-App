"""FastAPI routes for interactive budget optimisation (`interactive` queue).

Requests are validated before they are queued: channel names (display names are accepted), multiplier bounds inside
the response curves' 0–2× range, and a total budget the bounds can actually reach. An impossible request is a 422 that
says what is possible; nothing is relaxed silently.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from mmm_studio.api.deps import get_app_storage, get_db
from mmm_studio.core.schema import extract_role_config, slugify_channel
from mmm_studio.db.models import OptimisationRun, Run
from mmm_studio.jobs.app import defer_task, sweep_stale_runs_throttled
from mmm_studio.jobs.tasks import load_optimiser_inputs, run_optimiser_job
from mmm_studio.storage import StorageBackend

router = APIRouter(tags=["optimisations"])

# The response curves are computed for 0–200 % of historical spend
MAX_MULTIPLIER = 2.0


class OptimisationCreateRequest(BaseModel):
    name: str = Field(default="Constrained Budget Optimisation", max_length=200)
    total_budget: float | None = Field(default=None, gt=0)
    min_multiplier: float = Field(default=0.7, ge=0.0, le=MAX_MULTIPLIER)
    max_multiplier: float = Field(default=1.3, ge=0.0, le=MAX_MULTIPLIER)
    # Per-channel (min, max) multipliers of historical spend, keyed by channel slug or display name
    channel_bounds: dict[str, tuple[float, float]] | None = None
    include_catalog: bool = True

    @field_validator("channel_bounds")
    @classmethod
    def _check_bounds(cls, value: dict[str, tuple[float, float]] | None) -> dict[str, tuple[float, float]] | None:
        for key, (lo, hi) in (value or {}).items():
            if not (math.isfinite(lo) and math.isfinite(hi)) or lo < 0 or hi > MAX_MULTIPLIER or lo > hi:
                raise ValueError(
                    f"Bounds for {key!r} must be multipliers with 0 ≤ min ≤ max ≤ {MAX_MULTIPLIER:g} (got {lo:g}, {hi:g})."
                )
        return value

    @model_validator(mode="after")
    def _check_range(self) -> OptimisationCreateRequest:
        if self.min_multiplier > self.max_multiplier:
            raise ValueError("min_multiplier must not exceed max_multiplier.")
        return self


def _serialize_optimisation(opt: OptimisationRun, storage: StorageBackend) -> dict[str, Any]:
    inputs = opt.inputs or {}
    out: dict[str, Any] = {
        "id": opt.id,
        "run_id": opt.run_id,
        "name": str(inputs.get("name") or "Constrained Budget Optimisation"),
        "inputs": inputs,
        "status": opt.status,
        "summary": opt.summary,
        "error": opt.error,
        "created_at": opt.created_at.isoformat() if opt.created_at else None,
        "updated_at": opt.updated_at.isoformat() if opt.updated_at else None,
    }
    if opt.result_path and storage.exists(opt.result_path):
        out["result"] = storage.read_json(opt.result_path)
    return out


def _normalise_bounds(run: Run, bounds: dict[str, tuple[float, float]] | None, media: list[str]) -> dict[str, list[float]]:
    """Key channel bounds by slug; unknown channels or channels that are not media of the model are a 422."""
    if not bounds:
        return {}
    cfg = extract_role_config(run.config or {})
    lookup = {slugify_channel(c.slug): c.slug for c in cfg.channels}
    lookup |= {slugify_channel(c.name): c.slug for c in cfg.channels}
    out: dict[str, list[float]] = {}
    for key, (lo, hi) in bounds.items():
        slug = lookup.get(slugify_channel(str(key)))
        if slug is None or slug not in media:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{key!r} is not a media channel of this run's model; use one of: {', '.join(media)}.",
            )
        out[slug] = [float(lo), float(hi)]
    return out


@router.get("/runs/{run_id}/optimisations")
def list_optimisations(
    run_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """List all interactive budget optimisation runs for `run_id`."""
    sweep_stale_runs_throttled()
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")

    items = (
        db.query(OptimisationRun)
        .filter(OptimisationRun.run_id == run_id)
        .order_by(OptimisationRun.created_at.desc())
        .all()
    )
    return {
        "run_id": run_id,
        "optimisations": [_serialize_optimisation(o, storage) for o in items],
    }


@router.post("/runs/{run_id}/optimisations", status_code=status.HTTP_202_ACCEPTED)
def create_optimisation(
    run_id: str,
    payload: OptimisationCreateRequest,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Validate a constrained budget optimisation and enqueue it on the `interactive` queue."""
    from mmm_studio.core.meridian.optimise import feasible_budget_range
    from mmm_studio.report.narrative import fmt_eur

    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")
    try:
        inputs_data = load_optimiser_inputs(storage, run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    roi_df = inputs_data["roi_df"]
    media = [str(c) for c in roi_df.drop(index="All Channels", errors="ignore").index if c in inputs_data["response_curves"]]
    bounds = _normalise_bounds(run, payload.channel_bounds, media)
    cfg = extract_role_config(run.config or {})
    print_slugs = [c.slug for c in cfg.channels if c.enabled and c.role == "print_event"]
    fixed = [] if payload.include_catalog else [s for s in print_slugs if s in media]
    lo, hi = feasible_budget_range(
        roi_df,
        inputs_data["response_curves"],
        channel_bounds={k: (v[0], v[1]) for k, v in bounds.items()} or None,
        default_bounds=(payload.min_multiplier, payload.max_multiplier),
        fixed_channels=fixed,
    )
    budget = payload.total_budget if payload.total_budget is not None else float(roi_df.drop(index="All Channels", errors="ignore")["spend"].sum())
    if not (lo - 1.0 <= budget <= hi + 1.0):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"A total budget of {fmt_eur(budget)} cannot be allocated within these limits: they allow between "
                f"{fmt_eur(lo)} and {fmt_eur(hi)}. Change the budget or widen the limits."
            ),
        )

    opt_id = f"opt_{uuid.uuid4().hex[:10]}"
    inputs = payload.model_dump(mode="json")
    inputs["channel_bounds"] = bounds or None
    inputs["print_slugs"] = print_slugs
    inputs["cache_version"] = run.updated_at.isoformat() if run.updated_at else ""
    opt = OptimisationRun(
        id=opt_id,
        run_id=run_id,
        inputs=inputs,
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
