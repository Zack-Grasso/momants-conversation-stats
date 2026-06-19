"""Spawn background pipeline subprocesses for web UI and Slack triggers."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.locks import acquire_agent_job_lock, get_lock_client, release_agent_job_lock

logger = logging.getLogger(__name__)

PIPELINE_LOCK_KIND = "pipeline"
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PipelineLaunchResult:
    agent_id: str
    initiated_by: str | None


class PipelineBusyError(Exception):
    """Raised when a pipeline run is already in progress for the agent."""


class PipelineConfigError(Exception):
    """Raised when required configuration is missing."""


def _pipeline_lock_key(agent_id: str) -> str:
    return f"job:agent:{PIPELINE_LOCK_KIND}:{agent_id}"


def is_pipeline_busy(agent_id: str) -> bool:
    """Return True when an active pipeline lock exists and jobs are still running."""
    try:
        if not get_lock_client().get(_pipeline_lock_key(agent_id)):
            return False
    except Exception:
        logger.exception("Failed to read pipeline lock for agent %s", agent_id)
        return False

    db = SessionLocal()
    try:
        from app.services.job_concurrency import count_running_jobs

        if count_running_jobs(db) == 0:
            return False
    finally:
        db.close()
    return True


def _spawn_pipeline(command: str, agent_id: str, initiated_by: str | None, *, extra_args: list[str] | None = None) -> None:
    env = os.environ.copy()
    env["PRELOAD_MODELS"] = "true"
    env["APP_ROLE"] = "scheduler"
    cmd = [sys.executable, "-m", "app.pipeline", command, f"--agent-id={agent_id}"]
    if initiated_by:
        cmd.append(f"--initiated-by={initiated_by}")
    if extra_args:
        cmd.extend(extra_args)
    subprocess.Popen(cmd, env=env, cwd=str(_BACKEND_ROOT))


def launch_run(agent_id: str, initiated_by: str | None = None) -> PipelineLaunchResult:
    settings = get_settings()
    if not settings.momants_api_base_url:
        raise PipelineConfigError("MOMANTS_API_BASE_URL is not configured")
    if is_pipeline_busy(agent_id):
        raise PipelineBusyError(f"Pipeline already running for agent {agent_id}")

    try:
        _spawn_pipeline("run", agent_id, initiated_by)
    except Exception as exc:
        logger.exception("Failed to spawn pipeline for agent %s", agent_id)
        raise RuntimeError(f"Failed to start pipeline: {exc}") from exc

    return PipelineLaunchResult(agent_id=agent_id, initiated_by=initiated_by)


def launch_reanalyze(agent_id: str, initiated_by: str | None = None) -> PipelineLaunchResult:
    if is_pipeline_busy(agent_id):
        raise PipelineBusyError(f"Pipeline already running for agent {agent_id}")

    try:
        _spawn_pipeline("reanalyze", agent_id, initiated_by)
    except Exception as exc:
        logger.exception("Failed to spawn reanalyze for agent %s", agent_id)
        raise RuntimeError(f"Failed to start reanalyze: {exc}") from exc

    return PipelineLaunchResult(agent_id=agent_id, initiated_by=initiated_by)


def launch_referred_intent(
    agent_id: str,
    initiated_by: str | None = None,
    *,
    reanalyze: bool = False,
) -> PipelineLaunchResult:
    db = SessionLocal()
    try:
        from app.services.job_concurrency import count_running_intent_jobs

        if count_running_intent_jobs(db, agent_id) > 0:
            raise PipelineBusyError(f"Referred intent labeling already running for agent {agent_id}")
    finally:
        db.close()

    extra_args = ["--reanalyze"] if reanalyze else None
    try:
        _spawn_pipeline("referred-intent", agent_id, initiated_by, extra_args=extra_args)
    except Exception as exc:
        logger.exception("Failed to spawn referred intent labeling for agent %s", agent_id)
        raise RuntimeError(f"Failed to start referred intent labeling: {exc}") from exc

    return PipelineLaunchResult(agent_id=agent_id, initiated_by=initiated_by)


def clear_stale_pipeline_lock(agent_id: str) -> None:
    """Release a stale pipeline lock when no jobs are running (test helper)."""
    release_agent_job_lock(agent_id, PIPELINE_LOCK_KIND)


def acquire_pipeline_lock(agent_id: str) -> bool:
    """Acquire the pipeline lock (test helper)."""
    return acquire_agent_job_lock(agent_id, PIPELINE_LOCK_KIND)
