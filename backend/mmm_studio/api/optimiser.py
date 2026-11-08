"""FastAPI routes for interactive budget optimisation (`interactive` queue).

Requests are validated before they are queued: channel names (display names are accepted), multiplier bounds inside
the response curves' 0–2× range, and a total budget the bounds can actually reach. An impossible request is a 422 that
says what is possible; nothing is relaxed silently.
"""

from __future__ import annotations

import io
import math
import uuid
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from mmm_studio.api.deps import get_app_storage, get_db
from mmm_studio.core.meridian.spec import channel_label, demand_following_slugs
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
    budget_mode: Literal["fixed", "target_roi", "target_mroi"] = "fixed"
    total_budget: float | None = Field(default=None, gt=0)
    target_value: float | None = Field(default=None, gt=0, le=100.0)
    min_multiplier: float = Field(default=0.7, ge=0.0, le=MAX_MULTIPLIER)
    max_multiplier: float = Field(default=1.3, ge=0.0, le=MAX_MULTIPLIER)
    # Per-channel (min, max) multipliers of historical spend, keyed by channel slug or display name
    channel_bounds: dict[str, tuple[float, float]] | None = None
    include_catalog: bool = True
    include_demand_following: bool = True

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


def _upper_bound_slugs(storage: StorageBackend, run: Run, media: list[str]) -> list[str]:
    """Demand-following media channels exactly as the run's pipeline used them (optimiser section, Drivers, Overview).

    The pipeline stores its list with the default optimisation; older runs fall back to the same definition
    (`demand_following_slugs`) on the run's frozen settings.
    """
    key = f"runs/{run.id}/data/default_optimisation.json"
    if storage.exists(key):
        stored = storage.read_json(key).get("upper_bound_channels")
        if stored is not None:
            return [str(s) for s in stored if str(s) in media]
    return [s for s in demand_following_slugs(extract_role_config(run.config or {})) if s in media]


