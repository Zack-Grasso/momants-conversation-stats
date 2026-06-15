"""Proactively warm Redis with all dashboard read payloads."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.cache import (
    cache_set,
    conversation_cache_key,
    conversations_cache_key,
    insights_cache_key,
    set_pipeline_run_state,
)
from app.config import get_settings
from app.schemas.conversation import ConversationRead, ConversationStats
from app.schemas.insights import ConversationTimeline, InsightsOverview, QuestionClusterRead, UnansweredQuestionRead
from app.services.conversation_service import ConversationService
from app.services.insights_service import InsightsService

logger = logging.getLogger(__name__)

DEFAULT_UNANSWERED_LIMIT = 50
DEFAULT_REVIEW_SAMPLE_COUNT = 5


def warm_agent_cache(db: Session, agent_id: str) -> None:
    settings = get_settings()
    ttl = settings.cache_ttl_seconds
    insights = InsightsService(db)
    conversations = ConversationService(db)

    overview = insights.get_overview(agent_id)
    cache_set(insights_cache_key(agent_id, "overview"), overview, ttl=ttl)

    clusters = insights.get_questions(agent_id)
    questions_payload = [QuestionClusterRead.model_validate(item).model_dump() for item in clusters]
    cache_set(insights_cache_key(agent_id, "questions"), questions_payload, ttl=ttl)

    unanswered = insights.get_unanswered(agent_id, limit=DEFAULT_UNANSWERED_LIMIT)
    unanswered_payload = [UnansweredQuestionRead.model_validate(item).model_dump() for item in unanswered]
    cache_set(insights_cache_key(agent_id, f"unanswered:{DEFAULT_UNANSWERED_LIMIT}"), unanswered_payload, ttl=ttl)

    listed = conversations.list_conversations(agent_id=agent_id)
    list_payload = [ConversationRead.model_validate(item).model_dump() for item in listed]
    cache_set(conversations_cache_key(agent_id, "list"), list_payload, ttl=ttl)

    review = conversations.get_random_sample(count=DEFAULT_REVIEW_SAMPLE_COUNT, agent_id=agent_id)
    review_payload = [ConversationRead.model_validate(item).model_dump() for item in review]
    cache_set(
        conversations_cache_key(agent_id, f"review_sample:{DEFAULT_REVIEW_SAMPLE_COUNT}"),
        review_payload,
        ttl=ttl,
    )

    total = len(listed)
    set_pipeline_run_state(agent_id, stage="warming", cache_done=0, cache_total=total)
    for index, conversation in enumerate(listed, start=1):
        _warm_conversation(db, insights, conversations, conversation.id, ttl)
        # Throttle Redis writes: update every 10 conversations and on the final one.
        if index % 10 == 0 or index == total:
            set_pipeline_run_state(agent_id, cache_done=index, cache_total=total)

    logger.info(
        "Warmed cache for agent %s (%s conversations, ttl=%ss)",
        agent_id,
        total,
        ttl,
    )


def _warm_conversation(
    db: Session,
    insights: InsightsService,
    conversations: ConversationService,
    conversation_id: int,
    ttl: int,
) -> None:
    conversation = conversations.get(conversation_id)
    if conversation is None:
        return

    cache_set(
        conversation_cache_key(conversation_id, "detail"),
        ConversationRead.model_validate(conversation).model_dump(),
        ttl=ttl,
    )

    stats = conversations.get_stats(conversation_id)
    if stats is not None:
        cache_set(
            conversation_cache_key(conversation_id, "stats"),
            ConversationStats.model_validate(stats).model_dump(),
            ttl=ttl,
        )

    timeline = insights.get_timeline(conversation_id)
    if timeline is not None:
        cache_set(
            conversation_cache_key(conversation_id, "timeline"),
            ConversationTimeline(**timeline).model_dump(),
            ttl=ttl,
        )


def expected_cache_keys(agent_id: str, conversation_ids: list[int] | None = None) -> list[str]:
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
    from app.cache import cache_exists

    return all(cache_exists(key) for key in expected_cache_keys(agent_id, conversation_ids))
