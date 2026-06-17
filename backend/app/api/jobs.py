from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.jobs import RunningJobRead, RunningJobsResponse
from app.services.ingestion_service import IngestionService
from app.services.insights_service import InsightsService
from app.services.job_concurrency import concurrency_snapshot
from app.services.sentiment_service import SentimentService

router = APIRouter()


def _ingest_to_running(job) -> RunningJobRead:
    return RunningJobRead(
        job_type="ingest",
        id=job.id,
        agent_id=job.agent_id,
        status=job.status,
        processed=job.processed,
        limit=job.limit,
        failed=job.failed,
        messages_analyzed=job.messages_analyzed,
        reanalyze=job.reanalyze,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


def _insights_to_running(job) -> RunningJobRead:
    return RunningJobRead(
        job_type="insights",
        id=job.id,
        agent_id=job.agent_id,
        status=job.status,
        processed=job.processed,
        limit=job.limit,
        failed=job.failed,
        messages_analyzed=job.messages_analyzed,
        phase=job.phase,
        phase_detail=job.phase_detail,
        phase_progress=job.phase_progress,
        phase_total=job.phase_total,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


def _sentiment_to_running(job) -> RunningJobRead:
    return RunningJobRead(
        job_type="sentiment",
        id=job.id,
        agent_id=job.agent_id,
        status=job.status,
        processed=job.processed,
        limit=job.limit,
        failed=job.failed,
        messages_analyzed=job.messages_analyzed,
        reanalyze=job.reanalyze,
        phase=job.phase,
        phase_detail=job.phase_detail,
        phase_progress=job.phase_progress,
        phase_total=job.phase_total,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.get("/running", response_model=RunningJobsResponse)
def list_running_jobs(
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=10),
    db: Session = Depends(get_db),
) -> RunningJobsResponse:
    ingest_jobs = IngestionService(db).list_running_jobs(agent_id)
    sentiment_jobs = SentimentService(db).list_running_jobs(agent_id)
    insights_jobs = InsightsService(db).list_running_jobs(agent_id)

    combined: list[RunningJobRead] = [_ingest_to_running(job) for job in ingest_jobs]
    combined.extend(_sentiment_to_running(job) for job in sentiment_jobs)
    combined.extend(_insights_to_running(job) for job in insights_jobs)
    combined.sort(key=lambda job: job.created_at, reverse=True)

    snapshot = concurrency_snapshot(db, agent_id)
    return RunningJobsResponse(jobs=combined[:limit], **snapshot)
