import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.auth.session import AuthUser
from app.config import get_settings
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
    settings = get_settings()
    if not settings.momants_api_base_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MOMANTS_API_BASE_URL is not configured")
    env = os.environ.copy()
    env["PRELOAD_MODELS"] = "true"
    env["APP_ROLE"] = "scheduler"
    cmd = [sys.executable, "-m", "app.pipeline", "run", f"--agent-id={payload.agent_id}"]
    if user.email:
        cmd.append(f"--initiated-by={user.email}")

    try:
        backend_root = Path(__file__).resolve().parents[2]
        subprocess.Popen(cmd, env=env, cwd=str(backend_root))
    except Exception as exc:
        logger.exception("Failed to spawn pipeline for agent %s", payload.agent_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start pipeline: {exc}",
        ) from exc

    return PipelineRunResponse(
        status="started",
        message="Pipeline started in background. You'll get Slack updates as each stage completes.",
    )
