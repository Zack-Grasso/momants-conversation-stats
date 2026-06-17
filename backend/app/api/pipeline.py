import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.auth.session import AuthUser
from app.config import get_settings
from app.services.pipeline_launcher import (
    PipelineBusyError,
    PipelineConfigError,
    launch_reanalyze,
    launch_run,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class PipelineRunRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=36)


class PipelineRunResponse(BaseModel):
    status: str
    message: str


@router.post("/run", response_model=PipelineRunResponse, status_code=status.HTTP_202_ACCEPTED)
def start_pipeline(
    payload: PipelineRunRequest,
    user: AuthUser = Depends(get_current_user),
) -> PipelineRunResponse:
    try:
        launch_run(payload.agent_id, user.email)
    except PipelineConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PipelineBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("Failed to spawn pipeline for agent %s", payload.agent_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return PipelineRunResponse(
        status="started",
        message="Pipeline started in background. You'll get Slack updates as each stage completes.",
    )


@router.post("/reanalyze", response_model=PipelineRunResponse, status_code=status.HTTP_202_ACCEPTED)
def reanalyze_pipeline(
    payload: PipelineRunRequest,
    user: AuthUser = Depends(get_current_user),
) -> PipelineRunResponse:
    """Re-run stages 2 + 3 over conversations already in the DB (skips ingest)."""
    try:
        launch_reanalyze(payload.agent_id, user.email)
    except PipelineBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("Failed to spawn reanalyze for agent %s", payload.agent_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return PipelineRunResponse(
        status="started",
        message="Reanalyze started in background (sentiment + insights). You'll get Slack updates as each stage completes.",
    )
