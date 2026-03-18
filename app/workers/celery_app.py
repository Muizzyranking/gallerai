import logging

from celery import Celery
from celery.signals import worker_ready

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "gallerai",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.photo_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """
    Trigger model warmup when the Celery worker starts.
    This pre-loads DeepFace weights into memory so the first real
    task doesn't pay the cold-start penalty.
    """
    logger.info("Worker ready — triggering DeepFace warmup")
    from app.workers.photo_tasks import warmup_models_task

    warmup_models_task.delay()  # type: ignore
