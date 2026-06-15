"""Per-agent incremental ingest watermarks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_ingest_state import AgentIngestState
from app.models.ingestion_job import IngestionJob

logger = logging.getLogger(__name__)


class AgentIngestStateService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create(self, agent_id: str) -> AgentIngestState:
        state = self.db.get(AgentIngestState, agent_id)
        if state is None:
            state = AgentIngestState(agent_id=agent_id)
            self.db.add(state)
            self.db.flush()
        return state

    def get_inbox_start_date(self, agent_id: str) -> datetime | None:
        state = self.get_or_create(agent_id)
        if state.last_sync_completed_at is not None:
            return state.last_sync_completed_at

        # Backfill watermark from prior runs before incremental sync existed.
        last_job = self.db.scalar(
            select(IngestionJob)
            .where(IngestionJob.agent_id == agent_id, IngestionJob.status == "complete")
            .order_by(IngestionJob.completed_at.desc())
            .limit(1)
        )
        if last_job and last_job.completed_at:
            logger.info(
                "Using last ingest job %s completed_at as sync watermark for agent %s",
                last_job.id,
                agent_id,
            )
            return last_job.completed_at
        return None

    def mark_sync_started(self, agent_id: str) -> datetime:
        started_at = datetime.now(timezone.utc)
        state = self.get_or_create(agent_id)
        state.last_sync_started_at = started_at
        self.db.commit()
        logger.info(
            "Ingest sync started for agent %s (watermark=%s)",
            agent_id,
            state.last_sync_completed_at,
        )
        return started_at

    def mark_sync_completed(
        self,
        agent_id: str,
        sync_started_at: datetime,
        *,
        imported: int,
        skipped: int,
    ) -> None:
        state = self.get_or_create(agent_id)
        state.last_sync_completed_at = sync_started_at
        state.last_conversations_imported = imported
        state.last_conversations_skipped = skipped
        state.total_conversations_imported += imported
        self.db.commit()
        logger.info(
            "Ingest sync completed for agent %s: imported=%s skipped=%s next_start_date=%s",
            agent_id,
            imported,
            skipped,
            sync_started_at.isoformat(),
        )

    def reset(self, agent_id: str) -> None:
        state = self.db.get(AgentIngestState, agent_id)
        if state is not None:
            self.db.delete(state)
            self.db.commit()
