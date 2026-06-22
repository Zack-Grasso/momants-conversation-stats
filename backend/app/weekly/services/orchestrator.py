from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.integrations.momants_client import get_momants_client
from app.integrations.slack_client import post_weekly_unanswered_bundle
from app.weekly.models import WeeklyAgentRun, WeeklyQuestionCluster, WeeklyRun, WeeklyUnansweredFinding
from app.weekly.services.analysis_service import WeeklyAnalysisService
from app.weekly.services.ingest_service import WeeklyIngestService
from app.weekly.services.report_service import WeeklyReportService
from app.weekly.services.storage import agent_pdf_path, bundle_zip_path, safe_agent_filename, week_window
from app.weekly.settings_store import set_weekly_run_state

logger = logging.getLogger(__name__)

AGENT_STATUS_SKIPPED = "skipped"


def _clear_agent_outputs(agent_run: WeeklyAgentRun) -> None:
    from sqlalchemy import delete

    if agent_run.pdf_path:
        pdf_path = Path(agent_run.pdf_path)
        if pdf_path.is_file():
            pdf_path.unlink()
    agent_run.pdf_path = None
    agent_run.counts_json = None
    agent_run.value_stats_json = None


def _clear_agent_analysis(db: Session, agent_run: WeeklyAgentRun) -> None:
    from sqlalchemy import delete

    db.execute(delete(WeeklyUnansweredFinding).where(WeeklyUnansweredFinding.agent_run_id == agent_run.id))
    db.execute(delete(WeeklyQuestionCluster).where(WeeklyQuestionCluster.agent_run_id == agent_run.id))


def _set_progress(**fields) -> None:
    set_weekly_run_state(**fields)


def _set_weekly_heartbeat(week_id: str, agent_count: int) -> None:
    from app.cache import get_cache_client

    try:
        client = get_cache_client()
        client.set(
            "weekly:heartbeat",
            json.dumps({"week_id": week_id, "agent_count": agent_count, "ts": int(datetime.now(timezone.utc).timestamp())}),
        )
    except Exception:
        logger.exception("Failed to set weekly heartbeat")


