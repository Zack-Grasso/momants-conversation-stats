from __future__ import annotations

from datetime import datetime
from html import escape

from app.utils.report_format import format_short_date


def _plot_x(index: int, count: int, plot_left: float, plot_right: float) -> float:
    if count <= 1:
        return (plot_left + plot_right) / 2
    return plot_left + (index / (count - 1)) * (plot_right - plot_left)


def _plot_clip_open(
    parts: list[str],
    clip_id: str,
    plot_left: float,
    plot_right: float,
    plot_top: float,
    plot_bottom: float,
) -> None:
    width = plot_right - plot_left
    height = plot_bottom - plot_top
    parts.append(
        f'<defs><clipPath id="{clip_id}">'
        f'<rect x="{plot_left:.1f}" y="{plot_top:.1f}" width="{width:.1f}" height="{height:.1f}"/>'
        f"</clipPath></defs>"
    )
    parts.append(f'<g clip-path="url(#{clip_id})">')


def _plot_clip_close(parts: list[str]) -> None:
    parts.append("</g>")


def _x_label_x(index: int, count: int, plot_left: float, plot_right: float) -> float:
    return _plot_x(index, count, plot_left, plot_right)


def _select_x_label_indices(
    count: int,
    x_at,
    *,
    max_labels: int = 7,
    min_spacing: float = 50,
) -> list[int]:
    if count <= 0:
        return []
    if count == 1:
        return [0]

    step = max(1, (count - 1) // max(max_labels - 1, 1))
    indices = list(range(0, count, step))
    if indices[-1] != count - 1:
        indices.append(count - 1)

    filtered = [indices[0]]
    for idx in indices[1:]:
        if x_at(idx) - x_at(filtered[-1]) >= min_spacing:
            filtered.append(idx)

    if filtered[-1] != count - 1:
        if x_at(count - 1) - x_at(filtered[-1]) >= min_spacing:
            filtered.append(count - 1)
        elif len(filtered) >= 2 and x_at(count - 1) - x_at(filtered[-2]) >= min_spacing:
            filtered[-1] = count - 1

    return filtered


def _dedupe_ticks_by_y(ticks: list[int], max_val: float, plot_bottom: float, plot_h: float) -> list[int]:
    seen_y: set[int] = set()
    unique: list[int] = []
    for tick in ticks:
        y_key = round(plot_bottom - (tick / max_val) * plot_h)
        if y_key in seen_y:
            continue
        seen_y.add(y_key)
        unique.append(tick)
    return unique


def _x_tick_anchor(index: int, count: int) -> str:
    if count <= 1:
        return "middle"
    if index == 0:
        return "start"
    if index == count - 1:
        return "end"
    return "middle"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sqrt_y(value: float, max_val: float, plot_bottom: float, plot_h: float) -> float:
    """Map a value to plot Y using sqrt scaling so low/mid days stay visible beside spikes."""
    import math

    if max_val <= 0 or value <= 0:
        return plot_bottom
    return plot_bottom - (math.sqrt(value) / math.sqrt(max_val)) * plot_h


def _badge_rect_x(center_x: float, width: float, plot_left: float, plot_right: float) -> float:
    return _clamp(center_x - width / 2, plot_left, plot_right - width)


def _y_axis_label(x: float, y: float, text: str) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="Inter" font-size="11" fill="#bbb" '
        f'text-anchor="end">{escape(text)}</text>'
    )


def _plot_frame(
    parts: list[str],
    *,
    plot_left: float,
    plot_right: float,
    plot_top: float,
    plot_bottom: float,
) -> None:
    parts.append(
        f'<line x1="{plot_left:.1f}" y1="{plot_top:.1f}" x2="{plot_left:.1f}" y2="{plot_bottom:.1f}" '
        f'stroke="#e0e0dc" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{plot_left:.1f}" y1="{plot_bottom:.1f}" x2="{plot_right:.1f}" y2="{plot_bottom:.1f}" '
        f'stroke="#e0e0dc" stroke-width="1"/>'
    )


def _h_grid(parts: list[str], y: float, plot_left: float, plot_right: float) -> None:
    parts.append(
        f'<line x1="{plot_left:.1f}" y1="{y:.1f}" x2="{plot_right:.1f}" y2="{y:.1f}" '
        f'stroke="#efefed" stroke-width="1"/>'
    )


def _nice_ticks(max_value: float, count: int = 5) -> list[int]:
    if max_value <= 0:
        return [0]
    step = max(1, round(max_value / (count - 1)))
    ticks = [0]
    current = step
    while current < max_value:
        ticks.append(current)
        current += step
    ticks.append(int(max_value))
    return sorted(set(ticks))


