"""Scheduled preprocessing pipeline — ingest, insights, cache warm."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.orm import Session

from app.cache import set_pipeline_run_state
from app.config import get_settings
from app.database import SessionLocal
from app.integrations.momants_client import get_momants_client
from app.integrations.slack_client import (
    MILESTONE_FAILED,
    MILESTONE_INGEST_DONE,
    MILESTONE_INGEST_STARTED,
    MILESTONE_INSIGHTS_DONE,
    MILESTONE_INSIGHTS_STARTED,
    MILESTONE_PDF_READY,
    MILESTONE_SENTIMENT_DONE,
    MILESTONE_SENTIMENT_STARTED,
    notify_milestone,
)
from app.locks import acquire_agent_job_lock, release_agent_job_lock
from app.services.agent_ingest_state_service import AgentIngestStateService
from app.services.cache_warmer import warm_agent_cache
from app.services.ingestion_service import IngestionService
from app.services.insights_service import InsightsService
from app.services.report_service import ReportService
from app.services.job_concurrency import fail_orphaned_jobs
from app.services.sentiment_service import SentimentService
from app.utils.report_storage import save_report_pdf

logger = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = (15, 45)
MAX_RETRIES = 2
PIPELINE_LOCK_KIND = "pipeline"


def _run_with_retries(label: str, run_fn):
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return run_fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= MAX_RETRIES:
                break
            wait = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
            logger.exception("%s failed (attempt %s/%s), retrying in %ss", label, attempt + 1, MAX_RETRIES, wait)
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def run_ingest(
    db: Session,
    agent_id: str,
    limit: int | None = None,
    reanalyze: bool = False,
    *,
    skip: int = 0,
) -> int:
    settings = get_settings()
    batch_limit = limit or settings.ingestion_batch_size
    capped = min(batch_limit, settings.ingestion_max_conversations)
    service = IngestionService(db)
    job = service.create_job(agent_id, capped, reanalyze=reanalyze)

    def _execute() -> None:
        service.run_job(job.id, skip=skip)

    _run_with_retries(f"ingest job {job.id}", _execute)
    return job.id


def run_insights(
    db: Session,
    agent_id: str,
    *,
    conversation_ids: list[int] | None = None,
    ingest_job_id: int | None = None,
) -> int:
    service = InsightsService(db)
    job = service.create_job(
        agent_id,
        conversation_ids=conversation_ids,
        ingest_job_id=ingest_job_id,
    )

    def _execute() -> None:
        service.run_job(job.id)

    _run_with_retries(f"insights job {job.id}", _execute)
    return job.id


def run_sentiment(
    db: Session,
    agent_id: str,
    *,
    conversation_ids: list[int] | None = None,
    reanalyze: bool = False,
) -> int:
    service = SentimentService(db)
    job = service.create_job(agent_id, conversation_ids=conversation_ids, reanalyze=reanalyze)

    def _execute() -> None:
        service.run_job(job.id)

    _run_with_retries(f"sentiment job {job.id}", _execute)
    return job.id


def _all_conversation_ids(db: Session, agent_id: str) -> list[int]:
    from sqlalchemy import select

    from app.models.conversation import Conversation

    return list(db.scalars(select(Conversation.id).where(Conversation.agent_id == agent_id)).all())


def _conversation_ids_for_entries(db: Session, agent_id: str, entries: list[dict]) -> list[int]:
    external_ids = [entry["conversation_id"] for entry in entries if entry.get("conversation_id")]
    if not external_ids:
        return []
    from sqlalchemy import select

    from app.models.conversation import Conversation

    return list(
        db.scalars(
            select(Conversation.id).where(
                Conversation.agent_id == agent_id,
                Conversation.external_id.in_(external_ids),
            )
        ).all()
    )


def _run_pipeline_batch(
    agent_id: str,
    batch_num: int,
    entries: list[dict],
    *,
    reanalyze: bool,
    run_insights_after: bool,
    sync_start_date=None,
) -> tuple[int, int, list[dict]]:
    db = SessionLocal()
    imported = 0
    skipped = 0
    processed_entries: list[dict] = []
    try:
        ingest_service = IngestionService(db)
        job = ingest_service.create_job(
            agent_id,
            len(entries),
            reanalyze=reanalyze,
            sync_start_date=sync_start_date,
        )
        logger.info("Pipeline batch %s: ingest job %s started (%s conversations)", batch_num, job.id, len(entries))

        def _execute_ingest() -> list[dict]:
            return ingest_service.run_job(job.id, entries=entries)

        processed_entries = _run_with_retries(f"ingest job {job.id}", _execute_ingest) or []
        db.refresh(job)
        imported = job.processed
        skipped = job.skipped

        if run_insights_after and processed_entries:
            conversation_ids = _conversation_ids_for_entries(db, agent_id, processed_entries)
            logger.info(
                "Pipeline batch %s: running insights for ingest job %s (%s conversations)",
                batch_num,
                job.id,
                len(conversation_ids),
            )
            run_insights(
                db,
                agent_id,
                conversation_ids=conversation_ids or None,
                ingest_job_id=job.id,
            )
    finally:
        db.close()
    return imported, skipped, processed_entries


def _collect_batches(
    client,
    agent_id: str,
    batch_size: int,
    max_total: int,
    *,
    start_date=None,
) -> tuple[list[tuple[int, list[dict]]], int]:
    """Fetch inbox entries (optionally since start_date), dedupe, then slice into batches."""
    settings = get_settings()
    if settings.ingestion_use_date_windows:
        all_entries = client.collect_inbox_entries_by_window(
            agent_id, until_date=start_date, hard_limit=max_total
        )
    else:
        all_entries = client.collect_inbox_entries(agent_id, max_total, start_date=start_date)

    # Dedupe by conversation_id: inbox pagination can drift and list the same conversation
    # twice, which otherwise lands in two batches and races on the sentiment UNIQUE key.
    seen: set[str] = set()
    deduped: list[dict] = []
    for entry in all_entries:
        cid = entry.get("conversation_id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        deduped.append(entry)
    all_entries = deduped
    if not all_entries:
        return [], 0

    batches: list[tuple[int, list[dict]]] = []
    for index in range(0, len(all_entries), batch_size):
        batch_num = index // batch_size + 1
        batches.append((batch_num, all_entries[index : index + batch_size]))

    return batches, len(all_entries)


def _acquire_pipeline_lock(agent_id: str) -> bool:
    if acquire_agent_job_lock(agent_id, PIPELINE_LOCK_KIND):
        return True

    db = SessionLocal()
    try:
        from app.services.job_concurrency import count_running_jobs

        if count_running_jobs(db) == 0:
            release_agent_job_lock(agent_id, PIPELINE_LOCK_KIND)
            if acquire_agent_job_lock(agent_id, PIPELINE_LOCK_KIND):
                logger.info("Cleared stale pipeline lock for agent %s", agent_id)
                return True
    finally:
        db.close()

    return False


def _report_preview_link(agent_id: str) -> str:
    """Public preview page for the pre-generated rapport PDF."""
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}/reports/preview?agent_id={agent_id}"


def _persist_report_pdf(db: Session, agent_id: str) -> None:
    """Render and store the rapport PDF so the preview page can serve it."""
    try:
        pdf = ReportService(db).render_pdf(agent_id)
        save_report_pdf(agent_id, pdf)
        logger.info("Stored report PDF for agent %s", agent_id)
    except Exception:
        logger.exception("Failed to store report PDF for agent %s", agent_id)


def _safe_agent_name(client, agent_id: str) -> str | None:
    """Resolve the Momants agent display name; None if it can't be fetched."""
    try:
        data = client.get_agent(agent_id)
        name = (data.get("name") or "").strip()
        return name or None
    except Exception:
        logger.warning("Could not resolve agent name for %s", agent_id)
        return None


