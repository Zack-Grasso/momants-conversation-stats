"""Global concurrent job limits shared across ingest and insights."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, Literal, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.locks import job_admission_lock
from app.models.ingestion_job import IngestionJob
from app.models.insights import InsightsJob
from app.models.sentiment_job import SentimentJob

logger = logging.getLogger(__name__)

JobKind = Literal["ingest", "insights", "sentiment"]

T = TypeVar("T")


class JobConcurrencyLimitError(Exception):
    """Raised when a job cannot start because concurrency limits are reached."""


def count_running_ingest_jobs(db: Session, agent_id: str | None = None) -> int:
    stmt = select(func.count()).select_from(IngestionJob).where(IngestionJob.status == "running")
    if agent_id:
        stmt = stmt.where(IngestionJob.agent_id == agent_id)
    return db.scalar(stmt) or 0


def count_running_insights_jobs(db: Session, agent_id: str | None = None) -> int:
    stmt = select(func.count()).select_from(InsightsJob).where(InsightsJob.status == "running")
    if agent_id:
        stmt = stmt.where(InsightsJob.agent_id == agent_id)
    return db.scalar(stmt) or 0


def count_running_sentiment_jobs(db: Session, agent_id: str | None = None) -> int:
    stmt = select(func.count()).select_from(SentimentJob).where(SentimentJob.status == "running")
    if agent_id:
        stmt = stmt.where(SentimentJob.agent_id == agent_id)
    return db.scalar(stmt) or 0


def count_running_jobs(db: Session, agent_id: str | None = None) -> int:
    return (
        count_running_ingest_jobs(db, agent_id)
        + count_running_insights_jobs(db, agent_id)
        + count_running_sentiment_jobs(db, agent_id)
    )


def can_start_job(db: Session, job_kind: JobKind, agent_id: str | None = None) -> bool:
    settings = get_settings()
    ingest_running = count_running_ingest_jobs(db)
    insights_running = count_running_insights_jobs(db)
    sentiment_running = count_running_sentiment_jobs(db)
    total_running = ingest_running + insights_running + sentiment_running

    if total_running >= settings.max_concurrent_jobs:
        return False
    if job_kind == "ingest":
        if ingest_running >= settings.max_concurrent_ingest:
            return False
        # Per-agent fairness: one agent must not monopolise all global ingest slots.
        if agent_id and count_running_ingest_jobs(db, agent_id) >= settings.max_concurrent_ingest_per_agent:
            return False
        return True
    if job_kind == "sentiment":
        if sentiment_running >= settings.max_concurrent_sentiment:
            return False
        if agent_id and count_running_sentiment_jobs(db, agent_id) >= settings.max_concurrent_sentiment_per_agent:
            return False
        return True
    if insights_running >= settings.max_concurrent_insights:
        return False
    if agent_id and count_running_insights_jobs(db, agent_id) >= settings.max_concurrent_insights_per_agent:
        return False
    return True


def wait_for_job_slot(
    db: Session,
    job_kind: JobKind,
    agent_id: str,
    *,
    poll_seconds: float = 2.0,
    timeout_seconds: float = 7200.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        db.expire_all()
        if can_start_job(db, job_kind, agent_id):
            return
        # Release the DB connection back to the pool while we sleep, so blocked batches
        # don't pin a connection each and exhaust the pool (QueuePool TimeoutError).
        db.rollback()
        time.sleep(poll_seconds)
    settings = get_settings()
    raise JobConcurrencyLimitError(
        f"Timed out waiting for a {job_kind} slot for agent {agent_id} "
        f"(global {settings.max_concurrent_jobs}, ingest {settings.max_concurrent_ingest}, "
        f"insights {settings.max_concurrent_insights})"
    )


def assert_can_start_job(db: Session, job_kind: JobKind, agent_id: str) -> None:
    if not can_start_job(db, job_kind, agent_id):
        settings = get_settings()
        raise JobConcurrencyLimitError(
            f"{job_kind} slot unavailable for agent {agent_id}: "
            f"ingest {count_running_ingest_jobs(db)}/{settings.max_concurrent_ingest}, "
            f"insights {count_running_insights_jobs(db)}/{settings.max_concurrent_insights}, "
            f"total {count_running_jobs(db)}/{settings.max_concurrent_jobs}"
        )


def admit_and_create(
    db: Session,
    job_kind: JobKind,
    agent_id: str,
    persist_fn: Callable[[], T],
    *,
    wait_for_slot: bool = True,
    timeout_seconds: float = 7200.0,
) -> T:
    """Atomically admit a job and create its row under the global admission mutex.

    The mutex guarantees the slot check and the row insert happen together, so two
    concurrent creators cannot both observe the same free slot and overshoot the limit.
    The (potentially long) wait happens OUTSIDE the mutex so we never serialise on it.
    """
    if not wait_for_slot:
        with job_admission_lock():
            db.expire_all()
            assert_can_start_job(db, job_kind, agent_id)
            return persist_fn()

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        wait_for_job_slot(db, job_kind, agent_id, timeout_seconds=timeout_seconds)
        with job_admission_lock():
            db.expire_all()
            if can_start_job(db, job_kind, agent_id):
                return persist_fn()
        # Lost the race for the slot under the mutex; release the connection and retry.
        db.rollback()
        time.sleep(0.2)
    raise JobConcurrencyLimitError(
        f"Timed out admitting a {job_kind} job for agent {agent_id}"
    )


def fail_orphaned_jobs(db: Session, agent_id: str, *, error: str = "Orphaned by an interrupted run") -> int:
    """Mark this agent's lingering ``running`` jobs as ``failed`` and free their slots.

    Safe to call only when no live pipeline is processing this agent (e.g. right after
    acquiring the per-agent pipeline lock), since it cannot distinguish a live job from a
    crash-orphaned one other than by agent scope.
    """
    now = datetime.now(timezone.utc)
    cleared = 0
    for model in (IngestionJob, InsightsJob, SentimentJob):
        rows = db.scalars(
            select(model).where(model.agent_id == agent_id, model.status == "running")
        ).all()
        for row in rows:
            row.status = "failed"
            row.error = error
            row.completed_at = now
            cleared += 1
    if cleared:
        db.commit()
    return cleared


def concurrency_snapshot(db: Session, agent_id: str | None = None) -> dict[str, int]:
    settings = get_settings()
    ingest_running = count_running_ingest_jobs(db, agent_id)
    insights_running = count_running_insights_jobs(db, agent_id)
    sentiment_running = count_running_sentiment_jobs(db, agent_id)
    total_running = (
        ingest_running + insights_running + sentiment_running if agent_id else count_running_jobs(db)
    )
    return {
        "global_running": count_running_jobs(db),
        "global_limit": settings.max_concurrent_jobs,
        "ingest_running": count_running_ingest_jobs(db),
        "ingest_limit": settings.max_concurrent_ingest,
        "ingest_slots_left": min(
            settings.max_concurrent_ingest - count_running_ingest_jobs(db),
            settings.max_concurrent_jobs - count_running_jobs(db),
        ),
        "insights_running": count_running_insights_jobs(db),
        "insights_limit": settings.max_concurrent_insights,
        "insights_slots_left": min(
            settings.max_concurrent_insights - count_running_insights_jobs(db),
            settings.max_concurrent_jobs - count_running_jobs(db),
        ),
        "sentiment_running": count_running_sentiment_jobs(db),
        "sentiment_limit": settings.max_concurrent_sentiment,
        "sentiment_slots_left": min(
            settings.max_concurrent_sentiment - count_running_sentiment_jobs(db),
            settings.max_concurrent_jobs - count_running_jobs(db),
        ),
        "agent_running": total_running,
    }
