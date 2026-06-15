import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.locks import clear_job_cancel, is_job_cancelled, release_agent_job_lock, request_job_cancel
from app.config import get_settings
from app.models.conversation import Conversation
from app.models.insights import ConversationMetrics, InsightsJob, QuestionCluster, UnansweredQuestion
from app.pubsub import publish_job_progress
from app.services.job_concurrency import admit_and_create
from app.services.metrics_service import MetricsService
from app.services.question_analysis_service import QuestionAnalysisService
from app.services.unanswered_question_service import UnansweredQuestionService

logger = logging.getLogger(__name__)

class _JobCancelled(Exception):
    pass


PHASE_LABELS = {
    "metrics": "Computing metrics",
    "questions": "Clustering top questions",
    "unanswered": "Analyzing unanswered questions",
    "done": "Complete",
}


class InsightsService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.metrics = MetricsService(db)
        self.questions = QuestionAnalysisService(db)
        self.unanswered = UnansweredQuestionService(db)

    def create_job(
        self,
        agent_id: str,
        *,
        conversation_ids: list[int] | None = None,
        ingest_job_id: int | None = None,
        wait_for_slot: bool = True,
    ) -> InsightsJob:
        scope_json = json.dumps(conversation_ids) if conversation_ids else None

        def _persist() -> InsightsJob:
            job = InsightsJob(
                agent_id=agent_id,
                status="running",
                phase="metrics",
                phase_detail="Starting insights job",
                phase_progress=0,
                phase_total=0,
                ingest_job_id=ingest_job_id,
                scope_conversation_ids=scope_json,
            )
            self.db.add(job)
            self.db.commit()
            self.db.refresh(job)
            return job

        return admit_and_create(self.db, "insights", agent_id, _persist, wait_for_slot=wait_for_slot)

    @staticmethod
    def scoped_conversation_ids(job: InsightsJob) -> list[int] | None:
        if not job.scope_conversation_ids:
            return None
        return json.loads(job.scope_conversation_ids)

    def _conversation_ids_for_job(self, job: InsightsJob) -> list[int]:
        scoped = self.scoped_conversation_ids(job)
        if scoped is not None:
            return scoped
        return list(
            self.db.scalars(select(Conversation.id).where(Conversation.agent_id == job.agent_id)).all()
        )

    def get_job(self, job_id: int) -> InsightsJob | None:
        return self.db.get(InsightsJob, job_id)

    def get_latest_job(self, agent_id: str | None = None) -> InsightsJob | None:
        stmt = select(InsightsJob).order_by(InsightsJob.created_at.desc()).limit(1)
        if agent_id:
            stmt = (
                select(InsightsJob)
                .where(InsightsJob.agent_id == agent_id)
                .order_by(InsightsJob.created_at.desc())
                .limit(1)
            )
        return self.db.scalar(stmt)

    def list_running_jobs(self, agent_id: str | None = None) -> list[InsightsJob]:
        stmt = select(InsightsJob).where(InsightsJob.status == "running").order_by(InsightsJob.created_at.desc())
        if agent_id:
            stmt = stmt.where(InsightsJob.agent_id == agent_id)
        return list(self.db.scalars(stmt).all())

    def cancel_job(self, job_id: int) -> InsightsJob | None:
        job = self.get_job(job_id)
        if job is None:
            return None
        if job.status != "running":
            return job

        request_job_cancel("insights", job_id)
        job.status = "cancelled"
        job.phase_detail = "Cancelled by user"
        job.error = "Cancelled by user"
        job.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        release_agent_job_lock(job.agent_id, "insights")
        self._publish(job, "done")
        logger.info("Insights job %s cancelled for agent %s", job_id, job.agent_id)
        return job

    def run_job(self, job_id: int) -> None:
        job = self.get_job(job_id)
        if job is None:
            return

        job_start = time.monotonic()
        logger.info("Insights job %s started for agent %s", job_id, job.agent_id)

        try:
            conversations = self._conversation_ids_for_job(job)
            batch_note = f" (batch of {len(conversations)})" if job.scope_conversation_ids else ""
            job.limit = len(conversations)
            self._update_phase(job, "metrics", f"Computing conversation metrics{batch_note}", 0, len(conversations))

            for index, conversation_id in enumerate(conversations, start=1):
                if self._check_cancelled(job):
                    return
                try:
                    self.metrics.compute_for_conversation(conversation_id, tier1_only=True)
                    job.processed += 1
                except Exception as exc:
                    logger.exception("Metrics failed for conversation %s", conversation_id)
                    job.failed += 1
                    job.error = str(exc)[:2000]
                self._update_phase(
                    job,
                    "metrics",
                    f"Computing metrics for conversation {index}/{len(conversations)}",
                    index,
                    len(conversations),
                )

            if self._check_cancelled(job):
                return

            # Question clustering must run once over the agent's full corpus. A scoped
            # (per-batch) job only sees its slice, so clustering here would produce a
            # partial, duplicate set of clusters for every batch. Skip it for scoped jobs;
            # the pipeline runs a single global clustering pass after all batches complete
            # (see InsightsService.finalize_questions).
            if job.scope_conversation_ids is None:
                phase_start = time.monotonic()
                self._update_phase(job, "questions", "Embedding and clustering questions", 0, 1)
                logger.info("Insights job %s: questions phase started", job_id)
                clusters = self.questions.analyze(job.agent_id, job.id)
                job.messages_analyzed = len(clusters)
                self._update_phase(job, "questions", f"Found {len(clusters)} question clusters", 1, 1)
                logger.info(
                    "Insights job %s: questions phase done in %.1fs (%s clusters)",
                    job_id,
                    time.monotonic() - phase_start,
                    len(clusters),
                )
            else:
                logger.info("Insights job %s: skipping per-batch question clustering (global pass runs later)", job_id)

            phase_start = time.monotonic()
            self._update_phase(job, "unanswered", "Running embedding + NLI answer checks", 0, len(conversations))
            logger.info("Insights job %s: unanswered phase started", job_id)

            def unanswered_progress(current: int, total: int, detail: str) -> None:
                if self._check_cancelled(job):
                    raise _JobCancelled()
                self._update_phase(job, "unanswered", detail, current, total)

            if self._check_cancelled(job):
                return

            self.unanswered.analyze_agent(
                job.agent_id,
                conversation_ids=conversations if job.scope_conversation_ids else None,
                on_progress=unanswered_progress,
                should_cancel=lambda: is_job_cancelled("insights", job.id),
            )
            logger.info(
                "Insights job %s: unanswered phase done in %.1fs",
                job_id,
                time.monotonic() - phase_start,
            )

            job.status = "complete"
            job.phase = "done"
            job.phase_detail = "Insights complete"
            job.phase_progress = job.phase_total
            job.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            if job.scope_conversation_ids is None:
                from app.services.cache_warmer import warm_agent_cache

                warm_agent_cache(self.db, job.agent_id)
            self._publish(job, "done")
            logger.info(
                "Insights job %s completed in %.1fs",
                job_id,
                time.monotonic() - job_start,
            )
        except _JobCancelled:
            self._check_cancelled(job)
        except Exception as exc:
            logger.exception("Insights job %s failed after %.1fs", job_id, time.monotonic() - job_start)
            job.status = "failed"
            job.phase_detail = f"Failed: {str(exc)[:500]}"
            job.error = str(exc)[:2000]
            job.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            self._publish(job, "done")

    def finalize_questions(self, agent_id: str) -> list[QuestionCluster]:
        """Run a single global question-clustering pass over the agent's full corpus.

        Per-batch insights jobs intentionally skip clustering (it cannot be sharded), so
        this runs once after parallel ingestion to produce one coherent, de-duplicated set
        of top-question clusters. It also self-heals any duplicate clusters left by prior
        runs, since QuestionAnalysisService.analyze clears every cluster for the agent first.
        """
        job = self.create_job(agent_id)
        try:
            phase_start = time.monotonic()
            self._update_phase(job, "questions", "Clustering all questions for agent", 0, 1)
            clusters = self.questions.analyze(agent_id, job.id)
            job.messages_analyzed = len(clusters)
            job.status = "complete"
            job.phase = "done"
            job.phase_detail = f"Found {len(clusters)} question clusters"
            job.phase_progress = 1
            job.phase_total = 1
            job.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            self._publish(job, "done")
            logger.info(
                "Global question clustering for agent %s done in %.1fs (%s clusters)",
                agent_id,
                time.monotonic() - phase_start,
                len(clusters),
            )
            return clusters
        except Exception as exc:
            logger.exception("Global question clustering failed for agent %s", agent_id)
            job.status = "failed"
            job.phase_detail = f"Failed: {str(exc)[:500]}"
            job.error = str(exc)[:2000]
            job.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            self._publish(job, "done")
            raise

    def _check_cancelled(self, job: InsightsJob) -> bool:
        if not is_job_cancelled("insights", job.id):
            return False
        job.status = "cancelled"
        job.phase_detail = "Cancelled by user"
        job.error = "Cancelled by user"
        job.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        clear_job_cancel("insights", job.id)
        self._publish(job, "done")
        logger.info("Insights job %s stopped (cancelled)", job.id)
        return True

    def _update_phase(
        self,
        job: InsightsJob,
        phase: str,
        detail: str,
        progress: int,
        total: int,
    ) -> None:
        job.phase = phase
        job.phase_detail = detail
        job.phase_progress = progress
        job.phase_total = total
        self.db.commit()
        self._publish(job, "progress")

    def _publish(self, job: InsightsJob, event: str) -> None:
        publish_job_progress(
            "insights",
            job.id,
            event,
            {
                "status": job.status,
                "phase": job.phase,
                "phase_label": PHASE_LABELS.get(job.phase, job.phase),
                "phase_detail": job.phase_detail,
                "phase_progress": job.phase_progress,
                "phase_total": job.phase_total,
                "processed": job.processed,
                "limit": job.limit,
                "failed": job.failed,
                "messages_analyzed": job.messages_analyzed,
                "error": job.error,
                "created_at": job.created_at,
                "completed_at": job.completed_at,
            },
        )

    def get_overview(self, agent_id: str) -> dict:
        metrics = list(
            self.db.scalars(select(ConversationMetrics).where(ConversationMetrics.agent_id == agent_id)).all()
        )
        if not metrics:
            return self._empty_overview(agent_id)

        trajectories = [m.trajectory for m in metrics if m.trajectory]
        depths = [m.depth_bucket for m in metrics if m.depth_bucket]
        response_times = [m.median_response_seconds for m in metrics if m.median_response_seconds is not None]
        first_responses = [m.first_response_seconds for m in metrics if m.first_response_seconds is not None]
        stars = [m.avg_stars for m in metrics if m.avg_stars is not None]

        intent_counts: dict[str, int] = {}
        for item in metrics:
            if item.intent_label:
                intent_counts[item.intent_label] = intent_counts.get(item.intent_label, 0) + 1

        unanswered_total = sum(m.unanswered_question_count for m in metrics)
        question_total = sum(m.member_messages for m in metrics)

        return {
            "agent_id": agent_id,
            "conversation_count": len(metrics),
            "average_stars": sum(stars) / len(stars) if stars else None,
            "improving_pct": self._pct(trajectories, "improving"),
            "worsening_pct": self._pct(trajectories, "worsening"),
            "mixed_pct": self._pct(trajectories, "mixed"),
            "median_response_seconds": sorted(response_times)[len(response_times) // 2] if response_times else None,
            "p95_response_seconds": sorted(response_times)[int(len(response_times) * 0.95)] if response_times else None,
            "sla_met_pct": (
                100 * sum(1 for s in first_responses if s <= self.settings.response_sla_seconds) / len(first_responses)
                if first_responses
                else None
            ),
            "depth_distribution": {
                "shallow": depths.count("shallow"),
                "medium": depths.count("medium"),
                "deep": depths.count("deep"),
            },
            "intent_breakdown": intent_counts,
            "unanswered_pct": (100 * unanswered_total / question_total) if question_total else 0,
            "unanswered_breakdown": {
                "no_reply": sum(m.unanswered_no_reply_count for m in metrics),
                "weak_answer": sum(m.unanswered_weak_answer_count for m in metrics),
                "not_answered": sum(m.unanswered_semantic_count for m in metrics),
            },
        }

    def _empty_overview(self, agent_id: str) -> dict:
        return {
            "agent_id": agent_id,
            "conversation_count": 0,
            "average_stars": None,
            "improving_pct": 0,
            "worsening_pct": 0,
            "mixed_pct": 0,
            "median_response_seconds": None,
            "p95_response_seconds": None,
            "sla_met_pct": None,
            "depth_distribution": {"shallow": 0, "medium": 0, "deep": 0},
            "intent_breakdown": {},
            "unanswered_pct": 0,
            "unanswered_breakdown": {"no_reply": 0, "weak_answer": 0, "not_answered": 0},
        }

    def _pct(self, values: list[str], target: str) -> float:
        if not values:
            return 0.0
        return round(100 * values.count(target) / len(values), 1)

    def get_questions(self, agent_id: str) -> list[QuestionCluster]:
        return list(
            self.db.scalars(
                select(QuestionCluster)
                .where(QuestionCluster.agent_id == agent_id)
                .order_by(QuestionCluster.count.desc(), QuestionCluster.rank)
                .limit(20)
            ).all()
        )

    def get_unanswered(self, agent_id: str, limit: int = 50) -> list[UnansweredQuestion]:
        return list(
            self.db.scalars(
                select(UnansweredQuestion)
                .where(UnansweredQuestion.agent_id == agent_id)
                .order_by(UnansweredQuestion.computed_at.desc())
                .limit(limit)
            ).all()
        )

    def get_timeline(self, conversation_id: int) -> dict | None:
        from app.models.conversation import Message

        conversation = self.db.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages).selectinload(Message.sentiment))
        )
        if conversation is None:
            return None
        metrics = self.db.scalar(
            select(ConversationMetrics).where(ConversationMetrics.conversation_id == conversation_id)
        )
        unanswered = {
            row.message_id: row
            for row in self.db.scalars(
                select(UnansweredQuestion).where(UnansweredQuestion.conversation_id == conversation_id)
            ).all()
        }

        ordered = self.metrics._ordered_messages(conversation.messages)
        points = []

        for message in ordered:
            response_seconds = self.metrics.message_response_seconds(ordered, message)
            unanswered_row = unanswered.get(message.id)
            points.append(
                {
                    "message_id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "source_created_at": (message.source_created_at or message.created_at).isoformat(),
                    "response_seconds": response_seconds,
                    "sentiment": (
                        {
                            "stars": message.sentiment.stars,
                            "label": message.sentiment.label,
                            "score": message.sentiment.score,
                        }
                        if message.sentiment
                        else None
                    ),
                    "unanswered_status": unanswered_row.status if unanswered_row else None,
                    "similarity_score": unanswered_row.similarity_score if unanswered_row else None,
                    "nli_label": unanswered_row.nli_label if unanswered_row else None,
                }
            )

        timeline = json.loads(metrics.timeline_json) if metrics and metrics.timeline_json else []
        return {
            "conversation_id": conversation_id,
            "title": conversation.title,
            "trajectory": metrics.trajectory if metrics else None,
            "intent_label": metrics.intent_label if metrics else None,
            "depth_bucket": metrics.depth_bucket if metrics else None,
            "timeline": timeline,
            "messages": points,
        }