def run_pipeline(
    agent_id: str,
    limit: int | None = None,
    reanalyze: bool = False,
    initiated_by: str | None = None,
) -> None:
    if not _acquire_pipeline_lock(agent_id):
        logger.warning("Pipeline already running for agent %s, skipping", agent_id)
        return

    settings = get_settings()
    db = SessionLocal()
    # We now hold this agent's pipeline lock, so any of its jobs still marked "running" are
    # leftovers from a crashed/killed run. Fail them so they stop occupying global slots.
    orphaned = fail_orphaned_jobs(db, agent_id)
    if orphaned:
        logger.warning("Cleared %s orphaned running job(s) for agent %s before starting", orphaned, agent_id)
    client = get_momants_client()
    agent_name = _safe_agent_name(client, agent_id)
    sync_state = AgentIngestStateService(db)
    sync_started_at = sync_state.mark_sync_started(agent_id)
    inbox_start_date = sync_state.get_inbox_start_date(agent_id)
    set_pipeline_run_state(
        agent_id, started_at=time.time(), stage="ingest", cache_done=0, cache_total=0
    )
    notify_milestone(initiated_by, agent_id, MILESTONE_INGEST_STARTED, agent_name=agent_name)
    try:
        if inbox_start_date:
            logger.info(
                "Incremental ingest for agent %s since %s",
                agent_id,
                inbox_start_date.isoformat(),
            )
        else:
            logger.info("Initial ingest for agent %s (no prior watermark)", agent_id)

        logger.info("Pipeline started for agent %s", agent_id)
        batch_size = limit or settings.ingestion_batch_size
        max_total = settings.ingestion_max_conversations

        batches, total_conversations = _collect_batches(
            client,
            agent_id,
            batch_size,
            max_total,
            start_date=inbox_start_date,
        )
        if not batches:
            logger.info("No new conversations to ingest for agent %s since last sync", agent_id)
            notify_milestone(initiated_by, agent_id, MILESTONE_INGEST_DONE, agent_name=agent_name)
            sync_state.mark_sync_completed(agent_id, sync_started_at, imported=0, skipped=0)
            # Keep the milestone sequence consistent even when there is nothing new to analyze.
            set_pipeline_run_state(agent_id, stage="sentiment")
            notify_milestone(initiated_by, agent_id, MILESTONE_SENTIMENT_STARTED, agent_name=agent_name)
            notify_milestone(initiated_by, agent_id, MILESTONE_SENTIMENT_DONE, agent_name=agent_name)
            set_pipeline_run_state(agent_id, stage="insights")
            notify_milestone(initiated_by, agent_id, MILESTONE_INSIGHTS_STARTED, agent_name=agent_name)
            notify_milestone(initiated_by, agent_id, MILESTONE_INSIGHTS_DONE, agent_name=agent_name)
            set_pipeline_run_state(agent_id, stage="warming")
            warm_agent_cache(db, agent_id)
            _set_scheduler_heartbeat(agent_id)
            set_pipeline_run_state(agent_id, stage="ready")
            _persist_report_pdf(db, agent_id)
            notify_milestone(
                initiated_by,
                agent_id,
                MILESTONE_PDF_READY,
                agent_name=agent_name,
                link=_report_preview_link(agent_id),
            )
            return

        logger.info(
            "Pipeline queued %s batches (%s conversations) with up to %s concurrent jobs",
            len(batches),
            total_conversations,
            settings.max_concurrent_jobs,
        )

        total_imported = 0
        total_skipped = 0
        all_processed_entries: list[dict] = []
        # Cap workers at the per-agent ingest limit so a single agent's run can't open more
        # concurrent batch sessions than its slot budget (which also keeps DB connections
        # well under the pool size and prevents QueuePool exhaustion).
        max_workers = max(1, min(settings.max_concurrent_ingest_per_agent, len(batches)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _run_pipeline_batch,
                    agent_id,
                    batch_num,
                    entries,
                    reanalyze=reanalyze,
                    run_insights_after=settings.pipeline_insights_per_batch,
                    sync_start_date=inbox_start_date,
                )
                for batch_num, entries in batches
            ]
            for future in as_completed(futures):
                imported, skipped, processed_entries = future.result()
                total_imported += imported
                total_skipped += skipped
                all_processed_entries.extend(processed_entries)

        notify_milestone(initiated_by, agent_id, MILESTONE_INGEST_DONE, agent_name=agent_name)

        # Stage 2: dual sentiment over the newly-imported conversations (consolidated pass).
        set_pipeline_run_state(agent_id, stage="sentiment")
        notify_milestone(initiated_by, agent_id, MILESTONE_SENTIMENT_STARTED, agent_name=agent_name)
        if all_processed_entries:
            sentiment_conversation_ids = _conversation_ids_for_entries(db, agent_id, all_processed_entries)
            if sentiment_conversation_ids:
                logger.info(
                    "Running sentiment pass for agent %s (%s new conversations)",
                    agent_id,
                    len(sentiment_conversation_ids),
                )
                run_sentiment(db, agent_id, conversation_ids=sentiment_conversation_ids)
        notify_milestone(initiated_by, agent_id, MILESTONE_SENTIMENT_DONE, agent_name=agent_name)

        set_pipeline_run_state(agent_id, stage="insights")
        notify_milestone(initiated_by, agent_id, MILESTONE_INSIGHTS_STARTED, agent_name=agent_name)
        if settings.pipeline_insights_per_batch:
            # Legacy path: per-batch jobs already computed the per-conversation phases but
            # skipped question clustering (it must be global). Run the single consolidated
            # clustering pass now that every batch is in.
            logger.info("Running global question clustering for agent %s", agent_id)
            InsightsService(db).finalize_questions(agent_id)
        elif all_processed_entries:
            # Default path: one consolidated insights pass over only the newly-imported
            # conversations (big batches → best inference throughput), then the single global
            # question-clustering pass. Incremental syncs only touch new conversations.
            conversation_ids = _conversation_ids_for_entries(db, agent_id, all_processed_entries)
            logger.info(
                "Running consolidated insights pass for agent %s (%s new conversations)",
                agent_id,
                len(conversation_ids),
            )
            if conversation_ids:
                run_insights(db, agent_id, conversation_ids=conversation_ids)
            InsightsService(db).finalize_questions(agent_id)

        notify_milestone(initiated_by, agent_id, MILESTONE_INSIGHTS_DONE, agent_name=agent_name)
        sync_state.mark_sync_completed(
            agent_id,
            sync_started_at,
            imported=total_imported,
            skipped=total_skipped,
        )
        set_pipeline_run_state(agent_id, stage="warming")
        warm_agent_cache(db, agent_id)
        _set_scheduler_heartbeat(agent_id)
        set_pipeline_run_state(agent_id, stage="ready")
        _persist_report_pdf(db, agent_id)
        notify_milestone(
            initiated_by,
            agent_id,
            MILESTONE_PDF_READY,
            agent_name=agent_name,
            link=_report_preview_link(agent_id),
        )
        logger.info(
            "Pipeline completed for agent %s (%s batches, %s listed, %s imported, %s skipped)",
            agent_id,
            len(batches),
            total_conversations,
            total_imported,
            total_skipped,
        )
    except Exception as exc:
        logger.exception("Pipeline failed for agent %s", agent_id)
        # Release any of this agent's in-flight slots so a failed run can't starve others.
        try:
            db.rollback()
            cleared = fail_orphaned_jobs(db, agent_id, error=f"Pipeline aborted: {str(exc)[:300]}")
            if cleared:
                logger.warning("Failed %s in-flight job(s) for agent %s after abort", cleared, agent_id)
        except Exception:
            logger.exception("Failed to clean up in-flight jobs for agent %s", agent_id)
        notify_milestone(initiated_by, agent_id, MILESTONE_FAILED, agent_name=agent_name, error=str(exc))
        raise
    finally:
        client.close()
        release_agent_job_lock(agent_id, PIPELINE_LOCK_KIND)
        db.close()


