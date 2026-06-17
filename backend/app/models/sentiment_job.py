from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SentimentJob(Base):
    __tablename__ = "sentiment_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(50), default="running")
    phase: Mapped[str] = mapped_column(String(50), default="sentiment")
    processed: Mapped[int] = mapped_column(Integer, default=0)
    limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    messages_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    reanalyze: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    phase_detail: Mapped[str | None] = mapped_column(Text)
    phase_progress: Mapped[int] = mapped_column(Integer, default=0)
    phase_total: Mapped[int] = mapped_column(Integer, default=0)
    scope_conversation_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
