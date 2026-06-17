from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.jobs import SentimentJobRead
from app.services.sentiment_service import SentimentService

router = APIRouter()


def get_service(db: Session = Depends(get_db)) -> SentimentService:
    return SentimentService(db)


@router.get("/running", response_model=list[SentimentJobRead])
def list_running_sentiment_jobs(
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=10),
    service: SentimentService = Depends(get_service),
) -> list[SentimentJobRead]:
    return service.list_running_jobs(agent_id)[:limit]


@router.get("/latest", response_model=SentimentJobRead | None)
def get_latest_sentiment_job(
    agent_id: str | None = Query(default=None),
    service: SentimentService = Depends(get_service),
) -> SentimentJobRead | None:
    job = service.get_latest_job(agent_id)
    if job is None:
        return None
    return job


@router.get("/{job_id}", response_model=SentimentJobRead)
def get_sentiment_job(job_id: int, service: SentimentService = Depends(get_service)) -> SentimentJobRead:
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sentiment job not found")
    return job
