from datetime import datetime, timezone
from pathlib import Path

from app.utils.report_charts import (
    daily_volume_chart_svg,
    emotion_timeline_chart_svg,
    hourly_bars_chart_svg,
    sentiment_arc_chart_svg,
)
from app.utils.report_data import EmotionTimeline, EMOTION_LABEL_NL

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "conversation-analysis-template-v2.html"


def test_template_contains_dynamic_chart_placeholders():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "{{chart_slide2_inner}}" in template
    assert "{{chart_slide3_inner}}" in template
    assert "{{chart_slide4_inner}}" in template
    assert "{{chart_emotion_timeline_inner}}" in template
    assert "WhatsApp is het dominante kanaal" not in template
    # Channels are rendered conditionally via fragments (Instagram hidden when empty).
    assert "{{channel_pills}}" in template
    assert "{{channel_breakdown_rows}}" in template
    # Intent / conversation-depth reporting has been removed.
    assert "{{intent_breakdown_html}}" not in template
    assert "Gespreksdiepte" not in template
def test_daily_volume_chart_svg_renders_polyline_and_peak_label():
    day = datetime(2026, 3, 1, tzinfo=timezone.utc)
    svg = daily_volume_chart_svg({day: 12, day.replace(day=2): 20}, day)

    assert "<polyline" in svg
    assert "Piek:" in svg


def test_daily_volume_chart_svg_empty_state():
    svg = daily_volume_chart_svg({}, None)

    assert "Geen gespreksdata" in svg


def test_hourly_bars_chart_svg_renders_bars_and_peak_marker():
    svg = hourly_bars_chart_svg({9: 2, 18: 8}, peak_hour=18)

    assert "<rect" in svg
    assert "piek" in svg


def test_hourly_bars_chart_svg_empty_state():
    svg = hourly_bars_chart_svg({}, None)

    assert "Geen uurdata" in svg


def test_sentiment_arc_chart_svg_renders_grid_and_endpoints():
    svg = sentiment_arc_chart_svg([3.4, 3.8, 4.1], 3.4, 4.1)

    assert "1★" in svg
    assert "3.4★" in svg
    assert "4.1★" in svg
    assert "<polyline" in svg


def test_emotion_timeline_chart_svg_renders_lines_and_legend():
    timeline = EmotionTimeline(
        emotions=("curiosity", "joy"),
        points=({"curiosity": 0.7, "joy": 0.3}, {"curiosity": 0.2, "joy": 0.8}),
    )
    svg = emotion_timeline_chart_svg(timeline, EMOTION_LABEL_NL)

    assert "<polyline" in svg
    assert "<circle" in svg
    assert "nieuwsgierigheid" in svg
    assert "vreugde" in svg


def test_emotion_timeline_chart_svg_empty_state():
    svg = emotion_timeline_chart_svg(None, EMOTION_LABEL_NL)

    assert "Geen emotiedata" in svg