def run_reanalyze(agent_id: str, initiated_by: str | None = None) -> None:
    """Re-run stages 2 + 3 (sentiment + insights) over conversations already in the DB.

    Skips ingest entirely (no Momants fetch, no watermark change) and re-derives sentiment
    for every member message, then recomputes metrics/insights and warms the cache so the
    dashboard and PDF report reflect the new models.
    """
    if not _acquire_pipeline_lock(agent_id):
        logger.warning("Pipeline already running for agent %s, skipping reanalyze", agent_id)
        return

    db = SessionLocal()
    orphaned = fail_orphaned_jobs(db, agent_id)
    if orphaned:
        logger.warning("Cleared %s orphaned running job(s) for agent %s before reanalyze", orphaned, agent_id)
    client = get_momants_client()
    agent_name = _safe_agent_name(client, agent_id)
    set_pipeline_run_state(
        agent_id, started_at=time.time(), stage="sentiment", cache_done=0, cache_total=0
    )
    try:
        conversation_ids = _all_conversation_ids(db, agent_id)
        logger.info("Reanalyze for agent %s (%s conversations)", agent_id, len(conversation_ids))

        notify_milestone(initiated_by, agent_id, MILESTONE_SENTIMENT_STARTED, agent_name=agent_name)
        if conversation_ids:
            run_sentiment(db, agent_id, conversation_ids=conversation_ids, reanalyze=True)
        notify_milestone(initiated_by, agent_id, MILESTONE_SENTIMENT_DONE, agent_name=agent_name)

        set_pipeline_run_state(agent_id, stage="insights")
        notify_milestone(initiated_by, agent_id, MILESTONE_INSIGHTS_STARTED, agent_name=agent_name)
        if conversation_ids:
            run_insights(db, agent_id, conversation_ids=conversation_ids)
            InsightsService(db).finalize_questions(agent_id)
        notify_milestone(initiated_by, agent_id, MILESTONE_INSIGHTS_DONE, agent_name=agent_name)

        set_pipeline_run_state(agent_id, stage="warming")
        warm_agent_cache(db, agent_id)
        _set_scheduler_heartbeat(agent_id)
        set_pipeline_run_state(agent_id, stage="ready")
        _persist_report_pdf(db, agent_id)
        notify_milestone(
            initiated_by,
            agent_id,
            MILESTONE_PDF_READY,
            agent_name=agent_name,
            link=_report_preview_link(agent_id),
        )
        logger.info("Reanalyze completed for agent %s (%s conversations)", agent_id, len(conversation_ids))
    except Exception as exc:
        logger.exception("Reanalyze failed for agent %s", agent_id)
        try:
            db.rollback()
            cleared = fail_orphaned_jobs(db, agent_id, error=f"Reanalyze aborted: {str(exc)[:300]}")
            if cleared:
                logger.warning("Failed %s in-flight job(s) for agent %s after abort", cleared, agent_id)
        except Exception:
            logger.exception("Failed to clean up in-flight jobs for agent %s", agent_id)
        notify_milestone(initiated_by, agent_id, MILESTONE_FAILED, agent_name=agent_name, error=str(exc))
        raise
    finally:
        client.close()
        release_agent_job_lock(agent_id, PIPELINE_LOCK_KIND)
        db.close()


