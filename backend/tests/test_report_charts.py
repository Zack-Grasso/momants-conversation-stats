from datetime import datetime, timezone
from pathlib import Path

from app.utils.report_charts import (
    build_channel_volume_charts_html,
    build_channel_volume_slides_html,
    build_office_hours_channel_slides_html,
    build_office_hours_page_html,
    daily_volume_chart_svg,
    emotion_timeline_chart_svg,
    hourly_bars_chart_svg,
    office_hours_dual_pie_svg,
    office_hours_pie_chart_svg,
    sentiment_arc_chart_svg,
)
from app.utils.report_data import EmotionTimeline, EMOTION_LABEL_NL

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "conversation-analysis-template-v2.html"


def test_template_contains_dynamic_chart_placeholders():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "{{chart_slide2_inner}}" in template
    assert "{{chart_slide3_inner}}" in template
    assert "{{channel_volume_slides}}" in template
    assert "{{channels_timing_intro}}" in template
    assert "{{channel_timing_stats}}" in template
    assert "{{office_hours_charts}}" in template
    assert "{{office_hours_channel_slides}}" in template
    assert "{{bereikbaarheid_insight}}" in template
    assert "{{channel_sentiment_cols}}" not in template
    assert "{{chart_slide4_inner}}" in template
    assert "WhatsApp is het dominante kanaal" not in template
    # Channels are rendered conditionally via fragments (Instagram hidden when empty).
    assert "{{channel_pills}}" in template
    assert "{{channel_breakdown_rows}}" in template
    # Intent / conversation-depth reporting has been removed.
    assert "{{intent_breakdown_html}}" not in template
    assert "Gespreksdiepte" not in template
    assert "{{stats_conversations_total}}" in template
    assert "{{stats_total_value_detail}}" in template
    assert "{{report_page_total}}" in template
    assert "{{page_num_total_volume}}" in template
    assert "/ 10" not in template
def test_select_x_label_indices_drops_crowded_end_labels():
    from app.utils.report_charts import _select_x_label_indices

    def x_at(index: int) -> float:
        return index * 10

    # Last two candidate labels would sit only 20px apart; keep the final day only.
    indices = _select_x_label_indices(40, x_at, max_labels=7, min_spacing=56)
    assert indices[-1] == 39
    assert len(indices) >= 2
    for left, right in zip(indices, indices[1:]):
        assert x_at(right) - x_at(left) >= 56 or right == 39


def test_daily_volume_chart_svg_renders_polyline_and_peak_label():
    day = datetime(2026, 3, 1, tzinfo=timezone.utc)
    svg = daily_volume_chart_svg({day: 12, day.replace(day=2): 20}, day)

    assert "<polyline" in svg
    assert "Piek:" in svg


def test_daily_volume_chart_svg_sqrt_scale_with_spike():
    from datetime import timedelta

    start = datetime(2026, 5, 8, tzinfo=timezone.utc)
    counts = {start + timedelta(days=i): (18 if i < 10 else 2650) for i in range(11)}
    peak = start + timedelta(days=10)
    svg = daily_volume_chart_svg(counts, peak)

    assert "<polyline" in svg
    assert "Piek:" in svg
    assert "<circle" in svg


def test_daily_volume_chart_svg_empty_state():
    svg = daily_volume_chart_svg({}, None)

    assert "Geen gespreksdata" in svg


def test_build_channel_volume_charts_html_renders_active_channels():
    day = datetime(2026, 5, 24, tzinfo=timezone.utc)
    day_key = day.replace(hour=0, minute=0, second=0, microsecond=0)
    html = build_channel_volume_charts_html(
        {"whatsapp": {day_key: 12}, "chat": {day_key: 4}},
        [day_key],
        {"whatsapp": 12, "chat": 4},
    )

    assert "WhatsApp" in html
    assert "Chat" in html
    assert "channel-volume-grid cols-2" in html
    assert "<polyline" in html


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
    assert 'clipPath id="plot-clip-sentiment"' in svg
    assert 'clip-path="url(#plot-clip-sentiment)"' in svg
    assert "<polyline" in svg
    assert "bericht 1" in svg


