import json
import logging
from datetime import datetime
from functools import lru_cache
from typing import Any

import redis

from app.config import get_settings

logger = logging.getLogger(__name__)


def _serialize_payload(payload: dict[str, Any]) -> str:
    def default(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    return json.dumps(payload, default=default)


@lru_cache
def get_pubsub_client() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_pubsub_url, decode_responses=True)


def job_channel(job_type: str, job_id: int) -> str:
    return f"job:progress:{job_type}:{job_id}"


def publish_job_progress(job_type: str, job_id: int, event: str, payload: dict[str, Any]) -> None:
    message = {"event": event, "job_type": job_type, "job_id": job_id, **payload}
    try:
        get_pubsub_client().publish(job_channel(job_type, job_id), _serialize_payload(message))
    except Exception:
        logger.exception("Failed to publish job progress for %s %s", job_type, job_id)
