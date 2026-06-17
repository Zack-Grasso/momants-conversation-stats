from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, select, text

from app.cache import get_cache_client, get_pipeline_run_state
from app.config import get_settings
from app.database import SessionLocal
from app.models.ingestion_job import IngestionJob
from app.models.insights import InsightsJob
from app.models.sentiment_job import SentimentJob
from app.services.cache_warmer import is_agent_cache_ready, trigger_background_warm_if_needed

router = APIRouter()


def _check_database() -> str:
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "error"
    finally:
        db.close()


def _check_redis() -> str:
    try:
        return "ok" if get_cache_client().ping() else "error"
    except Exception:
        return "error"


def _scheduler_heartbeat(agent_id: str) -> int | None:
    try:
        raw = get_cache_client().get(f"scheduler:heartbeat:{agent_id}")
        return int(raw) if raw else None
    except Exception:
        return None


@router.get("/health")
def health_check(response: Response) -> dict[str, str]:
    checks = {
        "api": "ok",
        "database": _check_database(),
        "redis": _check_redis(),
    }
    if checks["database"] != "ok" or checks["redis"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        checks["status"] = "degraded"
    else:
        checks["status"] = "ok"
    return checks


@router.get("/health/scheduler")
def scheduler_health(response: Response, agent_id: str | None = Query(default=None)) -> dict:
    settings = get_settings()
    agent_ids = [agent_id] if agent_id else settings.scheduled_agent_id_list
    heartbeats = {aid: _scheduler_heartbeat(aid) for aid in agent_ids}
    cache_ready = {aid: is_agent_cache_ready(aid) for aid in agent_ids} if agent_ids else {}

    has_recent = any(ts is not None for ts in heartbeats.values())
    all_cache_ready = all(cache_ready.values()) if cache_ready else False
    overall = "ok" if has_recent and all_cache_ready else "degraded"
    if not has_recent:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": overall,
        "scheduled_agents": agent_ids,
        "heartbeats": heartbeats,
        "cache_ready": cache_ready,
    }


@router.get("/health/status")
def system_status(response: Response, agent_id: str | None = Query(default=None)) -> dict:
    db = SessionLocal()
    try:
        running_ingest_stmt = select(func.count()).select_from(IngestionJob).where(IngestionJob.status == "running")
        running_sentiment_stmt = select(func.count()).select_from(SentimentJob).where(SentimentJob.status == "running")
        running_insights_stmt = select(func.count()).select_from(InsightsJob).where(InsightsJob.status == "running")
        if agent_id:
            running_ingest_stmt = running_ingest_stmt.where(IngestionJob.agent_id == agent_id)
            running_sentiment_stmt = running_sentiment_stmt.where(SentimentJob.agent_id == agent_id)
            running_insights_stmt = running_insights_stmt.where(InsightsJob.agent_id == agent_id)

        running_ingest = db.scalar(running_ingest_stmt)
        running_sentiment = db.scalar(running_sentiment_stmt)
        running_insights = db.scalar(running_insights_stmt)

        latest_ingest_stmt = select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(1)
        latest_sentiment_stmt = select(SentimentJob).order_by(SentimentJob.created_at.desc()).limit(1)
        latest_insights_stmt = select(InsightsJob).order_by(InsightsJob.created_at.desc()).limit(1)
        if agent_id:
            latest_ingest_stmt = (
                select(IngestionJob)
                .where(IngestionJob.agent_id == agent_id)
                .order_by(IngestionJob.created_at.desc())
                .limit(1)
            )
            latest_sentiment_stmt = (
                select(SentimentJob)
                .where(SentimentJob.agent_id == agent_id)
                .order_by(SentimentJob.created_at.desc())
                .limit(1)
            )
            latest_insights_stmt = (
                select(InsightsJob)
                .where(InsightsJob.agent_id == agent_id)
                .order_by(InsightsJob.created_at.desc())
                .limit(1)
            )

        latest_ingest = db.scalar(latest_ingest_stmt)
        latest_sentiment = db.scalar(latest_sentiment_stmt)
        latest_insights = db.scalar(latest_insights_stmt)
    finally:
        db.close()

    database = _check_database()
    redis = _check_redis()
    cache_ready = is_agent_cache_ready(agent_id) if agent_id else None
    if agent_id and cache_ready is False:
        trigger_background_warm_if_needed(agent_id)
    heartbeat = _scheduler_heartbeat(agent_id) if agent_id else None

    overall = "ok" if database == "ok" and redis == "ok" else "degraded"
    if database == "error" or redis == "error":
        overall = "degraded"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": overall,
        "api": "ok",
        "database": database,
        "redis": redis,
        "scheduler": {
            "heartbeat": heartbeat,
            "cache_ready": cache_ready,
            "run": get_pipeline_run_state(agent_id) if agent_id else None,
        },
        "queue": {
            "running_ingest_jobs": running_ingest or 0,
            "running_sentiment_jobs": running_sentiment or 0,
            "running_insights_jobs": running_insights or 0,
        },
        "latest_jobs": {
            "ingest": _job_summary(latest_ingest),
            "sentiment": _job_summary(latest_sentiment),
            "insights": _job_summary(latest_insights),
        },
    }


def _job_summary(job: IngestionJob | InsightsJob | SentimentJob | None) -> dict | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "agent_id": job.agent_id,
        "status": job.status,
        "phase": getattr(job, "phase", None),
        "processed": job.processed,
        "limit": getattr(job, "limit", None),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.get("/health/jobs")
def active_jobs(agent_id: str | None = Query(default=None)) -> dict:
    from app.services.ingestion_service import IngestionService
    from app.services.insights_service import InsightsService
    from app.services.sentiment_service import SentimentService

    db = SessionLocal()
    try:
        ingest = IngestionService(db).list_running_jobs(agent_id)
        sentiment = SentimentService(db).list_running_jobs(agent_id)
        insights = InsightsService(db).list_running_jobs(agent_id)
        return {
            "ingest": [_job_summary(job) for job in ingest],
            "sentiment": [_job_summary(job) for job in sentiment],
            "insights": [_job_summary(job) for job in insights],
        }
    finally:
        db.close()
