"""Optional Google Cloud Run Jobs wake-up helper for scale-to-zero workers."""

from __future__ import annotations

import logging

from mmm_studio.settings import get_settings

logger = logging.getLogger(__name__)


def job_resource_name(queue: str) -> str:
    settings = get_settings()
    if queue == "analysis":
        job_name = settings.worker_analysis_job
    elif queue == "meridian":
        job_name = settings.worker_meridian_job
    else:
        job_name = settings.worker_interactive_job
    return f"projects/{settings.gcp_project_id}/locations/{settings.gcp_region}/jobs/{job_name}"


def wake_worker(queue: str) -> None:
    """Trigger a Cloud Run Job execution for `queue` if `WAKE_WORKERS` is enabled."""
    settings = get_settings()
    if settings.wake_workers.strip().lower() not in ("cloudrun", "true", "1", "yes") or not settings.gcp_project_id:
        return
    try:
        from google.cloud import run_v2

        client = run_v2.JobsClient()
        client.run_job(name=job_resource_name(queue))
    except Exception:
        logger.warning(
            "wake_worker(%s) failed or job already at max concurrency; existing worker will drain queue.",
            queue,
            exc_info=True,
        )
