"""`POST /explain`: a Gemini explanation of one chart or table, cached by its content.

The answer is stored under `explanations/{sha256}.json` (model, prompt version and the full prompt are hashed), so a
second click, another viewer or a page reload costs nothing. New explanations are rate-limited per client address.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from mmm_studio.api.deps import client_ip, get_app_settings, get_app_storage, get_db
from mmm_studio.db.models import Run
from mmm_studio.explain.context import (
    GLOSSARY,
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    ExplainRequest,
    build_prompt,
    unverified_numbers,
)
from mmm_studio.explain.gemini import ExplainFailed, ExplainUnavailable, generate_explanation
from mmm_studio.explain.run_facts import run_facts
from mmm_studio.settings import Settings
from mmm_studio.storage import StorageBackend

logger = logging.getLogger(__name__)

router = APIRouter(tags=["explain"])

MAX_BODY_BYTES = 3_000_000
_WINDOW_SEC = 60.0
_MAX_TOTAL_FACTOR = 6  # all clients together: six times one client's allowance
_CALLS: dict[str, deque[float]] = defaultdict(deque)
_ALL_CALLS: deque[float] = deque()
_LOCK = threading.Lock()


def reset_explain_limiter() -> None:
    """Clear the rate limiter (used by tests)."""
    with _LOCK:
        _CALLS.clear()
        _ALL_CALLS.clear()


def take_slot(ip: str, per_minute: int, what: str = "explanations") -> None:
    """Count one new Gemini call for `ip` (429 when it or all clients together are over the per-minute limit)."""
    now = time.monotonic()
    with _LOCK:
        for q in (_CALLS[ip], _ALL_CALLS):
            while q and now - q[0] >= _WINDOW_SEC:
                q.popleft()
        if len(_CALLS[ip]) >= per_minute or len(_ALL_CALLS) >= per_minute * _MAX_TOTAL_FACTOR:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many new {what} in the last minute. Please wait a moment and try again.",
            )
        _CALLS[ip].append(now)
        _ALL_CALLS.append(now)
        if len(_CALLS) > 10_000:
            for key in [k for k, q in _CALLS.items() if not q]:
                _CALLS.pop(key, None)


def cache_key(model: str, prompt: str) -> str:
    digest = hashlib.sha256()
    for part in (PROMPT_VERSION, model, SYSTEM_INSTRUCTION, prompt):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _check_size(request: Request) -> None:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="This chart or table is too large to explain.")


def _run_facts(run_id: str, storage: StorageBackend) -> str:
    """The run's facts for the prompt (404 for an unknown run). The session is opened only when a run is named."""
    sessions = get_db()
    try:
        db: Session = next(sessions)
        run = db.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found.")
        return run_facts(run, storage)
    finally:
        sessions.close()  # runs the dependency's own cleanup, which closes the session


@router.post("/explain", dependencies=[Depends(_check_size)])
async def explain(
    payload: ExplainRequest,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_app_storage),
) -> dict[str, Any]:
    """Explain the chart or table in `payload` with Gemini (503 when Gemini is not configured)."""
    if not settings.explain_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini explanations are not configured on this server.",
        )
    facts = await run_in_threadpool(_run_facts, payload.run_id, storage) if payload.run_id else None
    prompt = build_prompt(payload, facts)
    key = f"explanations/{cache_key(settings.gemini_model, prompt)}.json"
    if await run_in_threadpool(storage.exists, key):
        try:
            cached = await run_in_threadpool(storage.read_json, key)
            return {**cached, "cached": True}
        except Exception:  # an unreadable cache entry is simply regenerated
            logger.warning("Unreadable explanation cache entry %s", key)

    take_slot(client_ip(request, settings), settings.explain_per_minute)
    try:
        text = await run_in_threadpool(generate_explanation, SYSTEM_INSTRUCTION, prompt, settings)
    except ExplainUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ExplainFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    record = {
        "explanation": text,
        "model": settings.gemini_model,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        # Numbers Gemini wrote that the chart, table or page text does not contain (after rounding)
        "unverified_numbers": unverified_numbers(text, prompt + "\n" + GLOSSARY),
    }
    try:
        await run_in_threadpool(storage.write_json, key, record)
    except Exception:  # the answer is still useful without the cache
        logger.warning("Could not cache explanation %s", key)
    return {**record, "cached": False}
