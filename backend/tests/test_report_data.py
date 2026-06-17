from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.utils.report_data import (
    ActionBodyContext,
    apply_momants_stats_fallback,
    aggregate_emotion_timeline,
    aggregate_sentiment_arc,
    build_action_bodies,
    build_emotion_timeline_insight,
    classify_conversation_time_bucket,
    conversation_time_buckets,
    daily_conversation_counts,
    daily_counts_from_momants_stats,
    dominant_channel,
    fetch_momants_report_stats,
    hourly_conversation_counts,
    hourly_counts_from_momants_stats,
    parse_momants_report_stats,
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


def test_aggregate_emotion_timeline_tracks_dominant_emotions_by_index():
    conversations = [
        SimpleNamespace(
            messages=[
                SimpleNamespace(
                    id=1,
                    from_agent=False,
                    source_created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                    created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                    sentiment=SimpleNamespace(emotions=[{"label": "curiosity", "score": 0.9}]),
                ),
                SimpleNamespace(
                    id=2,
                    from_agent=False,
                    source_created_at=datetime(2026, 3, 1, 1, tzinfo=timezone.utc),
                    created_at=datetime(2026, 3, 1, 1, tzinfo=timezone.utc),
                    sentiment=SimpleNamespace(emotions=[{"label": "joy", "score": 0.8}]),
                ),
            ],
            created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            integration_type="chat",
        ),
        SimpleNamespace(
            messages=[
                SimpleNamespace(
                    id=3,
                    from_agent=False,
                    source_created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                    created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                    sentiment=SimpleNamespace(emotions=[{"label": "curiosity", "score": 0.7}]),
                )
            ],
            created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            integration_type="chat",
        ),
    ]

    timeline = aggregate_emotion_timeline(conversations, max_index=2)

    assert timeline is not None
    assert timeline.points[0]["curiosity"] == 1.0
    assert timeline.points[1]["joy"] == 1.0
    assert "nieuwsgierigheid" in build_emotion_timeline_insight(timeline)


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


def test_parse_momants_report_stats():
    stats = {
        "conversations": {"total_current_period": 57202},
        "hours_saved": {"total_current_period": 1240},
        "support_cost_saved": {"total_current_period": 186000},
        "assisted_revenue": {"total_current_period": 557000},
        "direct_revenue": {"total_current_period": 42000},
        "conversations_office_vs_non_office": {
            "data": [
                {"name": "office_hours", "value": 22880},
                {"name": "non_office", "value": 34322},
            ]
        },
    }

    parsed = parse_momants_report_stats(stats)

    assert parsed.conversations_total == 57202
    assert parsed.hours_saved == 1240
    assert parsed.support_cost_saved == 186000
    assert parsed.assisted_revenue == 557000
    assert parsed.direct_revenue == 42000
    assert parsed.pct_outside_office == 60.0
    assert parsed.total_value_creation == 743000


def test_parse_momants_report_stats_conversations_from_summary():
    stats = {
        "conversations": {"summary": {"total": 1200}},
        "conversations_office_vs_non_office": {
            "data": [
                {"label": "Binnen kantooruren", "count": 300},
                {"label": "Buiten kantooruren", "count": 700},
            ]
        },
    }

    parsed = parse_momants_report_stats(stats)

    assert parsed.conversations_total == 1200
    assert parsed.pct_outside_office == 70.0


def test_fetch_momants_report_stats_fallback_on_api_error():
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 31, tzinfo=timezone.utc)

    with patch("app.utils.report_data.get_momants_client") as mock_get_client:
        mock_get_client.return_value.get_dashboard_stats.side_effect = RuntimeError("API down")
        result = fetch_momants_report_stats("agent-id", start, end)

    assert result.conversations_total is None
    assert result.hours_saved is None
    assert result.pct_outside_office is None
    assert result.total_value_creation is None


def test_classify_conversation_time_bucket():
    office = datetime(2026, 3, 3, 10, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 3, 3, 19, 0, tzinfo=timezone.utc)
    night = datetime(2026, 3, 3, 23, 0, tzinfo=timezone.utc)
    weekend = datetime(2026, 3, 7, 14, 0, tzinfo=timezone.utc)

    assert classify_conversation_time_bucket(office) == "kantooruren"
    assert classify_conversation_time_bucket(evening) == "na_kantooruren"
    assert classify_conversation_time_bucket(night) == "nacht"
    assert classify_conversation_time_bucket(weekend) == "weekend"


def test_conversation_time_buckets_groups_conversations():
    day = datetime(2026, 3, 3, 10, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 3, 3, 19, 0, tzinfo=timezone.utc)

    conversations = [
        SimpleNamespace(
            created_at=day,
            messages=[SimpleNamespace(source_created_at=day, created_at=day)],
        ),
        SimpleNamespace(
            created_at=evening,
            messages=[SimpleNamespace(source_created_at=evening, created_at=evening)],
        ),
    ]

    buckets = conversation_time_buckets(conversations)

    assert buckets["kantooruren"] == 1
    assert buckets["na_kantooruren"] == 1