def _set_scheduler_heartbeat(agent_id: str) -> None:
    from app.cache import get_cache_client

    try:
        client = get_cache_client()
        client.set(f"scheduler:heartbeat:{agent_id}", str(int(time.time())))
    except Exception:
        logger.exception("Failed to set scheduler heartbeat for %s", agent_id)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Momants preprocessing pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Full pipeline: batched ingest → insights → warm cache")
    run_parser.add_argument("--agent-id", required=True)
    run_parser.add_argument("--limit", type=int, default=None, help="Batch size override")
    run_parser.add_argument("--reanalyze", action="store_true")
    run_parser.add_argument(
        "--initiated-by",
        default=None,
        help="Email of the user who requested this run (for Slack milestone DMs)",
    )

    ingest_parser = sub.add_parser("ingest", help="Ingest only")
    ingest_parser.add_argument("--agent-id", required=True)
    ingest_parser.add_argument("--limit", type=int, default=None)
    ingest_parser.add_argument("--skip", type=int, default=0)
    ingest_parser.add_argument("--reanalyze", action="store_true")

    sentiment_parser = sub.add_parser("sentiment", help="Sentiment (stage 2) only")
    sentiment_parser.add_argument("--agent-id", required=True)
    sentiment_parser.add_argument("--reanalyze", action="store_true")

    insights_parser = sub.add_parser("insights", help="Insights only")
    insights_parser.add_argument("--agent-id", required=True)

    reanalyze_parser = sub.add_parser(
        "reanalyze", help="Re-run stages 2+3 (sentiment + insights) over existing conversations"
    )
    reanalyze_parser.add_argument("--agent-id", required=True)
    reanalyze_parser.add_argument(
        "--initiated-by",
        default=None,
        help="Email of the user who requested this run (for Slack milestone DMs)",
    )

    warm_parser = sub.add_parser("warm", help="Warm cache only")
    warm_parser.add_argument("--agent-id", required=True)

    agents_parser = sub.add_parser("run-all", help="Run pipeline for all SCHEDULED_AGENT_IDS")
    agents_parser.add_argument("--limit", type=int, default=None)
    agents_parser.add_argument("--reanalyze", action="store_true")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )

    settings = get_settings()
    if settings.preload_models:
        from app.ml.model_registry import get_model_registry

        logger.info("Preloading Hugging Face models")
        get_model_registry().preload()

    args = _parse_args(argv)
    db = SessionLocal()

    try:
        if args.command == "run":
            run_pipeline(
                args.agent_id,
                limit=args.limit,
                reanalyze=args.reanalyze,
                initiated_by=args.initiated_by,
            )
        elif args.command == "ingest":
            if not acquire_agent_job_lock(args.agent_id, PIPELINE_LOCK_KIND):
                logger.warning("Pipeline lock held for %s, skipping ingest", args.agent_id)
                return 0
            try:
                run_ingest(db, args.agent_id, limit=args.limit, reanalyze=args.reanalyze, skip=args.skip)
            finally:
                release_agent_job_lock(args.agent_id, PIPELINE_LOCK_KIND)
        elif args.command == "sentiment":
            if not acquire_agent_job_lock(args.agent_id, PIPELINE_LOCK_KIND):
                logger.warning("Pipeline lock held for %s, skipping sentiment", args.agent_id)
                return 0
            try:
                run_sentiment(db, args.agent_id, reanalyze=args.reanalyze)
            finally:
                release_agent_job_lock(args.agent_id, PIPELINE_LOCK_KIND)
        elif args.command == "insights":
            if not acquire_agent_job_lock(args.agent_id, PIPELINE_LOCK_KIND):
                logger.warning("Pipeline lock held for %s, skipping insights", args.agent_id)
                return 0
            try:
                run_insights(db, args.agent_id)
                warm_agent_cache(db, args.agent_id)
                _set_scheduler_heartbeat(args.agent_id)
            finally:
                release_agent_job_lock(args.agent_id, PIPELINE_LOCK_KIND)
        elif args.command == "reanalyze":
            run_reanalyze(args.agent_id, initiated_by=args.initiated_by)
        elif args.command == "warm":
            warm_agent_cache(db, args.agent_id)
            _set_scheduler_heartbeat(args.agent_id)
        elif args.command == "run-all":
            agent_ids = settings.scheduled_agent_id_list
            if not agent_ids:
                logger.error("SCHEDULED_AGENT_IDS is empty")
                return 1
            with ThreadPoolExecutor(max_workers=len(agent_ids)) as executor:
                futures = [
                    executor.submit(
                        run_pipeline,
                        agent_id.strip(),
                        limit=args.limit,
                        reanalyze=args.reanalyze,
                    )
                    for agent_id in agent_ids
                ]
                for future in as_completed(futures):
                    future.result()
        else:
            return 1
    except Exception:
        logger.exception("Pipeline command failed")
        return 1
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
