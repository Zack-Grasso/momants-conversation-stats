from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.ingest import IngestJobRead
from app.services.ingestion_service import IngestionService

router = APIRouter()


def get_service(db: Session = Depends(get_db)) -> IngestionService:
    return IngestionService(db)


@router.get("/running", response_model=list[IngestJobRead])
def list_running_ingest_jobs(
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=10),
    service: IngestionService = Depends(get_service),
) -> list[IngestJobRead]:
    return service.list_running_jobs(agent_id)[:limit]


@router.get("/latest", response_model=IngestJobRead | None)
def get_latest_ingest_job(
    agent_id: str | None = Query(default=None),
    service: IngestionService = Depends(get_service),
) -> IngestJobRead | None:
    job = service.get_latest_job(agent_id)
    if job is None:
        return None
    return job


@router.get("/sync-state")
def get_ingest_sync_state(
    agent_id: str = Query(..., min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.agent_ingest_state_service import AgentIngestStateService

    state = AgentIngestStateService(db).get_or_create(agent_id)
    return {
        "agent_id": state.agent_id,
        "last_sync_started_at": state.last_sync_started_at,
        "last_sync_completed_at": state.last_sync_completed_at,
        "last_conversations_imported": state.last_conversations_imported,
        "last_conversations_skipped": state.last_conversations_skipped,
        "total_conversations_imported": state.total_conversations_imported,
        "updated_at": state.updated_at,
    }


@router.get("/{job_id}", response_model=IngestJobRead)
def get_ingest_job(job_id: int, service: IngestionService = Depends(get_service)) -> IngestJobRead:
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion job not found")
    return job
