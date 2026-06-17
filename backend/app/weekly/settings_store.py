"""Runtime weekly scheduler settings stored in Redis (overrides env defaults)."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from app.config import get_settings
from app.locks import get_lock_client

logger = logging.getLogger(__name__)

SETTINGS_KEY = "weekly:settings"
RUN_STATE_KEY = "weekly:run:state"


@dataclass
class WeeklyConfig:
    cron: str
    days: int
    enabled: bool
    agent_id: str

    @property
    def scoped(self) -> bool:
        return bool(self.agent_id.strip())


def _defaults() -> WeeklyConfig:
    settings = get_settings()
    return WeeklyConfig(
        cron=settings.weekly_unanswered_cron,
        days=settings.weekly_unanswered_days,
        enabled=settings.weekly_unanswered_enabled,
        agent_id=settings.weekly_unanswered_agent_id.strip(),
    )


def get_weekly_config() -> WeeklyConfig:
    try:
        raw = get_lock_client().get(SETTINGS_KEY)
        if not raw:
            return _defaults()
        data = json.loads(raw)
        defaults = _defaults()
        return WeeklyConfig(
            cron=str(data.get("cron") or defaults.cron),
            days=max(1, min(int(data.get("days", defaults.days)), 30)),
            enabled=bool(data.get("enabled", defaults.enabled)),
            agent_id=str(data.get("agent_id") or "").strip(),
        )
    except Exception:
        logger.exception("Failed to read weekly settings from Redis")
        return _defaults()


def save_weekly_config(config: WeeklyConfig) -> WeeklyConfig:
    normalized = WeeklyConfig(
        cron=config.cron.strip(),
        days=max(1, min(config.days, 30)),
        enabled=config.enabled,
        agent_id=config.agent_id.strip(),
    )
    get_lock_client().set(SETTINGS_KEY, json.dumps(asdict(normalized)))
    return normalized


def get_weekly_run_state() -> dict | None:
    try:
        raw = get_lock_client().get(RUN_STATE_KEY)
        return json.loads(raw) if raw else None
    except Exception:
        logger.exception("Failed to read weekly run state")
        return None


def set_weekly_run_state(**fields) -> None:
    try:
        client = get_lock_client()
        raw = client.get(RUN_STATE_KEY)
        state = json.loads(raw) if raw else {}
        state.update(fields)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        client.setex(RUN_STATE_KEY, 7200, json.dumps(state, default=str))
    except Exception:
        logger.exception("Failed to set weekly run state")


def clear_weekly_run_state() -> None:
    try:
        get_lock_client().delete(RUN_STATE_KEY)
    except Exception:
        logger.exception("Failed to clear weekly run state")


def next_scheduled_run_utc(cron: str) -> datetime:
    from croniter import croniter

    base = datetime.now(timezone.utc)
    return croniter(cron, base).get_next(datetime).replace(tzinfo=timezone.utc)
