"""Scheduler pause/resume via Redis."""

from __future__ import annotations

from app.locks import get_lock_client

SCHEDULER_PAUSED_KEY = "scheduler:paused"


def is_scheduler_paused() -> bool:
    try:
        return get_lock_client().get(SCHEDULER_PAUSED_KEY) == "1"
    except Exception:
        return False


def pause_scheduler() -> None:
    get_lock_client().set(SCHEDULER_PAUSED_KEY, "1")


def resume_scheduler() -> None:
    get_lock_client().delete(SCHEDULER_PAUSED_KEY)
