from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.jobs import ReferredIntentJobRead
from app.services.referred_intent_service import ReferredIntentService

router = APIRouter()


def get_service(db: Session = Depends(get_db)) -> ReferredIntentService:
    return ReferredIntentService(db)


@router.get("/running", response_model=list[ReferredIntentJobRead])
def list_running_intent_jobs(
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=10),
    service: ReferredIntentService = Depends(get_service),
) -> list[ReferredIntentJobRead]:
    return service.list_running_jobs(agent_id)[:limit]


@router.get("/latest", response_model=ReferredIntentJobRead | None)
def get_latest_intent_job(
    agent_id: str | None = Query(default=None),
    service: ReferredIntentService = Depends(get_service),
) -> ReferredIntentJobRead | None:
    job = service.get_latest_job(agent_id)
    if job is None:
        return None
    return job


@router.get("/{job_id}", response_model=ReferredIntentJobRead)
def get_intent_job(job_id: int, service: ReferredIntentService = Depends(get_service)) -> ReferredIntentJobRead:
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Referred intent job not found")
    return job
