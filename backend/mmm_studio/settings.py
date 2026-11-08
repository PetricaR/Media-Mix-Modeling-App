"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_ACCESS_CODE_LENGTH = 16


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
    # Fetched holidays (Google Calendar) and weather (Open-Meteo); default `<local data dir>/external`
    external_cache_dir: str = Field(
        default="",
        validation_alias=AliasChoices("MMM_EXTERNAL_CACHE_DIR", "EXTERNAL_CACHE_DIR"),
    )
    # Google API key for the Calendar API v3; without one the public holiday calendars are read from their iCal export
    google_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("MMM_GOOGLE_API_KEY", "GOOGLE_API_KEY"),
    )
    app_access_code: str = Field(
        default="",
        validation_alias=AliasChoices("APP_ACCESS_CODE", "MMM_APP_ACCESS_CODE"),
    )
    # "production" refuses to start without an access code of at least MIN_ACCESS_CODE_LENGTH characters
    environment: Literal["development", "production"] = Field(
        default="development",
        validation_alias=AliasChoices("ENVIRONMENT", "MMM_ENV", "MMM_ENVIRONMENT"),
    )
    # Proxies in front of the API that append to X-Forwarded-For (Cloud Run front end + nginx sidecar = 2); 0 trusts
    # only the socket's peer address
    trusted_proxy_hops: int = Field(
        default=0,
        ge=0,
        le=5,
        validation_alias=AliasChoices("TRUSTED_PROXY_HOPS", "MMM_TRUSTED_PROXY_HOPS"),
    )
    session_ttl_hours: int = Field(
        default=12,
        ge=1,
        le=24 * 30,
        validation_alias=AliasChoices("SESSION_TTL_HOURS", "MMM_SESSION_TTL_HOURS"),
    )
    meridian_fit_timeout_seconds: int = Field(
        default=3600,
        ge=60,
        validation_alias=AliasChoices("MERIDIAN_FIT_TIMEOUT_SECONDS", "MMM_MERIDIAN_FIT_TIMEOUT_SECONDS"),
    )
    # The stale-run sweep runs at most this often per API process (it was run on every poll)
    sweep_interval_seconds: int = Field(
        default=30,
        ge=0,
        validation_alias=AliasChoices("SWEEP_INTERVAL_SECONDS", "MMM_SWEEP_INTERVAL_SECONDS"),
    )

    gcp_project_id: str = Field(
        default="",
        validation_alias=AliasChoices("GCP_PROJECT_ID", "MMM_GCP_PROJECT", "MMM_GCP_PROJECT_ID"),
    )
    gcp_region: str = Field(
        default="europe-west3",
        validation_alias=AliasChoices("GCP_REGION", "MMM_GCP_REGION"),
    )
    wake_workers: Literal["none", "cloudrun", "false", "true", "0", "1", "no", "yes"] = Field(
        default="none",
        validation_alias=AliasChoices("WAKE_WORKERS", "MMM_WAKE_WORKERS"),
    )
    worker_analysis_job: str = Field(
        default="mmm-studio-worker-analysis",
        validation_alias=AliasChoices("WORKER_ANALYSIS_JOB", "MMM_WORKER_ANALYSIS_JOB"),
    )
    worker_meridian_job: str = Field(
        default="mmm-studio-worker-meridian",
        validation_alias=AliasChoices("WORKER_MERIDIAN_JOB", "MMM_WORKER_MERIDIAN_JOB"),
    )
    worker_interactive_job: str = Field(
        default="mmm-studio-worker-interactive",
        validation_alias=AliasChoices("WORKER_INTERACTIVE_JOB", "MMM_WORKER_INTERACTIVE_JOB"),
    )

    # Gemini explanations of charts and tables (`POST /explain`): an API key from Google AI Studio, or Vertex AI with
    # the service account's own credentials (`MMM_GEMINI_VERTEX=true`, project = `gcp_project_id`). Neither set = off.
    gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("MMM_GEMINI_API_KEY", "GEMINI_API_KEY"),
    )
    gemini_use_vertex: bool = Field(
        default=False,
        validation_alias=AliasChoices("MMM_GEMINI_VERTEX", "GOOGLE_GENAI_USE_VERTEXAI"),
    )
    # Vertex AI location of the model; "global" serves every current Gemini model
    gemini_location: str = Field(
        default="global",
        validation_alias=AliasChoices("MMM_GEMINI_LOCATION", "GOOGLE_CLOUD_LOCATION"),
    )
    # Gemini 4 Argon (GA 13 Oct 2026), Google's newest model; `gemini-3.5-flash` is the faster, cheaper choice
    gemini_model: str = Field(
        default="gemini-4-argon",
        validation_alias=AliasChoices("MMM_GEMINI_MODEL", "GEMINI_MODEL"),
    )
    # Below nginx's 120 s proxy timeout; Argon answers these short prompts in seconds with low thinking
    gemini_timeout_seconds: int = Field(
        default=90,
        ge=5,
        le=300,
        validation_alias=AliasChoices("MMM_GEMINI_TIMEOUT_SECONDS", "GEMINI_TIMEOUT_SECONDS"),
    )
    # A settings recommendation reviews every setting (about 50 decisions, ~50 s with Argon); below nginx's 120 s
    gemini_recommend_timeout_seconds: int = Field(
        default=115,
        ge=5,
        le=300,
        validation_alias=AliasChoices("MMM_GEMINI_RECOMMEND_TIMEOUT_SECONDS", "GEMINI_RECOMMEND_TIMEOUT_SECONDS"),
    )
    # New (uncached) explanations per client address and minute; cached ones are free
    explain_per_minute: int = Field(
        default=20,
        ge=1,
        validation_alias=AliasChoices("MMM_EXPLAIN_PER_MINUTE", "EXPLAIN_PER_MINUTE"),
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
    def auth_enabled(self) -> bool:
        return bool(self.app_access_code.strip())

    @property
    def explain_available(self) -> bool:
        """Whether Gemini explanations are configured (Vertex AI with a project, or an API key)."""
        if self.gemini_use_vertex:
            return bool(self.gcp_project_id.strip())
        return bool(self.gemini_api_key.strip())

    def production_problems(self) -> list[str]:
        """Why this configuration must not serve production traffic (empty when it may)."""
        if self.environment != "production":
            return []
        problems = []
        if len(self.app_access_code.strip()) < MIN_ACCESS_CODE_LENGTH:
            problems.append(f"MMM_APP_ACCESS_CODE must be set to at least {MIN_ACCESS_CODE_LENGTH} characters in production.")
        return problems

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
