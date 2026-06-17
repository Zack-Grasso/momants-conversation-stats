from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from app.utils.report_format import all_message_timestamps, format_date_range, format_report_num, resolve_event_name


def test_format_report_num_preserves_one_hundred():
    assert format_report_num(100, 0) == "100"
    assert format_report_num(100.0, 0) == "100"
    assert format_report_num(3.5, 1) == "3.5"


def test_format_date_range_uses_earliest_and_latest_message():
    start = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 8, 18, 0, tzinfo=timezone.utc)
    assert format_date_range([end, start, start.replace(day=15)]) == "1 mrt – 8 apr"


def test_format_date_range_single_day():
    day = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    assert format_date_range([day, day.replace(hour=20)]) == "15 mrt"


def test_all_message_timestamps_collects_every_message():
    first = datetime(2026, 3, 1, tzinfo=timezone.utc)
    second = datetime(2026, 3, 2, tzinfo=timezone.utc)
    conversation = SimpleNamespace(
        messages=[
            SimpleNamespace(source_created_at=first, created_at=first),
            SimpleNamespace(source_created_at=second, created_at=second),
        ]
    )
    assert all_message_timestamps([conversation]) == [first, second]


def test_resolve_event_name_prefers_manual_override():
    name, missing = resolve_event_name("agent-id", "Lowlands 2025")
    assert name == "Lowlands 2025"
    assert missing == []


def test_resolve_event_name_fetches_agent_name_from_momants():
    with patch("app.utils.report_format.get_momants_client") as mock_get_client:
        mock_get_client.return_value.get_agent.return_value = {"name": "Tomorrowland Agent"}
        name, missing = resolve_event_name("agent-id", None)

    assert name == "Tomorrowland Agent"
    assert missing == []
    mock_get_client.return_value.get_agent.assert_called_once_with("agent-id")


def test_resolve_event_name_falls_back_when_momants_lookup_fails():
    response = httpx.Response(404, request=MagicMock(), text="not found")
    with patch("app.utils.report_format.get_momants_client") as mock_get_client:
        mock_get_client.return_value.get_agent.side_effect = httpx.HTTPStatusError(
            "not found",
            request=MagicMock(),
            response=response,
        )
        name, missing = resolve_event_name("12345678-abcd", None)

    assert name == "Agent 12345678"
    assert missing == ["event_name"]


def test_sentiment_summary_resolves_dominant_mood_label():
    db = MagicMock()
    db.execute.return_value.all.return_value = [("positive", 3)]
    db.scalars.return_value.all.return_value = ['[{"label": "joy", "score": 0.9}]']
    service = ReportService(db)

    polarity, mood = service._sentiment_summary("agent-123")

    assert polarity == "positive"
    assert mood == "vreugde"
