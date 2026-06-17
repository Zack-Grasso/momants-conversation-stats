from __future__ import annotations

from datetime import datetime
from html import escape

from app.utils.report_format import format_short_date


def _plot_x(index: int, count: int, plot_left: float, plot_right: float) -> float:
    if count <= 1:
        return (plot_left + plot_right) / 2
    return plot_left + (index / (count - 1)) * (plot_right - plot_left)


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
    width: int = 800,
    height: int = 210,
) -> str:
    y_label_x = 46
    plot_left, plot_right = 56, width - 20
    plot_top, plot_bottom = 24, 168
    x_label_y = plot_bottom + 18
    plot_w = plot_right - plot_left
    plot_h = plot_bottom - plot_top

    if not daily_counts:
        return (
            f'<text x="{width / 2:.1f}" y="{height / 2:.1f}" font-family="Inter" font-size="12" '
            f'fill="#bbb" text-anchor="middle">Geen gespreksdata</text>'
        )

    days = sorted(daily_counts.keys())
    values = [daily_counts[day] for day in days]
    max_val = max(values) or 1
    ticks = _nice_ticks(max_val)

    parts: list[str] = []

    for tick in ticks:
        y = plot_bottom - (tick / max_val) * plot_h
        _h_grid(parts, y, plot_left, plot_right)
        parts.append(_y_axis_label(y_label_x, y + 4, str(tick)))

    points: list[tuple[float, float]] = []
    for index, day in enumerate(days):
        x = _plot_x(index, len(days), plot_left, plot_right)
        y = plot_bottom - (daily_counts[day] / max_val) * plot_h
        points.append((x, y))

    area_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_points += f" {points[-1][0]:.1f},{plot_bottom} {points[0][0]:.1f},{plot_bottom}"
    line_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    parts.append(f'<polygon fill="url(#vg)" points="{area_points}"/>')
    parts.append(
        f'<polyline fill="none" stroke="#151515" stroke-width="2.2" stroke-linejoin="round" '
        f'stroke-linecap="round" points="{line_points}"/>'
    )

    if peak_day is None:
        peak_day = max(daily_counts, key=daily_counts.get)
    peak_index = days.index(peak_day) if peak_day in days else values.index(max(values))
    peak_x, peak_y = points[peak_index]
    peak_label = escape(format_short_date(peak_day))
    badge_w = 88
    badge_h = 17
    badge_x = _badge_rect_x(peak_x, badge_w, plot_left, plot_right)
    badge_y = _clamp(peak_y - 22, plot_top + 2, plot_bottom - badge_h - 4)

    parts.append(f'<circle cx="{peak_x:.1f}" cy="{peak_y:.1f}" r="5" fill="#151515"/>')
    parts.append(f'<rect x="{badge_x:.1f}" y="{badge_y:.1f}" width="{badge_w}" height="{badge_h}" rx="4" fill="#151515"/>')
    parts.append(
        f'<text x="{badge_x + badge_w / 2:.1f}" y="{badge_y + 11:.1f}" font-family="Inter" font-size="10.5" '
        f'fill="#E2F5C9" text-anchor="middle" font-weight="700">Piek: {peak_label}</text>'
    )

    label_count = min(7, len(days))
    if label_count:
        step = max(1, (len(days) - 1) // (label_count - 1)) if label_count > 1 else 1
        label_indices = list(range(0, len(days), step))
        if label_indices[-1] != len(days) - 1:
            label_indices.append(len(days) - 1)
        for index in label_indices:
            x, _ = points[index]
            parts.append(
                f'<text x="{x:.1f}" y="{x_label_y}" font-family="Inter" font-size="11" fill="#bbb" '
                f'text-anchor="{_x_tick_anchor(index, len(days))}">{escape(format_short_date(days[index]))}</text>'
            )

    _plot_frame(parts, plot_left=plot_left, plot_right=plot_right, plot_top=plot_top, plot_bottom=plot_bottom)
    return "\n        ".join(parts)


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
    y_label_x = 46
    plot_left, plot_right = 56, width - 24
    plot_top, plot_bottom = 20, 168
    x_label_y = plot_bottom + 18
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

    start_label = f"{start_stars:.1f}★" if start_stars is not None else "—"
    end_label = f"{end_stars:.1f}★" if end_stars is not None else "—"
    if coords:
        sx, sy = coords[0]
        ex, ey = coords[-1]
        start_badge_w = 44
        start_badge_h = 15
        start_badge_x = _clamp(sx + 6, plot_left, plot_right - start_badge_w)
        start_badge_y = _clamp(sy - 22, plot_top + 2, plot_bottom - start_badge_h - 6)
        parts.append(
            f'<rect x="{start_badge_x:.1f}" y="{start_badge_y:.1f}" width="{start_badge_w}" '
            f'height="{start_badge_h}" rx="4" fill="#f0f0ee"/>'
        )
        parts.append(
            f'<text x="{start_badge_x + 6:.1f}" y="{start_badge_y + 11:.1f}" font-family="Inter" '
            f'font-size="10" fill="#555" text-anchor="start">{escape(start_label)}</text>'
        )

        end_badge_w = 44
        end_badge_h = 15
        end_badge_x = _clamp(ex - end_badge_w - 6, plot_left, plot_right - end_badge_w)
        end_badge_y = _clamp(ey + 8, plot_top + 2, plot_bottom - end_badge_h - 4)
        parts.append(
            f'<rect x="{end_badge_x:.1f}" y="{end_badge_y:.1f}" width="{end_badge_w}" '
            f'height="{end_badge_h}" rx="4" fill="#151515"/>'
        )
        parts.append(
            f'<text x="{end_badge_x + end_badge_w - 6:.1f}" y="{end_badge_y + 11:.1f}" '
            f'font-family="Inter" font-size="10" fill="#E2F5C9" text-anchor="end" font-weight="700">'
            f'{escape(end_label)}</text>'
        )

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
    y_label_x = 46
    plot_left, plot_right = 56, width - 24
    plot_top, plot_bottom = 20, 152
    x_label_y = plot_bottom + 16
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
    legend_y = plot_bottom + 34
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
