from celery import Celery

from opensansad.config import get_settings

_settings = get_settings()

app = Celery("opensansad", broker=_settings.redis_url, backend=_settings.redis_url)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,  # resumable ingestion: re-deliver if a worker dies mid-task
    worker_prefetch_multiplier=1,
    timezone="Asia/Kolkata",
)


@app.task(name="opensansad.ping")
def ping() -> str:
    return "pong"
