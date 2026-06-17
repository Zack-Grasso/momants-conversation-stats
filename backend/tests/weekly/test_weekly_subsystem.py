from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.weekly.database import WeeklyBase
from app.weekly.models import WeeklyAgentRun, WeeklyConversation, WeeklyMessage, WeeklyRun, WeeklyUnansweredFinding
from app.weekly.services.analysis_service import WeeklyAnalysisService
from app.weekly.services.storage import week_id_for, week_window
from app.utils.report_html import build_top_questions_insight, render_top_questions_grid
from app.weekly.services.report_service import WeeklyReportService


@pytest.fixture
def weekly_db():
    engine = create_engine("sqlite:///:memory:")
    WeeklyBase.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


def _seed_conversation(db, agent_id: str = "agent-1"):
    run = WeeklyRun(
        week_id=week_id_for(datetime.now(timezone.utc)),
        since=datetime.now(timezone.utc),
        until=datetime.now(timezone.utc),
        status="running",
    )
    db.add(run)
    db.flush()
    agent_run = WeeklyAgentRun(weekly_run_id=run.id, agent_id=agent_id, status="running")
    db.add(agent_run)
    db.flush()
    conv = WeeklyConversation(
        agent_run_id=agent_run.id,
        external_id="ext-1",
        agent_id=agent_id,
        title="Test",
    )
    db.add(conv)
    db.flush()
    db.add(
        WeeklyMessage(
            conversation_id=conv.id,
            role="member",
            from_agent=False,
            content="Waar is mijn ticket?",
        )
    )
    db.commit()
    return agent_run, conv


def test_tier1_no_reply_persisted(weekly_db):
    agent_run, conv = _seed_conversation(weekly_db)
    conv.messages = list(weekly_db.scalars(select(WeeklyMessage).where(WeeklyMessage.conversation_id == conv.id)).all())
    service = WeeklyAnalysisService(weekly_db)
    with patch.object(service.models, "cosine_similarity_pairs", return_value=[]), patch.object(
        service.models, "classify_zero_shot_batch", return_value=[]
    ), patch.object(service.models, "embed_texts", return_value=[[0.0], [0.0]]):
        totals = service.analyze(agent_run, [conv])
    assert totals["no_reply"] == 1
    assert totals["total"] == 1


def test_report_html_helpers():
    grid = render_top_questions_grid([("Waar is mijn ticket?", 3)])
    assert "Vraag #1" in grid
    assert build_top_questions_insight([("Test?", 2)]).startswith("De meest gestelde")


def test_weekly_analysis_does_not_use_main_db(weekly_db):
    agent_run, conv = _seed_conversation(weekly_db)
    conv.messages = list(weekly_db.scalars(select(WeeklyMessage).where(WeeklyMessage.conversation_id == conv.id)).all())
    with patch("app.database.SessionLocal") as mock_main:
        WeeklyAnalysisService(weekly_db).analyze(agent_run, [conv])
    mock_main.assert_not_called()


def test_week_window_defaults_to_seven_days(monkeypatch):
    from app.weekly.settings_store import WeeklyConfig

    monkeypatch.setattr(
        "app.weekly.services.storage.get_weekly_config",
        lambda: WeeklyConfig(cron="0 6 * * 1", days=7, enabled=True, agent_id=""),
    )
    since, until, week_id = week_window()
    assert (until - since).days == 7
    assert week_id.startswith("20")


def test_report_service_build_context(weekly_db, tmp_path, monkeypatch):
    agent_run, conv = _seed_conversation(weekly_db)
    agent_run.weekly_run = weekly_db.scalar(select(WeeklyRun))
    conv.messages = list(weekly_db.scalars(select(WeeklyMessage).where(WeeklyMessage.conversation_id == conv.id)).all())
    agent_run.conversations = [conv]
    monkeypatch.setattr(
        "app.weekly.services.report_service.fetch_momants_report_stats",
        lambda *a, **k: type("S", (), {
            "assisted_revenue": 1000,
            "direct_revenue": 200,
            "hours_saved": 5,
            "support_cost_saved": 300,
            "total_value_creation": 1300,
        })(),
    )
    monkeypatch.setattr(
        "app.weekly.services.report_service.TEMPLATE_PATH",
        tmp_path / "t.html",
    )
    (tmp_path / "t.html").write_text("<html>{{event_name}} {{top_questions_grid}}</html>", encoding="utf-8")
    ctx = WeeklyReportService(weekly_db).build_context(agent_run)
    assert ctx["variables"]["event_name"]
    assert "top_questions_grid" in ctx["fragments"]