def daily_volume_chart_svg(
    daily_counts: dict[datetime, int],
    peak_day: datetime | None = None,
    *,
    width: int = 1280,
    height: int = 340,
    gradient_id: str = "vg",
    clip_suffix: str = "volume",
    compact: bool = False,
) -> str:
    if compact:
        height = 240
    plot_left, plot_right = 56, width - 20
    if compact:
        plot_top, plot_bottom = 44, 210
    else:
        plot_top, plot_bottom = 52, max(300, height - 42)
    x_label_y = plot_bottom + 22
    plot_h = plot_bottom - plot_top
    clip_id = f"plot-clip-{clip_suffix}"

    if not daily_counts:
        return (
            f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" font-family="Inter" font-size="12" '
            f'fill="#bbb" text-anchor="middle">Geen gespreksdata</text>'
        )

    days = sorted(daily_counts.keys())
    values = [daily_counts[day] for day in days]
    max_val = max(values) or 1
    raw_ticks = _nice_ticks(max_val)
    seen_y: set[int] = set()
    ticks: list[int] = []
    for tick in raw_ticks:
        y_key = round(_sqrt_y(float(tick), float(max_val), plot_bottom, plot_h))
        if y_key in seen_y:
            continue
        seen_y.add(y_key)
        ticks.append(tick)

    parts: list[str] = []
    y_label_x = 44

    for tick in ticks:
        y = _sqrt_y(float(tick), float(max_val), plot_bottom, plot_h)
        _h_grid(parts, y, plot_left, plot_right)
        parts.append(_y_axis_label(y_label_x, y + 4, str(tick)))

    points: list[tuple[float, float]] = []
    for index, day in enumerate(days):
        x = _plot_x(index, len(days), plot_left, plot_right)
        y = _sqrt_y(float(daily_counts[day]), float(max_val), plot_bottom, plot_h)
        points.append((x, y))

    area_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_points += f" {points[-1][0]:.1f},{plot_bottom} {points[0][0]:.1f},{plot_bottom}"
    line_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    _plot_clip_open(parts, clip_id, plot_left, plot_right, plot_top, plot_bottom)
    parts.append(f'<polygon fill="url(#{gradient_id})" points="{area_points}"/>')
    _plot_clip_close(parts)

    parts.append(
        f'<polyline fill="none" stroke="#151515" stroke-width="2.2" stroke-linejoin="round" '
        f'stroke-linecap="round" points="{line_points}"/>'
    )

    if peak_day is None:
        peak_day = max(daily_counts, key=daily_counts.get)
    peak_index = days.index(peak_day) if peak_day in days else values.index(max(values))
    peak_x, peak_y = points[peak_index]
    parts.append(f'<circle cx="{peak_x:.1f}" cy="{peak_y:.1f}" r="5" fill="#151515"/>')

    peak_label = escape(format_short_date(peak_day))
    badge_w = 88
    badge_h = 17
    badge_x = _badge_rect_x(peak_x, badge_w, plot_left, plot_right)
    badge_gap = 10
    preferred_badge_y = peak_y - badge_h - badge_gap
    if preferred_badge_y >= 4:
        badge_y = preferred_badge_y
    else:
        badge_y = 4
        parts.append(
            f'<line x1="{peak_x:.1f}" y1="{badge_y + badge_h:.1f}" x2="{peak_x:.1f}" y2="{peak_y - 6:.1f}" '
            f'stroke="#151515" stroke-width="1.2" stroke-dasharray="3 2"/>'
        )
    parts.append(f'<rect x="{badge_x:.1f}" y="{badge_y:.1f}" width="{badge_w}" height="{badge_h}" rx="4" fill="#151515"/>')
    parts.append(
        f'<text x="{badge_x + badge_w / 2:.1f}" y="{badge_y + 11:.1f}" font-family="Inter" font-size="10.5" '
        f'fill="#E2F5C9" text-anchor="middle" font-weight="700">Piek: {peak_label}</text>'
    )

    def label_x(index: int) -> float:
        return _x_label_x(index, len(days), plot_left, plot_right)

    for index in _select_x_label_indices(len(days), label_x, max_labels=7, min_spacing=56):
        x = label_x(index)
        parts.append(
            f'<text x="{x:.1f}" y="{x_label_y}" font-family="Inter" font-size="11" fill="#bbb" '
            f'text-anchor="{_x_tick_anchor(index, len(days))}">{escape(format_short_date(days[index]))}</text>'
        )

    _plot_frame(parts, plot_left=plot_left, plot_right=plot_right, plot_top=plot_top, plot_bottom=plot_bottom)
    return "\n        ".join(parts)


