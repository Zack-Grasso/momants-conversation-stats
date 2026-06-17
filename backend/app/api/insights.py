from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.cache_read import get_or_compute_model, get_or_compute_model_list
from app.cache import insights_cache_key
from app.database import get_db
from app.schemas.insights import (
    InsightsDeleteResponse,
    InsightsJobRead,
    InsightsOverview,
    QuestionClusterRead,
    UnansweredQuestionRead,
)
from app.services.agent_service import AgentService
from app.services.cache_builders import build_overview, build_questions, build_unanswered
from app.services.insights_service import InsightsService

router = APIRouter()


def get_service(db: Session = Depends(get_db)) -> InsightsService:
    return InsightsService(db)


@router.get("/jobs/running", response_model=list[InsightsJobRead])
def list_running_insights_jobs(
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=10),
    service: InsightsService = Depends(get_service),
) -> list[InsightsJobRead]:
    return service.list_running_jobs(agent_id)[:limit]


@router.get("/jobs/latest", response_model=InsightsJobRead | None)
def get_latest_insights_job(
    agent_id: str | None = Query(default=None),
    service: InsightsService = Depends(get_service),
) -> InsightsJobRead | None:
    job = service.get_latest_job(agent_id)
    if job is None:
        return None
    return job


@router.get("/jobs/{job_id}", response_model=InsightsJobRead)
def get_insights_job(job_id: int, service: InsightsService = Depends(get_service)) -> InsightsJobRead:
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insights job not found")
    return job


@router.delete("", response_model=InsightsDeleteResponse, status_code=status.HTTP_200_OK)
def delete_insights_for_agent(
    agent_id: str = Query(..., min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> InsightsDeleteResponse:
    deleted = AgentService(db).purge_insights(agent_id)
    return InsightsDeleteResponse(agent_id=agent_id, deleted=deleted)


@router.get("/overview", response_model=InsightsOverview)
def get_overview(agent_id: str = Query(...), db: Session = Depends(get_db)) -> InsightsOverview:
    return get_or_compute_model(
        insights_cache_key(agent_id, "overview"),
        InsightsOverview,
        lambda: build_overview(db, agent_id),
    )


@router.get("/questions", response_model=list[QuestionClusterRead])
def get_questions(agent_id: str = Query(...), db: Session = Depends(get_db)) -> list[QuestionClusterRead]:
    return get_or_compute_model_list(
        insights_cache_key(agent_id, "questions"),
        QuestionClusterRead,
        lambda: build_questions(db, agent_id),
    )


@router.get("/unanswered", response_model=list[UnansweredQuestionRead])
def get_unanswered(
    agent_id: str = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[UnansweredQuestionRead]:
    return get_or_compute_model_list(
        insights_cache_key(agent_id, f"unanswered:{limit}"),
        UnansweredQuestionRead,
        lambda: build_unanswered(db, agent_id, limit=limit),
    )
