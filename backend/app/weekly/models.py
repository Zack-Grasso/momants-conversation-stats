from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.weekly.database import WeeklyBase


class WeeklyRun(WeeklyBase):
    __tablename__ = "weekly_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="running")
    summary_json: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agent_runs: Mapped[list["WeeklyAgentRun"]] = relationship(back_populates="weekly_run")


class WeeklyAgentRun(WeeklyBase):
    __tablename__ = "weekly_agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    weekly_run_id: Mapped[int] = mapped_column(ForeignKey("weekly_runs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="running")
    error: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(512))
    value_stats_json: Mapped[str | None] = mapped_column(Text)
    counts_json: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    weekly_run: Mapped["WeeklyRun"] = relationship(back_populates="agent_runs")
    conversations: Mapped[list["WeeklyConversation"]] = relationship(back_populates="agent_run")
    findings: Mapped[list["WeeklyUnansweredFinding"]] = relationship(back_populates="agent_run")
    clusters: Mapped[list["WeeklyQuestionCluster"]] = relationship(back_populates="agent_run")


class WeeklyConversation(WeeklyBase):
    __tablename__ = "weekly_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("weekly_agent_runs.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(255))
    integration_type: Mapped[str | None] = mapped_column(String(100))

    agent_run: Mapped["WeeklyAgentRun"] = relationship(back_populates="conversations")
    messages: Mapped[list["WeeklyMessage"]] = relationship(back_populates="conversation")


class WeeklyMessage(WeeklyBase):
    __tablename__ = "weekly_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("weekly_conversations.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(50))
    from_agent: Mapped[bool] = mapped_column(default=False)
    content: Mapped[str] = mapped_column(Text)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation: Mapped["WeeklyConversation"] = relationship(back_populates="messages")


class WeeklyUnansweredFinding(WeeklyBase):
    __tablename__ = "weekly_unanswered_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("weekly_agent_runs.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("weekly_conversations.id", ondelete="CASCADE"))
    message_id: Mapped[int] = mapped_column(ForeignKey("weekly_messages.id", ondelete="CASCADE"))
    question_text: Mapped[str] = mapped_column(Text)
    agent_reply_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50))
    similarity_score: Mapped[float | None] = mapped_column(Float)
    nli_label: Mapped[str | None] = mapped_column(String(100))
    nli_score: Mapped[float | None] = mapped_column(Float)

    agent_run: Mapped["WeeklyAgentRun"] = relationship(back_populates="findings")


class WeeklyQuestionCluster(WeeklyBase):
    __tablename__ = "weekly_question_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("weekly_agent_runs.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer)
    representative_text: Mapped[str] = mapped_column(Text)

    agent_run: Mapped["WeeklyAgentRun"] = relationship(back_populates="clusters")
