from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.integrations.message_normalizer import normalize_message_content
from app.integrations.momants_client import get_momants_client
from app.services.ingestion_service import resolve_integration_type
from app.weekly.models import WeeklyAgentRun, WeeklyConversation, WeeklyMessage

logger = logging.getLogger(__name__)


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class WeeklyIngestService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.client = get_momants_client()

    def ingest_agent_window(
        self,
        agent_run: WeeklyAgentRun,
        *,
        since: datetime,
        until: datetime,
    ) -> int:
        self.db.execute(
            delete(WeeklyMessage).where(
                WeeklyMessage.conversation_id.in_(
                    select(WeeklyConversation.id).where(WeeklyConversation.agent_run_id == agent_run.id)
                )
            )
        )
        self.db.execute(delete(WeeklyConversation).where(WeeklyConversation.agent_run_id == agent_run.id))
        self.db.commit()

        entries = self.client.collect_inbox_entries(
            agent_run.agent_id,
            self.settings.weekly_unanswered_max_conversations,
            start_date=since,
            end_date=until,
        )
        imported = 0
        for entry in entries:
            try:
                count = self._process_entry(agent_run, entry)
                if count is not None:
                    imported += 1
            except Exception:
                logger.exception("Weekly ingest failed for conversation %s", entry.get("conversation_id"))
            time.sleep(self.settings.ingestion_fetch_delay_seconds)
        self.db.commit()
        return imported

    def _process_entry(self, agent_run: WeeklyAgentRun, entry: dict) -> int | None:
        conversation_id = entry.get("conversation_id")
        if not conversation_id:
            return None
        details, raw_messages = self.client.fetch_conversation(agent_run.agent_id, conversation_id)
        integration_type = resolve_integration_type(entry, details)
        conversation = WeeklyConversation(
            agent_run_id=agent_run.id,
            external_id=conversation_id,
            agent_id=agent_run.agent_id,
            title=(entry.get("member_name") or f"Conversation {conversation_id[:8]}")[:255],
            integration_type=integration_type,
        )
        self.db.add(conversation)
        self.db.flush()

        for raw in raw_messages:
            from_agent = bool(raw.get("from_agent", False))
            message = WeeklyMessage(
                conversation_id=conversation.id,
                external_id=raw.get("id"),
                role="agent" if from_agent else "member",
                from_agent=from_agent,
                content=normalize_message_content(raw.get("message_content")),
                source_created_at=_parse_datetime(raw.get("created_at")),
            )
            self.db.add(message)
        self.db.flush()
        return len(raw_messages)

    def load_conversations(self, agent_run_id: int) -> list[WeeklyConversation]:
        return list(
            self.db.scalars(
                select(WeeklyConversation)
                .where(WeeklyConversation.agent_run_id == agent_run_id)
                .options(selectinload(WeeklyConversation.messages))
            ).all()
        )
