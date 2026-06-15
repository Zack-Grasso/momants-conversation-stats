"""Full-system purge and job cancellation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.locks import get_lock_client, request_job_cancel
from app.scheduler_control import pause_scheduler, resume_scheduler
from app.services.ingestion_service import IngestionService
from app.services.insights_service import InsightsService

logger = logging.getLogger(__name__)

TRUNCATE_TABLES = (
    "sentiment_analyses",
    "messages",
    "conversation_metrics",
    "unanswered_questions",
    "question_clusters",
    "conversations",
    "ingestion_jobs",
    "insights_jobs",
    "agent_ingest_states",
)


class SystemService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.ingestion = IngestionService(db)
        self.insights = InsightsService(db)

    def stop_scheduler_and_jobs(self) -> dict:
        pause_scheduler()
        cancelled = self.cancel_all_running_jobs()
        locks_cleared = self._clear_job_locks()
        logger.info("Scheduler stopped: cancelled=%s locks=%s", cancelled, locks_cleared)
        return {"paused": True, "cancelled_jobs": cancelled, "pipeline_locks_cleared": locks_cleared}

    def resume_scheduler(self) -> dict:
        resume_scheduler()
        locks_cleared = 0
        if len(self.ingestion.list_running_jobs()) + len(self.insights.list_running_jobs()) == 0:
            locks_cleared = self._clear_job_locks()
        return {"paused": False, "pipeline_locks_cleared": locks_cleared}

    def scheduler_status(self) -> dict:
        from app.scheduler_control import is_scheduler_paused

        return {
            "paused": is_scheduler_paused(),
            "running_ingest_jobs": len(self.ingestion.list_running_jobs()),
            "running_insights_jobs": len(self.insights.list_running_jobs()),
        }

    def cancel_all_running_jobs(self) -> list[str]:
        cancelled: list[str] = []
        now = datetime.now(timezone.utc)
        for job in self.ingestion.list_running_jobs():
            request_job_cancel("ingest", job.id)
            job.status = "cancelled"
            job.error = "Cancelled by system stop"
            job.completed_at = now
            cancelled.append(f"ingest:{job.id}")
        for job in self.insights.list_running_jobs():
            request_job_cancel("insights", job.id)
            job.status = "cancelled"
            job.error = "Cancelled by system stop"
            job.phase_detail = "Cancelled by system stop"
            job.completed_at = now
            cancelled.append(f"insights:{job.id}")
        self.db.commit()
        return cancelled

    def purge_everything(self) -> dict:
        pause_scheduler()
        cancelled = self.cancel_all_running_jobs()
        terminated_sessions = self._terminate_other_db_sessions()
        self.db.execute(text(f"TRUNCATE TABLE {', '.join(TRUNCATE_TABLES)} RESTART IDENTITY CASCADE"))
        self.db.commit()
        redis_databases = self._flush_redis()
        pause_scheduler()
        locks_cleared = self._clear_job_locks()
        logger.info("System purge complete: cancelled=%s", cancelled)
        return {
            "purged": True,
            "cancelled_jobs": cancelled,
            "terminated_db_sessions": terminated_sessions,
            "tables_truncated": list(TRUNCATE_TABLES),
            "redis_databases_flushed": redis_databases,
            "pipeline_locks_cleared": locks_cleared,
        }

    def _terminate_other_db_sessions(self) -> int:
        """Drop other connections so TRUNCATE is not blocked by in-flight ingest threads."""
        result = self.db.execute(
            text(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND pid <> pg_backend_pid()
                """
            )
        )
        terminated = sum(1 for row in result if row[0])
        if terminated:
            logger.info("Terminated %s other DB session(s) before purge truncate", terminated)
        return terminated

    def _clear_job_locks(self) -> int:
        client = get_lock_client()
        cleared = 0
        for key in client.scan_iter("job:*"):
            client.delete(key)
            cleared += 1
        return cleared

    def _flush_redis(self) -> list[int]:
        base = get_settings().redis_url.rsplit("/", 1)[0]
        flushed: list[int] = []
        for db_index in (0, 1, 2, 3):
            Redis.from_url(f"{base}/{db_index}", decode_responses=True).flushdb()
            flushed.append(db_index)
        return flushed
