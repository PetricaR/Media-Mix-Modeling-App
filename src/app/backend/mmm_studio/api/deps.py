"""FastAPI dependencies: database session, storage, authentication and the failed-attempt rate limiter.

When an access code is configured, every `/api/v1` route except `/health` and the sign-in routes requires either the
`X-Access-Code` header (API clients) or the signed, HttpOnly session cookie set by `POST /auth/login` (the browser,
including links and report frames that cannot send headers).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from collections.abc import Generator

from fastapi import Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from mmm_studio.db.session import get_db as _get_db_session
from mmm_studio.db.session import init_db
from mmm_studio.settings import Settings, get_settings
from mmm_studio.storage import StorageBackend, get_storage

SESSION_COOKIE = "mmm_session"

# Failed sign-in attempts: 5 per minute per client address, and 30 per minute in total (a spoofed address cannot get
# around the global cap)
_RATE_WINDOW_SEC = 60.0
_MAX_FAILURES_PER_CLIENT = 5
_MAX_FAILURES_TOTAL = 30
_MAX_TRACKED_CLIENTS = 10_000
_FAILURES: dict[str, deque[float]] = defaultdict(deque)
_ALL_FAILURES: deque[float] = deque()
_LOCK = threading.Lock()


def reset_rate_limiter() -> None:
    """Clear rate limiter state (used by tests)."""
    with _LOCK:
        _FAILURES.clear()
        _ALL_FAILURES.clear()


def client_ip(request: Request, settings: Settings | None = None) -> str:
    """The caller's address: the `X-Forwarded-For` entry added by the outermost trusted proxy, else the socket peer."""
    hops = (settings or get_settings()).trusted_proxy_hops
    forwarded = request.headers.get("x-forwarded-for", "")
    if hops > 0 and forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-hops] if len(parts) >= hops else parts[0]
    return request.client.host if request.client else "unknown"


def _prune(now: float) -> None:
    while _ALL_FAILURES and now - _ALL_FAILURES[0] >= _RATE_WINDOW_SEC:
        _ALL_FAILURES.popleft()
    if len(_FAILURES) > _MAX_TRACKED_CLIENTS:
        for ip in [ip for ip, q in _FAILURES.items() if not q or now - q[-1] >= _RATE_WINDOW_SEC]:
            _FAILURES.pop(ip, None)


def check_rate_limit(ip: str) -> None:
    """Refuse further attempts from `ip` (or from anyone) after too many recent failures."""
    now = time.monotonic()
    with _LOCK:
        _prune(now)
        history = _FAILURES[ip]
        while history and now - history[0] >= _RATE_WINDOW_SEC:
            history.popleft()
        if len(history) >= _MAX_FAILURES_PER_CLIENT or len(_ALL_FAILURES) >= _MAX_FAILURES_TOTAL:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed access-code attempts. Please wait a minute and try again.",
            )


def record_failure(ip: str) -> None:
    now = time.monotonic()
    with _LOCK:
        _FAILURES[ip].append(now)
        _ALL_FAILURES.append(now)


def _session_key(code: str) -> bytes:
    return hashlib.sha256(f"mmm-studio-session|{code}".encode()).digest()


def make_session_token(code: str, ttl_seconds: int, now: float | None = None) -> str:
    """`<expiry>.<signature>`, signed with a key derived from the access code (changing the code ends every session)."""
    expiry = str(int((now if now is not None else time.time()) + ttl_seconds))
    sig = hmac.new(_session_key(code), expiry.encode(), hashlib.sha256).digest()
    return f"{expiry}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def valid_session_token(token: str | None, code: str, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expiry, sig = token.split(".", 1)
    if not expiry.isdigit() or int(expiry) < (now if now is not None else time.time()):
        return False
    expected = base64.urlsafe_b64encode(hmac.new(_session_key(code), expiry.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return hmac.compare_digest(sig, expected)


def code_matches(provided: str | None, expected: str) -> bool:
    return bool(provided) and hmac.compare_digest((provided or "").strip().encode(), expected.encode())


def is_authenticated(request: Request, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    expected = settings.app_access_code.strip()
    if not expected:
        return True
    if code_matches(request.headers.get("x-access-code"), expected):
        return True
    return valid_session_token(request.cookies.get(SESSION_COOKIE), expected)


def require_auth(
    request: Request,
    x_access_code: str | None = Header(default=None, alias="X-Access-Code"),
) -> None:
    """Guard a route: a valid session cookie or `X-Access-Code` header is needed whenever an access code is set."""
    settings = get_settings()
    expected = settings.app_access_code.strip()
    if not expected:
        return
    if valid_session_token(request.cookies.get(SESSION_COOKIE), expected):
        return
    ip = client_ip(request, settings)
    if x_access_code is None or not x_access_code.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in with the studio access code.")
    check_rate_limit(ip)
    if not code_matches(x_access_code, expected):
        record_failure(ip)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid access code.")


# Kept for routes and tests that still name the old guard; every route is now guarded by `require_auth`
require_access_code = require_auth


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
