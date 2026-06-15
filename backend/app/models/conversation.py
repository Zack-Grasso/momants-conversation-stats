from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(255))
    member_name: Mapped[str | None] = mapped_column(String(255))
    integration_type: Mapped[str | None] = mapped_column(String(100))
    resolved: Mapped[bool | None] = mapped_column(Boolean)
    takeover: Mapped[bool | None] = mapped_column(Boolean)
    source_last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(50))
    from_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[str] = mapped_column(Text)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    sentiment: Mapped["SentimentAnalysis | None"] = relationship(
        back_populates="message", uselist=False, cascade="all, delete-orphan"
    )


class SentimentAnalysis(Base):
    __tablename__ = "sentiment_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    stars: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(50))
    score: Mapped[float] = mapped_column(Float)
    model_name: Mapped[str] = mapped_column(String(255))
    raw_label: Mapped[str | None] = mapped_column(String(100))
    raw_score: Mapped[float | None] = mapped_column(Float)
    low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    message: Mapped["Message"] = relationship(back_populates="sentiment")
