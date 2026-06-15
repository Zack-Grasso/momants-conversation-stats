from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.scheduler import PurgeResponse
from app.database import get_db
from app.services.system_service import SystemService

router = APIRouter()


@router.post("/purge", response_model=PurgeResponse)
def purge_system(db: Session = Depends(get_db)) -> PurgeResponse:
    result = SystemService(db).purge_everything()
    return PurgeResponse(**result)
