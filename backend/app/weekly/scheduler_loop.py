"""Wait until the configured weekly cron time, then run the pipeline."""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

from app.weekly.database import init_weekly_db
from app.weekly.pipeline import run_all
from app.weekly.settings_store import get_weekly_config, next_scheduled_run_utc

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    init_weekly_db()
    logger.info("Weekly scheduler loop started")
    while True:
        config = get_weekly_config()
        if not config.enabled:
            logger.info("Weekly scheduler disabled — rechecking in 60s")
            time.sleep(60)
            continue

        try:
            next_run = next_scheduled_run_utc(config.cron)
        except Exception:
            logger.exception("Invalid weekly cron %r — rechecking in 60s", config.cron)
            time.sleep(60)
            continue

        now = datetime.now(timezone.utc)
        wait_seconds = max((next_run - now).total_seconds(), 1.0)
        logger.info(
            "Next weekly run at %s UTC (cron=%s, in %.0f seconds)",
            next_run.strftime("%Y-%m-%d %H:%M"),
            config.cron,
            wait_seconds,
        )

        deadline = next_run
        while datetime.now(timezone.utc) < deadline:
            time.sleep(min(60.0, max((deadline - datetime.now(timezone.utc)).total_seconds(), 1.0)))
            latest = get_weekly_config()
            if not latest.enabled:
                break
            if latest.cron != config.cron or latest.days != config.days:
                break

        latest = get_weekly_config()
        if not latest.enabled:
            continue
        if datetime.now(timezone.utc) < deadline:
            continue

        logger.info("Starting scheduled weekly pipeline run")
        try:
            run_all(trigger="scheduled")
        except Exception:
            logger.exception("Scheduled weekly pipeline run failed")
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
