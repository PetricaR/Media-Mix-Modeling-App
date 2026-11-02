"""Integration tests for Procrastinate jobs, burst worker execution, and stale heartbeat sweeper."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from mmm_studio.core.workbook import profile_workbook
from mmm_studio.db.models import Base, Run, StageProgress
from mmm_studio.db.session import get_engine, get_session_factory, init_db
from mmm_studio.jobs.app import (
    defer_task,
    ensure_procrastinate_schema,
    get_procrastinate_app,
    release_stalled_jobs,
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

    # Stale heartbeat sweeper: a run with a running stage and no heartbeat lost its worker; a run between its
    # analysis and Meridian jobs (no running stage, e.g. queued behind another fit) must not be failed.
    stuck_id, waiting_id = "run_stuck01", "run_waiting01"
    old_hb = datetime.now(timezone.utc) - timedelta(seconds=900)
    with SessionLocal() as db:
        db.add(Run(id=stuck_id, name="Stuck Run", status="running", heartbeat_at=old_hb))
        db.add(Run(id=waiting_id, name="Waiting Run", status="running", heartbeat_at=old_hb))
        db.commit()
    ensure_stage_rows(stuck_id)
    ensure_stage_rows(waiting_id)
    with SessionLocal() as db:
        stage = db.query(StageProgress).filter(StageProgress.run_id == stuck_id, StageProgress.key == "s12_meridian_fit").one()
        stage.status = "running"
        db.commit()

    swept = sweep_stale_runs(stale_seconds=60)
    assert stuck_id in swept
    assert waiting_id not in swept

    with SessionLocal() as db:
        stuck_after = db.get(Run, stuck_id)
        assert stuck_after is not None
        assert stuck_after.status == "failed"
        assert "Worker lost" in (stuck_after.error or "")


def test_stalled_job_of_a_dead_worker_releases_its_lock(test_env: Path) -> None:
    """A Meridian job left in `doing` by a killed worker must not hold `meridian_singleton` forever."""
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        job_id = conn.execute(
            "WITH w AS (INSERT INTO procrastinate_workers (last_heartbeat) VALUES (now() - interval '1 hour') RETURNING id) "
            "INSERT INTO procrastinate_jobs (queue_name, task_name, lock, args, status, worker_id) "
            "SELECT 'meridian', 'mmm_studio.jobs.tasks.run_meridian_job', 'meridian_singleton', '{}', 'doing', id FROM w RETURNING id"
        ).fetchone()[0]

    assert job_id in release_stalled_jobs()

    with psycopg.connect(TEST_DB_URL) as conn:
        status = conn.execute("SELECT status FROM procrastinate_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    assert status == "failed"
    assert release_stalled_jobs() == []
