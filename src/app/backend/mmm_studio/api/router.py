"""Root `/api/v1` router combining health, auth check, runs, sections, forecasts, optimiser, and artifacts."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from mmm_studio import __version__
from mmm_studio.api import exports, forecasts, optimiser, runs, sections
from mmm_studio.api.deps import check_rate_limit, get_app_settings
from mmm_studio.settings import Settings

api_router = APIRouter(prefix="/api/v1")


class AuthCheckPayload(BaseModel):
    access_code: str | None = None


@api_router.get("/health", tags=["system"])
def health_check(settings: Settings = Depends(get_app_settings)) -> dict[str, Any]:
    """Liveness and configuration probe."""
    return {
        "status": "ok",
        "version": __version__,
        "git_sha": settings.git_sha,
        "auth_required": bool((settings.app_access_code or "").strip()),
        "max_meridian_queued": settings.max_meridian_queued,
    }


@api_router.post("/auth/check", tags=["system"])
def verify_access_code(
    request: Request,
    payload: AuthCheckPayload | None = None,
    x_access_code: str | None = Header(default=None, alias="X-Access-Code"),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Rate-limited endpoint (5 attempts / minute / IP) to verify the write/compute access code."""
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(client_ip)

    expected = (settings.app_access_code or "").strip()
    if not expected:
        return {"ok": True, "required": False}

    provided = (x_access_code or (payload.access_code if payload else "") or "").strip()
    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access code required.",
        )
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid access code.",
        )
    return {"ok": True, "required": True}


api_router.include_router(runs.router)
api_router.include_router(sections.router)
api_router.include_router(forecasts.router)
api_router.include_router(optimiser.router)
api_router.include_router(exports.router)
