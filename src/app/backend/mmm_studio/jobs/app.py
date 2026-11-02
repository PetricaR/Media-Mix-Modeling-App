"""Procrastinate application setup, schema migration, job deferral, and stale-heartbeat sweeper."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import procrastinate
import psycopg

from mmm_studio.db.models import ForecastRun, Run
from mmm_studio.db.session import get_session_factory
from mmm_studio.jobs.cloud_run import wake_worker
from mmm_studio.settings import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def get_procrastinate_app(conninfo: str | None = None) -> procrastinate.App:
    """Return a configured Procrastinate App bound to PostgreSQL."""
    dsn = conninfo or get_settings().psycopg_conninfo
    connector = procrastinate.PsycopgConnector(conninfo=dsn)
    proc_app = procrastinate.App(
        connector=connector,
        import_paths=["mmm_studio.jobs.tasks"],
    )
    from mmm_studio.jobs.tasks import register_tasks

    register_tasks(proc_app)
    return proc_app


def ensure_procrastinate_schema(conninfo: str | None = None) -> None:
    """Idempotently apply the Procrastinate SQL schema to PostgreSQL."""
    dsn = conninfo or get_settings().psycopg_conninfo
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT to_regclass('public.procrastinate_jobs')"
        ).fetchone()
        if row and row[0] is not None:
            return
    app = get_procrastinate_app(conninfo)
    with app.open():
        app.schema_manager.apply_schema()


def count_active_queue_jobs(queue: str, conninfo: str | None = None) -> int:
    """Return the number of `todo` + `doing` jobs on a given Procrastinate queue."""
    dsn = conninfo or get_settings().psycopg_conninfo
    try:
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM procrastinate_jobs WHERE queue_name = %s AND status IN ('todo', 'doing')",
                (queue,),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def count_todo_queue_jobs(queue: str, conninfo: str | None = None) -> int:
    """Return the number of `todo` (waiting) jobs on a given Procrastinate queue."""
    dsn = conninfo or get_settings().psycopg_conninfo
    try:
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM procrastinate_jobs WHERE queue_name = %s AND status = 'todo'",
                (queue,),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def get_queue_position(job_id: int | None, queue: str, conninfo: str | None = None) -> int | None:
    """Return 0 if the job is currently `doing`, 1..N if `todo` in queue order, or None if finished."""
    if not job_id:
        return None
    dsn = conninfo or get_settings().psycopg_conninfo
    try:
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT status FROM procrastinate_jobs WHERE id = %s",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            status = str(row[0])
            if status == "doing":
                return 0
            if status != "todo":
                return None
            pos_row = conn.execute(
                "SELECT COUNT(*) FROM procrastinate_jobs WHERE queue_name = %s AND status = 'todo' AND id <= %s",
                (queue, job_id),
            ).fetchone()
            return int(pos_row[0]) if pos_row else 1
    except Exception:
        return None


def defer_task(
    task_obj: Any,
    queue: str,
    lock: str | None = None,
    queueing_lock: str | None = None,
    conninfo: str | None = None,
    **kwargs: Any,
) -> int:
    """Defer a Procrastinate task synchronously, wake its Cloud Run worker if configured, and return job_id."""
    ensure_procrastinate_schema(conninfo)
    app = get_procrastinate_app(conninfo)
    task_name = getattr(task_obj, "name", None) or str(task_obj)
    with app.open():
        job_id = app.configure_task(
            name=task_name,
            queue=queue,
            lock=lock,
            queueing_lock=queueing_lock,
        ).defer(**kwargs)
    wake_worker(queue)
    return int(job_id)


def cancel_procrastinate_job(
    job_id: int | None,
    abort: bool = True,
    conninfo: str | None = None,
) -> bool:
    """Cancel or abort a Procrastinate job by ID."""
    if not job_id:
        return False
    app = get_procrastinate_app(conninfo)
    try:
        with app.open():
            return bool(app.job_manager.cancel_job_by_id(job_id, abort=abort))
    except Exception:
        logger.warning("Failed to cancel Procrastinate job %s", job_id, exc_info=True)
        return False


def sweep_stale_runs(
    stale_seconds: int | None = None,
    sqlalchemy_url: str | None = None,
    conninfo: str | None = None,
) -> list[str]:
    """Mark any `running` Run or ForecastRun whose heartbeat is older than `stale_seconds` as `failed`."""
    threshold_sec = stale_seconds if stale_seconds is not None else get_settings().stale_heartbeat_seconds
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold_sec)
    SessionLocal = get_session_factory(sqlalchemy_url)
    marked_ids: list[str] = []

    with SessionLocal() as db:
        running_runs = db.query(Run).filter(Run.status == "running").all()
        for r in running_runs:
            # A run between its analysis and Meridian jobs (e.g. queued behind another run's Meridian fit) has no
            # running stage and no heartbeat; only a stage that is running without a heartbeat means a lost worker.
            if not any(st.status == "running" for st in r.stages):
                continue
            hb = r.heartbeat_at
            if hb is not None:
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                if hb < cutoff:
                    r.status = "failed"
                    r.error = f"Worker lost (no heartbeat for > {threshold_sec}s)."
                    for st in r.stages:
                        if st.status == "running":
                            st.status = "failed"
                            st.error = r.error
                            st.finished_at = datetime.now(timezone.utc)
                    cancel_procrastinate_job(r.analysis_job_id, abort=True, conninfo=conninfo)
                    cancel_procrastinate_job(r.meridian_job_id, abort=True, conninfo=conninfo)
                    marked_ids.append(r.id)

        running_forecasts = db.query(ForecastRun).filter(ForecastRun.status == "running").all()
        for f in running_forecasts:
            hb = f.heartbeat_at
            if hb is not None:
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                if hb < cutoff:
                    f.status = "failed"
                    f.error = f"Worker lost (no heartbeat for > {threshold_sec}s)."
                    cancel_procrastinate_job(f.job_id, abort=True, conninfo=conninfo)
                    marked_ids.append(f.id)

        if marked_ids:
            db.commit()

    return marked_ids
