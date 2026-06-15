from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InsightsJob(Base):
    __tablename__ = "insights_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(50), default="running")
    phase: Mapped[str] = mapped_column(String(50), default="metrics")
    processed: Mapped[int] = mapped_column(Integer, default=0)
    limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    messages_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    phase_detail: Mapped[str | None] = mapped_column(Text)
    phase_progress: Mapped[int] = mapped_column(Integer, default=0)
    phase_total: Mapped[int] = mapped_column(Integer, default=0)
    ingest_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    scope_conversation_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationMetrics(Base):
    __tablename__ = "conversation_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(String(36), index=True)
    start_stars: Mapped[int | None] = mapped_column(Integer)
    end_stars: Mapped[int | None] = mapped_column(Integer)
    delta_stars: Mapped[int | None] = mapped_column(Integer)
    avg_stars: Mapped[float | None] = mapped_column(Float)
    low_point_stars: Mapped[int | None] = mapped_column(Integer)
    high_point_stars: Mapped[int | None] = mapped_column(Integer)
    trajectory: Mapped[str | None] = mapped_column(String(50))
    timeline_json: Mapped[str | None] = mapped_column(Text)
    first_response_seconds: Mapped[float | None] = mapped_column(Float)
    median_response_seconds: Mapped[float | None] = mapped_column(Float)
    max_response_seconds: Mapped[float | None] = mapped_column(Float)
    unanswered_member_count: Mapped[int] = mapped_column(Integer, default=0)
    total_messages: Mapped[int] = mapped_column(Integer, default=0)
    member_messages: Mapped[int] = mapped_column(Integer, default=0)
    agent_messages: Mapped[int] = mapped_column(Integer, default=0)
    depth_ratio: Mapped[float | None] = mapped_column(Float)
    depth_bucket: Mapped[str | None] = mapped_column(String(20))
    intent_label: Mapped[str | None] = mapped_column(String(100))
    intent_score: Mapped[float | None] = mapped_column(Float)
    unanswered_question_count: Mapped[int] = mapped_column(Integer, default=0)
    unanswered_no_reply_count: Mapped[int] = mapped_column(Integer, default=0)
    unanswered_weak_answer_count: Mapped[int] = mapped_column(Integer, default=0)
    unanswered_semantic_count: Mapped[int] = mapped_column(Integer, default=0)
    last_unanswered_question_text: Mapped[str | None] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UnansweredQuestion(Base):
    __tablename__ = "unanswered_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), index=True)
    question_text: Mapped[str] = mapped_column(Text)
    agent_reply_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    agent_reply_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50))
    similarity_score: Mapped[float | None] = mapped_column(Float)
    nli_label: Mapped[str | None] = mapped_column(String(100))
    nli_score: Mapped[float | None] = mapped_column(Float)
    intent_label: Mapped[str | None] = mapped_column(String(100))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestionCluster(Base):
    __tablename__ = "question_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    insights_job_id: Mapped[int] = mapped_column(ForeignKey("insights_jobs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(String(36), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer)
    representative_text: Mapped[str] = mapped_column(Text)
    examples_json: Mapped[str | None] = mapped_column(Text)
    intent_label: Mapped[str | None] = mapped_column(String(100))
    intent_score: Mapped[float | None] = mapped_column(Float)
