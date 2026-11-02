"""Procrastinate worker runner supporting persistent, `--burst`, and `--idle-exit` modes."""

from __future__ import annotations

import logging
import time

from mmm_studio.db.session import init_db
from mmm_studio.jobs.app import (
    count_active_queue_jobs,
    ensure_procrastinate_schema,
    get_procrastinate_app,
    release_stalled_jobs,
)

logger = logging.getLogger(__name__)


def run_worker_loop(
    queue: str = "all",
    burst: bool = False,
    idle_exit: int = 0,
    conninfo: str | None = None,
    sqlalchemy_url: str | None = None,
) -> int:
    """Run the Procrastinate worker on the specified queue(s)."""
    # Ensure ORM tables and Procrastinate schema exist
    init_db(sqlalchemy_url)
    ensure_procrastinate_schema(conninfo)
    # Jobs left in `doing` by a killed worker would otherwise keep their lock (e.g. `meridian_singleton`) forever
    release_stalled_jobs(conninfo=conninfo)

    # Import tasks to ensure registration
    import mmm_studio.jobs.tasks  # noqa: F401

    queues = ["analysis", "meridian", "interactive"] if queue == "all" else [queue]
    app = get_procrastinate_app(conninfo)

    if burst:
        with app.open():
            app.run_worker(
                queues=queues,
                wait=False,
                concurrency=1,
                listen_notify=False,
            )
        return 0

    if idle_exit > 0:
        last_active = time.monotonic()
        with app.open():
            while True:
                has_work = any(count_active_queue_jobs(q, conninfo=conninfo) > 0 for q in queues)
                if has_work:
                    app.run_worker(
                        queues=queues,
                        wait=False,
                        concurrency=1,
                        listen_notify=False,
                    )
                    last_active = time.monotonic()
                else:
                    if (time.monotonic() - last_active) >= idle_exit:
                        logger.info("Worker idle for %ss on %s — exiting.", idle_exit, queues)
                        break
                    time.sleep(1.0)
        return 0

    with app.open():
        app.run_worker(
            queues=queues,
            wait=True,
            concurrency=1,
        )
    return 0
