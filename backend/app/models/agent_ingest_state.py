from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentIngestState(Base):
    """Tracks incremental ingest watermarks per agent."""

    __tablename__ = "agent_ingest_states"

    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    last_sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_conversations_imported: Mapped[int] = mapped_column(Integer, default=0)
    last_conversations_skipped: Mapped[int] = mapped_column(Integer, default=0)
    total_conversations_imported: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