def build_channel_volume_chart_html(
    channel: str,
    by_channel: dict[str, dict[datetime, int]],
    all_days: list[datetime],
    channel_counts: dict[str, int],
) -> str:
    from app.utils.report_data import CHANNEL_DISPLAY, align_daily_counts
    from app.utils.report_format import format_dutch_int

    cfg = CHANNEL_DISPLAY[channel]
    aligned = align_daily_counts(all_days, by_channel.get(channel, {}))
    peak_day = None
    if any(aligned.values()):
        peak_day, peak_count = max(aligned.items(), key=lambda item: item[1])
        if peak_count <= 0:
            peak_day = None
    total = channel_counts.get(channel, 0)
    svg_height = 380
    chart_inner = daily_volume_chart_svg(
        aligned,
        peak_day,
        width=1280,
        height=svg_height,
        gradient_id=f"vg-{channel}",
        clip_suffix=f"vol-{channel}",
        compact=False,
    )
    return (
        f'<div class="card volume-chart-card channel-volume-single">'
        f'<div class="channel-volume-head">'
        f'<span class="channel-volume-name">{escape(cfg["label"])}</span>'
        f'<span class="channel-volume-meta">{format_dutch_int(total)} gesprekken</span>'
        f"</div>"
        f'<svg viewBox="0 0 1280 {svg_height}" style="overflow:visible;width:100%;flex:1" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f"<defs>"
        f'<linearGradient id="vg-{channel}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="#E2F5C9" stop-opacity=".85"/>'
        f'<stop offset="100%" stop-color="#E2F5C9" stop-opacity="0"/>'
        f"</linearGradient></defs>"
        f"{chart_inner}"
        f"</svg></div>"
    )


def build_channel_volume_slides_html(
    by_channel: dict[str, dict[datetime, int]],
    all_days: list[datetime],
    channel_counts: dict[str, int],
    *,
    event_name: str,
    date_range: str,
    insight: str,
    page_start: int,
    total_pages: int,
) -> str:
    from app.utils.report_data import CHANNEL_DISPLAY, active_channels

    active = active_channels(channel_counts)
    if not all_days or not active:
        return ""

    slides: list[str] = []
    for index, channel in enumerate(active):
        page_num = page_start + index
        label = CHANNEL_DISPLAY[channel]["label"]
        chart = build_channel_volume_chart_html(channel, by_channel, all_days, channel_counts)
        insight_block = (
            f'<div class="insight channel-volume-insight">{escape(insight)}</div>' if index == 0 else ""
        )
        slides.append(
            f'<section class="slide slide-channel-volume">'
            f'<div class="topbar"><div class="logo"><span class="mom-logo-text">momants</span></div>'
            f'<div class="doc">{escape(event_name)} · Conversation Analysis</div></div>'
            f'<div class="eyebrow">Gespreksvolume · per kanaal</div>'
            f'<h1>{escape(label)}</h1>'
            f'<div class="body">{insight_block}{chart}</div>'
            f'<div class="footer"><span>{escape(event_name)} · Conversation Analysis {escape(date_range)} · Momants</span>'
            f"<span>{page_num} / {total_pages}</span></div>"
            f"</section>"
        )
    return "".join(slides)


