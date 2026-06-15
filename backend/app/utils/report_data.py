from __future__ import annotations

import json
import logging
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Protocol

from app.integrations.momants_client import get_momants_client
from app.ml.intent_labels import INTENT_DESCRIPTIONS
from app.utils.report_format import all_message_timestamps, format_report_num, format_short_date

logger = logging.getLogger(__name__)


def lucide(name: str) -> str:
    """Inline Lucide icon placeholder (rendered to SVG by the template's script)."""
    return f'<i data-lucide="{name}" class="ic"></i>'


# Per-channel display config for the report. Channels with zero conversations are hidden.
CHANNEL_DISPLAY = {
    "whatsapp": {"label": "WhatsApp", "icon": "message-circle", "pill": "wa", "bar": "#151515"},
    "chat": {"label": "Chat", "icon": "monitor", "pill": "chat", "bar": "#151515"},
    "instagram": {"label": "Instagram", "icon": "instagram", "pill": "ig", "bar": "#9ecf6a"},
}
CHANNEL_ORDER = ["whatsapp", "chat", "instagram"]


def active_channels(channel_counts: dict[str, int]) -> list[str]:
    active = [c for c in CHANNEL_ORDER if channel_counts.get(c, 0) > 0]
    return active or ["chat"]


def build_channel_fragments(
    channel_counts: dict[str, int],
    channel_sentiments: dict[str, float],
    total: int,
) -> dict[str, str]:
    active = active_channels(channel_counts)

    labels = [CHANNEL_DISPLAY[c]["label"] for c in active]
    if len(labels) == 1:
        sentence = labels[0]
    else:
        sentence = ", ".join(labels[:-1]) + " en " + labels[-1]

    pills = "\n".join(
        f'<span class="pill {CHANNEL_DISPLAY[c]["pill"]}">{lucide(CHANNEL_DISPLAY[c]["icon"])} '
        f'{CHANNEL_DISPLAY[c]["label"]} · {channel_counts.get(c, 0)}</span>'
        for c in active
    )

    rows: list[str] = []
    for c in active:
        cfg = CHANNEL_DISPLAY[c]
        count = channel_counts.get(c, 0)
        pct = round(100 * count / total) if total else 0
        rows.append(
            '<div>'
            '<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">'
            f'<span style="font-weight:600;font-size:14.5px">{lucide(cfg["icon"])} {cfg["label"]}</span>'
            f'<span style="font-weight:800;font-size:14.5px">{count} '
            f'<span style="color:var(--muted);font-weight:400;font-size:12px">{pct}%</span></span>'
            '</div>'
            f'<div class="bar-track"><div class="bar-fill" style="background:{cfg["bar"]};width:{pct}%"></div></div>'
            '</div>'
        )

    cols: list[str] = []
    for c in active:
        cfg = CHANNEL_DISPLAY[c]
        score = channel_sentiments.get(c)
        value = format_report_num(score, 1) if score is not None else "—"
        cols.append(
            '<div style="text-align:center">'
            f'<div style="font-size:22px;font-weight:800">{value} ★</div>'
            f'<div style="font-size:12px;color:var(--muted);margin-top:3px">{cfg["label"]}</div>'
            '</div>'
        )
    cols_html = (
        f'<div style="display:grid;grid-template-columns:repeat({len(active)},1fr)">' + "".join(cols) + "</div>"
    )

    return {
        "channels_sentence": sentence,
        "channel_pills": pills,
        "channel_breakdown_rows": "\n".join(rows),
        "channel_sentiment_cols": cols_html,
    }


@dataclass(frozen=True)
class RecommendationContext:
    cluster_1_count: int
    cluster_1_text: str
    no_reply: int
    weak_answer: int
    takeover_count: int
    total_conversations: int
    peak_hour: str
    peak_hour_range: str
    peak_hour_avg: str
    lowest_channel: str | None
    lowest_score: float | None
    active_channel_count: int
    declining_pct: float
    avg_stars: float | None


