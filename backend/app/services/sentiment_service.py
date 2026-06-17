import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.locks import clear_job_cancel, is_job_cancelled, release_agent_job_lock, request_job_cancel
from app.ml.model_registry import get_model_registry
from app.models.conversation import Conversation, Message, SentimentAnalysis
from app.models.sentiment_job import SentimentJob
from app.pubsub import publish_job_progress
from app.services.job_concurrency import admit_and_create

logger = logging.getLogger(__name__)

# Messages analyzed per chunk; bounds memory and gives steady progress updates.
SENTIMENT_CHUNK_SIZE = 50


class SentimentService:
    """Stage 2: language detection -> translation -> dual polarity + emotion analysis."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.models = get_model_registry()

    def create_job(
        self,
        agent_id: str,
        *,
        conversation_ids: list[int] | None = None,
        reanalyze: bool = False,
        wait_for_slot: bool = True,
    ) -> SentimentJob:
        scope_json = json.dumps(conversation_ids) if conversation_ids else None

        def _persist() -> SentimentJob:
            job = SentimentJob(
                agent_id=agent_id,
                status="running",
                phase="sentiment",
                phase_detail="Starting sentiment job",
                phase_progress=0,
                phase_total=0,
                reanalyze=reanalyze,
                scope_conversation_ids=scope_json,
            )
            self.db.add(job)
            self.db.commit()
            self.db.refresh(job)
            return job

        return admit_and_create(self.db, "sentiment", agent_id, _persist, wait_for_slot=wait_for_slot)

    @staticmethod
    def scoped_conversation_ids(job: SentimentJob) -> list[int] | None:
        if not job.scope_conversation_ids:
            return None
        return json.loads(job.scope_conversation_ids)

    def get_job(self, job_id: int) -> SentimentJob | None:
        return self.db.get(SentimentJob, job_id)

    def get_latest_job(self, agent_id: str | None = None) -> SentimentJob | None:
        stmt = select(SentimentJob).order_by(SentimentJob.created_at.desc()).limit(1)
        if agent_id:
            stmt = (
                select(SentimentJob)
                .where(SentimentJob.agent_id == agent_id)
                .order_by(SentimentJob.created_at.desc())
                .limit(1)
            )
        return self.db.scalar(stmt)

    def list_running_jobs(self, agent_id: str | None = None) -> list[SentimentJob]:
        stmt = select(SentimentJob).where(SentimentJob.status == "running").order_by(SentimentJob.created_at.desc())
        if agent_id:
            stmt = stmt.where(SentimentJob.agent_id == agent_id)
        return list(self.db.scalars(stmt).all())

    def cancel_job(self, job_id: int) -> SentimentJob | None:
        job = self.get_job(job_id)
        if job is None:
            return None
        if job.status != "running":
            return job

        request_job_cancel("sentiment", job_id)
        job.status = "cancelled"
        job.phase_detail = "Cancelled by user"
        job.error = "Cancelled by user"
        job.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        release_agent_job_lock(job.agent_id, "sentiment")
        self._publish(job, "done")
        logger.info("Sentiment job %s cancelled for agent %s", job_id, job.agent_id)
        return job

    def _conversation_ids_for_job(self, job: SentimentJob) -> list[int]:
        scoped = self.scoped_conversation_ids(job)
        if scoped is not None:
            return scoped
        return list(
            self.db.scalars(select(Conversation.id).where(Conversation.agent_id == job.agent_id)).all()
        )

    def _pending_messages(self, conversation_ids: list[int], reanalyze: bool) -> list[Message]:
        if not conversation_ids:
            return []
        stmt = (
            select(Message)
            .where(
                Message.conversation_id.in_(conversation_ids),
                Message.from_agent.is_(False),
            )
            .order_by(Message.id)
        )
        messages = [m for m in self.db.scalars(stmt).all() if m.content and m.content.strip()]
        if reanalyze:
            return messages
        existing = set(
            self.db.scalars(
                select(SentimentAnalysis.message_id).where(
                    SentimentAnalysis.message_id.in_([m.id for m in messages])
                )
            ).all()
        )
        return [m for m in messages if m.id not in existing]

    def run_job(self, job_id: int) -> None:
        job = self.get_job(job_id)
        if job is None:
            return

        job_start = time.monotonic()
        logger.info("Sentiment job %s started for agent %s", job_id, job.agent_id)

        try:
            conversation_ids = self._conversation_ids_for_job(job)
            pending = self._pending_messages(conversation_ids, job.reanalyze)
            job.limit = len(pending)
            self._update_phase(job, "sentiment", f"Analyzing {len(pending)} messages", 0, len(pending))

            if not pending:
                self._complete(job)
                return

            analyzed = 0
            for start in range(0, len(pending), SENTIMENT_CHUNK_SIZE):
                if self._check_cancelled(job):
                    return
                chunk = pending[start : start + SENTIMENT_CHUNK_SIZE]
                analyzed += self._analyze_chunk(chunk, reanalyze=job.reanalyze)
                job.processed += len(chunk)
                job.messages_analyzed = analyzed
                self.db.commit()
                self._update_phase(
                    job,
                    "sentiment",
                    f"Analyzed {job.processed}/{len(pending)} messages",
                    job.processed,
                    len(pending),
                )

            self._complete(job)
            logger.info(
                "Sentiment job %s completed in %.1fs (%s analyzed)",
                job_id,
                time.monotonic() - job_start,
                analyzed,
            )
        except Exception as exc:
            logger.exception("Sentiment job %s failed after %.1fs", job_id, time.monotonic() - job_start)
            self.db.rollback()
            job.status = "failed"
            job.phase_detail = f"Failed: {str(exc)[:500]}"
            job.error = str(exc)[:2000]
            job.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            self._publish(job, "done")

    def _analyze_chunk(self, messages: list[Message], *, reanalyze: bool) -> int:
        results = self.models.analyze_sentiment_v2_batch([m.content for m in messages])
        rows = []
        for message, result in zip(messages, results, strict=True):
            rows.append(
                {
                    "message_id": message.id,
                    "stars": int(result["stars"]),
                    "label": str(result["label"]),
                    "score": float(result["score"]),
                    "model_name": str(result["model_name"]),
                    "raw_label": str(result["raw_label"]) if result.get("raw_label") else None,
                    "raw_score": float(result["raw_score"]) if result.get("raw_score") is not None else None,
                    "low_confidence": bool(result.get("low_confidence", False)),
                    "polarity": str(result["polarity"]) if result.get("polarity") else None,
                    "polarity_score": (
                        float(result["polarity_score"]) if result.get("polarity_score") is not None else None
                    ),
                    "emotions_json": json.dumps(result.get("emotions") or []),
                    "original_language": str(result["original_language"]) if result.get("original_language") else None,
                    "translated": bool(result.get("translated", False)),
                }
            )
        if not rows:
            return 0

        stmt = pg_insert(SentimentAnalysis).values(rows)
        if reanalyze:
            update_cols = {
                col: getattr(stmt.excluded, col)
                for col in (
                    "stars",
                    "label",
                    "score",
                    "model_name",
                    "raw_label",
                    "raw_score",
                    "low_confidence",
                    "polarity",
                    "polarity_score",
                    "emotions_json",
                    "original_language",
                    "translated",
                )
            }
            stmt = stmt.on_conflict_do_update(index_elements=["message_id"], set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["message_id"])
        self.db.execute(stmt)
        # Every row in the chunk is written (inserted on a fresh run, updated on reanalyze), so the
        # analyzed count is simply len(rows). We can't use result.rowcount here: a multi-row
        # ON CONFLICT statement reports rowcount = -1 ("unknown"), which would make the running
        # "analyzed" tally count down by one per chunk instead of up by the chunk size.
        return len(rows)

    def _complete(self, job: SentimentJob) -> None:
        job.status = "complete"
        job.phase = "done"
        job.phase_detail = "Sentiment analysis complete"
        job.phase_progress = job.phase_total
        job.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        self._publish(job, "done")

    def _check_cancelled(self, job: SentimentJob) -> bool:
        if not is_job_cancelled("sentiment", job.id):
            return False
        job.status = "cancelled"
        job.phase_detail = "Cancelled by user"
        job.error = "Cancelled by user"
        job.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        clear_job_cancel("sentiment", job.id)
        self._publish(job, "done")
        logger.info("Sentiment job %s stopped (cancelled)", job.id)
        return True

    def _update_phase(self, job: SentimentJob, phase: str, detail: str, progress: int, total: int) -> None:
        job.phase = phase
        job.phase_detail = detail
        job.phase_progress = progress
        job.phase_total = total
        self.db.commit()
        self._publish(job, "progress")

    def _publish(self, job: SentimentJob, event: str) -> None:
        publish_job_progress(
            "sentiment",
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