def build_channel_volume_charts_html(
    by_channel: dict[str, dict[datetime, int]],
    all_days: list[datetime],
    channel_counts: dict[str, int],
) -> str:
    from app.utils.report_data import CHANNEL_DISPLAY, active_channels, align_daily_counts
    from app.utils.report_format import format_dutch_int

    active = active_channels(channel_counts)
    if not all_days:
        return (
            '<div class="channel-volume-grid cols-1">'
            '<div class="channel-volume-item">'
            '<p style="color:#bbb;font-size:13px">Geen gespreksdata per kanaal</p>'
            "</div></div>"
        )

    channel_total = len(active)
    grid_class = "cols-1" if channel_total == 1 else ("cols-2" if channel_total == 2 else "cols-3")
    compact = channel_total > 1
    svg_height = 240 if compact else 340
    items: list[str] = []

    for channel in active:
        cfg = CHANNEL_DISPLAY[channel]
        aligned = align_daily_counts(all_days, by_channel.get(channel, {}))
        peak_day = None
        if any(aligned.values()):
            peak_day, peak_count = max(aligned.items(), key=lambda item: item[1])
            if peak_count <= 0:
                peak_day = None
        total = channel_counts.get(channel, 0)
        chart_inner = daily_volume_chart_svg(
            aligned,
            peak_day,
            width=1280,
            height=svg_height,
            gradient_id=f"vg-{channel}",
            clip_suffix=f"vol-{channel}",
            compact=compact,
        )
        items.append(
            f'<div class="channel-volume-item">'
            f'<div class="channel-volume-head">'
            f'<span class="channel-volume-name">{escape(cfg["label"])}</span>'
            f'<span class="channel-volume-meta">{format_dutch_int(total)} gesprekken</span>'
            f"</div>"
            f'<svg viewBox="0 0 1280 {svg_height}" style="overflow:visible" xmlns="http://www.w3.org/2000/svg">'
            f"<defs>"
            f'<linearGradient id="vg-{channel}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="#E2F5C9" stop-opacity=".85"/>'
            f'<stop offset="100%" stop-color="#E2F5C9" stop-opacity="0"/>'
            f"</linearGradient></defs>"
            f"{chart_inner}"
            f"</svg></div>"
        )

    return f'<div class="channel-volume-grid {grid_class}">{"".join(items)}</div>'


