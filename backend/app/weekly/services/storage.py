from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings


def week_id_for(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_window(days: int | None = None) -> tuple[datetime, datetime, str]:
    if days is None:
        from app.weekly.settings_store import get_weekly_config

        days = get_weekly_config().days
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    return since, until, week_id_for(until)


def week_dir(week_id: str) -> Path:
    path = Path(get_settings().weekly_reports_dir) / week_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent_pdf_path(week_id: str, agent_id: str) -> Path:
    return week_dir(week_id) / f"{agent_id.strip()}.pdf"


def bundle_zip_path(week_id: str) -> Path:
    return week_dir(week_id) / "bundle.zip"


def safe_agent_filename(agent_name: str | None, agent_id: str) -> str:
    base = (agent_name or agent_id).strip()
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in base)
    return (cleaned or agent_id[:8]).strip()[:80]
