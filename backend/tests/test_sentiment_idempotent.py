"""Integration test: concurrent/duplicate sentiment inserts must not raise.

Uses the real database but never commits — everything is rolled back at the end, so
no rows persist. This validates the ON CONFLICT DO NOTHING behaviour against the actual
``sentiment_analyses_message_id_key`` UNIQUE constraint.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import SessionLocal
from app.models.conversation import Conversation, Message, SentimentAnalysis


def _sentiment_row(message_id: int) -> dict:
    return {
        "message_id": message_id,
        "stars": 3,
        "label": "neutral",
        "score": 0.5,
        "model_name": "test-model",
        "raw_label": None,
        "raw_score": None,
        "low_confidence": False,
    }


def test_duplicate_sentiment_insert_is_idempotent():
    db = SessionLocal()
    try:
        conv = Conversation(
            external_id=f"idem-{uuid.uuid4().hex[:18]}",
            agent_id="test-agent",
            title="idempotency test",
        )
        db.add(conv)
        db.flush()

        msg = Message(
            external_id=f"idem-{uuid.uuid4().hex[:18]}",
            conversation_id=conv.id,
            role="member",
            from_agent=False,
            content="hello",
        )
        db.add(msg)
        db.flush()

        stmt = (
            pg_insert(SentimentAnalysis)
            .values([_sentiment_row(msg.id)])
            .on_conflict_do_nothing(index_elements=["message_id"])
        )
        db.execute(stmt)
        # Second insert for the same message_id must be a no-op, not an IntegrityError.
        db.execute(stmt)

        count = db.scalar(
            select(func.count()).select_from(SentimentAnalysis).where(
                SentimentAnalysis.message_id == msg.id
            )
        )
        assert count == 1
    finally:
        db.rollback()
        db.close()
