"""Integration tests for Procrastinate jobs, burst worker execution, and stale heartbeat sweeper."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from mmm_studio.core.workbook import profile_workbook
from mmm_studio.db.models import Base, Run
from mmm_studio.db.session import get_engine, get_session_factory, init_db
from mmm_studio.jobs.app import (
    defer_task,
    ensure_procrastinate_schema,
    get_procrastinate_app,
    sweep_stale_runs,
)
from mmm_studio.jobs.tasks import ensure_stage_rows, run_analysis_job
from mmm_studio.jobs.worker import run_worker_loop
from mmm_studio.settings import get_settings
from mmm_studio.storage import get_storage

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql:///mmm_studio_test?host=/tmp&port=5433")
FIXTURE_XLSX = Path(__file__).parent / "fixtures" / "case_study.xlsx"


@pytest.fixture()
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MMM_DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("MMM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("MMM_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MMM_WAKE_WORKERS", "false")
    monkeypatch.setenv("MMM_APP_ACCESS_CODE", "")

    get_settings.cache_clear()
    get_storage.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    get_procrastinate_app.cache_clear()

    init_db()
    ensure_procrastinate_schema()

    # Clean tables before test
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        conn.execute("TRUNCATE TABLE procrastinate_events, procrastinate_jobs RESTART IDENTITY CASCADE")

    yield tmp_path

    get_settings.cache_clear()
    get_storage.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    get_procrastinate_app.cache_clear()


def test_worker_executes_analysis_job_and_sweeps_stale_heartbeat(test_env: Path) -> None:
    raw = FIXTURE_XLSX.read_bytes()
    _, prof = profile_workbook(raw)

    storage = get_storage()
    run_id = "run_jobtest01"
    storage.write_bytes(f"runs/{run_id}/input/workbook.xlsx", raw)
    storage.write_json(f"runs/{run_id}/meta/profile.json", prof.model_dump(mode="json"))

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        r = Run(
            id=run_id,
            name="Worker Test Run",
            filename="case_study.xlsx",
            sha256=hashlib.sha256(raw).hexdigest(),
            profile="smoke",
            status="queued",
            schema_json=prof.model_dump(mode="json"),
            config={"role_config": prof.role_config.model_dump(mode="json"), "skip_meridian": True},
        )
        db.add(r)
        db.commit()

    ensure_stage_rows(run_id, skip_meridian=True)
    job_id = defer_task(
        run_analysis_job,
        queue="analysis",
        lock=f"analysis:{run_id}",
        queueing_lock=f"analysis:{run_id}",
        run_id=run_id,
        skip_meridian=True,
    )
    assert job_id > 0

    # Run burst worker on analysis queue
    rc = run_worker_loop(queue="analysis", burst=True)
    assert rc == 0

    with SessionLocal() as db:
        finished = db.get(Run, run_id)
        assert finished is not None
        assert finished.status == "succeeded"
        assert finished.summary is not None
        assert finished.summary["n_sections"] >= 12
        analysis_stages = [s for s in finished.stages if s.queue == "analysis"]
        assert all(s.status == "succeeded" for s in analysis_stages)

    # Test stale heartbeat sweeper on a simulated stuck run
    stuck_id = "run_stuck01"
    with SessionLocal() as db:
        stuck = Run(
            id=stuck_id,
            name="Stuck Run",
            status="running",
            heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=900),
        )
        db.add(stuck)
        db.commit()

    swept = sweep_stale_runs(stale_seconds=60)
    assert stuck_id in swept

    with SessionLocal() as db:
        stuck_after = db.get(Run, stuck_id)
        assert stuck_after is not None
        assert stuck_after.status == "failed"
        assert "Worker lost" in (stuck_after.error or "")
