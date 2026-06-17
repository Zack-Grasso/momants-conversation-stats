"""Spawn background weekly pipeline runs from the API."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.locks import get_lock_client
from app.weekly.settings_store import clear_weekly_run_state, set_weekly_run_state

logger = logging.getLogger(__name__)

WEEKLY_LOCK_KEY = "weekly:run:lock"
WEEKLY_LOCK_TTL_SECONDS = 7200
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class WeeklyBusyError(Exception):
    """Raised when a weekly run is already in progress."""


@dataclass(frozen=True)
class WeeklyLaunchResult:
    initiated_by: str | None


def is_weekly_running() -> bool:
    try:
        return bool(get_lock_client().get(WEEKLY_LOCK_KEY))
    except Exception:
        logger.exception("Failed to read weekly run lock")
        return False


def _acquire_weekly_lock() -> bool:
    try:
        return bool(
            get_lock_client().set(WEEKLY_LOCK_KEY, "1", nx=True, ex=WEEKLY_LOCK_TTL_SECONDS)
        )
    except Exception:
        logger.exception("Failed to acquire weekly run lock")
        return False


def release_weekly_lock() -> None:
    try:
        get_lock_client().delete(WEEKLY_LOCK_KEY)
    except Exception:
        logger.exception("Failed to release weekly run lock")


def mark_weekly_run_started(initiated_by: str | None = None, trigger: str = "manual") -> None:
    set_weekly_run_state(
        status="running",
        trigger=trigger,
        initiated_by=initiated_by,
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=None,
        error=None,
        week_id=None,
        phase="starting",
        agent_total=0,
        agent_index=0,
        agents_complete=0,
        agents_failed=0,
        current_agent_id=None,
        current_agent_name=None,
        current_step=None,
    )


def mark_weekly_run_finished(*, week_id: str | None = None, error: str | None = None) -> None:
    set_weekly_run_state(
        status="failed" if error else "complete",
        phase="failed" if error else "done",
        current_step="complete" if not error else None,
        completed_at=datetime.now(timezone.utc).isoformat(),
        week_id=week_id,
        error=error,
    )
    release_weekly_lock()


def launch_weekly_run(initiated_by: str | None = None) -> WeeklyLaunchResult:
    if is_weekly_running():
        raise WeeklyBusyError("A weekly report run is already in progress")

    if not _acquire_weekly_lock():
        raise WeeklyBusyError("A weekly report run is already in progress")

    mark_weekly_run_started(initiated_by=initiated_by, trigger="manual")
    env = os.environ.copy()
    env["PRELOAD_MODELS"] = "true"
    env["APP_ROLE"] = "weekly-scheduler"
    cmd = [sys.executable, "-m", "app.weekly.pipeline", "run-all"]
    if initiated_by:
        cmd.append(f"--initiated-by={initiated_by}")

    try:
        subprocess.Popen(cmd, env=env, cwd=str(_BACKEND_ROOT))
    except Exception:
        release_weekly_lock()
        clear_weekly_run_state()
        raise

    return WeeklyLaunchResult(initiated_by=initiated_by)
