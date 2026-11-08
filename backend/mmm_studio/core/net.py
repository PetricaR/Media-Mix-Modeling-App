"""HTTPS reads of the external data sources (Open-Meteo, Google Calendar) with a CA bundle that always verifies.

The python.org macOS build of Python ships OpenSSL without any CA certificates (until its "Install Certificates" script
is run), so every HTTPS request fails with CERTIFICATE_VERIFY_FAILED unless `SSL_CERT_FILE` happens to be set. The
context here starts from the default trust store (which honours `SSL_CERT_FILE` / `SSL_CERT_DIR`, e.g. a corporate proxy's
CA) and adds certifi's Mozilla bundle on top, so the same code verifies on a fresh Mac, in Docker and on Cloud Run.
"""

from __future__ import annotations

import json
import ssl
from functools import lru_cache
from http.client import HTTPResponse
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

__all__ = ["error_detail", "open_url", "ssl_context"]


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """The default verifying context plus certifi's CA bundle (when certifi is installed)."""
    ctx = ssl.create_default_context()
    try:
        import certifi
    except ImportError:  # pragma: no cover - certifi comes with requests/httpx
        return ctx
    ctx.load_verify_locations(cafile=certifi.where())
    return ctx


def open_url(url: str, timeout: float) -> HTTPResponse:
    """`urlopen` over HTTPS with `ssl_context()`; use as a context manager."""
    return urlopen(url, timeout=timeout, context=ssl_context())  # noqa: S310 - callers pass fixed https endpoints


def error_detail(exc: BaseException) -> str:
    """Why a request failed, in plain words: the HTTP status with the service's own message, or the network problem.

    Google APIs answer `{"error": {"message": …}}`, Open-Meteo `{"error": true, "reason": …}`. Neither echoes the
    request URL, so an API key in the query string never reaches the message.
    """
    if isinstance(exc, HTTPError):
        message = ""
        try:
            body = json.loads(exc.read().decode("utf-8", "replace") or "null")
        except (OSError, ValueError):
            body = None
        if isinstance(body, dict):
            error = body.get("error")
            message = str((error.get("message") if isinstance(error, dict) else None) or body.get("reason") or "")
        message = " ".join(message.split())[:300]
        return f"HTTP {exc.code}: {message}" if message else f"HTTP {exc.code} {exc.reason}"
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "the server's TLS certificate is not trusted here; behind a TLS-inspecting proxy, set SSL_CERT_FILE to its CA"
    if isinstance(reason, TimeoutError):
        return "no answer in time"
    return reason.__class__.__name__