def test_emotion_timeline_chart_svg_renders_lines_and_legend():
    timeline = EmotionTimeline(
        emotions=("curiosity", "joy"),
        points=({"curiosity": 0.7, "joy": 0.3}, {"curiosity": 0.2, "joy": 0.8}),
    )
    svg = emotion_timeline_chart_svg(timeline, EMOTION_LABEL_NL)

    assert 'clipPath id="plot-clip-emotion"' in svg
    assert "<polyline" in svg
    assert "<circle" in svg
    assert "nieuwsgierigheid" in svg
    assert "vreugde" in svg


def test_emotion_timeline_chart_svg_empty_state():
    svg = emotion_timeline_chart_svg(None, EMOTION_LABEL_NL)

    assert "Geen emotiedata" in svg


def test_office_hours_dual_pie_svg_renders_main_and_zoom():
    svg = office_hours_dual_pie_svg(
        {"kantooruren": 40, "na_kantooruren": 30, "nacht": 20, "weekend": 10}
    )

    assert "Tijdens kantooruren" in svg
    assert "Buiten kantooruren" in svg
    assert "Avond (ma–vr 17–22)" in svg
    assert "Buiten kantooruren" in svg
    assert "Zoom:" not in svg


def test_build_office_hours_page_html_total_only():
    html = build_office_hours_page_html(
        {"kantooruren": 10, "na_kantooruren": 5, "nacht": 3, "weekend": 2},
        {
            "whatsapp": {"kantooruren": 8, "na_kantooruren": 4, "nacht": 2, "weekend": 1},
            "chat": {"kantooruren": 2, "na_kantooruren": 1, "nacht": 1, "weekend": 1},
        },
        {"whatsapp": 15, "chat": 5},
    )

    assert "Totaal · alle kanalen" in html
    assert "WhatsApp" not in html
    assert "Chat" not in html


def test_build_channel_volume_slides_html_one_page_per_channel():
    day = datetime(2026, 5, 24, tzinfo=timezone.utc)
    day_key = day.replace(hour=0, minute=0, second=0, microsecond=0)
    html = build_channel_volume_slides_html(
        {"whatsapp": {day_key: 12}, "chat": {day_key: 4}},
        [day_key],
        {"whatsapp": 12, "chat": 4},
        event_name="Test Event",
        date_range="8 mei – 16 jun",
        insight="Test insight",
        page_start=4,
        total_pages=13,
    )

    assert html.count("slide-channel-volume") == 2
    assert "4 / 13" in html
    assert "5 / 13" in html
    assert "WhatsApp" in html
    assert "Chat" in html


def test_build_office_hours_channel_slides_html_one_page_per_channel():
    html = build_office_hours_channel_slides_html(
        {
            "whatsapp": {"kantooruren": 8, "na_kantooruren": 4, "nacht": 2, "weekend": 1},
            "chat": {"kantooruren": 2, "na_kantooruren": 1, "nacht": 1, "weekend": 1},
        },
        {"whatsapp": 15, "chat": 5},
        event_name="Test Event",
        date_range="8 mei – 16 jun",
        page_start=8,
        total_pages=13,
    )

    assert html.count("slide-bereikbaarheid-channels") == 2
    assert "8 / 13" in html
    assert "9 / 13" in html
    assert "WhatsApp" in html
    assert "Chat" in html


def test_office_hours_pie_chart_svg_renders_segments():
    svg = office_hours_pie_chart_svg(
        {"kantooruren": 40, "na_kantooruren": 30, "nacht": 20, "weekend": 10}
    )

    assert "<path" in svg
    assert "Tijdens kantooruren" in svg
    assert "Avond (ma–vr 17–22)" in svg
    assert "Nacht (ma–vr 22–09)" in svg
    assert "Weekend" in svg
