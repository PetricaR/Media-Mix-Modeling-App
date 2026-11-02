"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for MMM Studio API and workers."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql:///mmm_studio?host=/tmp&port=5433",
        validation_alias=AliasChoices("DATABASE_URL", "MMM_DATABASE_URL"),
    )
    storage_backend: Literal["local", "gcs"] = Field(
        default="local",
        validation_alias=AliasChoices("STORAGE_BACKEND", "MMM_STORAGE_BACKEND"),
    )
    storage_local_dir: str = Field(
        default=str(Path(__file__).resolve().parents[2] / ".app_data"),
        validation_alias=AliasChoices("STORAGE_LOCAL_DIR", "MMM_LOCAL_DATA_DIR", "MMM_STORAGE_LOCAL_DIR"),
    )
    gcs_bucket: str = Field(
        default="",
        validation_alias=AliasChoices("GCS_BUCKET", "MMM_GCS_BUCKET"),
    )
    app_access_code: str = Field(
        default="",
        validation_alias=AliasChoices("APP_ACCESS_CODE", "MMM_APP_ACCESS_CODE"),
    )

    gcp_project_id: str = Field(
        default="",
        validation_alias=AliasChoices("GCP_PROJECT_ID", "MMM_GCP_PROJECT", "MMM_GCP_PROJECT_ID"),
    )
    gcp_region: str = Field(
        default="europe-west3",
        validation_alias=AliasChoices("GCP_REGION", "MMM_GCP_REGION"),
    )
    wake_workers: Literal["none", "cloudrun", "false", "true"] = Field(
        default="none",
        validation_alias=AliasChoices("WAKE_WORKERS", "MMM_WAKE_WORKERS"),
    )
    worker_analysis_job: str = Field(
        default="mmm-worker-analysis",
        validation_alias=AliasChoices("WORKER_ANALYSIS_JOB", "MMM_WORKER_ANALYSIS_JOB"),
    )
    worker_meridian_job: str = Field(
        default="mmm-worker-meridian",
        validation_alias=AliasChoices("WORKER_MERIDIAN_JOB", "MMM_WORKER_MERIDIAN_JOB"),
    )
    worker_interactive_job: str = Field(
        default="mmm-worker-interactive",
        validation_alias=AliasChoices("WORKER_INTERACTIVE_JOB", "MMM_WORKER_INTERACTIVE_JOB"),
    )

    max_queued_jobs: int = Field(
        default=10,
        validation_alias=AliasChoices("MAX_QUEUED_JOBS", "MMM_MAX_QUEUED_JOBS"),
    )
    max_meridian_queued: int = Field(
        default=3,
        validation_alias=AliasChoices("MAX_MERIDIAN_QUEUED", "MMM_MAX_MERIDIAN_QUEUED"),
    )
    max_upload_mb: int = Field(
        default=10,
        validation_alias=AliasChoices("MAX_UPLOAD_MB", "MMM_MAX_UPLOAD_MB"),
    )
    stale_heartbeat_seconds: int = Field(
        default=120,
        validation_alias=AliasChoices("STALE_HEARTBEAT_SECONDS", "MMM_STALE_HEARTBEAT_SECONDS"),
    )
    git_sha: str = Field(
        default="local",
        validation_alias=AliasChoices("GIT_SHA", "MMM_GIT_SHA"),
    )
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, v: object) -> list[str]:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json

                return list(json.loads(v))
            return [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            return [str(x) for x in v]
        return ["http://localhost:5173"]

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        return url

    @property
    def psycopg_conninfo(self) -> str:
        url = self.database_url
        if url.startswith("postgresql+psycopg://"):
            return "postgresql://" + url[len("postgresql+psycopg://") :]
        if url.startswith("postgres://"):
            return "postgresql://" + url[len("postgres://") :]
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
