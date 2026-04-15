import os

from celery import Celery
from celery.signals import worker_init


@worker_init.connect
def _configure_worker_logging(**kwargs):
    from app.core.logging_config import configure_logging
    configure_logging()


@worker_init.connect
def _log_worker_started(sender=None, **kwargs):
    import asyncio
    from app.services.event_log_service import log_event

    hostname = getattr(sender, "hostname", "unknown")
    concurrency = getattr(sender, "concurrency", None)

    async def _do():
        await log_event(
            None, "celery.worker_started",
            f"Celery worker '{hostname}' started",
            details={"hostname": hostname, "concurrency": concurrency},
        )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_do())
        else:
            loop.run_until_complete(_do())
    except Exception:
        pass


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "code_reviewer",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # fair dispatch for long-running tasks
    task_routes={
        "app.workers.tasks.run_review_job": {"queue": "reviews"},
    },
)