@router.get("/runs/{run_id}/optimiser-context")
def get_optimiser_context(
    run_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Return the Meridian Scenario Planner context for `run_id`: date range, baseline ROI/mROI per channel,
    posterior response curves (mean + 90 % CI across 0–200 % spend), and both precomputed baseline scenarios
    (demand-following channels held fixed vs unconstrained ±30 %)."""
    from mmm_studio.core.meridian.optimise import (
        UPPER_BOUND_LIFT_SHARE,
        _native_allocations,
        _summary,
        lift_share,
        solve_constrained_optimisation,
    )

    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")
    try:
        inputs_data = load_optimiser_inputs(storage, run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    roi_df = inputs_data["roi_df"]
    rc_frames: dict[str, pd.DataFrame] = inputs_data["response_curves"]
    rc_raw = storage.read_json(f"runs/{run_id}/data/response_curves.json")

    profile_key = f"runs/{run_id}/meta/profile.json"
    profile_data = storage.read_json(profile_key) if storage.exists(profile_key) else (run.schema_json or {})
    start_date = str(profile_data.get("start_date") or profile_data.get("date_min") or "2023-01-01")
    end_date = str(profile_data.get("modelling_end_date") or profile_data.get("end_date") or "2025-01-01")
    modelling_days = int(profile_data.get("modelling_days") or profile_data.get("calendar_days") or 732)

    cfg = extract_role_config(run.config or {})
    ch_cfg_map = {c.slug: c for c in cfg.channels}
    media_rt = roi_df.drop(index="All Channels", errors="ignore")
    media_slugs = [str(c) for c in media_rt.index if str(c) in rc_frames]

    upper_bound_slugs = _upper_bound_slugs(storage, run, media_slugs)
    print_slugs = [slug for slug in media_slugs if (ch_cfg_map.get(slug) and ch_cfg_map[slug].role == "print_event")]

    channels_meta: list[dict[str, Any]] = []
    for slug in media_slugs:
        row = media_rt.loc[slug]
        ch_cfg = ch_cfg_map.get(slug)
        rc_df = rc_frames[slug].sort_values("spend")
        c_max = float(rc_df["spend"].max()) if len(rc_df) else float(row["spend"]) * MAX_MULTIPLIER
        p_below = row.get("p_roi_below_1")
        hist_sp = float(row["spend"])
        hist_r = float(row.get("roi", 1.0))
        hist_inc_ch = float(row.get("incremental_outcome", hist_sp * hist_r))
        channels_meta.append(
            {
                "channel": slug,
                "label": channel_label(slug),
                "role": ch_cfg.role if ch_cfg else ("print_event" if slug == "catalog" else "paid"),
                "demand_following": slug in upper_bound_slugs,
                "is_print": slug in print_slugs,
                "historical_spend": round(hist_sp, 0),
                "historical_incremental": round(hist_inc_ch, 0),
                "historical_roi": round(hist_r, 2),
                "historical_mroi": round(float(row.get("mroi", hist_r)), 2),
                "roi_lo": round(float(row.get("roi_lo", hist_r)), 2),
                "roi_hi": round(float(row.get("roi_hi", hist_r)), 2),
                "p_roi_below_1": round(float(p_below), 4) if p_below is not None and pd.notna(p_below) else None,
                "curve_max_spend": round(c_max, 0),
            }
        )

    hist_budget = float(sum(c["historical_spend"] for c in channels_meta))
    hist_inc = float(sum(c["historical_incremental"] for c in channels_meta))
    hist_roi = round(hist_inc / max(hist_budget, 1.0), 2)

    free_pq_key = f"runs/{run_id}/data/meridian_optimised_budget.parquet"
    fixed_pq_key = f"runs/{run_id}/data/meridian_optimised_budget_fixed.parquet"
    if storage.exists(free_pq_key):
        free_df = pd.read_parquet(io.BytesIO(storage.read_bytes(free_pq_key)))
        default_unconstrained = _summary(
            _native_allocations(free_df, response_curves=rc_frames, roi_df=roi_df),
            "Meridian BudgetOptimizer (same total budget, ±30 %)",
        )
    else:
        default_unconstrained = solve_constrained_optimisation(
            roi_df=roi_df,
            response_curves=rc_frames,
            default_bounds=(0.7, 1.3),
            include_catalog=True,
            print_slugs=print_slugs,
            include_demand_following=True,
            upper_bound_slugs=upper_bound_slugs,
        )
        default_unconstrained["method"] = "Response-curve optimiser (±30 % per channel)"

    if upper_bound_slugs and storage.exists(fixed_pq_key):
        fixed_df = pd.read_parquet(io.BytesIO(storage.read_bytes(fixed_pq_key)))
        default_constrained = _summary(
            _native_allocations(fixed_df, fixed=upper_bound_slugs, response_curves=rc_frames, roi_df=roi_df),
            f"Meridian BudgetOptimizer, {', '.join(channel_label(c) for c in upper_bound_slugs)} held at current spend",
            fixed=upper_bound_slugs,
        )
    elif upper_bound_slugs:
        default_constrained = solve_constrained_optimisation(
            roi_df=roi_df,
            response_curves=rc_frames,
            default_bounds=(0.7, 1.3),
            include_catalog=True,
            print_slugs=print_slugs,
            include_demand_following=False,
            upper_bound_slugs=upper_bound_slugs,
        )
        default_constrained["method"] = (
            f"Response-curve optimiser, {', '.join(channel_label(c) for c in upper_bound_slugs)} held at current spend"
        )
    else:
        default_constrained = dict(default_unconstrained)

    upper_share = lift_share(default_unconstrained, upper_bound_slugs) if upper_bound_slugs else 0.0
    conditional = bool(upper_bound_slugs) and upper_share > UPPER_BOUND_LIFT_SHARE
    for sc_obj in (default_constrained, default_unconstrained):
        sc_obj["unconstrained"] = {
            k: default_unconstrained[k] for k in ("lift_eur", "lift_pct", "method") if k in default_unconstrained
        }
        sc_obj["upper_bound_channels"] = upper_bound_slugs
        sc_obj["upper_bound_lift_share"] = round(upper_share, 3)
        sc_obj["conditional"] = conditional

    return {
        "run_id": run_id,
        "start_date": start_date,
        "end_date": end_date,
        "modelling_days": modelling_days,
        "historical_budget": round(hist_budget, 0),
        "historical_incremental": round(hist_inc, 0),
        "historical_roi": hist_roi,
        "upper_bound_channels": upper_bound_slugs,
        "print_channels": print_slugs,
        "channels": channels_meta,
        "response_curves": {slug: rc_raw.get(slug, []) for slug in media_slugs},
        "default_constrained": default_constrained,
        "default_unconstrained": default_unconstrained,
    }


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
    from mmm_studio.core.meridian.optimise import (
        OptimisationError,
        feasible_budget_range,
        solve_constrained_optimisation,
    )
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
    upper_bound_slugs = _upper_bound_slugs(storage, run, media)
    fixed: list[str] = []
    if not payload.include_catalog:
        fixed.extend(s for s in print_slugs if s in media)
    if not payload.include_demand_following:
        for s in upper_bound_slugs:
            if s not in fixed:
                fixed.append(s)

    if payload.budget_mode == "fixed":
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
    elif payload.budget_mode == "target_roi":
        try:
            solve_constrained_optimisation(
                roi_df=roi_df,
                response_curves=inputs_data["response_curves"],
                channel_bounds={k: (v[0], v[1]) for k, v in bounds.items()} or None,
                default_bounds=(payload.min_multiplier, payload.max_multiplier),
                include_catalog=payload.include_catalog,
                print_slugs=print_slugs,
                budget_mode="target_roi",
                target_value=payload.target_value,
                include_demand_following=payload.include_demand_following,
                upper_bound_slugs=upper_bound_slugs,
            )
        except OptimisationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    opt_id = f"opt_{uuid.uuid4().hex[:10]}"
    inputs = payload.model_dump(mode="json")
    inputs["channel_bounds"] = bounds or None
    inputs["print_slugs"] = print_slugs
    inputs["upper_bound_slugs"] = upper_bound_slugs
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
