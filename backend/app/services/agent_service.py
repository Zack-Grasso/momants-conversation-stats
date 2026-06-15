import logging

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.cache import cache_delete, cache_delete_prefix, insights_cache_key
from app.locks import release_agent_job_lock
from app.models.agent_ingest_state import AgentIngestState
from app.models.conversation import Conversation
from app.models.ingestion_job import IngestionJob
from app.models.insights import ConversationMetrics, InsightsJob, QuestionCluster, UnansweredQuestion
from app.services.ingestion_service import IngestionService
from app.services.insights_service import InsightsService

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.ingestion = IngestionService(db)
        self.insights = InsightsService(db)

    def purge_insights(self, agent_id: str, *, cancel_running: bool = True) -> dict[str, int]:
        cancelled = self._cancel_running_jobs(agent_id) if cancel_running else []
        if cancelled:
            logger.info("Cancelled jobs for agent %s before purge: %s", agent_id, cancelled)

        conversation_ids = select(Conversation.id).where(Conversation.agent_id == agent_id)

        metrics_deleted = self.db.execute(
            delete(ConversationMetrics).where(
                or_(
                    ConversationMetrics.agent_id == agent_id,
                    ConversationMetrics.conversation_id.in_(conversation_ids),
                )
            )
        ).rowcount
        unanswered_deleted = self.db.execute(
            delete(UnansweredQuestion).where(
                or_(
                    UnansweredQuestion.agent_id == agent_id,
                    UnansweredQuestion.conversation_id.in_(conversation_ids),
                )
            )
        ).rowcount
        clusters_deleted = self.db.execute(
            delete(QuestionCluster).where(QuestionCluster.agent_id == agent_id)
        ).rowcount
        insights_jobs_deleted = self.db.execute(
            delete(InsightsJob).where(InsightsJob.agent_id == agent_id)
        ).rowcount
        ingest_jobs_deleted = self.db.execute(
            delete(IngestionJob).where(IngestionJob.agent_id == agent_id)
        ).rowcount
        ingest_state_deleted = self.db.execute(
            delete(AgentIngestState).where(AgentIngestState.agent_id == agent_id)
        ).rowcount

        self.db.commit()
        self._clear_agent_cache(agent_id)

        deleted = {
            "metrics": metrics_deleted or 0,
            "unanswered": unanswered_deleted or 0,
            "question_clusters": clusters_deleted or 0,
            "insights_jobs": insights_jobs_deleted or 0,
            "ingest_jobs": ingest_jobs_deleted or 0,
            "ingest_state": ingest_state_deleted or 0,
        }
        logger.info("Purged insights for agent %s: %s", agent_id, deleted)
        return deleted

    def purge_conversations(self, agent_id: str) -> int:
        stmt = select(Conversation.id).where(Conversation.agent_id == agent_id)
        ids = list(self.db.scalars(stmt).all())
        for conversation_id in ids:
            conversation = self.db.get(Conversation, conversation_id)
            if conversation is not None:
                self.db.delete(conversation)
        self.db.commit()
        return len(ids)

    def purge_all(self, agent_id: str) -> dict[str, int]:
        self._cancel_running_jobs(agent_id)
        conversations_deleted = self.purge_conversations(agent_id)
        insights_deleted = self.purge_insights(agent_id, cancel_running=False)
        release_agent_job_lock(agent_id, "ingest")
        release_agent_job_lock(agent_id, "insights")
        release_agent_job_lock(agent_id, "pipeline")
        return {
            **insights_deleted,
            "conversations": conversations_deleted,
        }

    def _cancel_running_jobs(self, agent_id: str) -> list[str]:
        cancelled: list[str] = []
        for job in self.ingestion.list_running_jobs(agent_id):
            self.ingestion.cancel_job(job.id)
            cancelled.append(f"ingest:{job.id}")
        for job in self.insights.list_running_jobs(agent_id):
            self.insights.cancel_job(job.id)
            cancelled.append(f"insights:{job.id}")
        return cancelled

    def clear_agent_cache(self, agent_id: str) -> None:
        cache_delete(insights_cache_key(agent_id, "overview"))
        cache_delete(insights_cache_key(agent_id, "questions"))
        cache_delete_prefix(f"insights:{agent_id}:")
        cache_delete_prefix(f"conversations:{agent_id}:")

    def _clear_agent_cache(self, agent_id: str) -> None:
        self.clear_agent_cache(agent_id)
