import json
import logging
from functools import lru_cache
from typing import Any

import redis

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_cache_client() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_cache_url, decode_responses=True)


def cache_get(key: str) -> Any | None:
    settings = get_settings()
    if not settings.cache_enabled:
        return None
    try:
        raw = get_cache_client().get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        logger.exception("Cache get failed for %s", key)
        return None


def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    settings = get_settings()
    if not settings.cache_enabled:
        return
    try:
        get_cache_client().setex(key, ttl or settings.cache_ttl_seconds, json.dumps(value, default=str))
    except Exception:
        logger.exception("Cache set failed for %s", key)


def cache_delete(key: str) -> None:
    settings = get_settings()
    if not settings.cache_enabled:
        return
    try:
        get_cache_client().delete(key)
    except Exception:
        logger.exception("Cache delete failed for %s", key)


def cache_delete_prefix(prefix: str) -> None:
    settings = get_settings()
    if not settings.cache_enabled:
        return
    try:
        client = get_cache_client()
        for key in client.scan_iter(match=f"{prefix}*"):
            client.delete(key)
    except Exception:
        logger.exception("Cache delete prefix failed for %s", prefix)


def insights_cache_key(agent_id: str, suffix: str) -> str:
    return f"insights:{agent_id}:{suffix}"


def conversations_cache_key(agent_id: str, suffix: str) -> str:
    return f"conversations:{agent_id}:{suffix}"


def conversation_cache_key(conversation_id: int, suffix: str) -> str:
    return f"conversation:{conversation_id}:{suffix}"


def cache_exists(key: str) -> bool:
    settings = get_settings()
    if not settings.cache_enabled:
        return False
    try:
        return bool(get_cache_client().exists(key))
    except Exception:
        logger.exception("Cache exists check failed for %s", key)
        return False


CACHE_NOT_READY_DETAIL = "Dashboard data not ready — pipeline has not warmed cache yet"


# --- Pipeline run-state (drives the Run page's live progress / elapsed timer) -----------------
# A single JSON blob per agent describing the in-flight pipeline run so the UI can show a stable
# elapsed time across every stage (including cache warming, where there is no ingest/insights job
# to anchor on) and a determinate cache-warm progress bar.

import time as _time

_RUN_STATE_TTL_SECONDS = 3600


def pipeline_run_state_key(agent_id: str) -> str:
    return f"scheduler:run:{agent_id}"


def set_pipeline_run_state(agent_id: str, **fields: Any) -> None:
    """Merge ``fields`` into the agent's pipeline run-state, stamping updated_at."""
    try:
        client = get_cache_client()
        key = pipeline_run_state_key(agent_id)
        raw = client.get(key)
        state = json.loads(raw) if raw else {}
        state.update(fields)
        state["updated_at"] = _time.time()
        client.setex(key, _RUN_STATE_TTL_SECONDS, json.dumps(state, default=str))
    except Exception:
        logger.exception("Failed to set pipeline run state for %s", agent_id)


def get_pipeline_run_state(agent_id: str) -> dict | None:
    try:
        raw = get_cache_client().get(pipeline_run_state_key(agent_id))
        return json.loads(raw) if raw else None
    except Exception:
        logger.exception("Failed to read pipeline run state for %s", agent_id)
        return None


def clear_pipeline_run_state(agent_id: str) -> None:
    try:
        get_cache_client().delete(pipeline_run_state_key(agent_id))
    except Exception:
        logger.exception("Failed to clear pipeline run state for %s", agent_id)
