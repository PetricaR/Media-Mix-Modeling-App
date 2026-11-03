"""SQLAlchemy 2.0 ORM models for MMM Studio metadata (runs, stages, forecasts, scenarios, optimisations)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Run(Base):
    """Represents a single MMM Studio analysis & modelling run."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="Case Study Run")
    filename: Mapped[str] = mapped_column(String(255), default="case_study.xlsx")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    profile: Mapped[str] = mapped_column(String(32), default="quick")  # quick | full | smoke | custom
    status: Mapped[str] = mapped_column(
        String(32), default="draft", index=True
    )  # draft | queued | running | succeeded | failed | cancelled

    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    analysis_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meridian_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    stages: Mapped[list[StageProgress]] = relationship(
        "StageProgress",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="StageProgress.order_idx",
    )


class StageProgress(Base):
    """Tracks per-stage execution status and timings for a Run."""

    __tablename__ = "stages"
    __table_args__ = (UniqueConstraint("run_id", "key", name="uq_stages_run_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    order_idx: Mapped[int] = mapped_column(Integer, default=0)
    key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(255), default="")
    queue: Mapped[str] = mapped_column(String(32), default="analysis")
    status: Mapped[str] = mapped_column(
        String(32), default="pending"
    )  # pending | running | succeeded | failed | skipped | cancelled
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[Run] = relationship("Run", back_populates="stages")


class ForecastRun(Base):
    """Represents a budget-plan forecast refit on a future window."""

    __tablename__ = "forecasts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), default="Budget Plan Forecast")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    plan_path: Mapped[str] = mapped_column(String(512), default="")
    result_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ScenarioRun(Base):
    """Represents an interactive what-if scenario evaluated on a fitted forecast window."""

    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    forecast_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("forecasts.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255), default="What-If Scenario")
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    result_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OptimisationRun(Base):
    """Represents an interactive budget optimisation run."""

    __tablename__ = "optimisations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    result_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
