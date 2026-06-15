from __future__ import annotations

from datetime import datetime
from html import escape

from app.utils.report_format import format_short_date


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
    left, right, top, bottom = 40, 785, 10, 192
    plot_w = right - left
    plot_h = bottom - top

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
        y = bottom - (tick / max_val) * plot_h
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#efefed" stroke-width="1"/>')
        parts.append(
            f'<text x="34" y="{y + 4:.1f}" font-family="Inter" font-size="11" fill="#bbb" text-anchor="end">{tick}</text>'
        )

    points: list[tuple[float, float]] = []
    for index, day in enumerate(days):
        x = left + (index / max(len(days) - 1, 1)) * plot_w
        y = bottom - (daily_counts[day] / max_val) * plot_h
        points.append((x, y))

    area_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_points += f" {points[-1][0]:.1f},{bottom} {points[0][0]:.1f},{bottom}"
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

    parts.append(f'<circle cx="{peak_x:.1f}" cy="{peak_y:.1f}" r="5" fill="#151515"/>')
    parts.append(f'<rect x="{peak_x - 44:.1f}" y="{peak_y - 20:.1f}" width="88" height="17" rx="4" fill="#151515"/>')
    parts.append(
        f'<text x="{peak_x:.1f}" y="{peak_y - 7:.1f}" font-family="Inter" font-size="10.5" fill="#E2F5C9" '
        f'text-anchor="middle" font-weight="700">Piek: {peak_label}</text>'
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
                f'<text x="{x:.1f}" y="206" font-family="Inter" font-size="11" fill="#bbb" '
                f'text-anchor="middle">{escape(format_short_date(days[index]))}</text>'
            )

    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#ddd" stroke-width="1"/>')
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#ddd" stroke-width="1"/>')
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
    left, right, top, bottom = 40, 650, 10, 180
    plot_w = right - left
    plot_h = bottom - top
    count = len(arc_points) or 1

    def star_y(stars: float) -> float:
        clamped = min(5.0, max(1.0, stars))
        return bottom - ((clamped - 1) / 4) * plot_h

    parts: list[str] = []

    for star in range(1, 6):
        y = star_y(float(star))
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right + 40}" y2="{y:.1f}" stroke="#efefed" stroke-width="1"/>')
        parts.append(
            f'<text x="34" y="{y + 4:.1f}" font-family="Inter" font-size="11" fill="#bbb" text-anchor="end">{star}★</text>'
        )

    coords: list[tuple[float, float]] = []
    for index, stars in enumerate(arc_points):
        x = left + (index / max(count - 1, 1)) * plot_w
        y = star_y(stars)
        coords.append((x, y))

    if coords:
        area = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        area += f" {coords[-1][0]:.1f},{bottom} {coords[0][0]:.1f},{bottom}"
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
        parts.append(f'<rect x="{sx - 22:.1f}" y="{sy - 18:.1f}" width="44" height="15" rx="4" fill="#f0f0ee"/>')
        parts.append(
            f'<text x="{sx:.1f}" y="{sy - 6:.1f}" font-family="Inter" font-size="10" fill="#555" '
            f'text-anchor="middle">{escape(start_label)}</text>'
        )
        parts.append(f'<rect x="{ex - 22:.1f}" y="{ey - 12:.1f}" width="44" height="15" rx="4" fill="#151515"/>')
        parts.append(
            f'<text x="{ex:.1f}" y="{ey:.1f}" font-family="Inter" font-size="10" fill="#E2F5C9" '
            f'text-anchor="middle" font-weight="700">{escape(end_label)}</text>'
        )

    label_indices = [0, min(2, count - 1), min(4, count - 1), min(6, count - 1), min(8, count - 1), count - 1]
    seen: set[int] = set()
    for index in label_indices:
        if index in seen or index >= count:
            continue
        seen.add(index)
        x, _ = coords[index] if coords else (left, bottom)
        label = "bericht 1" if index == 0 else ("10" if index == count - 1 else str(index + 1))
        parts.append(
            f'<text x="{x:.1f}" y="200" font-family="Inter" font-size="11" fill="#bbb" text-anchor="middle">{label}</text>'
        )

    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#e0e0dc" stroke-width="1"/>')
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{right + 40}" y2="{bottom}" stroke="#e0e0dc" stroke-width="1"/>')
    return "\n        ".join(parts)
