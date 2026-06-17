"""Shared cache payload builders used by read-through endpoints and cache warming."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.cache import cache_set, conversation_cache_key
from app.schemas.conversation import ConversationRead, ConversationStats
from app.schemas.insights import ConversationTimeline, QuestionClusterRead, UnansweredQuestionRead
from app.services.conversation_service import ConversationService
from app.services.insights_service import InsightsService

DEFAULT_UNANSWERED_LIMIT = 50
DEFAULT_REVIEW_SAMPLE_COUNT = 5


def build_overview(db: Session, agent_id: str) -> dict:
    return InsightsService(db).get_overview(agent_id)


def build_questions(db: Session, agent_id: str) -> list[dict]:
    clusters = InsightsService(db).get_questions(agent_id)
    return [QuestionClusterRead.model_validate(item).model_dump() for item in clusters]


def build_unanswered(db: Session, agent_id: str, limit: int = DEFAULT_UNANSWERED_LIMIT) -> list[dict]:
    rows = InsightsService(db).get_unanswered(agent_id, limit=limit)
    return [UnansweredQuestionRead.model_validate(item).model_dump() for item in rows]


def build_conversation_list(db: Session, agent_id: str) -> list[dict]:
    listed = ConversationService(db).list_conversations(agent_id=agent_id)
    return [ConversationRead.model_validate(item).model_dump() for item in listed]


def build_review_sample(db: Session, agent_id: str, count: int = DEFAULT_REVIEW_SAMPLE_COUNT) -> list[dict]:
    review = ConversationService(db).get_random_sample(count=count, agent_id=agent_id)
    return [ConversationRead.model_validate(item).model_dump() for item in review]


def build_conversation_detail(db: Session, conversation_id: int) -> dict | None:
    conversation = ConversationService(db).get(conversation_id)
    if conversation is None:
        return None
    return ConversationRead.model_validate(conversation).model_dump()


def build_conversation_stats(db: Session, conversation_id: int) -> dict | None:
    stats = ConversationService(db).get_stats(conversation_id)
    if stats is None:
        return None
    return ConversationStats.model_validate(stats).model_dump()


def build_conversation_timeline(db: Session, conversation_id: int) -> dict | None:
    timeline = InsightsService(db).get_timeline(conversation_id)
    if timeline is None:
        return None
    return ConversationTimeline(**timeline).model_dump()


def warm_conversation_caches(db: Session, conversation_id: int, ttl: int) -> None:
    """Write detail/stats/timeline keys for one conversation (used by cache warmer)."""
    detail = build_conversation_detail(db, conversation_id)
    if detail is None:
        return

    cache_set(conversation_cache_key(conversation_id, "detail"), detail, ttl=ttl)

    stats = build_conversation_stats(db, conversation_id)
    if stats is not None:
        cache_set(conversation_cache_key(conversation_id, "stats"), stats, ttl=ttl)

    timeline = build_conversation_timeline(db, conversation_id)
    if timeline is not None:
        cache_set(conversation_cache_key(conversation_id, "timeline"), timeline, ttl=ttl)
