"""Procrastinate application setup, schema migration, job deferral, and stale-heartbeat sweeper."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import procrastinate
import psycopg

from mmm_studio.db.models import ForecastRun, OptimisationRun, Run, ScenarioRun
from mmm_studio.db.session import get_session_factory
from mmm_studio.jobs.cloud_run import wake_idle_queues, wake_worker
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


class QueueUnavailable(RuntimeError):
    """The job queue's tables could not be read; callers fail closed instead of assuming an empty queue."""


def _queue_rows(sql: str, params: dict[str, Any]) -> list[Any]:
    """Run a read-only query on the queue tables through the application's pooled engine."""
    from sqlalchemy import text

    from mmm_studio.db.session import get_engine

    try:
        with get_engine().connect() as conn:
            return list(conn.execute(text(sql), params).fetchall())
    except Exception as exc:  # noqa: BLE001 - surfaced as QueueUnavailable
        raise QueueUnavailable(str(exc)) from exc


def count_active_queue_jobs(queue: str, conninfo: str | None = None) -> int:
    """Return the number of `todo` + `doing` jobs on a queue (raises `QueueUnavailable` rather than guessing 0)."""
    rows = _queue_rows(
        "SELECT COUNT(*) FROM procrastinate_jobs WHERE queue_name = :queue AND status IN ('todo', 'doing')",
        {"queue": queue},
    )
    return int(rows[0][0]) if rows else 0


def count_todo_queue_jobs(queue: str, conninfo: str | None = None) -> int:
    """Return the number of `todo` (waiting) jobs on a given Procrastinate queue."""
    rows = _queue_rows("SELECT COUNT(*) FROM procrastinate_jobs WHERE queue_name = :queue AND status = 'todo'", {"queue": queue})
    return int(rows[0][0]) if rows else 0


def pending_queueing_locks(locks: list[str]) -> list[str]:
    """Which of `locks` still have a waiting job (deferring another job with that queueing lock would fail)."""
    if not locks:
        return []
    rows = _queue_rows(
        "SELECT DISTINCT queueing_lock FROM procrastinate_jobs WHERE status = 'todo' AND queueing_lock = ANY(:locks)",
        {"locks": list(locks)},
    )
    return [str(r[0]) for r in rows]


def get_queue_positions(job_ids: list[int], queue: str, conninfo: str | None = None) -> dict[int, int]:
    """`{job_id: position}` in one query: 0 while `doing`, 1..N while `todo` in queue order; finished jobs are absent."""
    ids = [int(j) for j in job_ids if j]
    if not ids:
        return {}
    try:
        rows = _queue_rows(
            """
            SELECT id, status,
                   (SELECT COUNT(*) FROM procrastinate_jobs t
                     WHERE t.queue_name = :queue AND t.status = 'todo' AND t.id <= j.id) AS pos
              FROM procrastinate_jobs j
             WHERE j.id = ANY(:ids)
            """,
            {"queue": queue, "ids": ids},
        )
    except QueueUnavailable:
        return {}
    out: dict[int, int] = {}
    for job_id, job_status, pos in rows:
        if job_status == "doing":
            out[int(job_id)] = 0
        elif job_status == "todo":
            out[int(job_id)] = int(pos)
    return out


def get_queue_position(job_id: int | None, queue: str, conninfo: str | None = None) -> int | None:
    """Return 0 if the job is currently `doing`, 1..N if `todo` in queue order, or None if finished."""
    if not job_id:
        return None
    return get_queue_positions([job_id], queue).get(int(job_id))


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

    def _defer() -> int:
        return app.configure_task(name=task_name, queue=queue, lock=lock, queueing_lock=queueing_lock).defer(**kwargs)

    if getattr(app.connector, "_async_pool", None) is not None:
        # Inside a worker (a job chaining the next one) the app is already open asynchronously: defer through it
        job_id = _defer()
    else:
        with app.open():
            job_id = _defer()
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


def release_stalled_jobs(seconds_since_heartbeat: float | None = None, conninfo: str | None = None) -> list[int]:
    """Fail jobs stuck in `doing` on a worker that stopped heartbeating (process killed), releasing their locks.

    Without this, a Meridian job whose worker died keeps the `meridian_singleton` lock forever and every later Meridian
    job (new runs and plan forecasts) waits behind it. Uses Procrastinate's own stalled-job and finish-job queries.
    """
    from procrastinate import sql

    dsn = conninfo or get_settings().psycopg_conninfo
    timeout = seconds_since_heartbeat or max(2 * get_settings().stale_heartbeat_seconds, 300)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            rows = conn.execute(
                sql.queries["select_stalled_jobs_by_heartbeat"],
                {"queue": None, "task_name": None, "seconds_since_heartbeat": timeout},
            ).fetchall()
            job_ids = [int(r[0]) for r in rows]
            for job_id in job_ids:
                conn.execute(sql.queries["finish_job"], {"job_id": job_id, "status": "failed", "delete_job": False})
    except Exception:
        logger.warning("Could not release stalled Procrastinate jobs", exc_info=True)
        return []
    if job_ids:
        logger.warning("Released stalled Procrastinate jobs (worker lost): %s", job_ids)
    return job_ids


def sweep_stale_runs(
    stale_seconds: int | None = None,
    sqlalchemy_url: str | None = None,
    conninfo: str | None = None,
) -> list[str]:
    """Mark any `running` Run or ForecastRun whose heartbeat is older than `stale_seconds` as `failed`."""
    threshold_sec = stale_seconds if stale_seconds is not None else get_settings().stale_heartbeat_seconds
    release_stalled_jobs(conninfo=conninfo)
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

        # Interactive jobs have no heartbeat: a scenario or optimisation left running past the threshold lost its worker
        for model in (ScenarioRun, OptimisationRun):
            for rec in db.query(model).filter(model.status.in_(("queued", "running"))).all():
                upd = rec.updated_at
                if upd is not None and upd.tzinfo is None:
                    upd = upd.replace(tzinfo=timezone.utc)
                if upd is not None and upd < cutoff - timedelta(seconds=threshold_sec):
                    rec.status = "failed"
                    rec.error = "The worker stopped before this finished; run it again."
                    marked_ids.append(rec.id)

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


_LAST_SWEEP = 0.0
_SWEEP_LOCK = threading.Lock()


def sweep_stale_runs_throttled() -> None:
    """`sweep_stale_runs` at most once per `sweep_interval_seconds` per process, for the API's read paths.

    Also wakes the Cloud Run worker of any queue that has waiting jobs, in case a wake-up was skipped while a worker was
    finishing.
    """
    global _LAST_SWEEP
    interval = get_settings().sweep_interval_seconds
    with _SWEEP_LOCK:
        now = time.monotonic()
        if interval and now - _LAST_SWEEP < interval:
            return
        _LAST_SWEEP = now
    try:
        sweep_stale_runs()
        wake_idle_queues(count_todo_queue_jobs)
    except Exception:  # noqa: BLE001 - a failed sweep must never fail the request that triggered it
        logger.warning("Stale-run sweep failed", exc_info=True)
