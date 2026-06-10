from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
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

    def list_conversations(self) -> list[Conversation]:
        stmt = select(Conversation).options(selectinload(Conversation.messages).selectinload(Message.sentiment))
        return list(self.db.scalars(stmt).all())

    def get(self, conversation_id: int) -> Conversation | None:
        return self._get_conversation(conversation_id)

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
        scores = [item.score for item in sentiments]
        average = sum(scores) / len(scores) if scores else None

        return ConversationStats(
            conversation_id=conversation.id,
            title=conversation.title,
            message_count=len(conversation.messages),
            positive_count=positive,
            negative_count=negative,
            average_sentiment_score=average,
        )

    def _attach_sentiment(self, message: Message) -> None:
        result = self.models.analyze_sentiment(message.content)
        analysis = SentimentAnalysis(
            message_id=message.id,
            label=str(result["label"]),
            score=float(result["score"]),
            model_name=get_settings().sentiment_model,
        )
        self.db.add(analysis)

    def _get_conversation(self, conversation_id: int) -> Conversation | None:
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages).selectinload(Message.sentiment))
        )
        return self.db.scalar(stmt)
