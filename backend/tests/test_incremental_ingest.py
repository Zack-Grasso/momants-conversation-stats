from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.models.conversation import Conversation
from app.models.ingestion_job import IngestionJob
from app.services.ingestion_service import IngestionService, _parse_datetime
from app.utils.datetime_utils import format_momants_datetime


def test_format_momants_datetime_uses_z_suffix():
    value = datetime(2026, 6, 12, 14, 30, 0, tzinfo=timezone.utc)
    assert format_momants_datetime(value) == "2026-06-12T14:30:00Z"


def test_should_skip_fetch_when_last_seen_unchanged():
    job = IngestionJob(agent_id="agent", limit=10, reanalyze=False)
    conversation = Conversation(
        external_id="abc",
        agent_id="agent",
        title="Test",
        source_last_seen=_parse_datetime("2026-06-12T10:00:00Z"),
    )
    entry = {"conversation_id": "abc", "last_seen": "2026-06-12T10:00:00Z"}

    assert IngestionService._should_skip_fetch(job, entry, conversation) is True


def test_should_fetch_when_last_seen_is_newer():
    job = IngestionJob(agent_id="agent", limit=10, reanalyze=False)
    conversation = Conversation(
        external_id="abc",
        agent_id="agent",
        title="Test",
        source_last_seen=_parse_datetime("2026-06-06T10:00:00Z"),
    )
    entry = {"conversation_id": "abc", "last_seen": "2026-06-12T10:00:00Z"}

    assert IngestionService._should_skip_fetch(job, entry, conversation) is False


def test_collect_inbox_entries_passes_start_date():
    from app.integrations.momants_client import MomantsClient

    client = MomantsClient()
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)

    with patch.object(client, "list_inbox_page") as mock_page:
        mock_page.return_value = {
            "inbox_entries": [{"conversation_id": "1"}],
            "total_pages": 1,
        }
        entries = client.collect_inbox_entries("agent", 10, start_date=start)

    assert len(entries) == 1
    mock_page.assert_called_once_with("agent", page=1, start_date=start, end_date=None)


def test_agent_ingest_state_watermark():
    from app.services.agent_ingest_state_service import AgentIngestStateService

    db = MagicMock()
    state = MagicMock()
    state.last_sync_completed_at = None
    db.get.return_value = None
    db.add = MagicMock()

    service = AgentIngestStateService(db)

    def flush_side_effect():
        state.agent_id = "agent"
        return None

    db.flush.side_effect = flush_side_effect
    service.get_or_create("agent")
    assert db.add.called
