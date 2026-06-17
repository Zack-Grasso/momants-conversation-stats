from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.ml.model_registry import get_model_registry
from app.models.conversation import Conversation, Message, SentimentAnalysis
from app.schemas.conversation import ConversationCreate, ConversationStats


class ConversationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.models = get_model_registry()

    def create(self, payload: ConversationCreate) -> Conversation:
        conversation = Conversation(title=payload.title)
        self.db.add(conversation)
        self.db.flush()

        for item in payload.messages:
            message = Message(conversation_id=conversation.id, role=item.role, content=item.content)
            self.db.add(message)
            self.db.flush()
            self._attach_sentiment(message)

        self.db.commit()
        return self._get_conversation(conversation.id)

    def list_conversations(self, agent_id: str | None = None) -> list[Conversation]:
        stmt = select(Conversation).options(selectinload(Conversation.messages).selectinload(Message.sentiment))
        if agent_id:
            stmt = stmt.where(Conversation.agent_id == agent_id)
        stmt = stmt.order_by(Conversation.created_at.desc())
        return list(self.db.scalars(stmt).all())

    def get(self, conversation_id: int) -> Conversation | None:
        return self._get_conversation(conversation_id)

    def delete(self, conversation_id: int) -> bool:
        conversation = self.db.get(Conversation, conversation_id)
        if conversation is None:
            return False
        self.db.delete(conversation)
        self.db.commit()
        return True

    def delete_by_agent(self, agent_id: str) -> int:
        stmt = select(Conversation.id).where(Conversation.agent_id == agent_id)
        ids = list(self.db.scalars(stmt).all())
        for conversation_id in ids:
            conversation = self.db.get(Conversation, conversation_id)
            if conversation is not None:
                self.db.delete(conversation)
        self.db.commit()
        return len(ids)

    def delete_all(self) -> int:
        stmt = select(Conversation.id)
        ids = list(self.db.scalars(stmt).all())
        for conversation_id in ids:
            conversation = self.db.get(Conversation, conversation_id)
            if conversation is not None:
                self.db.delete(conversation)
        self.db.commit()
        return len(ids)

    def get_random_sample(self, count: int = 5, agent_id: str | None = None) -> list[Conversation]:
        stmt = select(Conversation).options(
            selectinload(Conversation.messages).selectinload(Message.sentiment)
        )
        if agent_id:
            stmt = stmt.where(Conversation.agent_id == agent_id)
        stmt = stmt.order_by(func.random()).limit(count)
        return list(self.db.scalars(stmt).all())

    def add_message(self, conversation_id: int, role: str, content: str) -> Message | None:
        conversation = self._get_conversation(conversation_id)
        if conversation is None:
            return None

        message = Message(conversation_id=conversation_id, role=role, content=content)
        self.db.add(message)
        self.db.flush()
        self._attach_sentiment(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def get_stats(self, conversation_id: int) -> ConversationStats | None:
        conversation = self._get_conversation(conversation_id)
        if conversation is None:
            return None

        sentiments = [message.sentiment for message in conversation.messages if message.sentiment]
        positive = sum(1 for item in sentiments if item.label.upper() == "POSITIVE")
        negative = sum(1 for item in sentiments if item.label.upper() == "NEGATIVE")
        neutral = sum(1 for item in sentiments if item.label.upper() == "NEUTRAL")
        scores = [item.score for item in sentiments]
        stars = [item.stars for item in sentiments]
        average_score = sum(scores) / len(scores) if scores else None
        average_stars = sum(stars) / len(stars) if stars else None

        return ConversationStats(
            conversation_id=conversation.id,
            title=conversation.title,
            message_count=len(conversation.messages),
            positive_count=positive,
            negative_count=negative,
            neutral_count=neutral,
            average_sentiment_score=average_score,
            average_stars=average_stars,
        )

    def _attach_sentiment(self, message: Message) -> None:
        import json as _json

        # Only member (user) messages get sentiment/translation; agent replies are skipped.
        if message.from_agent or not message.content or not message.content.strip():
            return

        result = self.models.analyze_sentiment_v2(message.content)
        analysis = SentimentAnalysis(
            message_id=message.id,
            stars=int(result["stars"]),
            label=str(result["label"]),
            score=float(result["score"]),
            model_name=str(result["model_name"]),
            raw_label=str(result["raw_label"]) if result.get("raw_label") else None,
            raw_score=float(result["raw_score"]) if result.get("raw_score") is not None else None,
            low_confidence=bool(result.get("low_confidence", False)),
            polarity=str(result["polarity"]) if result.get("polarity") else None,
            polarity_score=float(result["polarity_score"]) if result.get("polarity_score") is not None else None,
            emotions_json=_json.dumps(result.get("emotions") or []),
            original_language=str(result["original_language"]) if result.get("original_language") else None,
            translated=bool(result.get("translated", False)),
        )
        self.db.add(analysis)

    def _get_conversation(self, conversation_id: int) -> Conversation | None:
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages).selectinload(Message.sentiment))
        )
        return self.db.scalar(stmt)
