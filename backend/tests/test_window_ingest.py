from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.integrations.momants_client import MomantsClient


def test_window_stops_after_max_empty_windows():
    client = MomantsClient()
    calls = []

    def fake_collect(agent_id, limit, *, start_date=None, end_date=None):
        calls.append((start_date, end_date))
        return []

    with patch.object(client, "collect_inbox_entries", side_effect=fake_collect):
        result = client.collect_inbox_entries_by_window(
            "agent", max_empty_windows=4, window_days=7, hard_limit=1000
        )

    assert result == []
    assert len(calls) == 4  # stopped after 4 consecutive empty windows


def test_window_collects_and_dedupes_across_boundaries():
    client = MomantsClient()
    responses = [
        [{"conversation_id": "a"}, {"conversation_id": "b"}],
        [{"conversation_id": "b"}, {"conversation_id": "c"}],  # b overlaps the boundary
        [],
        [],
        [],
        [],
    ]
    it = iter(responses)

    def fake_collect(agent_id, limit, *, start_date=None, end_date=None):
        return next(it)

    with patch.object(client, "collect_inbox_entries", side_effect=fake_collect):
        result = client.collect_inbox_entries_by_window(
            "agent", max_empty_windows=4, window_days=7, hard_limit=1000
        )

    assert [entry["conversation_id"] for entry in result] == ["a", "b", "c"]


def test_window_stops_at_watermark_on_incremental_run():
    client = MomantsClient()
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    watermark = now - timedelta(days=10)
    calls = []

    def fake_collect(agent_id, limit, *, start_date=None, end_date=None):
        calls.append((start_date, end_date))
        return [{"conversation_id": f"c{len(calls)}"}]

    with patch.object(client, "collect_inbox_entries", side_effect=fake_collect):
        result = client.collect_inbox_entries_by_window(
            "agent", until_date=watermark, now=now, window_days=7, hard_limit=1000
        )

    # Window 1: [now-7, now]; window 2 clamps its start to the watermark and stops.
    assert len(calls) == 2
    assert calls[1][0] == watermark
    assert [entry["conversation_id"] for entry in result] == ["c1", "c2"]


def test_window_respects_hard_limit():
    client = MomantsClient()

    def fake_collect(agent_id, limit, *, start_date=None, end_date=None):
        return [{"conversation_id": f"x{i}"} for i in range(limit + 5)]

    with patch.object(client, "collect_inbox_entries", side_effect=fake_collect):
        result = client.collect_inbox_entries_by_window(
            "agent", max_empty_windows=4, window_days=7, hard_limit=3
        )

    assert len(result) == 3
