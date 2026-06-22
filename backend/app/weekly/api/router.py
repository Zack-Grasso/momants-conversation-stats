from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user
from app.auth.session import AuthUser
from app.weekly.database import get_weekly_db
from app.weekly.models import WeeklyAgentRun, WeeklyConversation, WeeklyQuestionCluster, WeeklyRun, WeeklyUnansweredFinding
from app.weekly.schemas import (
    WeeklyAgentSummary,
    WeeklyRunSummary,
    WeeklyRunTriggerResponse,
    WeeklySettingsResponse,
    WeeklySettingsUpdate,
    WeeklyTopQuestion,
)
from app.weekly.services.report_service import WeeklyReportService
from app.weekly.services.storage import bundle_zip_path

router = APIRouter()


def _parse_counts(raw: str | None) -> dict[str, int]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {k: int(v) for k, v in data.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _counts_from_findings(db: Session, agent_run_id: int) -> dict[str, int]:
    rows = db.scalars(
        select(WeeklyUnansweredFinding.status).where(WeeklyUnansweredFinding.agent_run_id == agent_run_id)
    ).all()
    totals = {"no_reply": 0, "weak_answer": 0, "not_answered": 0, "total": 0}
    for status in rows:
        if status in totals:
            totals[status] += 1
            totals["total"] += 1
    return totals


def _agent_counts(db: Session, agent_run: WeeklyAgentRun) -> dict[str, int]:
    parsed = _parse_counts(agent_run.counts_json)
    if parsed.get("total", 0) > 0 or any(parsed.get(k, 0) for k in ("no_reply", "weak_answer", "not_answered")):
        return parsed
    from_db = _counts_from_findings(db, agent_run.id)
    if from_db["total"] > 0:
        return from_db
    return parsed


def _agent_summary(db: Session, agent_run: WeeklyAgentRun) -> WeeklyAgentSummary:
    clusters = list(
        db.scalars(
            select(WeeklyQuestionCluster)
            .where(WeeklyQuestionCluster.agent_run_id == agent_run.id)
            .order_by(WeeklyQuestionCluster.rank)
            .limit(5)
        ).all()
    )
    value_stats = None
    if agent_run.value_stats_json:
        try:
            value_stats = json.loads(agent_run.value_stats_json)
        except json.JSONDecodeError:
            value_stats = None
    pdf_ok = bool(agent_run.pdf_path and Path(agent_run.pdf_path).is_file())
    return WeeklyAgentSummary(
        agent_id=agent_run.agent_id,
        agent_name=agent_run.agent_name,
        status=agent_run.status,
        error=agent_run.error,
        pdf_available=pdf_ok,
        counts=_agent_counts(db, agent_run),
        top_questions=[
            WeeklyTopQuestion(rank=c.rank, count=c.count, text=c.representative_text) for c in clusters
        ],
        value_stats=value_stats,
    )


def _run_summary(db: Session, run: WeeklyRun) -> WeeklyRunSummary:
    agent_runs = list(
        db.scalars(
            select(WeeklyAgentRun)
            .where(WeeklyAgentRun.weekly_run_id == run.id)
            .order_by(WeeklyAgentRun.agent_name, WeeklyAgentRun.agent_id)
        ).all()
    )
    agents = [_agent_summary(db, ar) for ar in agent_runs]
    totals = {"no_reply": 0, "weak_answer": 0, "not_answered": 0, "total": 0}
    for agent in agents:
        for key in totals:
            totals[key] += agent.counts.get(key, 0)
    summary = None
    if run.summary_json:
        try:
            summary = json.loads(run.summary_json)
        except json.JSONDecodeError:
            summary = None
    zip_ok = bundle_zip_path(run.week_id).is_file()
    return WeeklyRunSummary(
        week_id=run.week_id,
        since=run.since,
        until=run.until,
        status=run.status,
        agent_count=len(agent_runs),
        counts=totals,
        zip_available=zip_ok,
        agents=agents,
    )


def _get_run(db: Session, week_id: str | None) -> WeeklyRun:
    if week_id:
        run = db.scalar(select(WeeklyRun).where(WeeklyRun.week_id == week_id))
    else:
        run = db.scalar(select(WeeklyRun).order_by(WeeklyRun.started_at.desc()).limit(1))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Weekly run not found")
    return run


def _settings_response() -> WeeklySettingsResponse:
    from app.weekly.launcher import is_weekly_running
    from app.weekly.settings_store import (
        WeeklyConfig,
        get_weekly_config,
        get_weekly_run_state,
        next_scheduled_run_utc,
    )

    config = get_weekly_config()
    next_run = None
    if config.enabled:
        try:
            next_run = next_scheduled_run_utc(config.cron)
        except Exception:
            next_run = None
    return WeeklySettingsResponse(
        cron=config.cron,
        days=config.days,
        enabled=config.enabled,
        agent_id=config.agent_id,
        scoped=config.scoped,
        running=is_weekly_running(),
        next_run_at=next_run,
        run_state=get_weekly_run_state(),
    )


@router.get("/settings", response_model=WeeklySettingsResponse)
def read_weekly_settings() -> WeeklySettingsResponse:
    return _settings_response()


@router.put("/settings", response_model=WeeklySettingsResponse)
def write_weekly_settings(payload: WeeklySettingsUpdate) -> WeeklySettingsResponse:
    from croniter import croniter

    from app.weekly.settings_store import WeeklyConfig, get_weekly_config, save_weekly_config

    current = get_weekly_config()
    cron = payload.cron.strip() if payload.cron is not None else current.cron
    if not croniter.is_valid(cron):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid cron expression: {cron}")

    updated = save_weekly_config(
        WeeklyConfig(
            cron=cron,
            days=payload.days if payload.days is not None else current.days,
            enabled=payload.enabled if payload.enabled is not None else current.enabled,
            agent_id=payload.agent_id.strip() if payload.agent_id is not None else current.agent_id,
        )
    )
    return _settings_response()


@router.post("/run", response_model=WeeklyRunTriggerResponse, status_code=status.HTTP_202_ACCEPTED)
def trigger_run(user: AuthUser = Depends(get_current_user)) -> WeeklyRunTriggerResponse:
    from app.weekly.launcher import WeeklyBusyError, launch_weekly_run

    try:
        launch_weekly_run(initiated_by=user.email)
    except WeeklyBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    scoped = _settings_response().scoped
    scope = "test agent" if scoped else "all agents"
    return WeeklyRunTriggerResponse(
        status="started",
        message=f"Weekly report run started for {scope}. Refresh in a few minutes to see results.",
    )


@router.get("/runs", response_model=list[WeeklyRunSummary])
def list_runs(
    week_id: str | None = Query(default=None),
    db: Session = Depends(get_weekly_db),
) -> list[WeeklyRunSummary]:
    if week_id:
        run = _get_run(db, week_id)
        return [_run_summary(db, run)]
    runs = list(db.scalars(select(WeeklyRun).order_by(WeeklyRun.started_at.desc())).all())
    return [_run_summary(db, run) for run in runs]


@router.get("/runs/{week_id}/agents", response_model=list[WeeklyAgentSummary])
def list_agents(week_id: str, db: Session = Depends(get_weekly_db)) -> list[WeeklyAgentSummary]:
    run = _get_run(db, week_id)
    agent_runs = list(
        db.scalars(select(WeeklyAgentRun).where(WeeklyAgentRun.weekly_run_id == run.id)).all()
    )
    return [_agent_summary(db, ar) for ar in agent_runs]


@router.get("/runs/{week_id}/agents/{agent_id}/pdf")
def download_pdf(
    week_id: str,
    agent_id: str,
    inline: bool = Query(default=False),
    db: Session = Depends(get_weekly_db),
) -> Response:
    run = _get_run(db, week_id)
    agent_run = db.scalar(
        select(WeeklyAgentRun).where(
            WeeklyAgentRun.weekly_run_id == run.id,
            WeeklyAgentRun.agent_id == agent_id,
        )
    )
    if agent_run is None or agent_run.status == "skipped" or not agent_run.pdf_path:
        raise HTTPException(status_code=404, detail="PDF not found")
    path = Path(agent_run.pdf_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="PDF file missing")
    disposition = "inline" if inline else "attachment"
    return Response(
        content=path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{agent_id}.pdf"'},
    )


@router.get("/runs/{week_id}/agents/{agent_id}/preview", response_class=HTMLResponse)
def preview_html(week_id: str, agent_id: str, db: Session = Depends(get_weekly_db)) -> HTMLResponse:
    run = _get_run(db, week_id)
    agent_run = db.scalar(
        select(WeeklyAgentRun)
        .where(WeeklyAgentRun.weekly_run_id == run.id, WeeklyAgentRun.agent_id == agent_id)
        .options(
            selectinload(WeeklyAgentRun.weekly_run),
            selectinload(WeeklyAgentRun.conversations).selectinload(WeeklyConversation.messages),
        )
    )
    if agent_run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    if agent_run.status == "skipped":
        raise HTTPException(status_code=404, detail="Agent skipped due to no conversations")
    html = WeeklyReportService(db).render_html(agent_run)
    return HTMLResponse(content=html)


@router.get("/runs/{week_id}/zip")
def download_zip(week_id: str, db: Session = Depends(get_weekly_db)) -> Response:
    _get_run(db, week_id)
    path = bundle_zip_path(week_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Zip bundle not found")
    return Response(
        content=path.read_bytes(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="unanswered-{week_id}.zip"'},
    )