def build_recommendations(ctx: RecommendationContext) -> tuple[str, str]:
    """Pick the most relevant action points from the analysis and render them as cards.

    Returns (actions_html, priority_sentence). Only actions whose underlying signal is
    actually present are shown, ranked by impact, capped at four.
    """
    candidates: list[tuple[int, str, str, str]] = []  # (priority, icon, title, body)

    if ctx.cluster_1_count > 0:
        candidates.append(
            (
                100,
                "ticket",
                f"Train agent op cluster #1 ({ctx.cluster_1_count} vragen)",
                f"De meest voorkomende onbeantwoorde vraag komt {ctx.cluster_1_count}× voor. "
                f'Voorbeeld: "{ctx.cluster_1_text}". Prioriteer agent-training en flows voor dit onderwerp.',
            )
        )
    if ctx.no_reply > 0:
        candidates.append(
            (
                90,
                "bell-off",
                f"Beantwoord {ctx.no_reply} genegeerde vragen",
                f"{ctx.no_reply} vragen kregen geen enkele reactie. Elke genegeerde vraag is een gemiste kans — "
                "zet hier vaste flows of escalatie op.",
            )
        )
    if ctx.avg_stars is not None and ctx.avg_stars < 3.0:
        candidates.append(
            (
                85,
                "frown",
                f"Verhoog de algehele tevredenheid ({format_report_num(ctx.avg_stars, 1)}★)",
                "De gemiddelde stemming ligt onder neutraal. Focus op snellere en volledigere antwoorden "
                "om de tevredenheid te verhogen.",
            )
        )
    if ctx.takeover_count > 0:
        pct = round(100 * ctx.takeover_count / ctx.total_conversations) if ctx.total_conversations else 0
        candidates.append(
            (
                80,
                "trending-down",
                f"Analyseer {ctx.takeover_count} takeover-gesprekken",
                f"{pct}% van de gesprekken eindigde met menselijke overname. Identificeer terugkerende "
                "triggers en vertaal die naar agent-flows — direct trainingsmateriaal.",
            )
        )
    if ctx.weak_answer > 0:
        candidates.append(
            (
                70,
                "message-square",
                f"Versterk {ctx.weak_answer} zwakke antwoorden",
                f"{ctx.weak_answer} antwoorden waren onvolledig of naast de kwestie. Verrijk de kennisbank "
                "zodat de agent de vraag volledig afdekt.",
            )
        )
    if ctx.active_channel_count > 1 and ctx.lowest_score is not None and ctx.lowest_score < 3.5 and ctx.lowest_channel:
        icon = "instagram" if ctx.lowest_channel == "Instagram" else "monitor"
        candidates.append(
            (
                60,
                icon,
                f"Verbeter {ctx.lowest_channel}-afhandeling ({format_report_num(ctx.lowest_score, 1)}★)",
                f"{ctx.lowest_channel} scoort het laagst van alle kanalen. Controleer kanaalspecifieke flows "
                "en of de agent voldoende context heeft.",
            )
        )
    if ctx.declining_pct >= 15:
        candidates.append(
            (
                55,
                "trending-down",
                f"Onderzoek dalende gesprekken ({round(ctx.declining_pct)}%)",
                f"{round(ctx.declining_pct)}% van de gesprekken eindigde negatiever dan ze begonnen. "
                "Analyseer waar de stemming omslaat en grijp daar eerder in.",
            )
        )
    if ctx.peak_hour not in ("—", ""):
        candidates.append(
            (
                50,
                "clock",
                f"Versterk agent-capaciteit {ctx.peak_hour_range}",
                f"Gemiddeld {ctx.peak_hour_avg} gesprekken/uur rond {ctx.peak_hour}. Overweeg een proactieve "
                f"FAQ-push vóór {ctx.peak_hour} of snellere escalatielogica in {ctx.peak_hour_range}.",
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    chosen = candidates[:4]

    cards: list[str] = []
    for index, (_, icon, title, body) in enumerate(chosen):
        is_top = index == 0
        icon_bg = "#151515" if is_top else "#f0f0ee"
        icon_color = "var(--accent)" if is_top else "#151515"
        cards.append(
            f'<div class="action{" top" if is_top else ""}">'
            '<div class="ahead">'
            f'<div style="width:30px;height:30px;border-radius:8px;background:{icon_bg};color:{icon_color};'
            'font-size:16px;display:flex;align-items:center;justify-content:center;flex-shrink:0">'
            f'{lucide(icon)}</div>'
            f'<div class="atitle">{escape(title)}</div>'
            '</div>'
            f'<div class="abody">{escape(body)}</div>'
            '</div>'
        )

    if len(chosen) >= 2:
        priority = (
            f"Focus eerst op “{chosen[0][2]}” en “{chosen[1][2]}” — "
            "samen leveren die de grootste verbetering op."
        )
    elif chosen:
        priority = f"Focus eerst op “{chosen[0][2]}”."
    else:
        priority = "Geen directe actiepunten — de agent presteert op alle gemeten punten goed."

    return "\n".join(cards), priority

INTENT_ICONS = {
    "refund": "💳",
    "shipping": "📦",
    "order_status": "🎫",
    "account": "👤",
    "pricing": "💰",
    "product_info": "ℹ️",
    "complaint": "⚠️",
    "technical_support": "🔧",
    "general": "❓",
}

BAR_COLORS = ["#151515", "#8abe6a", "#8abe6a", "#c8e0b0", "#c8e0b0", "#c8e0b0"]

CHANNEL_LABELS = {
    "whatsapp": "WhatsApp",
    "chat": "Chat",
    "instagram": "Instagram",
}


class ConversationLike(Protocol):
    messages: list
    created_at: datetime
    integration_type: str | None


class MetricsLike(Protocol):
    timeline_json: str | None


@dataclass(frozen=True)
class IntentRow:
    slug: str
    label: str
    count: int
    pct: int
    bar_pct: float


def conversation_start(conversation: ConversationLike) -> datetime | None:
    timestamps = all_message_timestamps([conversation])
    if timestamps:
        return min(timestamps)
    return conversation.created_at


def daily_conversation_counts(conversations: list[ConversationLike]) -> dict[datetime, int]:
    counts: dict[datetime, int] = defaultdict(int)
    for conversation in conversations:
        start = conversation_start(conversation)
        if start is None:
            continue
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        counts[day] += 1
    return dict(counts)


def hourly_conversation_counts(conversations: list[ConversationLike]) -> dict[int, int]:
    counts: Counter[int] = Counter()
    for conversation in conversations:
        start = conversation_start(conversation)
        if start is None:
            continue
        counts[start.hour] += 1
    return dict(counts)


def hourly_conversation_averages(conversations: list[ConversationLike]) -> dict[int, float]:
    raw = hourly_conversation_counts(conversations)
    if not conversations:
        return {}
    days = max(len(daily_conversation_counts(conversations)), 1)
    return {hour: round(count / days, 1) for hour, count in raw.items()}


def aggregate_sentiment_arc(metrics: list[MetricsLike], max_index: int = 10) -> list[float]:
    buckets: dict[int, list[float]] = defaultdict(list)
    for metric in metrics:
        if not metric.timeline_json:
            continue
        try:
            timeline = json.loads(metric.timeline_json)
        except json.JSONDecodeError:
            continue
        for point in timeline:
            index = point.get("index")
            stars = point.get("stars")
            if index is None or stars is None:
                continue
            if 0 <= int(index) < max_index:
                buckets[int(index)].append(float(stars))

    arc: list[float] = []
    for index in range(max_index):
        values = buckets.get(index, [])
        if values:
            arc.append(round(statistics.mean(values), 1))
        elif arc:
            arc.append(arc[-1])
        else:
            arc.append(3.0)
    return arc


def intent_label(slug: str) -> str:
    nl = INTENT_DESCRIPTIONS.get("nl", {})
    if slug in nl:
        text = nl[slug]
        return text[0].upper() + text[1:] if text else slug
    return slug.replace("_", " ").title()


def top_intents(intent_breakdown: dict[str, int], limit: int = 6) -> list[IntentRow]:
    if not intent_breakdown:
        return []
    total = sum(intent_breakdown.values()) or 1
    top_count = max(intent_breakdown.values())
    rows: list[IntentRow] = []
    for slug, count in sorted(intent_breakdown.items(), key=lambda item: (-item[1], item[0]))[:limit]:
        pct = round(100 * count / total)
        bar_pct = round(100 * count / top_count, 1)
        rows.append(IntentRow(slug=slug, label=intent_label(slug), count=count, pct=pct, bar_pct=bar_pct))
    return rows


def render_intent_breakdown_html(intent_breakdown: dict[str, int], limit: int = 6) -> str:
    rows = top_intents(intent_breakdown, limit=limit)
    if not rows:
        return '<div style="font-size:13px;color:var(--muted)">Geen intent-data beschikbaar.</div>'

    parts: list[str] = []
    for index, row in enumerate(rows):
        icon = INTENT_ICONS.get(row.slug, "📋")
        color = BAR_COLORS[min(index, len(BAR_COLORS) - 1)]
        parts.append(
            f'<div>'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px">'
            f'<span style="font-weight:600;font-size:13.5px">{icon} {escape(row.label)}</span>'
            f'<span style="font-weight:800;font-size:13.5px">{row.pct}%</span>'
            f"</div>"
            f'<div class="bar-track"><div class="bar-fill" style="background:{color};width:{row.bar_pct}%"></div></div>'
            f"</div>"
        )
    return "\n        ".join(parts)


def peak_hour_range(peak_hour: int) -> str:
    end_hour = (peak_hour + 1) % 24
    return f"{peak_hour:02d}:00–{end_hour:02d}:00"


def dominant_channel(channel_counts: dict[str, int]) -> str | None:
    if not channel_counts:
        return None
    key = max(channel_counts, key=channel_counts.get)
    return CHANNEL_LABELS.get(key, key)


def highest_sentiment_channel(channel_sentiments: dict[str, float]) -> tuple[str | None, float | None]:
    if not channel_sentiments:
        return None, None
    key = max(channel_sentiments, key=channel_sentiments.get)
    return CHANNEL_LABELS.get(key, key), channel_sentiments[key]


@dataclass(frozen=True)
class ActionBodyContext:
    cluster_1_count: str
    cluster_1_text: str
    pct_takeover: str
    conversations_takeover: str
    peak_hour: str
    peak_hour_range: str
    peak_hour_avg: str
    lowest_sentiment_channel: str
    lowest_sentiment_score: str


def build_action_bodies(ctx: ActionBodyContext) -> dict[str, str]:
    cluster_count = ctx.cluster_1_count if ctx.cluster_1_count not in ("—", "") else "0"
    cluster_text = ctx.cluster_1_text if ctx.cluster_1_text not in ("—", "") else "geen voorbeeld beschikbaar"

    cluster_body = (
        f"De meest voorkomende onbeantwoorde vraag komt {cluster_count}× voor. "
        f'Voorbeeld: "{cluster_text}". Prioriteer agent-training en flows voor dit onderwerp.'
    )

    takeover_body = (
        f"{ctx.pct_takeover}% van de gesprekken eindigde met menselijke overname "
        f"({ctx.conversations_takeover} gesprekken). Identificeer terugkerende triggers "
        f"en vertaal die naar agent-flows — direct trainingsmateriaal."
    )

    peak_hour = ctx.peak_hour if ctx.peak_hour not in ("—", "") else "het piekuur"
    peak_range = ctx.peak_hour_range if ctx.peak_hour_range not in ("—", "") else "het piektijdvak"
    peak_avg = ctx.peak_hour_avg if ctx.peak_hour_avg not in ("—", "") else "—"
    peak_body = (
        f"Gemiddeld {peak_avg} gesprekken/uur rond {peak_hour}. "
        f"Overweeg een proactieve FAQ-push vóór {peak_hour} of snellere escalatielogica in {peak_range}."
    )

    channel = ctx.lowest_sentiment_channel if ctx.lowest_sentiment_channel not in ("—", "") else "dit kanaal"
    score = ctx.lowest_sentiment_score if ctx.lowest_sentiment_score not in ("—", "") else "—"
    channel_hint = (
        " Controleer of de agent voldoende context heeft bij DM-gesprekken en of typische Instagram-vragen gedekt zijn."
        if channel == "Instagram"
        else " Controleer kanaalspecifieke flows en of de agent voldoende context heeft voor dit kanaal."
    )
    channel_body = f"{channel} scoort het laagst ({score}★).{channel_hint}"

    return {
        "action_cluster_body": cluster_body,
        "action_takeover_body": takeover_body,
        "action_peak_body": peak_body,
        "action_channel_body": channel_body,
    }


def _parse_stats_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_timeseries(stats: dict, *metric_keys: str) -> list[dict]:
    for key in metric_keys:
        metric = stats.get(key)
        if not isinstance(metric, dict):
            continue
        data = metric.get("data") or metric.get("values") or metric.get("series")
        if isinstance(data, list) and data:
            return data
    return []


def _point_value(point: dict) -> int | None:
    for key in ("value", "count", "total", "y"):
        raw = point.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                continue
    return None


def _point_timestamp(point: dict) -> datetime | None:
    for key in ("timestamp", "date", "x", "time", "label"):
        parsed = _parse_stats_timestamp(point.get(key))
        if parsed is not None:
            return parsed
    return None


def daily_counts_from_momants_stats(stats: dict) -> dict[datetime, int]:
    counts: dict[datetime, int] = defaultdict(int)
    for point in _extract_timeseries(stats, "conversations", "conversation_count"):
        timestamp = _point_timestamp(point)
        value = _point_value(point)
        if timestamp is None or value is None:
            continue
        day = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        counts[day] += value
    return dict(counts)


def hourly_counts_from_momants_stats(stats: dict) -> dict[int, int]:
    counts: Counter[int] = Counter()
    for point in _extract_timeseries(stats, "conversation_heatmap", "conversations", "conversation_count"):
        timestamp = _point_timestamp(point)
        value = _point_value(point)
        if timestamp is not None and value is not None:
            counts[timestamp.hour] += value
            continue
        hour_raw = point.get("hour")
        if hour_raw is not None and value is not None:
            try:
                counts[int(hour_raw) % 24] += value
            except (TypeError, ValueError):
                continue
    return dict(counts)


def apply_momants_stats_fallback(
    agent_id: str,
    message_timestamps: list[datetime],
    daily_counts: dict[datetime, int],
    hour_counts: dict[int, int],
    chart_source: str,
) -> tuple[dict[datetime, int], dict[int, int], str]:
    if not message_timestamps:
        return daily_counts, hour_counts, chart_source

    start_date = min(message_timestamps)
    end_date = max(message_timestamps)
    used_momants = False

    try:
        client = get_momants_client()
        if not daily_counts or len(daily_counts) < 2:
            day_stats = client.get_dashboard_stats(
                agent_id,
                time_unit="day",
                start_date=start_date,
                end_date=end_date,
            )
            momants_daily = daily_counts_from_momants_stats(day_stats)
            if momants_daily:
                daily_counts = momants_daily
                used_momants = True

        if not hour_counts:
            hour_stats = client.get_dashboard_stats(
                agent_id,
                time_unit="hour",
                start_date=start_date,
                end_date=end_date,
            )
            momants_hourly = hourly_counts_from_momants_stats(hour_stats)
            if momants_hourly:
                hour_counts = momants_hourly
                used_momants = True
    except Exception as exc:
        logger.warning("Momants stats fallback failed for agent %s: %s", agent_id, exc)

    if used_momants and chart_source == "local":
        return daily_counts, hour_counts, "momants"
    if used_momants:
        return daily_counts, hour_counts, "mixed"
    return daily_counts, hour_counts, chart_source
