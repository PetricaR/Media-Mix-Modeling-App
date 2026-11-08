"""`POST /runs/{run_id}/recommendation`: Gemini's recommended settings for the run's next start or re-run.

The prompt holds the run's workbook profile, the settings the form currently shows (unsaved edits included) and, when
the run or an earlier run on the same workbook has results, its analysis pages. Gemini's JSON answer is cached under
`recommendations/{sha256}.json` (prompt version + model + prompt), so asking again with the same settings and results
costs nothing. Gemini reviews every setting the form offers; each suggested change is checked against the settings'
types, bounds and the run-start rules before it is returned, and the coverage lists any active setting it skipped.
Nothing is stored on the run or started here.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mmm_studio.api.deps import client_ip, get_app_settings, get_app_storage, get_db
from mmm_studio.api.explain import take_slot
from mmm_studio.core.model_settings import MAX_SEED, RUN_PROFILES, settings_defaults
from mmm_studio.core.schema import DataRoleConfig, extract_role_config
from mmm_studio.db.models import Run
from mmm_studio.explain.context import GLOSSARY, unverified_numbers
from mmm_studio.explain.gemini import ExplainFailed, ExplainUnavailable, generate_json
from mmm_studio.explain.recommend import (
    EVIDENCE_SECTIONS,
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    Advice,
    Goal,
    RecommendationFailed,
    State,
    build_prompt,
    evidence_settings_text,
    parse_advice,
    review,
    section_text,
)
from mmm_studio.explain.run_facts import run_facts
from mmm_studio.settings import Settings
from mmm_studio.storage import StorageBackend

logger = logging.getLogger(__name__)

router = APIRouter(tags=["explain"])


class RecommendRequest(BaseModel):
    """What to optimise for, and the form's current (possibly unsaved) settings; left out = the run's stored ones."""

    goal: Goal = "balanced"
    profile: str | None = None
    skip_meridian: bool | None = None
    seed: int | None = Field(default=None, ge=0, le=MAX_SEED)
    role_config: DataRoleConfig | None = None


@dataclass
class _Context:
    prompt: str
    state: State
    modelling_days: int | None
    limits: dict[str, Any]
    evidence: dict[str, Any]


def _has_results(run_id: str, storage: StorageBackend) -> bool:
    return storage.exists(f"runs/{run_id}/sections/_index.json")


def same_workbook_runs(db: Session, run: Run) -> list[Run]:
    """Finished runs on the same workbook (same file hash), newest first."""
    if not run.sha256:
        return []
    return (
        db.query(Run)
        .filter(Run.sha256 == run.sha256, Run.id != run.id, Run.status == "succeeded")
        .order_by(Run.updated_at.desc())
        .all()
    )


def _results(evidence: Run, this_run: Run, storage: StorageBackend) -> tuple[str | None, list[str]]:
    """The evidence run's result pages as prompt text, and which pages were used."""
    pages: list[str] = []
    used: list[str] = []
    for sid, limit in EVIDENCE_SECTIONS:
        key = f"runs/{evidence.id}/sections/{sid}.json"
        try:
            if not storage.exists(key):
                continue
            pages.append(section_text(storage.read_json(key), limit))
            used.append(sid)
        except Exception:  # an unreadable page is left out, like a missing one
            logger.warning("Unreadable section %s", key)
    if not pages:
        return None, []
    whose = "this run" if evidence.id == this_run.id else f'an earlier run "{evidence.name}" on the same workbook'
    head = (
        f"Results of {whose} (status {evidence.status}). Settings that produced them: "
        + evidence_settings_text(evidence.config or {}, evidence.profile)
    )
    facts = run_facts(evidence, storage)
    text = head + ("\nRun facts:\n" + facts if facts else "") + "\n\n" + "\n\n".join(pages)
    return text, used


