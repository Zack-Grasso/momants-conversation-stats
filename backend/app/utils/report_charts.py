from __future__ import annotations

from datetime import datetime
from html import escape

from app.utils.report_format import format_short_date
from app.utils.report_html import momants_logo_html


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


def _series_points(
    days: list[datetime],
    values: list[int],
    *,
    plot_left: float,
    plot_right: float,
    plot_bottom: float,
    plot_h: float,
    max_val: float,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        x = _plot_x(index, len(days), plot_left, plot_right)
        y = _sqrt_y(float(value), max_val, plot_bottom, plot_h)
        points.append((x, y))
    return points


def _render_volume_series(
    parts: list[str],
    *,
    days: list[datetime],
    values: list[int],
    plot_left: float,
    plot_right: float,
    plot_top: float,
    plot_bottom: float,
    plot_h: float,
    max_val: float,
    gradient_id: str,
    clip_id: str,
    stroke: str,
    show_peak: bool = True,
    peak_badge_offset: float = 0,
) -> None:
    from app.utils.report_format import format_dutch_int

    points = _series_points(
        days,
        values,
        plot_left=plot_left,
        plot_right=plot_right,
        plot_bottom=plot_bottom,
        plot_h=plot_h,
        max_val=max_val,
    )
    if not points:
        return

    area_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_points += f" {points[-1][0]:.1f},{plot_bottom} {points[0][0]:.1f},{plot_bottom}"
    line_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    _plot_clip_open(parts, clip_id, plot_left, plot_right, plot_top, plot_bottom)
    parts.append(f'<polygon fill="url(#{gradient_id})" points="{area_points}"/>')
    _plot_clip_close(parts)
    parts.append(
        f'<polyline fill="none" stroke="{stroke}" stroke-width="2.4" stroke-linejoin="round" '
        f'stroke-linecap="round" points="{line_points}"/>'
    )

    if not show_peak:
        return

    peak_index = max(range(len(values)), key=lambda index: values[index])
    if values[peak_index] <= 0:
        return
    peak_x, peak_y = points[peak_index]
    peak_count = values[peak_index]
    peak_day = days[peak_index]
    parts.append(f'<circle cx="{peak_x:.1f}" cy="{peak_y:.1f}" r="4.5" fill="{stroke}" stroke="#fff" stroke-width="1.5"/>')

    peak_label = escape(format_short_date(peak_day))
    badge_text = f"Piek: {peak_label}"
    badge_w = max(88, len(badge_text) * 6.2)
    badge_h = 17
    badge_x = _badge_rect_x(peak_x, badge_w, plot_left, plot_right)
    badge_gap = 10 + peak_badge_offset
    min_badge_y = 4.0
    preferred_badge_y = peak_y - badge_h - badge_gap
    if preferred_badge_y >= min_badge_y:
        badge_y = preferred_badge_y
    else:
        badge_y = min_badge_y
        parts.append(
            f'<line x1="{peak_x:.1f}" y1="{badge_y + badge_h:.1f}" x2="{peak_x:.1f}" y2="{peak_y - 6:.1f}" '
            f'stroke="#151515" stroke-width="1.2" stroke-dasharray="3 2"/>'
        )
    parts.append(f'<rect x="{badge_x:.1f}" y="{badge_y:.1f}" width="{badge_w}" height="{badge_h}" rx="4" fill="#151515"/>')
    parts.append(
        f'<text x="{badge_x + badge_w / 2:.1f}" y="{badge_y + 11:.1f}" font-family="Inter" font-size="10.5" '
        f'fill="#E2F5C9" text-anchor="middle" font-weight="700">{badge_text}</text>'
    )


def multi_channel_daily_volume_chart_svg(
    series: list[tuple[str, dict[datetime, int]]],
    *,
    days: list[datetime],
    width: int = 1280,
    height: int = 380,
) -> str:
    from app.utils.report_data import CHANNEL_DISPLAY, align_daily_counts

    if not series or not days:
        return (
            f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" font-family="Inter" font-size="12" '
            f'fill="#bbb" text-anchor="middle">Geen gespreksdata</text>'
        )

    aligned_series: list[tuple[str, list[int]]] = []
    for channel, counts in series:
        aligned = align_daily_counts(days, counts)
        aligned_series.append((channel, [aligned[day] for day in days]))

    max_val = float(max(max(values) for _, values in aligned_series if values) or 1)
    plot_left, plot_right = 56, width - 16
    plot_top, plot_bottom = 34, height - 30
    x_label_y = plot_bottom + 18
    plot_h = plot_bottom - plot_top
    clip_id = "plot-clip-volume-combined"

    raw_ticks = _nice_ticks(int(max_val))
    seen_y: set[int] = set()
    ticks: list[int] = []
    for tick in raw_ticks:
        y_key = round(_sqrt_y(float(tick), max_val, plot_bottom, plot_h))
        if y_key in seen_y:
            continue
        seen_y.add(y_key)
        ticks.append(tick)

    parts: list[str] = []
    y_label_x = 44
    for tick in ticks:
        y = _sqrt_y(float(tick), max_val, plot_bottom, plot_h)
        _h_grid(parts, y, plot_left, plot_right)
        parts.append(_y_axis_label(y_label_x, y + 4, str(tick)))

    draw_order = sorted(
        aligned_series,
        key=lambda item: max(item[1]) if item[1] else 0,
    )
    for index, (channel, values) in enumerate(draw_order):
        cfg = CHANNEL_DISPLAY[channel]
        _render_volume_series(
            parts,
            days=days,
            values=values,
            plot_left=plot_left,
            plot_right=plot_right,
            plot_top=plot_top,
            plot_bottom=plot_bottom,
            plot_h=plot_h,
            max_val=max_val,
            gradient_id=f"vg-combined-{channel}",
            clip_id=f"{clip_id}-{index}",
            stroke=cfg["chart_stroke"],
            peak_badge_offset=index * 22,
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


def build_combined_channel_volume_chart_html(
    by_channel: dict[str, dict[datetime, int]],
    all_days: list[datetime],
    channel_counts: dict[str, int],
) -> str:
    from app.utils.report_data import CHANNEL_DISPLAY, active_channels
    from app.utils.report_format import format_dutch_int

    active = active_channels(channel_counts)
    if not all_days or not active:
        return (
            '<div class="card volume-chart-card channel-volume-combined">'
            '<p style="color:#bbb;font-size:13px;margin:0">Geen gespreksdata per kanaal</p>'
            "</div>"
        )

    series = [(channel, by_channel.get(channel, {})) for channel in active]
    svg_height = 420
    chart_inner = multi_channel_daily_volume_chart_svg(
        series, days=all_days, width=1280, height=svg_height
    )

    legend_items: list[str] = []
    for channel in active:
        cfg = CHANNEL_DISPLAY[channel]
        total = channel_counts.get(channel, 0)
        legend_items.append(
            f'<div class="channel-volume-legend-item">'
            f'<span class="channel-volume-swatch" style="background:{cfg["chart_stroke"]}"></span>'
            f'<span class="channel-volume-legend-label">{escape(cfg["label"])}</span>'
            f'<span class="channel-volume-legend-meta">{format_dutch_int(total)} gesprekken</span>'
            f"</div>"
        )

    gradient_defs = "".join(
        f'<linearGradient id="vg-combined-{channel}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{CHANNEL_DISPLAY[channel]["chart_fill"]}" stop-opacity=".75"/>'
        f'<stop offset="100%" stop-color="{CHANNEL_DISPLAY[channel]["chart_fill"]}" stop-opacity="0"/>'
        f"</linearGradient>"
        for channel in active
    )

    return (
        f'<div class="card volume-chart-card channel-volume-combined">'
        f'<div class="channel-volume-legend">{"".join(legend_items)}</div>'
        f'<svg viewBox="0 0 1280 {svg_height}" preserveAspectRatio="xMidYMid meet" '
        f'style="overflow:visible;width:100%;flex:1;min-height:0;display:block" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f"<defs>{gradient_defs}</defs>"
        f"{chart_inner}"
        f"</svg></div>"
    )


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
    from app.utils.report_data import active_channels

    active = active_channels(channel_counts)
    if not all_days or not active:
        return ""

    chart = build_combined_channel_volume_chart_html(by_channel, all_days, channel_counts)
    insight_block = f'<div class="insight channel-volume-insight">{escape(insight)}</div>'
    return (
        f'<section class="slide slide-channel-volume">'
        f'<div class="topbar"><div class="logo">{momants_logo_html()}</div>'
        f'<div class="doc">{escape(event_name)} · Conversation Analysis</div></div>'
        f'<div class="eyebrow">Gespreksvolume · per kanaal</div>'
        f"<h1>Volume per kanaal</h1>"
        f'<div class="body">{insight_block}{chart}</div>'
        f'<div class="footer"><span>{escape(event_name)} · Conversation Analysis {escape(date_range)} · Momants</span>'
        f"<span>{page_start} / {total_pages}</span></div>"
        f"</section>"
    )


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
    height: int = 280,
) -> str:
    top_pad = 24
    baseline = height - 26
    max_height = baseline - top_pad - 6
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
                f'<text x="{x + bar_width / 2:.1f}" y="{height - 8:.1f}" font-family="Inter" font-size="9.5" fill="#bbb" '
                f'text-anchor="middle">{hour:02d}</text>'
            )

    if peak_hour is not None and hour_counts.get(peak_hour, 0):
        peak_x = peak_hour * step + bar_width / 2
        badge_w = 52
        badge_h = 16
        badge_x = peak_x - badge_w / 2
        parts.append(
            f'<rect x="{badge_x:.1f}" y="4" width="{badge_w}" height="{badge_h}" rx="4" fill="#151515"/>'
        )
        parts.append(
            f'<text x="{peak_x:.1f}" y="15" font-family="Inter" font-size="9.5" fill="#E2F5C9" '
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

TIME_BUCKET_SHORT = {
    "kantooruren": "Kantoor",
    "na_kantooruren": "Avond",
    "nacht": "Nacht",
    "weekend": "Weekend",
}

OFFICE_HOURS_COMPARE_WIDTH = 1280


def _format_chart_count(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _segment_text_fill(key: str) -> str:
    return "#fff" if key in {"kantooruren", "nacht"} else "#151515"


def _render_stacked_bar(
    parts: list[str],
    buckets: dict[str, int],
    *,
    order: tuple[str, ...],
    colors: dict[str, str],
    short_labels: dict[str, str],
    x: float,
    y: float,
    width: float,
    height: float,
    clip_id: str,
    min_label_pct: float = 7.5,
    font_scale: float = 1.0,
) -> None:
    total = sum(buckets.get(key, 0) for key in order)
    if total <= 0 or width <= 0:
        return

    parts.append(
        f'<defs><clipPath id="{clip_id}">'
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" rx="10"/>'
        f"</clipPath></defs>"
    )
    parts.append(f'<g clip-path="url(#{clip_id})">')

    cursor = x
    active = [(key, buckets.get(key, 0)) for key in order if buckets.get(key, 0) > 0]
    for key, value in active:
        seg_w = width * value / total
        if seg_w <= 0:
            continue
        color = colors.get(key, "#cbd5e1")
        parts.append(
            f'<rect x="{cursor:.1f}" y="{y:.1f}" width="{seg_w:.1f}" height="{height:.1f}" fill="{color}"/>'
        )
        pct = round(100 * value / total, 1)
        if pct >= min_label_pct and seg_w >= 42:
            cx = cursor + seg_w / 2
            cy = y + height / 2
            fill = _segment_text_fill(key)
            label = escape(short_labels.get(key, key))
            pct_size = 13 * font_scale
            label_size = 11 * font_scale
            parts.append(
                f'<text x="{cx:.1f}" y="{cy - 6:.1f}" font-family="Inter" font-size="{pct_size:.1f}" '
                f'fill="{fill}" text-anchor="middle" font-weight="800">{pct:g}%</text>'
            )
            parts.append(
                f'<text x="{cx:.1f}" y="{cy + 10:.1f}" font-family="Inter" font-size="{label_size:.1f}" '
                f'fill="{fill}" text-anchor="middle" font-weight="600">{label}</text>'
            )
        cursor += seg_w

    parts.append("</g>")
    parts.append(
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" rx="10" '
        f'fill="none" stroke="#e5e5e0" stroke-width="1"/>'
    )


def office_hours_timing_chart_svg(
    buckets: dict[str, int],
    *,
    width: int = 700,
    height: int = 220,
    compact: bool = False,
) -> str:
    from app.utils.report_data import (
        OFFICE_MAIN_COLORS,
        TIME_BUCKET_LABELS,
        TIME_BUCKET_ORDER,
        pct_outside_office,
        summarize_office_hours,
    )

    total = sum(buckets.get(key, 0) for key in TIME_BUCKET_ORDER)
    if total <= 0:
        return (
            f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" font-family="Inter" font-size="13" fill="#999" '
            f'text-anchor="middle">Geen gespreksdata</text>'
        )

    summary = summarize_office_hours(buckets)
    office_count = summary.get("kantooruren", 0)
    outside_count = summary.get("buiten_kantooruren", 0)
    office_pct = round(100 * office_count / total, 1)
    outside_pct = pct_outside_office(buckets) or round(100 * outside_count / total, 1)

    bar_x = 24.0
    bar_w = float(width) - 48.0
    main_bar_h = 44.0 if compact else 58.0
    main_bar_y = 72.0 if compact else 88.0
    split_bar_h = 28.0 if compact else 34.0
    split_bar_y = main_bar_y + main_bar_h + 28.0

    parts: list[str] = []
    parts.append(
        f'<text x="{bar_x:.1f}" y="34" font-family="Inter" font-size="12" fill="#888" font-weight="700">'
        f"Alle tijdvakken · 100%</text>"
    )

    _render_stacked_bar(
        parts,
        buckets,
        order=TIME_BUCKET_ORDER,
        colors=TIME_BUCKET_COLORS,
        short_labels=TIME_BUCKET_SHORT,
        x=bar_x,
        y=main_bar_y,
        width=bar_w,
        height=main_bar_h,
        clip_id="oh-main-bar",
    )

    parts.append(
        f'<text x="{bar_x:.1f}" y="{main_bar_y - 10:.1f}" font-family="Inter" font-size="11" '
        f'fill="#666" font-weight="600">Volledige verdeling</text>'
    )

    # Grouped split bar: office vs outside (same width, two segments)
    _render_stacked_bar(
        parts,
        {"kantooruren": office_count, "buiten_kantooruren": outside_count},
        order=("kantooruren", "buiten_kantooruren"),
        colors=OFFICE_MAIN_COLORS,
        short_labels={"kantooruren": "Tijdens", "buiten_kantooruren": "Buiten"},
        x=bar_x,
        y=split_bar_y,
        width=bar_w,
        height=split_bar_h,
        clip_id="oh-split-bar",
        min_label_pct=12.0,
    )
    parts.append(
        f'<text x="{bar_x:.1f}" y="{split_bar_y - 10:.1f}" font-family="Inter" font-size="11" '
        f'fill="#666" font-weight="600">Tijdens vs. buiten kantoor uren</text>'
    )

    callout_y = split_bar_y + split_bar_h + 28.0
    parts.append(
        f'<text x="{bar_x:.1f}" y="{callout_y:.1f}" font-family="Inter" font-size="12.5" fill="#444">'
        f'<tspan font-weight="700" fill="{OFFICE_MAIN_COLORS["kantooruren"]}">{office_pct:g}%</tspan>'
        f" tijdens kantooruren · "
        f'<tspan font-weight="700" fill="{OFFICE_MAIN_COLORS["buiten_kantooruren"]}">{outside_pct:g}%</tspan>'
        f" buiten kantooruren"
        f"</text>"
    )
    parts.append(
        f'<text x="{bar_x:.1f}" y="{callout_y + 18:.1f}" font-family="Inter" font-size="11.5" fill="#888">'
        f"{_format_chart_count(office_count)} vs. {_format_chart_count(outside_count)} gesprekken"
        f"</text>"
    )

    return "\n        ".join(parts)


def office_hours_channels_chart_svg(
    by_channel: dict[str, dict[str, int]],
    channel_counts: dict[str, int],
    *,
    width: int = OFFICE_HOURS_COMPARE_WIDTH,
    height: int = 300,
) -> str:
    from app.utils.report_data import CHANNEL_DISPLAY, TIME_BUCKET_ORDER, active_channels

    active = active_channels(channel_counts)
    if not active:
        return (
            f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" font-family="Inter" font-size="13" fill="#999" '
            f'text-anchor="middle">Geen kanaaldata</text>'
        )

    pad = 20.0
    label_w = 124.0
    bar_x = pad + label_w + 10.0
    bar_w = float(width) - bar_x - pad
    row_h = 58.0
    row_gap = 54.0
    y_start = 48.0

    parts: list[str] = []
    parts.append(
        f'<text x="{pad:.1f}" y="32" font-family="Inter" font-size="12" fill="#888" font-weight="700">'
        f"Vergelijking per kanaal · zelfde kleuren als totaal</text>"
    )

    for index, channel in enumerate(active):
        cfg = CHANNEL_DISPLAY[channel]
        buckets = by_channel.get(channel, {})
        total = channel_counts.get(channel, 0)
        if total <= 0:
            continue

        y = y_start + index * (row_h + row_gap)
        accent = cfg["chart_stroke"]
        parts.append(
            f'<rect x="{pad:.1f}" y="{y - 10:.1f}" width="4" height="{row_h + 20:.1f}" rx="2" fill="{accent}"/>'
        )
        parts.append(
            f'<text x="{pad + 12:.1f}" y="{y + 16:.1f}" font-family="Inter" font-size="15" fill="#151515" '
            f'font-weight="800">{escape(cfg["label"])}</text>'
        )
        parts.append(
            f'<text x="{pad + 12:.1f}" y="{y + 36:.1f}" font-family="Inter" font-size="11.5" fill="#888">'
            f"{_format_chart_count(total)} gesprekken</text>"
        )

        _render_stacked_bar(
            parts,
            buckets,
            order=TIME_BUCKET_ORDER,
            colors=TIME_BUCKET_COLORS,
            short_labels=TIME_BUCKET_SHORT,
            x=bar_x,
            y=y + 2,
            width=bar_w,
            height=row_h,
            clip_id=f"oh-channel-bar-{channel}",
            min_label_pct=7.0,
            font_scale=1.08,
        )

    return "\n        ".join(parts)


def build_office_hours_stat_grid_html(buckets: dict[str, int]) -> str:
    from app.utils.report_data import TIME_BUCKET_LABELS, TIME_BUCKET_ORDER

    total = sum(buckets.get(key, 0) for key in TIME_BUCKET_ORDER)
    if total <= 0:
        return ""

    tiles: list[str] = []
    for key in TIME_BUCKET_ORDER:
        value = buckets.get(key, 0)
        pct = round(100 * value / total, 1)
        color = TIME_BUCKET_COLORS[key]
        tiles.append(
            f'<div class="oh-stat-tile">'
            f'<span class="oh-stat-swatch" style="background:{color}"></span>'
            f'<div class="oh-stat-copy">'
            f'<div class="oh-stat-label">{escape(TIME_BUCKET_LABELS[key])}</div>'
            f'<div class="oh-stat-value">{pct:g}%</div>'
            f'<div class="oh-stat-count">{_format_chart_count(value)} gesprekken</div>'
            f"</div></div>"
        )
    return f'<div class="office-hours-stat-grid">{"".join(tiles)}</div>'


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


def _office_hours_pie_positions(
    width: float,
    height: float,
    *,
    layout: str,
    compact: bool,
) -> tuple[float, float, float, float, float, float]:
    if layout == "diagonal":
        scale = min(width, height)
        if compact:
            return (
                width * 0.30,
                height * 0.32,
                scale * 0.24,
                width * 0.72,
                height * 0.76,
                scale * 0.20,
            )
        return (
            width * 0.26,
            height * 0.36,
            scale * 0.30,
            width * 0.76,
            height * 0.76,
            scale * 0.24,
        )

    if compact:
        return width * 0.25, height * 0.42, scale * 0.18 if (scale := min(width, height)) else 52.0, width * 0.75, height * 0.42, scale * 0.14
    return width * 0.25, height * 0.45, 78.0, width * 0.75, height * 0.45, 62.0


def _render_pie_caption(
    parts: list[str],
    *,
    cx: float,
    cy: float,
    radius: float,
    title: str,
) -> None:
    parts.append(
        f'<text x="{cx:.1f}" y="{cy - radius - 12:.1f}" font-family="Inter" font-size="11.5" '
        f'fill="#888" font-weight="600" text-anchor="middle">{escape(title)}</text>'
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
    show_legend: bool = True,
    show_slice_labels: bool = False,
    label_scale: float = 1.0,
) -> None:
    import math

    total = sum(buckets.get(key, 0) for key in order)
    if total <= 0:
        return

    title_y = cy - radius - (14 if compact else 18)
    if legend_title and show_legend:
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
            if show_slice_labels and sweep >= 14:
                pct = round(100 * value / total, 1)
                mid = math.radians(angle + sweep / 2)
                label_r = radius * 0.58
                lx = cx + label_r * math.cos(mid)
                ly = cy + label_r * math.sin(mid)
                count_label = f"{value:,}".replace(",", ".")
                fill = "#fff" if key in {"nacht", "kantooruren"} else "#151515"
                pct_size = (10 if compact else 11) * label_scale
                count_size = (9 if compact else 10) * label_scale
                parts.append(
                    f'<text x="{lx:.1f}" y="{ly - 4:.1f}" font-family="Inter" font-size="{pct_size:.1f}" '
                    f'fill="{fill}" text-anchor="middle" font-weight="700">{pct:g}%</text>'
                )
                parts.append(
                    f'<text x="{lx:.1f}" y="{ly + 8:.1f}" font-family="Inter" font-size="{count_size:.1f}" '
                    f'fill="{fill}" text-anchor="middle">{count_label}</text>'
                )
        angle += sweep

    if not show_legend:
        return

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
    layout: str = "horizontal",
    show_legend: bool = True,
    show_slice_labels: bool = False,
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

    main_cx, main_cy, main_r, detail_cx, detail_cy, detail_r = _office_hours_pie_positions(
        float(width),
        float(height),
        layout=layout,
        compact=compact,
    )
    label_scale = 1.35 if layout == "diagonal" and show_slice_labels else 1.0

    main_pie_order = ("buiten_kantooruren", "kantooruren")
    main_legend_order = ("kantooruren", "buiten_kantooruren")

    if not show_legend:
        _render_pie_caption(
            parts,
            cx=main_cx,
            cy=main_cy,
            radius=main_r,
            title="Tijdens vs. buiten kantoor uren",
        )

    _render_pie_column(
        parts,
        main,
        order=main_pie_order,
        labels=OFFICE_MAIN_LABELS,
        colors=OFFICE_MAIN_COLORS,
        cx=main_cx,
        cy=main_cy,
        radius=main_r,
        legend_title="Tijdens vs. buiten kantoor uren",
        legend_order=main_legend_order,
        compact=compact,
        show_legend=show_legend,
        show_slice_labels=show_slice_labels,
        label_scale=label_scale,
    )

    if detail and main.get("buiten_kantooruren", 0) > 0:
        if not show_legend:
            _render_pie_caption(
                parts,
                cx=detail_cx,
                cy=detail_cy,
                radius=detail_r,
                title="Verdeling buiten kantoor uren",
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
            show_legend=show_legend,
            show_slice_labels=show_slice_labels,
            label_scale=label_scale,
        )
        import math

        if layout == "diagonal":
            angle = math.radians(40)
            x1 = main_cx + main_r * math.cos(angle)
            y1 = main_cy + main_r * math.sin(angle)
            x2 = detail_cx - detail_r * math.cos(angle)
            y2 = detail_cy - detail_r * math.sin(angle)
        else:
            x1 = main_cx + main_r + 6
            y1 = main_cy
            x2 = detail_cx - detail_r - 8
            y2 = main_cy
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#bbb" stroke-width="1.2" stroke-dasharray="4 3"/>'
        )
    elif not compact:
        parts.append(
            f'<text x="{detail_cx:.1f}" y="{detail_cy:.1f}" font-family="Inter" font-size="12" fill="#bbb" '
            f'text-anchor="middle">Geen buiten-kantooruren</text>'
        )

    return "\n        ".join(parts)


def build_office_hours_shared_legend_html(*, chart_kind: str = "bar") -> str:
    from app.utils.report_data import TIME_BUCKET_LABELS

    items = [
        (key, TIME_BUCKET_LABELS[key], TIME_BUCKET_COLORS[key])
        for key in ("kantooruren", "na_kantooruren", "nacht", "weekend")
    ]
    legend_row = "".join(
        f'<span class="office-hours-legend-item">'
        f'<span class="office-hours-legend-swatch" style="background:{color}"></span>'
        f"<span>{escape(label)}</span>"
        f"</span>"
        for _, label, color in items
    )
    if chart_kind == "pie":
        note = (
            "Kantooruren: ma–vr 09:00–17:00 (Europe/Amsterdam). "
            "Percentages en aantallen staan in elke taart."
        )
    else:
        note = (
            "Kantooruren: ma–vr 09:00–17:00 (Europe/Amsterdam). "
            "Elke balk toont het aandeel per tijdvak."
        )
    return (
        '<div class="office-hours-shared-legend">'
        f'<div class="office-hours-legend-row">{legend_row}</div>'
        f'<p class="office-hours-legend-note">{note}</p>'
        "</div>"
    )


def _office_hours_total_panel_html(title: str, buckets: dict[str, int]) -> str:
    svg_height = 360
    svg = office_hours_dual_pie_svg(
        buckets,
        width=700,
        height=svg_height,
        compact=False,
        layout="diagonal",
        show_legend=False,
        show_slice_labels=True,
    )
    return (
        f'<div class="office-hours-total card office-hours-total-pie">'
        f'<div class="office-hours-head">{escape(title)}</div>'
        f'<svg viewBox="0 0 700 {svg_height}" preserveAspectRatio="xMidYMid meet" style="width:100%" '
        f'xmlns="http://www.w3.org/2000/svg">{svg}</svg>'
        f"</div>"
    )


def _office_hours_panel_html(
    title: str,
    buckets: dict[str, int],
    *,
    width: int = 700,
    height: int = 240,
    compact: bool = True,
    layout: str = "diagonal",
) -> str:
    svg = office_hours_timing_chart_svg(
        buckets,
        width=width,
        height=height,
        compact=compact,
    )
    return (
        f'<div class="office-hours-panel card">'
        f'<div class="office-hours-head">{escape(title)}</div>'
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" style="width:100%" '
        f'xmlns="http://www.w3.org/2000/svg">{svg}</svg>'
        "</div>"
    )


def build_office_hours_total_html(total_buckets: dict[str, int]) -> str:
    legend = build_office_hours_shared_legend_html(chart_kind="pie")
    panel = _office_hours_total_panel_html("Totaal · alle kanalen", total_buckets)
    return f"{legend}{panel}"


def build_office_hours_channel_slides_html(
    by_channel: dict[str, dict[str, int]],
    channel_counts: dict[str, int],
    *,
    event_name: str,
    date_range: str,
    page_start: int,
    total_pages: int,
) -> str:
    from app.utils.report_data import active_channels

    active = active_channels(channel_counts)
    if not active:
        return ""

    svg_height = 108 + len(active) * 112
    chart_width = OFFICE_HOURS_COMPARE_WIDTH
    chart = office_hours_channels_chart_svg(
        by_channel,
        channel_counts,
        width=chart_width,
        height=svg_height,
    )
    legend = build_office_hours_shared_legend_html(chart_kind="bar")
    panel = (
        f'<div class="office-hours-compare card">'
        f'<svg viewBox="0 0 {chart_width} {svg_height}" preserveAspectRatio="xMidYMid meet" style="width:100%" '
        f'xmlns="http://www.w3.org/2000/svg">{chart}</svg>'
        f"</div>"
    )
    return (
        f'<section class="slide slide-bereikbaarheid-channels">'
        f'<div class="topbar"><div class="logo">{ momants_logo_html() }</div>'
        f'<div class="doc">{escape(event_name)} · Conversation Analysis</div></div>'
        f'<div class="eyebrow">Bereikbaarheid</div>'
        f"<h1>Per kanaal · wanneer?</h1>"
        f'<div class="body">'
        f"{legend}"
        f"{panel}"
        f"</div>"
        f'<div class="footer"><span>{escape(event_name)} · Conversation Analysis {escape(date_range)} · Momants</span>'
        f"<span>{page_start} / {total_pages}</span></div>"
        f"</section>"
    )






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
