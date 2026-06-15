from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.utils.report_data import (
    ActionBodyContext,
    apply_momants_stats_fallback,
    aggregate_sentiment_arc,
    build_action_bodies,
    daily_conversation_counts,
    daily_counts_from_momants_stats,
    dominant_channel,
    hourly_conversation_counts,
    hourly_counts_from_momants_stats,
    peak_hour_range,
    render_intent_breakdown_html,
)
from app.utils.report_format import format_report_num


def test_format_report_num_preserves_one_hundred():
    assert format_report_num(100, 0) == "100"


def test_apply_momants_stats_fallback_uses_momants_when_local_empty():
    day = datetime(2026, 3, 1, tzinfo=timezone.utc)

    with patch("app.utils.report_data.get_momants_client") as mock_get_client:
        client = mock_get_client.return_value
        client.get_dashboard_stats.side_effect = [
            {"conversations": {"data": [{"timestamp": day.isoformat(), "value": 7}]}},
            {"conversation_heatmap": {"data": [{"timestamp": day.replace(hour=10).isoformat(), "value": 2}]}},
        ]
        result_daily, result_hourly, source = apply_momants_stats_fallback(
            "agent-id",
            [day, day.replace(hour=10)],
            {},
            {},
            "local",
        )

    assert source == "momants"
    assert sum(result_daily.values()) == 7
    assert result_hourly[10] == 2


def test_daily_conversation_counts_groups_by_day():
    day_one = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    day_two = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)
    conversations = [
        SimpleNamespace(
            messages=[SimpleNamespace(source_created_at=day_one, created_at=day_one)],
            created_at=day_one,
            integration_type="chat",
        ),
        SimpleNamespace(
            messages=[SimpleNamespace(source_created_at=day_two, created_at=day_two)],
            created_at=day_two,
            integration_type="chat",
        ),
        SimpleNamespace(
            messages=[SimpleNamespace(source_created_at=day_one.replace(hour=15), created_at=day_one)],
            created_at=day_one,
            integration_type="chat",
        ),
    ]

    counts = daily_conversation_counts(conversations)

    assert len(counts) == 2
    assert sum(counts.values()) == 3


def test_hourly_conversation_counts_tracks_peak_hour():
    morning = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)
    conversations = [
        SimpleNamespace(messages=[SimpleNamespace(source_created_at=morning, created_at=morning)], created_at=morning, integration_type="chat"),
        SimpleNamespace(messages=[SimpleNamespace(source_created_at=evening, created_at=evening)], created_at=evening, integration_type="chat"),
        SimpleNamespace(messages=[SimpleNamespace(source_created_at=evening.replace(minute=30), created_at=evening)], created_at=evening, integration_type="chat"),
    ]

    counts = hourly_conversation_counts(conversations)

    assert counts[18] == 2
    assert counts[9] == 1


def test_aggregate_sentiment_arc_averages_timeline_points():
    metrics = [
        SimpleNamespace(timeline_json='[{"index": 0, "stars": 3}, {"index": 1, "stars": 5}]'),
        SimpleNamespace(timeline_json='[{"index": 0, "stars": 5}, {"index": 1, "stars": 3}]'),
    ]

    arc = aggregate_sentiment_arc(metrics, max_index=2)

    assert arc == [4.0, 4.0]


def test_render_intent_breakdown_html_includes_label_and_pct():
    html = render_intent_breakdown_html({"order_status": 8, "refund": 2})

    assert "Refund" in html or "Terugbetaling" in html or "order" in html.lower()
    assert "%" in html


def test_build_action_bodies_uses_cluster_and_peak_data():
    bodies = build_action_bodies(
        ActionBodyContext(
            cluster_1_count="12",
            cluster_1_text="Waar is mijn ticket?",
            pct_takeover="8",
            conversations_takeover="16",
            peak_hour="18:00",
            peak_hour_range="18:00–19:00",
            peak_hour_avg="4.2",
            lowest_sentiment_channel="Chat",
            lowest_sentiment_score="3.1",
        )
    )

    assert "12" in bodies["action_cluster_body"]
    assert "Waar is mijn ticket?" in bodies["action_cluster_body"]
    assert "18:00" in bodies["action_peak_body"]
    assert "Chat" in bodies["action_channel_body"]
    assert "16" in bodies["action_takeover_body"]


def test_peak_hour_range_formats_next_hour():
    assert peak_hour_range(18) == "18:00–19:00"


def test_dominant_channel_prefers_highest_count():
    assert dominant_channel({"whatsapp": 3, "chat": 10}) == "Chat"


def test_daily_counts_from_momants_stats_parses_conversations_series():
    stats = {
        "conversations": {
            "data": [
                {"timestamp": "2026-03-01T00:00:00+00:00", "value": 4},
                {"timestamp": "2026-03-02T00:00:00+00:00", "value": 6},
            ]
        }
    }

    counts = daily_counts_from_momants_stats(stats)

    assert len(counts) == 2
    assert sum(counts.values()) == 10


def test_hourly_counts_from_momants_stats_parses_heatmap_series():
    stats = {
        "conversation_heatmap": {
            "data": [
                {"timestamp": "2026-03-01T18:00:00+00:00", "value": 5},
                {"timestamp": "2026-03-01T09:00:00+00:00", "value": 2},
            ]
        }
    }

    counts = hourly_counts_from_momants_stats(stats)

    assert counts[18] == 5
    assert counts[9] == 2
