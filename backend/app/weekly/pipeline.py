from __future__ import annotations

import argparse
import logging
import sys

from app.config import get_settings
from app.weekly.database import WeeklySessionLocal, init_weekly_db
from app.weekly.services.orchestrator import WeeklyOrchestrator

logger = logging.getLogger(__name__)


def run_agent(agent_id: str, days: int | None = None) -> int:
    from app.integrations.momants_client import get_momants_client
    from app.weekly.services.storage import week_window

    since, until, week_id = week_window(days)
    db = WeeklySessionLocal()
    try:
        orchestrator = WeeklyOrchestrator(db)
        weekly_run = orchestrator.get_or_create_run(since, until, week_id)
        client = get_momants_client()
        name = None
        try:
            data = client.get_agent(agent_id)
            name = (data.get("name") or "").strip() or None
        except Exception:
            logger.warning("Could not resolve agent name for %s", agent_id)
        orchestrator.run_agent(weekly_run, agent_id, name)
        orchestrator.finalize_run(weekly_run)
    finally:
        db.close()
    return 0


def run_all(days: int | None = None, *, initiated_by: str | None = None, trigger: str = "manual") -> int:
    from app.weekly.launcher import (
        _acquire_weekly_lock,
        is_weekly_running,
        mark_weekly_run_finished,
        mark_weekly_run_started,
    )

    if trigger == "scheduled":
        if not _acquire_weekly_lock():
            logger.warning("Skipping scheduled weekly run — already in progress")
            return 0
        mark_weekly_run_started(trigger=trigger)
    elif initiated_by:
        pass
    elif is_weekly_running():
        logger.error("Weekly run already in progress")
        return 1
    else:
        if not _acquire_weekly_lock():
            logger.error("Weekly run already in progress")
            return 1
        mark_weekly_run_started(trigger=trigger)

    week_id = None
    error = None
    try:
        db = WeeklySessionLocal()
        try:
            weekly_run = WeeklyOrchestrator(db).run_all(days)
            week_id = weekly_run.week_id
        finally:
            db.close()
    except Exception as exc:
        error = str(exc)[:2000]
        logger.exception("Weekly run-all failed")
        raise
    finally:
        mark_weekly_run_finished(week_id=week_id, error=error)

    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    settings = get_settings()
    init_weekly_db()
    if settings.preload_models and (argv is None or "run-all" in (argv or sys.argv)):
        from app.weekly.settings_store import set_weekly_run_state

        set_weekly_run_state(phase="preload", current_step="models")
        from app.ml.model_registry import get_model_registry

        logger.info("Preloading Hugging Face models for weekly pipeline")
        get_model_registry().preload()

    parser = argparse.ArgumentParser(description="Weekly unanswered report pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="Run weekly report for one agent")
    run_parser.add_argument("--agent-id", required=True)
    run_parser.add_argument("--days", type=int, default=None)
    all_parser = sub.add_parser("run-all", help="Run weekly reports for all Momants agents")
    all_parser.add_argument("--days", type=int, default=None)
    all_parser.add_argument("--initiated-by", default=None)
    args = parser.parse_args(argv)

    initiated_by = getattr(args, "initiated_by", None)

    if args.command == "run":
        return run_agent(args.agent_id, args.days)
    if args.command == "run-all":
        return run_all(args.days, initiated_by=initiated_by)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
