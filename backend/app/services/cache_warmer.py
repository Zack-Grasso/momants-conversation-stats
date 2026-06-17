"""Proactively warm Redis with all dashboard read payloads."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from app.cache import (
    cache_set,
    conversations_cache_key,
    get_cache_client,
    insights_cache_key,
    set_pipeline_run_state,
)
from app.config import get_settings
from app.services.cache_builders import (
    DEFAULT_REVIEW_SAMPLE_COUNT,
    DEFAULT_UNANSWERED_LIMIT,
    build_conversation_list,
    build_overview,
    build_questions,
    build_review_sample,
    build_unanswered,
    warm_conversation_caches,
)

logger = logging.getLogger(__name__)

WARMING_LOCK_TTL_SECONDS = 1800

# Re-export for callers that import from this module.
__all__ = [
    "DEFAULT_REVIEW_SAMPLE_COUNT",
    "DEFAULT_UNANSWERED_LIMIT",
    "expected_cache_keys",
    "is_agent_cache_ready",
    "is_cache_warming",
    "trigger_background_warm_if_needed",
    "warm_agent_cache",
]


def _warming_lock_key(agent_id: str) -> str:
    return f"cache:warming:{agent_id}"


def is_cache_warming(agent_id: str) -> bool:
    try:
        return bool(get_cache_client().exists(_warming_lock_key(agent_id)))
    except Exception:
        logger.exception("Failed to check warming lock for agent %s", agent_id)
        return False


def trigger_background_warm_if_needed(agent_id: str) -> bool:
    """Spawn a background warm subprocess when agent keys are missing. Returns True if started."""
    if is_agent_cache_ready(agent_id) or is_cache_warming(agent_id):
        return False

    try:
        acquired = get_cache_client().set(_warming_lock_key(agent_id), "1", nx=True, ex=WARMING_LOCK_TTL_SECONDS)
    except Exception:
        logger.exception("Failed to acquire warming lock for agent %s", agent_id)
        return False

    if not acquired:
        return False

    set_pipeline_run_state(agent_id, stage="warming", cache_done=0, cache_total=0)
    backend_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.setdefault("APP_ROLE", "scheduler")
    cmd = [sys.executable, "-m", "app.pipeline", "warm", f"--agent-id={agent_id}"]

    try:
        subprocess.Popen(cmd, env=env, cwd=str(backend_root))
    except Exception:
        logger.exception("Failed to spawn background cache warm for agent %s", agent_id)
        try:
            get_cache_client().delete(_warming_lock_key(agent_id))
        except Exception:
            logger.exception("Failed to release warming lock for agent %s", agent_id)
        return False

    logger.info("Started background cache warm for agent %s", agent_id)
    return True


def warm_agent_cache(db: Session, agent_id: str) -> None:
    settings = get_settings()
    ttl = settings.cache_ttl_seconds

    cache_set(insights_cache_key(agent_id, "overview"), build_overview(db, agent_id), ttl=ttl)
    cache_set(insights_cache_key(agent_id, "questions"), build_questions(db, agent_id), ttl=ttl)
    cache_set(
        insights_cache_key(agent_id, f"unanswered:{DEFAULT_UNANSWERED_LIMIT}"),
        build_unanswered(db, agent_id, limit=DEFAULT_UNANSWERED_LIMIT),
        ttl=ttl,
    )

    list_payload = build_conversation_list(db, agent_id)
    cache_set(conversations_cache_key(agent_id, "list"), list_payload, ttl=ttl)
    cache_set(
        conversations_cache_key(agent_id, f"review_sample:{DEFAULT_REVIEW_SAMPLE_COUNT}"),
        build_review_sample(db, agent_id, count=DEFAULT_REVIEW_SAMPLE_COUNT),
        ttl=ttl,
    )

    total = len(list_payload)
    set_pipeline_run_state(agent_id, stage="warming", cache_done=0, cache_total=total)
    for index, conversation in enumerate(list_payload, start=1):
        warm_conversation_caches(db, conversation["id"], ttl)
        # Throttle Redis writes: update every 10 conversations and on the final one.
        if index % 10 == 0 or index == total:
            set_pipeline_run_state(agent_id, cache_done=index, cache_total=total)

    logger.info(
        "Warmed cache for agent %s (%s conversations, ttl=%ss)",
        agent_id,
        total,
        ttl,
    )
    set_pipeline_run_state(agent_id, stage="ready", cache_done=total, cache_total=total)
    try:
        get_cache_client().delete(_warming_lock_key(agent_id))
    except Exception:
        logger.exception("Failed to release warming lock for agent %s", agent_id)


def expected_cache_keys(agent_id: str, conversation_ids: list[int] | None = None) -> list[str]:
    from app.cache import conversation_cache_key

    keys = [
        insights_cache_key(agent_id, "overview"),
        insights_cache_key(agent_id, "questions"),
        insights_cache_key(agent_id, f"unanswered:{DEFAULT_UNANSWERED_LIMIT}"),
        conversations_cache_key(agent_id, "list"),
        conversations_cache_key(agent_id, f"review_sample:{DEFAULT_REVIEW_SAMPLE_COUNT}"),
    ]
    if conversation_ids:
        for conversation_id in conversation_ids:
            keys.extend(
                [
                    conversation_cache_key(conversation_id, "detail"),
                    conversation_cache_key(conversation_id, "stats"),
                    conversation_cache_key(conversation_id, "timeline"),
                ]
            )
    return keys


def is_agent_cache_ready(agent_id: str, conversation_ids: list[int] | None = None) -> bool:
    """True when agent-level dashboard keys are pre-warmed (fast-path), not required for reads."""
    from app.cache import cache_exists

    return all(cache_exists(key) for key in expected_cache_keys(agent_id, conversation_ids))