def test_agent_counts_fallback_from_findings(weekly_db):
    from app.weekly.api.router import _agent_counts

    agent_run, conv = _seed_conversation(weekly_db)
    weekly_db.add(
        WeeklyUnansweredFinding(
            agent_run_id=agent_run.id,
            conversation_id=conv.id,
            message_id=weekly_db.scalar(select(WeeklyMessage.id)),
            question_text="Test?",
            status="no_reply",
        )
    )
    weekly_db.commit()
    agent_run.counts_json = None
    counts = _agent_counts(weekly_db, agent_run)
    assert counts["no_reply"] == 1
    assert counts["total"] == 1


def test_post_weekly_bundle_uploads(tmp_path):
    from app.integrations import slack_client

    zip_path = tmp_path / "bundle.zip"
    zip_path.write_bytes(b"zip")
    mock_notifier = MagicMock()
    with patch.object(slack_client, "_get_notifier", return_value=mock_notifier):
        ok = slack_client.post_weekly_unanswered_bundle(
            "C0BC172EV6C",
            zip_path,
            "2026-W25",
            {"agent_count": 2, "counts": {"total": 3, "no_reply": 1, "weak_answer": 1, "not_answered": 1}},
        )
    assert ok is True
    mock_notifier.upload_file.assert_called_once()


def test_run_all_scoped_to_single_agent(weekly_db, monkeypatch):
    from app.weekly.services.orchestrator import WeeklyOrchestrator
    from app.weekly.settings_store import WeeklyConfig

    mock_client = MagicMock()
    mock_client.list_agents.return_value = [{"id": "other-agent", "name": "Other"}]
    mock_client.get_agent.return_value = {"name": "Test Agent"}
    monkeypatch.setattr(
        "app.weekly.services.orchestrator.get_weekly_config",
        lambda: WeeklyConfig(cron="0 6 * * 1", days=7, enabled=True, agent_id="test-agent-123"),
    )

    orchestrator = WeeklyOrchestrator(weekly_db)
    orchestrator.client = mock_client
    orchestrator.run_agent = MagicMock()
    orchestrator.finalize_run = MagicMock(return_value=None)

    with patch("app.weekly.services.orchestrator.week_window", return_value=(datetime.now(timezone.utc), datetime.now(timezone.utc), "2026-W25")):
        orchestrator.run_all()

    mock_client.list_agents.assert_not_called()
    mock_client.get_agent.assert_called_once_with("test-agent-123")
    orchestrator.run_agent.assert_called_once()
    assert orchestrator.run_agent.call_args[0][1] == "test-agent-123"


def test_next_scheduled_run_utc():
    from app.weekly.settings_store import next_scheduled_run_utc

    nxt = next_scheduled_run_utc("0 6 * * 1")
    assert nxt.tzinfo is not None
    assert nxt.weekday() == 0
    assert nxt.hour == 6


def test_pipeline_run_agent_invokes_orchestrator(monkeypatch):
    from app.weekly import pipeline as weekly_pipeline

    mock_orchestrator = MagicMock()
    mock_run = MagicMock()
    mock_orchestrator.get_or_create_run.return_value = mock_run
    monkeypatch.setattr(weekly_pipeline, "WeeklyOrchestrator", lambda db: mock_orchestrator)
    monkeypatch.setattr(weekly_pipeline, "WeeklySessionLocal", lambda: MagicMock(__enter__=lambda s: s, __exit__=lambda *a: None, close=lambda: None))
    monkeypatch.setattr(
        "app.integrations.momants_client.get_momants_client",
        lambda: MagicMock(get_agent=lambda aid: {"name": "Test Agent"}),
    )
    assert weekly_pipeline.run_agent("agent-123") == 0
    mock_orchestrator.run_agent.assert_called_once()
    mock_orchestrator.finalize_run.assert_called_once()
