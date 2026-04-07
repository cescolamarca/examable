from celery import Celery

from app.config import settings

celery_app = Celery("examable", broker=settings.redis_url, backend=settings.redis_url)


@celery_app.task(name="worker.ping")
def ping() -> str:
    return "pong"
