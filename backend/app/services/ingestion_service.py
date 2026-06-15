import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.integrations.message_normalizer import normalize_message_content
from app.integrations.momants_client import get_momants_client
from app.ml.model_registry import get_model_registry
from app.models.conversation import Conversation, Message, SentimentAnalysis
from app.models.ingestion_job import IngestionJob
from app.locks import clear_job_cancel, is_job_cancelled, release_agent_job_lock, request_job_cancel
from app.pubsub import publish_job_progress
from app.services.job_concurrency import admit_and_create
from app.services.metrics_service import MetricsService

logger = logging.getLogger(__name__)


def resolve_integration_type(entry: dict, details: dict | None) -> str | None:
    """Resolve the messaging channel for a conversation.

    The inbox listing only labels ``messaging_integration_type`` for the embedded web
    widget ("Embedded chat"); for messaging integrations (WhatsApp, etc.) it comes back
    empty, which previously got stored verbatim and then defaulted to "chat" downstream.
    When the listing has no label, fall back to the conversation detail's member_information:
    a phone number is only present for WhatsApp conversations.
    """
    raw = (entry.get("messaging_integration_type") or "").strip()
    if raw:
        return raw

    member_information = (details or {}).get("member_information") or {}
    if member_information.get("phone"):
        return "WhatsApp"

    return None


class IngestionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.client = get_momants_client()
        self.models = get_model_registry()
        self.metrics = MetricsService(db)

    def create_job(
        self,
        agent_id: str,
        limit: int,
        reanalyze: bool = False,
        *,
        wait_for_slot: bool = True,
        sync_start_date: datetime | None = None,
    ) -> IngestionJob:
        capped = min(limit, self.settings.ingestion_max_conversations)

        def _persist() -> IngestionJob:
            job = IngestionJob(
                agent_id=agent_id,
                limit=capped,
                reanalyze=reanalyze,
                status="running",
                sync_start_date=sync_start_date,
            )
            self.db.add(job)
            self.db.commit()
            self.db.refresh(job)
            return job

        return admit_and_create(self.db, "ingest", agent_id, _persist, wait_for_slot=wait_for_slot)

    def get_job(self, job_id: int) -> IngestionJob | None:
        return self.db.get(IngestionJob, job_id)

    def get_latest_job(self, agent_id: str | None = None) -> IngestionJob | None:
        stmt = select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(1)
        if agent_id:
            stmt = (
                select(IngestionJob)
                .where(IngestionJob.agent_id == agent_id)
                .order_by(IngestionJob.created_at.desc())
                .limit(1)
            )
        return self.db.scalar(stmt)

    def list_running_jobs(self, agent_id: str | None = None) -> list[IngestionJob]:
        stmt = select(IngestionJob).where(IngestionJob.status == "running").order_by(IngestionJob.created_at.desc())
        if agent_id:
            stmt = stmt.where(IngestionJob.agent_id == agent_id)
        return list(self.db.scalars(stmt).all())

    def cancel_job(self, job_id: int) -> IngestionJob | None:
        job = self.get_job(job_id)
        if job is None:
            return None
        if job.status != "running":
            return job

        request_job_cancel("ingest", job_id)
        job.status = "cancelled"
        job.error = "Cancelled by user"
        job.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        release_agent_job_lock(job.agent_id, "ingest")
        self._publish(job, "done")
        logger.info("Ingestion job %s cancelled for agent %s", job_id, job.agent_id)
        return job

    def run_job(
        self,
        job_id: int,
        *,
        skip: int = 0,
        entries: list[dict] | None = None,
        sync_start_date: datetime | None = None,
    ) -> list[dict]:
        job = self.get_job(job_id)
        if job is None:
            return []

        processed_entries: list[dict] = []
        try:
            batch_entries = entries
            if batch_entries is None:
                batch_entries = self.client.collect_conversation_ids(
                    job.agent_id,
                    job.limit,
                    skip=skip,
                    start_date=sync_start_date or job.sync_start_date,
                )
            for entry in batch_entries:
                if self._check_cancelled(job):
                    return processed_entries
                try:
                    analyzed = self._process_conversation(job, entry)
                    if analyzed is None:
                        continue
                    processed_entries.append(entry)
                    job.processed += 1
                    job.messages_analyzed += analyzed
                except Exception as exc:
                    logger.exception("Failed to ingest conversation %s", entry.get("conversation_id"))
                    # Roll back the poisoned transaction before touching the job row, so one
                    # bad conversation can't cascade into PendingRollbackError for the batch.
                    self.db.rollback()
                    job.failed += 1
                self.db.commit()
                self._publish(job, "progress")

            if self._check_cancelled(job):
                return processed_entries

            if job.failed:
                job.error = f"{job.failed} conversation(s) failed during ingest (API timeout or error)"
            else:
                job.error = None
            job.status = "complete"
            job.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            self._publish(job, "done")
        except Exception as exc:
            logger.exception("Ingestion job %s failed", job_id)
            # Ensure the session is usable so the failure status can actually be persisted.
            self.db.rollback()
            job.status = "failed"
            job.error = str(exc)[:2000]
            job.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            self._publish(job, "done")
        return processed_entries

    def _process_conversation(self, job: IngestionJob, entry: dict) -> int | None:
        conversation_id = entry["conversation_id"]
        existing = self._get_conversation(conversation_id)
        if self._should_skip_fetch(job, entry, existing):
            self._upsert_conversation(job.agent_id, entry)
            job.skipped += 1
            self.db.commit()
            return None

        details, raw_messages = self.client.fetch_conversation(job.agent_id, conversation_id)
        integration_type = resolve_integration_type(entry, details)
        conversation = self._upsert_conversation(job.agent_id, entry, integration_type)
        member_messages: list[Message] = []

        for raw in raw_messages:
            message = self._upsert_message(conversation, raw)
            if not message.from_agent and message.content.strip():
                member_messages.append(message)

        # Commit before ML so threads do not hold DB row locks while waiting on model inference.
        self.db.commit()

        pending: list[Message] = []
        for message in member_messages:
            existing = self._get_sentiment(message.id)
            if existing and not job.reanalyze:
                continue
            if existing and job.reanalyze:
                self.db.delete(existing)
            pending.append(message)

        analyzed = 0
        if pending:
            # Flush any pending reanalyze deletes so the upsert below doesn't collide with
            # rows we're about to remove in the same transaction.
            self.db.flush()
            results = self.models.analyze_sentiment_batch([message.content for message in pending])
            rows = [
                {
                    "message_id": message.id,
                    "stars": int(result["stars"]),
                    "label": str(result["label"]),
                    "score": float(result["score"]),
                    "model_name": str(result["model_name"]),
                    "raw_label": str(result["raw_label"]) if result.get("raw_label") else None,
                    "raw_score": float(result["raw_score"]) if result.get("raw_score") is not None else None,
                    "low_confidence": bool(result.get("low_confidence", False)),
                }
                for message, result in zip(pending, results, strict=True)
            ]
            if rows:
                # Idempotent insert: if another batch concurrently wrote sentiment for the
                # same message_id (UNIQUE), skip it instead of raising and poisoning the session.
                stmt = pg_insert(SentimentAnalysis).values(rows).on_conflict_do_nothing(
                    index_elements=["message_id"]
                )
                result_proxy = self.db.execute(stmt)
                analyzed = result_proxy.rowcount if result_proxy.rowcount is not None else len(rows)

        self.metrics.compute_for_conversation(conversation.id, tier1_only=True)
        self.db.commit()
        time.sleep(self.settings.ingestion_fetch_delay_seconds)
        return analyzed

    def _check_cancelled(self, job: IngestionJob) -> bool:
        if not is_job_cancelled("ingest", job.id):
            return False
        job.status = "cancelled"
        job.error = "Cancelled by user"
        job.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        clear_job_cancel("ingest", job.id)
        self._publish(job, "done")
        logger.info("Ingestion job %s stopped (cancelled)", job.id)
        return True

    def _publish(self, job: IngestionJob, event: str) -> None:
        publish_job_progress(
            "ingest",
            job.id,
            event,
            {
                "status": job.status,
                "phase": "ingest",
                "processed": job.processed,
                "skipped": job.skipped,
                "limit": job.limit,
                "failed": job.failed,
                "messages_analyzed": job.messages_analyzed,
                "error": job.error,
                "created_at": job.created_at,
                "completed_at": job.completed_at,
            },
        )

    def _get_sentiment(self, message_id: int) -> SentimentAnalysis | None:
        stmt = select(SentimentAnalysis).where(SentimentAnalysis.message_id == message_id)
        return self.db.scalar(stmt)

    def _get_conversation(self, external_id: str) -> Conversation | None:
        return self.db.scalar(select(Conversation).where(Conversation.external_id == external_id))

    @staticmethod
    def _should_skip_fetch(job: IngestionJob, entry: dict, conversation: Conversation | None) -> bool:
        if job.reanalyze or conversation is None:
            return False

        entry_last_seen = _parse_datetime(entry.get("last_seen"))
        if entry_last_seen is None or conversation.source_last_seen is None:
            return False

        return entry_last_seen <= conversation.source_last_seen

    def _upsert_conversation(
        self, agent_id: str, entry: dict, integration_type: str | None = None
    ) -> Conversation:
        external_id = entry["conversation_id"]
        stmt = select(Conversation).where(Conversation.external_id == external_id)
        conversation = self.db.scalar(stmt)

        member_name = entry.get("member_name") or "Unknown"
        title = member_name if member_name else f"Conversation {external_id[:8]}"

        if conversation is None:
            conversation = Conversation(
                external_id=external_id,
                agent_id=agent_id,
                title=title[:255],
                member_name=member_name,
                integration_type=integration_type,
                resolved=entry.get("resolved"),
                takeover=entry.get("takeover"),
            )
            self.db.add(conversation)
        else:
            conversation.agent_id = agent_id
            conversation.title = title[:255]
            conversation.member_name = member_name
            # Only overwrite when we resolved a channel. The skip path has no conversation
            # detail to derive from, so it must not clobber a previously-stored value with None.
            if integration_type is not None:
                conversation.integration_type = integration_type
            conversation.resolved = entry.get("resolved")
            conversation.takeover = entry.get("takeover")

        last_seen = entry.get("last_seen")
        if last_seen:
            conversation.source_last_seen = _parse_datetime(last_seen)

        self.db.flush()
        return conversation

    def _upsert_message(self, conversation: Conversation, raw: dict) -> Message:
        external_id = raw["id"]
        stmt = select(Message).where(Message.external_id == external_id)
        message = self.db.scalar(stmt)

        from_agent = bool(raw.get("from_agent", False))
        role = "agent" if from_agent else "member"
        content = normalize_message_content(raw.get("message_content"))
        source_created_at = _parse_datetime(raw.get("created_at"))

        if message is None:
            message = Message(
                external_id=external_id,
                conversation_id=conversation.id,
                role=role,
                from_agent=from_agent,
                content=content,
                source_created_at=source_created_at,
            )
            self.db.add(message)
        else:
            message.role = role
            message.from_agent = from_agent
            message.content = content
            message.source_created_at = source_created_at

        self.db.flush()
        return message


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