def hourly_bars_chart_svg(
    hour_counts: dict[int, int],
    peak_hour: int | None = None,
    *,
    width: int = 384,
    height: int = 175,
) -> str:
    baseline = 155
    max_height = 150
    bar_width = 13
    step = 16

    if not hour_counts:
        return (
            f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" font-family="Inter" font-size="12" '
            f'fill="#bbb" text-anchor="middle">Geen uurdata</text>'
        )

    max_val = max(hour_counts.values()) or 1
    if peak_hour is None:
        peak_hour = max(hour_counts, key=hour_counts.get)

    parts: list[str] = []
    for hour in range(24):
        count = hour_counts.get(hour, 0)
        bar_h = (count / max_val) * max_height if count else 3
        x = hour * step
        y = baseline - bar_h
        if hour == peak_hour and count:
            fill = "#151515"
        elif count >= max_val * 0.6:
            fill = "#8abe6a"
        elif count >= max_val * 0.3:
            fill = "#b8d9a0"
        else:
            fill = "#dce8d4"
        parts.append(f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{bar_h:.1f}" rx="2" fill="{fill}"/>')
        if hour % 4 == 0:
            parts.append(
                f'<text x="{x + bar_width / 2:.1f}" y="170" font-family="Inter" font-size="9.5" fill="#bbb" '
                f'text-anchor="middle">{hour:02d}</text>'
            )

    if peak_hour is not None and hour_counts.get(peak_hour, 0):
        peak_x = peak_hour * step + bar_width / 2
        parts.append(
            f'<text x="{peak_x:.1f}" y="-3" font-family="Inter" font-size="9.5" fill="#151515" '
            f'text-anchor="middle" font-weight="700">piek</text>'
        )

    parts.append(f'<line x1="0" y1="{baseline}" x2="{width}" y2="{baseline}" stroke="#e8e8e4" stroke-width="1"/>')
    return "".join(parts)


def sentiment_arc_chart_svg(
    arc_points: list[float],
    start_stars: float | None,
    end_stars: float | None,
    *,
    width: int = 700,
    height: int = 210,
) -> str:
    y_label_x = 44
    plot_left, plot_right = 56, width - 20
    plot_top, plot_bottom = 16, 172
    x_label_y = 188
    plot_h = plot_bottom - plot_top
    count = len(arc_points) or 1

    def star_y(stars: float) -> float:
        clamped = min(5.0, max(1.0, stars))
        return plot_bottom - ((clamped - 1) / 4) * plot_h

    parts: list[str] = []

    for star in range(1, 6):
        y = star_y(float(star))
        _h_grid(parts, y, plot_left, plot_right)
        parts.append(_y_axis_label(y_label_x, y + 4, f"{star}★"))

    coords: list[tuple[float, float]] = []
    for index, stars in enumerate(arc_points):
        x = _plot_x(index, count, plot_left, plot_right)
        y = star_y(stars)
        coords.append((x, y))

    _plot_clip_open(parts, "plot-clip-sentiment", plot_left, plot_right, plot_top, plot_bottom)
    if coords:
        area = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        area += f" {coords[-1][0]:.1f},{plot_bottom} {coords[0][0]:.1f},{plot_bottom}"
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        parts.append(f'<polygon fill="url(#sg)" points="{area}"/>')
        parts.append(
            f'<polyline fill="none" stroke="#151515" stroke-width="2.2" stroke-linejoin="round" '
            f'stroke-linecap="round" points="{line}"/>'
        )
        for x, y in coords:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#151515"/>')
    _plot_clip_close(parts)

    label_indices = [0, min(2, count - 1), min(4, count - 1), min(6, count - 1), min(8, count - 1), count - 1]
    seen: set[int] = set()
    for index in label_indices:
        if index in seen or index >= count:
            continue
        seen.add(index)
        x = _plot_x(index, count, plot_left, plot_right)
        label = "bericht 1" if index == 0 else ("10" if index == count - 1 else str(index + 1))
        parts.append(
            f'<text x="{x:.1f}" y="{x_label_y}" font-family="Inter" font-size="11" fill="#bbb" '
            f'text-anchor="{_x_tick_anchor(index, count)}">{label}</text>'
        )

    _plot_frame(parts, plot_left=plot_left, plot_right=plot_right, plot_top=plot_top, plot_bottom=plot_bottom)
    return "\n        ".join(parts)


EMOTION_CHART_COLORS = ("#151515", "#8abe6a", "#f5c842", "#e88b8b", "#7eb8da", "#d4d4cf")


def _emotion_label(label: str, label_nl: dict[str, str]) -> str:
    return label_nl.get(label, label.replace("_", " "))


def emotion_timeline_chart_svg(
    timeline,
    label_nl: dict[str, str],
    *,
    width: int = 700,
    height: int = 230,
) -> str:
    y_label_x = 44
    plot_left, plot_right = 56, width - 20
    plot_top, plot_bottom = 16, 156
    x_label_y = 172
    plot_h = plot_bottom - plot_top

    if timeline is None or not timeline.points or not timeline.emotions:
        return (
            f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" font-family="Inter" font-size="12" '
            f'fill="#bbb" text-anchor="middle">Geen emotiedata</text>'
        )

    count = len(timeline.points)
    parts: list[str] = []

    for tick in (0, 25, 50, 75, 100):
        y = plot_bottom - (tick / 100) * plot_h
        _h_grid(parts, y, plot_left, plot_right)
        parts.append(_y_axis_label(y_label_x, y + 4, f"{tick}%"))

    line_emotions = [emotion for emotion in timeline.emotions if emotion != "overig"]
    _plot_clip_open(parts, "plot-clip-emotion", plot_left, plot_right, plot_top, plot_bottom)
    for emotion_index, emotion in enumerate(line_emotions):
        fill = EMOTION_CHART_COLORS[emotion_index % len(EMOTION_CHART_COLORS)]
        coords: list[tuple[float, float]] = []
        for index, point in enumerate(timeline.points):
            x = _plot_x(index, count, plot_left, plot_right)
            share_pct = point.get(emotion, 0.0) * 100
            y = plot_bottom - (share_pct / 100) * plot_h
            coords.append((x, y))

        if not coords:
            continue

        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        parts.append(
            f'<polyline fill="none" stroke="{fill}" stroke-width="2.2" stroke-linejoin="round" '
            f'stroke-linecap="round" points="{line}"/>'
        )
        for x, y in coords:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{fill}"/>')
    _plot_clip_close(parts)

    label_indices = [0, min(2, count - 1), min(4, count - 1), min(6, count - 1), min(8, count - 1), count - 1]
    seen: set[int] = set()
    for index in label_indices:
        if index in seen or index >= count:
            continue
        seen.add(index)
        x = _plot_x(index, count, plot_left, plot_right)
        label = "bericht 1" if index == 0 else ("10" if index == count - 1 else str(index + 1))
        parts.append(
            f'<text x="{x:.1f}" y="{x_label_y}" font-family="Inter" font-size="11" fill="#bbb" '
            f'text-anchor="{_x_tick_anchor(index, count)}">{label}</text>'
        )

    legend_x = plot_left
    legend_y = 188
    legend_row_gap = 16
    legend_item_gap = 14
    for emotion_index, emotion in enumerate(line_emotions):
        fill = EMOTION_CHART_COLORS[emotion_index % len(EMOTION_CHART_COLORS)]
        name = escape(_emotion_label(emotion, label_nl))
        item_w = 14 + len(name) * 5.6 + legend_item_gap
        if legend_x + item_w > plot_right and legend_x > plot_left:
            legend_x = plot_left
            legend_y += legend_row_gap
        parts.append(f'<rect x="{legend_x:.1f}" y="{legend_y:.1f}" width="10" height="10" rx="2" fill="{fill}"/>')
        parts.append(
            f'<text x="{legend_x + 14:.1f}" y="{legend_y + 9:.1f}" font-family="Inter" font-size="10.5" '
            f'fill="#666">{name}</text>'
        )
        legend_x += item_w

    _plot_frame(parts, plot_left=plot_left, plot_right=plot_right, plot_top=plot_top, plot_bottom=plot_bottom)
    return "\n        ".join(parts)


TIME_BUCKET_COLORS = {
    "kantooruren": "#0f766e",
    "na_kantooruren": "#c5e86c",
    "nacht": "#151515",
    "weekend": "#94a3b8",
}


def _pie_slice_path(cx: float, cy: float, radius: float, start_deg: float, end_deg: float) -> str:
    import math

    span = end_deg - start_deg
    if span <= 0:
        return ""
    if span >= 360:
        span = 359.99
        end_deg = start_deg + span

    start = math.radians(start_deg)
    end = math.radians(end_deg)
    x1 = cx + radius * math.cos(start)
    y1 = cy + radius * math.sin(start)
    x2 = cx + radius * math.cos(end)
    y2 = cy + radius * math.sin(end)
    large_arc = 1 if span > 180 else 0
    return (
        f"M {cx:.2f} {cy:.2f} L {x1:.2f} {y1:.2f} "
        f"A {radius} {radius} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z"
    )


def _render_pie_column(
    parts: list[str],
    buckets: dict[str, int],
    *,
    order: tuple[str, ...],
    labels: dict[str, str],
    colors: dict[str, str],
    cx: float,
    cy: float,
    radius: float,
    legend_title: str | None = None,
    legend_order: tuple[str, ...] | None = None,
    compact: bool = False,
) -> None:
    total = sum(buckets.get(key, 0) for key in order)
    if total <= 0:
        return

    title_y = cy - radius - (14 if compact else 18)
    if legend_title:
        parts.append(
            f'<text x="{cx:.1f}" y="{title_y:.1f}" font-family="Inter" font-size="{10 if compact else 11}" '
            f'fill="#888" font-weight="600" text-anchor="middle">{escape(legend_title)}</text>'
        )

    angle = -90.0
    for key in order:
        value = buckets.get(key, 0)
        if value <= 0:
            continue
        sweep = 360.0 * value / total
        path = _pie_slice_path(cx, cy, radius, angle, angle + sweep)
        if path:
            color = colors.get(key, "#cbd5e1")
            parts.append(f'<path d="{path}" fill="{color}" stroke="#fff" stroke-width="{1.5 if compact else 2}"/>')
        angle += sweep

    legend_y = cy + radius + (18 if compact else 22)
    legend_x = cx - (58 if compact else 68)
    row_h = 30.0 if compact else 36.0
    for key in legend_order or order:
        value = buckets.get(key, 0)
        if value <= 0:
            continue
        pct = round(100 * value / total, 1)
        color = colors.get(key, "#cbd5e1")
        name = escape(labels.get(key, key))
        parts.append(f'<rect x="{legend_x:.1f}" y="{legend_y:.1f}" width="10" height="10" rx="2" fill="{color}"/>')
        parts.append(
            f'<text x="{legend_x + 16:.1f}" y="{legend_y + 9:.1f}" font-family="Inter" '
            f'font-size="{10 if compact else 11}" fill="#444">{name}</text>'
        )
        count_label = f"{value:,}".replace(",", ".")
        parts.append(
            f'<text x="{legend_x + 16:.1f}" y="{legend_y + 21:.1f}" font-family="Inter" '
            f'font-size="{9 if compact else 10}" fill="#888">{pct:g}% · {count_label}</text>'
        )
        legend_y += row_h


def _render_pie_with_legend(
    parts: list[str],
    buckets: dict[str, int],
    *,
    order: tuple[str, ...],
    labels: dict[str, str],
    colors: dict[str, str],
    cx: float,
    cy: float,
    radius: float,
    legend_x: float,
    legend_y: float,
    legend_title: str | None = None,
    compact: bool = False,
) -> None:
    total = sum(buckets.get(key, 0) for key in order)
    if total <= 0:
        return

    if legend_title:
        parts.append(
            f'<text x="{legend_x:.1f}" y="{legend_y - 8:.1f}" font-family="Inter" font-size="{10 if compact else 11}" '
            f'fill="#888" font-weight="600">{escape(legend_title)}</text>'
        )

    angle = -90.0
    for key in order:
        value = buckets.get(key, 0)
        if value <= 0:
            continue
        sweep = 360.0 * value / total
        path = _pie_slice_path(cx, cy, radius, angle, angle + sweep)
        if path:
            color = colors.get(key, "#cbd5e1")
            parts.append(f'<path d="{path}" fill="{color}" stroke="#fff" stroke-width="{1.5 if compact else 2}"/>')
        angle += sweep

    row_h = 34.0 if compact else 44.0
    for key in order:
        value = buckets.get(key, 0)
        if value <= 0:
            continue
        pct = round(100 * value / total, 1)
        color = colors.get(key, "#cbd5e1")
        name = escape(labels.get(key, key))
        parts.append(f'<rect x="{legend_x:.1f}" y="{legend_y:.1f}" width="10" height="10" rx="2" fill="{color}"/>')
        parts.append(
            f'<text x="{legend_x + 16:.1f}" y="{legend_y + 9:.1f}" font-family="Inter" '
            f'font-size="{11 if compact else 12}" fill="#444">{name}</text>'
        )
        count_label = f"{value:,}".replace(",", ".")
        parts.append(
            f'<text x="{legend_x + 16:.1f}" y="{legend_y + 22:.1f}" font-family="Inter" '
            f'font-size="{10 if compact else 11}" fill="#888">{pct:g}% · {count_label}</text>'
        )
        legend_y += row_h


def office_hours_dual_pie_svg(
    buckets: dict[str, int],
    *,
    width: int = 700,
    height: int = 240,
    compact: bool = False,
) -> str:
    from app.utils.report_data import (
        OFFICE_MAIN_COLORS,
        OFFICE_MAIN_LABELS,
        OUTSIDE_BUCKET_ORDER,
        TIME_BUCKET_LABELS,
        TIME_BUCKET_ORDER,
        outside_office_buckets,
        summarize_office_hours,
    )

    total = sum(buckets.get(key, 0) for key in TIME_BUCKET_ORDER)
    if total <= 0:
        return (
            f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" font-family="Inter" font-size="13" fill="#999" '
            f'text-anchor="middle">Geen gespreksdata</text>'
        )

    main = summarize_office_hours(buckets)
    detail = outside_office_buckets(buckets)
    parts: list[str] = []

    if compact:
        main_cx, main_cy, main_r = width * 0.25, 82.0, 52.0
        detail_cx, detail_cy, detail_r = width * 0.75, 82.0, 40.0
    else:
        main_cx, main_cy, main_r = width * 0.25, 98.0, 78.0
        detail_cx, detail_cy, detail_r = width * 0.75, 98.0, 62.0

    main_pie_order = ("buiten_kantooruren", "kantooruren")
    main_legend_order = ("kantooruren", "buiten_kantooruren")

    _render_pie_column(
        parts,
        main,
        order=main_pie_order,
        labels=OFFICE_MAIN_LABELS,
        colors=OFFICE_MAIN_COLORS,
        cx=main_cx,
        cy=main_cy,
        radius=main_r,
        legend_title="Tijdens vs. buiten",
        legend_order=main_legend_order,
        compact=compact,
    )

    if detail and main.get("buiten_kantooruren", 0) > 0:
        x1 = main_cx + main_r + 6
        y1 = main_cy
        x2 = detail_cx - detail_r - 8
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y1:.1f}" '
            f'stroke="#bbb" stroke-width="1.2" stroke-dasharray="4 3"/>'
        )
        _render_pie_column(
            parts,
            detail,
            order=OUTSIDE_BUCKET_ORDER,
            labels=TIME_BUCKET_LABELS,
            colors=TIME_BUCKET_COLORS,
            cx=detail_cx,
            cy=detail_cy,
            radius=detail_r,
            legend_title="Buiten kantooruren",
            compact=compact,
        )
    elif not compact:
        parts.append(
            f'<text x="{detail_cx:.1f}" y="{detail_cy:.1f}" font-family="Inter" font-size="12" fill="#bbb" '
            f'text-anchor="middle">Geen buiten-kantooruren</text>'
        )

    return "\n        ".join(parts)


