"""FastAPI application entrypoint for MMM Studio."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mmm_studio import __version__
from mmm_studio.api.router import api_router
from mmm_studio.db.session import init_db
from mmm_studio.jobs.app import ensure_procrastinate_schema
from mmm_studio.settings import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
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
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    return app


app = create_app()
