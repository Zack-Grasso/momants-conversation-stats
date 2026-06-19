"""Detect conversations referred to event customer service (doorverwezen)."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.conversation import Conversation
from app.models.insights import ConversationMetrics

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def conversation_is_referred(conversation: Conversation) -> bool:
    return any(
        message.from_agent and EMAIL_RE.search(message.content or "")
        for message in conversation.messages
    )


def referred_conversation_ids(
    db: Session,
    agent_id: str,
    *,
    conversation_ids: list[int] | None = None,
    skip_labeled: bool = True,
) -> list[int]:
    stmt = (
        select(Conversation)
        .where(Conversation.agent_id == agent_id)
        .options(selectinload(Conversation.messages))
    )
    if conversation_ids is not None:
        if not conversation_ids:
            return []
        stmt = stmt.where(Conversation.id.in_(conversation_ids))

    referred = [conversation.id for conversation in db.scalars(stmt).all() if conversation_is_referred(conversation)]
    if not referred or not skip_labeled:
        return referred

    labeled_ids = set(
        db.scalars(
            select(ConversationMetrics.conversation_id).where(
                ConversationMetrics.conversation_id.in_(referred),
                ConversationMetrics.intent_label.is_not(None),
            )
        ).all()
    )
    return [conversation_id for conversation_id in referred if conversation_id not in labeled_ids]