class WeeklyOrchestrator:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.ingest = WeeklyIngestService(db)
        self.analysis = WeeklyAnalysisService(db)
        self.report = WeeklyReportService(db)
        self.client = get_momants_client()

    def get_or_create_run(self, since: datetime, until: datetime, week_id: str) -> WeeklyRun:
        from sqlalchemy import select

        run = self.db.scalar(select(WeeklyRun).where(WeeklyRun.week_id == week_id))
        if run is None:
            run = WeeklyRun(week_id=week_id, since=since, until=until, status="running")
            self.db.add(run)
            self.db.commit()
            self.db.refresh(run)
        else:
            run.since = since
            run.until = until
            run.status = "running"
            run.completed_at = None
            self.db.commit()
        return run

    def run_agent(self, weekly_run: WeeklyRun, agent_id: str, agent_name: str | None) -> WeeklyAgentRun:
        from sqlalchemy import select

        agent_run = self.db.scalar(
            select(WeeklyAgentRun).where(
                WeeklyAgentRun.weekly_run_id == weekly_run.id,
                WeeklyAgentRun.agent_id == agent_id,
            )
        )
        if agent_run is None:
            agent_run = WeeklyAgentRun(
                weekly_run_id=weekly_run.id,
                agent_id=agent_id,
                agent_name=agent_name,
                status="running",
            )
            self.db.add(agent_run)
            self.db.commit()
            self.db.refresh(agent_run)
        else:
            agent_run.status = "running"
            agent_run.error = None
            agent_run.agent_name = agent_name
            self.db.commit()

        try:
            _set_progress(current_step="ingest")
            self.ingest.ingest_agent_window(agent_run, since=weekly_run.since, until=weekly_run.until)
            conversations = self.ingest.load_conversations(agent_run.id)
            if not conversations:
                _clear_agent_analysis(self.db, agent_run)
                _clear_agent_outputs(agent_run)
                agent_run.status = AGENT_STATUS_SKIPPED
                agent_run.error = None
                agent_run.completed_at = datetime.now(timezone.utc)
                self.db.commit()
                logger.info(
                    "Weekly report skipped for agent %s week %s (no conversations)",
                    agent_id,
                    weekly_run.week_id,
                )
                return agent_run

            _set_progress(current_step="analyze")
            counts = self.analysis.analyze(agent_run, conversations)
            self.report.persist_value_stats(agent_run)
            agent_run.counts_json = json.dumps(counts)

            agent_run.weekly_run = weekly_run
            for conv in conversations:
                conv.agent_run = agent_run
            agent_run.conversations = conversations

            _set_progress(current_step="pdf")
            pdf_bytes = self.report.render_pdf(agent_run)
            pdf_path = agent_pdf_path(weekly_run.week_id, agent_id)
            pdf_path.write_bytes(pdf_bytes)
            agent_run.pdf_path = str(pdf_path)
            agent_run.status = "complete"
            agent_run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            logger.info("Weekly report complete for agent %s week %s", agent_id, weekly_run.week_id)
        except Exception as exc:
            logger.exception("Weekly run failed for agent %s", agent_id)
            agent_run.status = "failed"
            agent_run.error = str(exc)[:2000]
            agent_run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
        return agent_run

    def finalize_run(self, weekly_run: WeeklyRun) -> Path | None:
        from sqlalchemy import select

        agent_runs = list(
            self.db.scalars(
                select(WeeklyAgentRun)
                .where(WeeklyAgentRun.weekly_run_id == weekly_run.id, WeeklyAgentRun.status == "complete")
                .options(selectinload(WeeklyAgentRun.clusters))
            ).all()
        )
        if not agent_runs:
            zip_path = bundle_zip_path(weekly_run.week_id)
            if zip_path.is_file():
                zip_path.unlink()
            weekly_run.summary_json = json.dumps({"agent_count": 0, "counts": {"no_reply": 0, "weak_answer": 0, "not_answered": 0, "total": 0}})
            weekly_run.status = "complete"
            weekly_run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return None

        zip_path = bundle_zip_path(weekly_run.week_id)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for ar in agent_runs:
                if ar.pdf_path and Path(ar.pdf_path).is_file():
                    filename = f"{safe_agent_filename(ar.agent_name, ar.agent_id)}.pdf"
                    zf.write(ar.pdf_path, arcname=filename)

        totals = {"no_reply": 0, "weak_answer": 0, "not_answered": 0, "total": 0}
        for ar in agent_runs:
            if ar.counts_json:
                c = json.loads(ar.counts_json)
                for key in totals:
                    totals[key] += int(c.get(key, 0))

        summary = {
            "agent_count": len(agent_runs),
            "counts": totals,
            "zip_path": str(zip_path),
        }
        weekly_run.summary_json = json.dumps(summary)
        weekly_run.status = "complete"
        weekly_run.completed_at = datetime.now(timezone.utc)
        self.db.commit()

        settings = get_settings()
        if settings.slack_enabled and settings.slack_weekly_report_channel_id:
            post_weekly_unanswered_bundle(
                settings.slack_weekly_report_channel_id,
                zip_path,
                weekly_run.week_id,
                summary,
            )

        _set_weekly_heartbeat(weekly_run.week_id, len(agent_runs))
        return zip_path

    def run_all(self, days: int | None = None) -> WeeklyRun:
        from app.weekly.settings_store import get_weekly_config

        config = get_weekly_config()
        effective_days = days if days is not None else config.days
        since, until, week_id = week_window(effective_days)
        weekly_run = self.get_or_create_run(since, until, week_id)
        scoped_agent_id = config.agent_id.strip()

        if scoped_agent_id:
            agent_entries = [{"id": scoped_agent_id, "name": None}]
        else:
            agent_entries = [
                {"id": agent.get("id"), "name": (agent.get("name") or "").strip() or None}
                for agent in self.client.list_agents()
                if agent.get("id")
            ]

        total = len(agent_entries)
        _set_progress(
            week_id=week_id,
            phase="agents",
            agent_total=total,
            agent_index=0,
            agents_complete=0,
            agents_failed=0,
        )

        completed = 0
        failed = 0

        if scoped_agent_id:
            logger.info("Weekly run scoped to test agent %s", scoped_agent_id)
            name = agent_entries[0]["name"]
            try:
                data = self.client.get_agent(scoped_agent_id)
                name = (data.get("name") or "").strip() or None
            except Exception:
                logger.warning("Could not resolve agent name for %s", scoped_agent_id)
            _set_progress(
                agent_index=1,
                current_agent_id=scoped_agent_id,
                current_agent_name=name,
                current_step="starting",
            )
            agent_run = self.run_agent(weekly_run, scoped_agent_id, name)
            if agent_run.status == "complete":
                completed += 1
            elif agent_run.status != AGENT_STATUS_SKIPPED:
                failed += 1
            _set_progress(agents_complete=completed, agents_failed=failed)
        else:
            for index, agent in enumerate(agent_entries, start=1):
                agent_id = agent["id"]
                name = agent["name"]
                _set_progress(
                    agent_index=index,
                    current_agent_id=agent_id,
                    current_agent_name=name,
                    current_step="starting",
                )
                agent_run = self.run_agent(weekly_run, agent_id, name)
                if agent_run.status == "complete":
                    completed += 1
                elif agent_run.status != AGENT_STATUS_SKIPPED:
                    failed += 1
                _set_progress(agents_complete=completed, agents_failed=failed)

        _set_progress(phase="finalize", current_step="zip", current_agent_name=None)
        self.finalize_run(weekly_run)
        _set_progress(phase="done", current_step="complete")
        return weekly_run
