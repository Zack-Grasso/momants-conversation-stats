from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.system_service import SystemService

router = APIRouter()


class SchedulerStatusResponse(BaseModel):
    paused: bool
    running_ingest_jobs: int
    running_insights_jobs: int


class SchedulerActionResponse(BaseModel):
    paused: bool
    cancelled_jobs: list[str] = []
    pipeline_locks_cleared: int = 0


class PurgeResponse(BaseModel):
    purged: bool
    cancelled_jobs: list[str]
    terminated_db_sessions: int = 0
    tables_truncated: list[str]
    redis_databases_flushed: list[int]
    pipeline_locks_cleared: int


@router.get("/status", response_model=SchedulerStatusResponse)
def scheduler_status(db: Session = Depends(get_db)) -> SchedulerStatusResponse:
    status = SystemService(db).scheduler_status()
    return SchedulerStatusResponse(**status)


@router.post("/stop", response_model=SchedulerActionResponse)
def stop_scheduler(db: Session = Depends(get_db)) -> SchedulerActionResponse:
    result = SystemService(db).stop_scheduler_and_jobs()
    return SchedulerActionResponse(**result)


@router.post("/resume", response_model=SchedulerActionResponse)
def resume_scheduler(db: Session = Depends(get_db)) -> SchedulerActionResponse:
    result = SystemService(db).resume_scheduler()
    return SchedulerActionResponse(paused=result["paused"])
