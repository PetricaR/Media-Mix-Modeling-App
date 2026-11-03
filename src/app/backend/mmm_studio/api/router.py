"""Root `/api/v1` router: health, sign-in/out, and the authenticated runs, sections, forecasts, optimiser and artifacts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from mmm_studio import __version__
from mmm_studio.api import exports, forecasts, optimiser, runs, sections
from mmm_studio.api.deps import (
    SESSION_COOKIE,
    check_rate_limit,
    client_ip,
    code_matches,
    get_app_settings,
    is_authenticated,
    make_session_token,
    record_failure,
    require_auth,
)
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
        "auth_required": settings.auth_enabled,
        "max_meridian_queued": settings.max_meridian_queued,
    }


@api_router.get("/auth/session", tags=["system"])
def auth_session(request: Request, settings: Settings = Depends(get_app_settings)) -> dict[str, Any]:
    """Whether this browser is signed in (always true when no access code is configured)."""
    return {"auth_required": settings.auth_enabled, "authenticated": is_authenticated(request, settings)}


def _login(request: Request, response: Response, payload: AuthCheckPayload | None, settings: Settings) -> dict[str, Any]:
    expected = settings.app_access_code.strip()
    if not expected:
        return {"ok": True, "required": False, "protected": False}
    ip = client_ip(request, settings)
    check_rate_limit(ip)
    provided = request.headers.get("x-access-code") or (payload.access_code if payload else None)
    if not provided or not provided.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access code required.")
    if not code_matches(provided, expected):
        record_failure(ip)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid access code.")
    ttl = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        make_session_token(expected, ttl),
        max_age=ttl,
        httponly=True,
        secure=settings.environment == "production" or request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return {"ok": True, "required": True, "protected": True}


@api_router.post("/auth/login", tags=["system"])
def login(
    request: Request,
    response: Response,
    payload: AuthCheckPayload | None = None,
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Check the access code (rate-limited on failures) and start a signed session cookie."""
    return _login(request, response, payload, settings)


@api_router.post("/auth/check", tags=["system"])
def verify_access_code(
    request: Request,
    response: Response,
    payload: AuthCheckPayload | None = None,
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Alias of `/auth/login` for older clients."""
    return _login(request, response, payload, settings)


@api_router.post("/auth/logout", tags=["system"])
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


_protected = [Depends(require_auth)]
api_router.include_router(runs.router, dependencies=_protected)
api_router.include_router(sections.router, dependencies=_protected)
api_router.include_router(forecasts.router, dependencies=_protected)
api_router.include_router(optimiser.router, dependencies=_protected)
api_router.include_router(exports.router, dependencies=_protected)
