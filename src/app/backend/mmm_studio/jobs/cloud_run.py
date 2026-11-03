"""Optional Google Cloud Run Jobs wake-up helper for scale-to-zero workers.

A queue gets a new worker execution only when none of its executions is still starting or running: every scenario
used to start its own 4-vCPU worker. The API's periodic sweep wakes queues that have waiting jobs but no worker.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from itertools import islice

from mmm_studio.settings import get_settings

logger = logging.getLogger(__name__)

QUEUES = ("analysis", "meridian", "interactive")
_WAKE_COOLDOWN_SEC = 5.0
_LAST_WAKE_AT: dict[str, float] = {}
_LOCK = threading.Lock()


def job_resource_name(queue: str) -> str:
    settings = get_settings()
    if queue == "analysis":
        job_name = settings.worker_analysis_job
    elif queue == "meridian":
        job_name = settings.worker_meridian_job
    else:
        job_name = settings.worker_interactive_job
    return f"projects/{settings.gcp_project_id}/locations/{settings.gcp_region}/jobs/{job_name}"


def _enabled() -> bool:
    settings = get_settings()
    return settings.wake_workers.strip().lower() in ("cloudrun", "true", "1", "yes") and bool(settings.gcp_project_id)


def _worker_active(queue: str) -> bool:
    """True when one of the queue job's recent executions has not completed yet (it will pick up new jobs)."""
    from google.cloud import run_v2

    executions = run_v2.ExecutionsClient().list_executions(parent=job_resource_name(queue))
    for execution in islice(executions, 5):
        if execution.running_count > 0 or not execution.completion_time:
            return True
    return False


def wake_worker(queue: str) -> None:
    """Start a Cloud Run Job execution for `queue` if `WAKE_WORKERS` is enabled and no worker is active."""
    if not _enabled():
        return
    with _LOCK:
        now = time.monotonic()
        if (now - _LAST_WAKE_AT.get(queue, 0.0)) < _WAKE_COOLDOWN_SEC:
            return
        _LAST_WAKE_AT[queue] = now
    try:
        active = _worker_active(queue)
    except Exception:
        # Without the execution list, a second worker is cheaper than a queue nobody drains
        logger.warning("Could not list the executions of %s; starting a worker anyway.", queue, exc_info=True)
        active = False
    if active:
        return
    try:
        from google.cloud import run_v2

        run_v2.JobsClient().run_job(name=job_resource_name(queue))
    except Exception:
        logger.warning("wake_worker(%s) failed; an existing worker or the next sweep will drain the queue.", queue, exc_info=True)


def wake_idle_queues(count_todo: Callable[[str], int]) -> None:
    """Wake the worker of every queue that has waiting jobs (no-op unless `WAKE_WORKERS` is enabled)."""
    if not _enabled():
        return
    for queue in QUEUES:
        try:
            if count_todo(queue) > 0:
                wake_worker(queue)
        except Exception:  # noqa: BLE001
            logger.warning("Could not check queue %s for waiting jobs", queue, exc_info=True)
