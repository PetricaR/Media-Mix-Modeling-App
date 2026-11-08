"""FastAPI application entrypoint for MMM Studio."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from mmm_studio import __version__
from mmm_studio.api.router import api_router
from mmm_studio.db.session import init_db
from mmm_studio.jobs.app import ensure_procrastinate_schema
from mmm_studio.settings import get_settings

logger = logging.getLogger(__name__)

# Applied to every API response; report HTML adds its own Content-Security-Policy (see api/exports.py)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "SAMEORIGIN",
    "Cache-Control": "no-store",
}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    problems = get_settings().production_problems()
    if problems:
        # Fail closed: a production deployment without authentication would expose every upload
        raise RuntimeError("Refusing to start: " + " ".join(problems))
    init_db()
    ensure_procrastinate_schema()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="MMM Studio API",
        version=__version__,
        description="End-to-end Bayesian Marketing Mix Modelling & Decision Workbench API",
        lifespan=lifespan,
        # The interactive API docs are a development aid; production serves the API only
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.environment == "production" else "/openapi.json",
    )
    if settings.environment != "production" and settings.cors_origins:
        # The SPA is served from the same origin in production (nginx); CORS is only for the Vite dev server
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    app.include_router(api_router)
    return app


app = create_app()
