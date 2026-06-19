import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.locks import clear_job_cancel, is_job_cancelled, release_agent_job_lock, request_job_cancel
from app.pubsub import publish_job_progress
from app.services.intent_service import IntentService
from app.services.job_concurrency import admit_and_create
from app.utils.referred_conversations import referred_conversation_ids

logger = logging.getLogger(__name__)


class ReferredIntentService:
    """Label intents for doorverwezen (referred) conversations only."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.intents = IntentService(db)

    def create_job(
        self,
        agent_id: str,
        *,
        conversation_ids: list[int] | None = None,
        reanalyze: bool = False,
        wait_for_slot: bool = True,
    ):
        from app.models.referred_intent_job import ReferredIntentJob

        scope_json = json.dumps(conversation_ids) if conversation_ids else None

        def _persist():
            job = ReferredIntentJob(
                agent_id=agent_id,
                status="running",
                phase="intents",
                phase_detail="Starting referred intent labeling",
                phase_progress=0,
                phase_total=0,
                reanalyze=reanalyze,
                scope_conversation_ids=scope_json,
            )
            self.db.add(job)
            self.db.commit()
            self.db.refresh(job)
            return job

        return admit_and_create(self.db, "intent", agent_id, _persist, wait_for_slot=wait_for_slot)

    @staticmethod
    def scoped_conversation_ids(job) -> list[int] | None:
        if not job.scope_conversation_ids:
            return None
        return json.loads(job.scope_conversation_ids)

    def get_job(self, job_id: int):
        from app.models.referred_intent_job import ReferredIntentJob

        return self.db.get(ReferredIntentJob, job_id)

    def get_latest_job(self, agent_id: str | None = None):
        from sqlalchemy import select

        from app.models.referred_intent_job import ReferredIntentJob

        stmt = select(ReferredIntentJob).order_by(ReferredIntentJob.created_at.desc()).limit(1)
        if agent_id:
            stmt = (
                select(ReferredIntentJob)
                .where(ReferredIntentJob.agent_id == agent_id)
                .order_by(ReferredIntentJob.created_at.desc())
                .limit(1)
            )
        return self.db.scalar(stmt)

    def list_running_jobs(self, agent_id: str | None = None):
        from sqlalchemy import select

        from app.models.referred_intent_job import ReferredIntentJob

        stmt = (
            select(ReferredIntentJob)
            .where(ReferredIntentJob.status == "running")
            .order_by(ReferredIntentJob.created_at.desc())
        )
        if agent_id:
            stmt = stmt.where(ReferredIntentJob.agent_id == agent_id)
        return list(self.db.scalars(stmt).all())

    def cancel_job(self, job_id: int):
        job = self.get_job(job_id)
        if job is None:
            return None
        if job.status != "running":
            return job

        request_job_cancel("intent", job_id)
        job.status = "cancelled"
        job.phase_detail = "Cancelled by user"
        job.error = "Cancelled by user"
        job.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        release_agent_job_lock(job.agent_id, "intent")
        self._publish(job, "done")
        logger.info("Referred intent job %s cancelled for agent %s", job_id, job.agent_id)
        return job

    def run_job(self, job_id: int) -> None:
        job = self.get_job(job_id)
        if job is None:
            return

        job_start = time.monotonic()
        logger.info("Referred intent job %s started for agent %s", job_id, job.agent_id)

        try:
            scoped = self.scoped_conversation_ids(job)
            target_ids = referred_conversation_ids(
                self.db,
                job.agent_id,
                conversation_ids=scoped,
                skip_labeled=not job.reanalyze,
            )
            job.limit = len(target_ids)
            self._update_phase(
                job,
                "intents",
                f"Labeling intents for {len(target_ids)} doorverwezen gesprekken",
                0,
                len(target_ids),
            )

            if not target_ids:
                job.phase_detail = "No doorverwezen gesprekken need intent labeling"
                self._complete(job)
                return

            def on_progress(current: int, total: int, detail: str) -> None:
                if self._check_cancelled(job):
                    raise _JobCancelled()
                job.processed = current
                job.messages_analyzed = current
                self._update_phase(job, "intents", detail, current, total)

            labeled = self.intents.label_conversations(
                job.agent_id,
                conversation_ids=target_ids,
                on_progress=on_progress,
                should_cancel=lambda: is_job_cancelled("intent", job.id),
            )
            job.messages_analyzed = labeled
            job.processed = len(target_ids)
            self._complete(job)
            logger.info(
                "Referred intent job %s completed in %.1fs (%s labeled / %s targeted)",
                job_id,
                time.monotonic() - job_start,
                labeled,
                len(target_ids),
            )
        except _JobCancelled:
            self._check_cancelled(job)
        except Exception as exc:
            logger.exception("Referred intent job %s failed after %.1fs", job_id, time.monotonic() - job_start)
            self.db.rollback()
            job.status = "failed"
            job.phase_detail = f"Failed: {str(exc)[:500]}"
            job.error = str(exc)[:2000]
            job.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            self._publish(job, "done")

    def _complete(self, job) -> None:
        job.status = "complete"
        job.phase = "done"
        job.phase_detail = "Referred intent labeling complete"
        job.phase_progress = job.phase_total
        job.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        release_agent_job_lock(job.agent_id, "intent")
        self._publish(job, "done")

    def _check_cancelled(self, job) -> bool:
        if not is_job_cancelled("intent", job.id):
            return False
        job.status = "cancelled"
        job.phase_detail = "Cancelled by user"
        job.error = "Cancelled by user"
        job.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        clear_job_cancel("intent", job.id)
        release_agent_job_lock(job.agent_id, "intent")
        self._publish(job, "done")
        logger.info("Referred intent job %s stopped (cancelled)", job.id)
        return True

    def _update_phase(self, job, phase: str, detail: str, progress: int, total: int) -> None:
        job.phase = phase
        job.phase_detail = detail
        job.phase_progress = progress
        job.phase_total = total
        self.db.commit()
        self._publish(job, "progress")

    def _publish(self, job, event: str) -> None:
        publish_job_progress(
            "intent",
            job.id,
            event,
            {
                "status": job.status,
                "phase": job.phase,
                "phase_detail": job.phase_detail,
                "phase_progress": job.phase_progress,
                "phase_total": job.phase_total,
                "processed": job.processed,
                "limit": job.limit,
                "failed": job.failed,
                "messages_analyzed": job.messages_analyzed,
                "reanalyze": job.reanalyze,
                "error": job.error,
                "created_at": job.created_at,
                "completed_at": job.completed_at,
            },
        )


class _JobCancelled(Exception):
    pass
