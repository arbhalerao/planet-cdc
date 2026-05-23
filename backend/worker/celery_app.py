from celery import Celery
from celery.schedules import crontab
from app.config import settings

celery_app = Celery(
    "planet_cdc",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "check-due-workflows": {
            "task": "worker.tasks.check_due_workflows",
            "schedule": crontab(minute="*"),  # every minute
        },
    },
)
