"""FastAPI dependencies: database session, storage, access-code guard, and rate limiter."""

from __future__ import annotations

import hmac
import time
from collections import defaultdict
from collections.abc import Generator

from fastapi import Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from mmm_studio.db.session import get_db as _get_db_session
from mmm_studio.db.session import init_db
from mmm_studio.settings import Settings, get_settings
from mmm_studio.storage import StorageBackend, get_storage

# In-memory sliding window rate limiter for `/api/v1/auth/check` (5 attempts / 60s / IP)
_AUTH_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW_SEC = 60.0
_MAX_ATTEMPTS_PER_WINDOW = 5


def reset_rate_limiter() -> None:
    """Clear rate limiter state (used by tests)."""
    _AUTH_ATTEMPTS.clear()


def check_rate_limit(client_ip: str) -> None:
    """Enforce max 5 auth check attempts per minute per IP."""
    now = time.monotonic()
    history = [ts for ts in _AUTH_ATTEMPTS[client_ip] if (now - ts) < _RATE_WINDOW_SEC]
    if len(history) >= _MAX_ATTEMPTS_PER_WINDOW:
        _AUTH_ATTEMPTS[client_ip] = history
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many access-code verification attempts. Please wait 60 seconds.",
        )
    history.append(now)
    _AUTH_ATTEMPTS[client_ip] = history


def require_access_code(
    request: Request,
    x_access_code: str | None = Header(default=None, alias="X-Access-Code"),
) -> None:
    """Guard write/compute endpoints with `X-Access-Code` when `APP_ACCESS_CODE` is configured."""
    settings = get_settings()
    expected = (settings.app_access_code or "").strip()
    if not expected:
        return

    provided = (x_access_code or "").strip()
    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access code required (`X-Access-Code` header missing).",
        )
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid access code.",
        )


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session (ensuring tables exist)."""
    init_db()
    yield from _get_db_session()


def get_app_storage() -> StorageBackend:
    """Return the active StorageBackend."""
    return get_storage()


def get_app_settings() -> Settings:
    """Return current Settings."""
    return get_settings()