def build_office_hours_total_html(total_buckets: dict[str, int]) -> str:
    svg_height = 300
    svg = office_hours_dual_pie_svg(total_buckets, width=700, height=svg_height, compact=False)
    return (
        '<div class="office-hours-total card">'
        '<div class="office-hours-head">Totaal · alle kanalen</div>'
        f'<svg viewBox="0 0 700 {svg_height}" preserveAspectRatio="xMidYMid meet" style="width:100%" '
        'xmlns="http://www.w3.org/2000/svg">'
        f"{svg}"
        "</svg></div>"
    )


def build_office_hours_channel_slides_html(
    by_channel: dict[str, dict[str, int]],
    channel_counts: dict[str, int],
    *,
    event_name: str,
    date_range: str,
    page_start: int,
    total_pages: int,
) -> str:
    from app.utils.report_data import CHANNEL_DISPLAY, active_channels
    from app.utils.report_format import format_dutch_int

    active = active_channels(channel_counts)
    slides: list[str] = []
    for index, channel in enumerate(active):
        buckets = by_channel.get(channel, {})
        channel_total = channel_counts.get(channel, 0)
        if channel_total <= 0:
            continue
        page_num = page_start + index
        svg_height = 280
        svg = office_hours_dual_pie_svg(buckets, width=700, height=svg_height, compact=True)
        label = CHANNEL_DISPLAY[channel]["label"]
        slides.append(
            f'<section class="slide slide-bereikbaarheid-channels">'
            f'<div class="topbar"><div class="logo"><span class="mom-logo-text">momants</span></div>'
            f'<div class="doc">{escape(event_name)} · Conversation Analysis</div></div>'
            f'<div class="eyebrow">Bereikbaarheid</div>'
            f'<h1>{escape(label)} · wanneer?</h1>'
            f'<div class="body"><div class="card office-hours-channel-full">'
            f'<div class="office-hours-head">{escape(label)} · {format_dutch_int(channel_total)} gesprekken</div>'
            f'<svg viewBox="0 0 700 {svg_height}" preserveAspectRatio="xMidYMid meet" style="width:100%" '
            f'xmlns="http://www.w3.org/2000/svg">{svg}</svg>'
            f"</div></div>"
            f'<div class="footer"><span>{escape(event_name)} · Conversation Analysis {escape(date_range)} · Momants</span>'
            f"<span>{page_num} / {total_pages}</span></div>"
            f"</section>"
        )
    return "".join(slides)


