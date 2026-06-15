import logging
import time
import uuid
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

import redis

from app.config import get_settings

logger = logging.getLogger(__name__)

LOCK_TTL_SECONDS = 3600

# Short-lived mutex that serialises the "is there a free slot? then insert the job row"
# admission decision across all ingest/insights creators (and across processes), so two
# concurrent batches can't both read the same free slot and overshoot the limit.
ADMISSION_LOCK_KEY = "job:admission"
ADMISSION_LOCK_TTL_SECONDS = 30

_RELEASE_IF_OWNER = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


@lru_cache
def get_lock_client() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


@contextmanager
def job_admission_lock(*, timeout_seconds: float = 30.0, poll_seconds: float = 0.1) -> Iterator[bool]:
    """Acquire the global job-admission mutex for the duration of the block.

    Yields True if the lock was held, False if it could not be acquired within the timeout
    (callers proceed best-effort rather than deadlocking the pipeline). Release is owner-safe
    via a compare-and-delete so an expired-then-reacquired lock is never deleted by us.
    """
    client = get_lock_client()
    token = uuid.uuid4().hex
    acquired = False
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            try:
                if client.set(ADMISSION_LOCK_KEY, token, nx=True, ex=ADMISSION_LOCK_TTL_SECONDS):
                    acquired = True
                    break
            except Exception:
                logger.exception("Failed to acquire job admission lock; proceeding without it")
                break
            time.sleep(poll_seconds)
        if not acquired:
            logger.warning("Job admission lock not acquired within %.1fs; proceeding best-effort", timeout_seconds)
        yield acquired
    finally:
        if acquired:
            try:
                client.eval(_RELEASE_IF_OWNER, 1, ADMISSION_LOCK_KEY, token)
            except Exception:
                logger.exception("Failed to release job admission lock")


def acquire_agent_job_lock(agent_id: str, job_kind: str) -> bool:
    key = f"job:agent:{job_kind}:{agent_id}"
    try:
        return bool(get_lock_client().set(key, "1", nx=True, ex=LOCK_TTL_SECONDS))
    except Exception:
        logger.exception("Failed to acquire lock %s", key)
        return False


def release_agent_job_lock(agent_id: str, job_kind: str) -> None:
    key = f"job:agent:{job_kind}:{agent_id}"
    try:
        get_lock_client().delete(key)
    except Exception:
        logger.exception("Failed to release lock %s", key)


def request_job_cancel(job_kind: str, job_id: int) -> None:
    key = f"job:cancel:{job_kind}:{job_id}"
    try:
        get_lock_client().set(key, "1", ex=LOCK_TTL_SECONDS)
    except Exception:
        logger.exception("Failed to set cancel flag %s", key)


def is_job_cancelled(job_kind: str, job_id: int) -> bool:
    key = f"job:cancel:{job_kind}:{job_id}"
    try:
        return get_lock_client().get(key) == "1"
    except Exception:
        logger.exception("Failed to read cancel flag %s", key)
        return False


def clear_job_cancel(job_kind: str, job_id: int) -> None:
    key = f"job:cancel:{job_kind}:{job_id}"
    try:
        get_lock_client().delete(key)
    except Exception:
        logger.exception("Failed to clear cancel flag %s", key)