def _load(run_id: str, payload: RecommendRequest, storage: StorageBackend) -> _Context:
    """Everything the prompt needs, read in one short database session (closed before Gemini is called)."""
    sessions = get_db()
    try:
        db: Session = next(sessions)
        run = db.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")
        if run.status in ("queued", "running"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Run {run_id} is {run.status}; its settings are locked until it finishes or is cancelled.",
            )
        config = run.config or {}
        profile = payload.profile or run.profile
        if profile not in RUN_PROFILES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"profile must be one of {', '.join(RUN_PROFILES)}.")
        skip = payload.skip_meridian if payload.skip_meridian is not None else bool(config.get("skip_meridian", False))
        rc = payload.role_config or extract_role_config(config)
        seed = payload.seed if payload.seed is not None else int(config.get("seed", 0) or 0)
        state = State(profile=profile, include_meridian=not skip, rc=rc.model_dump(mode="json"), seed=seed)

        evidence: Run | None = run if _has_results(run.id, storage) else None
        if evidence is None:
            evidence = next((r for r in same_workbook_runs(db, run) if _has_results(r.id, storage)), None)
        results, used = _results(evidence, run, storage) if evidence is not None else (None, [])
        if not used:
            evidence = None

        profile_json = run.schema_json or {}
        modelling_days = profile_json.get("modelling_days")
        limits = settings_defaults()["limits"]
        prompt = build_prompt(payload.goal, state, limits, profile_json, results)
        info = {
            "kind": "profile_only" if evidence is None else ("this_run" if evidence.id == run.id else "same_workbook"),
            "run_id": evidence.id if evidence is not None else None,
            "run_name": evidence.name if evidence is not None else None,
            "sections": used,
        }
        return _Context(prompt, state, int(modelling_days) if modelling_days else None, limits, info)
    finally:
        sessions.close()  # runs the dependency's own cleanup, which closes the session


def cache_key(model: str, prompt: str) -> str:
    digest = hashlib.sha256()
    for part in (PROMPT_VERSION, model, SYSTEM_INSTRUCTION, prompt):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


@router.post("/runs/{run_id}/recommendation")
async def recommend_settings(
    run_id: str,
    request: Request,
    payload: RecommendRequest | None = None,
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Gemini's best value for every setting of this run, each change checked before it is returned (503 without Gemini)."""
    if not settings.explain_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini is not configured on this server.",
        )
    payload = payload or RecommendRequest()
    ctx = await run_in_threadpool(_load, run_id, payload, storage)
    key = f"recommendations/{cache_key(settings.gemini_model, ctx.prompt)}.json"

    record: dict[str, Any] | None = None
    if await run_in_threadpool(storage.exists, key):
        try:
            record = await run_in_threadpool(storage.read_json, key)
            advice = Advice.model_validate(record["advice"])
        except Exception:  # an unreadable cache entry is simply regenerated
            logger.warning("Unreadable recommendation cache entry %s", key)
            record = None
    cached = record is not None
    if record is None:
        take_slot(client_ip(request, settings), settings.explain_per_minute, "recommendations")
        try:
            text = await run_in_threadpool(generate_json, SYSTEM_INSTRUCTION, ctx.prompt, settings, Advice)
            advice = parse_advice(text)
        except ExplainUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except (ExplainFailed, RecommendationFailed) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        record = {
            "advice": advice.model_dump(),
            "model": settings.gemini_model,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            await run_in_threadpool(storage.write_json, key, record)
        except Exception:  # the answer is still useful without the cache
            logger.warning("Could not cache recommendation %s", key)

    checked = review(advice, ctx.state, ctx.modelling_days, ctx.limits)
    written = "\n".join([advice.summary, *(c["reason"] for c in checked.changes), *checked.tab_notes.values()])
    return {
        "summary": advice.summary,
        "changes": checked.changes,
        "kept": checked.kept,
        "rejected": checked.rejected,
        "coverage": checked.coverage,
        "tab_notes": checked.tab_notes,
        "goal": payload.goal,
        "evidence": ctx.evidence,
        "model": record.get("model", settings.gemini_model),
        "generated_at": record.get("generated_at"),
        "cached": cached,
        # Numbers Gemini wrote that the data, results and settings in its prompt do not contain (after rounding)
        "unverified_numbers": unverified_numbers(written, ctx.prompt + "\n" + GLOSSARY),
    }