def build_office_hours_page_html(
    total_buckets: dict[str, int],
    by_channel: dict[str, dict[str, int]],
    channel_counts: dict[str, int],
) -> str:
    return build_office_hours_total_html(total_buckets)


def office_hours_pie_chart_svg(
    buckets: dict[str, int],
    *,
    labels: dict[str, str] | None = None,
    order: tuple[str, ...] = ("kantooruren", "na_kantooruren", "nacht", "weekend"),
) -> str:
    from app.utils.report_data import TIME_BUCKET_LABELS

    label_map = labels or TIME_BUCKET_LABELS
    total = sum(buckets.get(key, 0) for key in order)
    if total <= 0:
        return (
            '<text x="240" y="170" font-family="Inter" font-size="14" fill="#999" '
            'text-anchor="middle">Geen gespreksdata beschikbaar</text>'
        )

    cx, cy, radius = 170.0, 165.0, 115.0
    angle = -90.0
    parts: list[str] = []

    for key in order:
        value = buckets.get(key, 0)
        if value <= 0:
            continue
        sweep = 360.0 * value / total
        path = _pie_slice_path(cx, cy, radius, angle, angle + sweep)
        if path:
            color = TIME_BUCKET_COLORS.get(key, "#cbd5e1")
            parts.append(f'<path d="{path}" fill="{color}" stroke="#fff" stroke-width="2"/>')
        angle += sweep

    legend_x = 320.0
    legend_y = 95.0
    for key in order:
        value = buckets.get(key, 0)
        if value <= 0:
            continue
        pct = round(100 * value / total, 1)
        color = TIME_BUCKET_COLORS.get(key, "#cbd5e1")
        name = escape(label_map.get(key, key))
        parts.append(f'<rect x="{legend_x:.1f}" y="{legend_y:.1f}" width="12" height="12" rx="2" fill="{color}"/>')
        parts.append(
            f'<text x="{legend_x + 18:.1f}" y="{legend_y + 10:.1f}" font-family="Inter" font-size="12" '
            f'fill="#444">{name}</text>'
        )
        count_label = f"{value:,}".replace(",", ".")
        parts.append(
            f'<text x="{legend_x + 18:.1f}" y="{legend_y + 26:.1f}" font-family="Inter" font-size="11" '
            f'fill="#888">{pct:g}% · {count_label}</text>'
        )
        legend_y += 44.0

    return "\n        ".join(parts)
