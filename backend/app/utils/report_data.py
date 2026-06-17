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


def whatsapp_icon() -> str:
    """Official WhatsApp mark for hero channel pills."""
    return (
        '<svg class="pill-brand wa" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
        '<path fill="currentColor" d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.435 9.884-9.884 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>'
        "</svg>"
    )


def channel_pill_icon(channel: str) -> str:
    if channel == "whatsapp":
        return whatsapp_icon()
    return lucide(CHANNEL_DISPLAY[channel]["icon"])


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
        f'<span class="pill {CHANNEL_DISPLAY[c]["pill"]}">'
        f'{channel_pill_icon(c)}'
        f'<span class="pill-label">{CHANNEL_DISPLAY[c]["label"]}</span>'
        f'<span class="pill-count">{channel_counts.get(c, 0)}</span>'
        f"</span>"
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
            f'<div style="font-size:22px;font-weight:800">{value} / 5</div>'
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


TIME_BUCKET_ORDER = ("kantooruren", "na_kantooruren", "nacht", "weekend")
TIME_BUCKET_LABELS = {
    "kantooruren": "Tijdens kantooruren",
    "na_kantooruren": "Na kantooruren",
    "nacht": "Nacht",
    "weekend": "Weekend",
}


def classify_conversation_time_bucket(value: datetime) -> str:
    """Bucket a conversation start into office-hours segments (Europe/Amsterdam when tz-aware)."""
    dt = value
    if dt.tzinfo is not None:
        try:
            from zoneinfo import ZoneInfo

            dt = dt.astimezone(ZoneInfo("Europe/Amsterdam"))
        except Exception:
            pass

    weekday = dt.weekday()
    hour = dt.hour
    if weekday >= 5:
        return "weekend"
    if 9 <= hour < 17:
        return "kantooruren"
    if 17 <= hour < 22:
        return "na_kantooruren"
    return "nacht"


def conversation_time_buckets(conversations: list[ConversationLike]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for conversation in conversations:
        start = conversation_start(conversation)
        if start is None:
            continue
        counts[classify_conversation_time_bucket(start)] += 1
    return {key: counts.get(key, 0) for key in TIME_BUCKET_ORDER if counts.get(key, 0) > 0}


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


# go_emotions (28 labels) -> Dutch for report copy and chart legends.
EMOTION_LABEL_NL = {
    "admiration": "bewondering",
    "amusement": "vermaak",
    "anger": "boosheid",
    "annoyance": "ergernis",
    "approval": "instemming",
    "caring": "zorgzaamheid",
    "confusion": "verwarring",
    "curiosity": "nieuwsgierigheid",
    "desire": "verlangen",
    "disappointment": "teleurstelling",
    "disapproval": "afkeuring",
    "disgust": "afkeer",
    "embarrassment": "schaamte",
    "excitement": "enthousiasme",
    "fear": "angst",
    "gratitude": "dankbaarheid",
    "grief": "verdriet",
    "joy": "vreugde",
    "love": "liefde",
    "nervousness": "nervositeit",
    "optimism": "optimisme",
    "pride": "trots",
    "realization": "besef",
    "relief": "opluchting",
    "remorse": "spijt",
    "sadness": "verdriet",
    "surprise": "verrassing",
    "neutral": "neutraal",
    "overig": "overig",
}


@dataclass(frozen=True)
class EmotionTimeline:
    """Dominant-emotion shares per member-message index across conversations."""

    emotions: tuple[str, ...]
    points: tuple[dict[str, float], ...]


def _ordered_member_messages(conversation: ConversationLike) -> list:
    messages = sorted(
        conversation.messages,
        key=lambda message: (getattr(message, "source_created_at", None) or message.created_at, message.id),
    )
    return [message for message in messages if not getattr(message, "from_agent", False)]


def _message_top_emotion(message) -> str:
    sentiment = getattr(message, "sentiment", None)
    if sentiment is None:
        return "neutral"
    emotions = sentiment.emotions if hasattr(sentiment, "emotions") else []
    if not emotions:
        return "neutral"
    return str(emotions[0].get("label", "neutral")).lower()


def aggregate_emotion_timeline(
    conversations: list[ConversationLike],
    *,
    max_index: int = 10,
    top_n: int = 5,
) -> EmotionTimeline | None:
    index_counts: dict[int, Counter[str]] = defaultdict(Counter)
    for conversation in conversations:
        for index, message in enumerate(_ordered_member_messages(conversation)[:max_index]):
            index_counts[index][_message_top_emotion(message)] += 1

    if not any(index_counts.values()):
        return None

    global_counter: Counter[str] = Counter()
    for counter in index_counts.values():
        global_counter.update(counter)

    top_emotions = [
        label
        for label, _ in global_counter.most_common(top_n + 5)
        if label not in {"neutral", "overig"}
    ][:top_n]
    if not top_emotions:
        top_emotions = ["neutral"]

    points: list[dict[str, float]] = []
    for index in range(max_index):
        counter = index_counts.get(index, Counter())
        total = sum(counter.values())
        if total == 0:
            points.append(dict(points[-1]) if points else {emotion: 0.0 for emotion in top_emotions})
            continue

        row: dict[str, float] = {}
        tracked = 0.0
        for emotion in top_emotions:
            share = counter.get(emotion, 0) / total
            row[emotion] = round(share, 3)
            tracked += share
        other = max(0.0, 1.0 - tracked)
        if other >= 0.01:
            row["overig"] = round(other, 3)
        points.append(row)

    emotion_keys: list[str] = list(top_emotions)
    if any(point.get("overig", 0) > 0 for point in points):
        emotion_keys.append("overig")

    return EmotionTimeline(emotions=tuple(emotion_keys), points=tuple(points))


def build_emotion_timeline_insight(timeline: EmotionTimeline | None) -> str:
    if timeline is None or not timeline.points:
        return "Geen emotiedata beschikbaar in deze periode."

    def dominant_at(point: dict[str, float]) -> str:
        if not point:
            return "neutral"
        return max(point, key=point.get)

    start_key = dominant_at(timeline.points[0])
    end_key = dominant_at(timeline.points[-1])
    start_label = EMOTION_LABEL_NL.get(start_key, start_key)
    end_label = EMOTION_LABEL_NL.get(end_key, end_key)

    if start_key == end_key:
        return (
            f"{start_label.capitalize()} is de dominante emotie door het gesprek heen "
            f"(bericht #1 → #{len(timeline.points)})."
        )
    return (
        f"Mensen starten vaak met {start_label}; tegen bericht #{len(timeline.points)} "
        f"verschuift dat naar {end_label}."
    )


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


def peak_period_label(peak_hour: int) -> str:
    """Dutch label for the time-of-day bucket the peak hour falls in."""
    if 6 <= peak_hour <= 11:
        return "ochtendpiek"
    if 12 <= peak_hour <= 17:
        return "middagpiek"
    if 18 <= peak_hour <= 23:
        return "avondpiek"
    return "nachtpiek"


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


NON_OFFICE_LABEL_HINTS = ("non", "buiten", "outside", "after", "avond", "nacht", "weekend", "off-hours")
OFFICE_LABEL_HINTS = ("office", "kantoor", "during", "binnen", "werk")


@dataclass
class MomantsReportStats:
    conversations_total: float | None = None
    hours_saved: float | None = None
    support_cost_saved: float | None = None
    assisted_revenue: float | None = None
    direct_revenue: float | None = None
    pct_outside_office: float | None = None

    @property
    def total_value_creation(self) -> float | None:
        if self.assisted_revenue is None and self.support_cost_saved is None:
            return None
        return (self.assisted_revenue or 0) + (self.support_cost_saved or 0)


def _metric_total(stats: dict, key: str) -> float | None:
    metric = stats.get(key)
    if not isinstance(metric, dict):
        return None

    for field in ("total_current_period", "total", "current_total"):
        parsed = _to_float(metric.get(field))
        if parsed is not None:
            return parsed

    summary = metric.get("summary")
    if isinstance(summary, dict):
        parsed = _to_float(summary.get("total"))
        if parsed is not None:
            return parsed

    data = metric.get("data")
    if isinstance(data, list) and data:
        total = sum(v for point in data if (v := _point_value(point)) is not None)
        if total > 0:
            return float(total)

    return None


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_non_office_label(name: str) -> bool:
    lowered = name.lower()
    if any(hint in lowered for hint in NON_OFFICE_LABEL_HINTS):
        return True
    if "non" in lowered and "office" in lowered:
        return True
    return False


def _is_office_label(name: str) -> bool:
    lowered = name.lower()
    if _is_non_office_label(lowered):
        return False
    return any(hint in lowered for hint in OFFICE_LABEL_HINTS)


def _office_hours_pct(stats: dict) -> float | None:
    metric = stats.get("conversations_office_vs_non_office")
    if not isinstance(metric, dict):
        return None

    data = metric.get("data")
    if not isinstance(data, list) or not data:
        return None

    entries: list[tuple[str, float]] = []
    for point in data:
        if not isinstance(point, dict):
            continue
        name = str(point.get("name") or point.get("label") or "")
        value = _to_float(point.get("value"))
        if value is None:
            value = _to_float(point.get("count"))
        if value is None:
            continue
        entries.append((name, value))

    if not entries:
        return None

    total = sum(value for _, value in entries)
    if total <= 0:
        return None

    non_office = sum(value for name, value in entries if _is_non_office_label(name))
    if non_office > 0:
        return round(100 * non_office / total, 1)

    office = sum(value for name, value in entries if _is_office_label(name))
    if office > 0:
        return round(100 * (total - office) / total, 1)

    if len(entries) == 2:
        return round(100 * entries[1][1] / total, 1)

    return None


def parse_momants_report_stats(stats: dict) -> MomantsReportStats:
    return MomantsReportStats(
        conversations_total=_metric_total(stats, "conversations"),
        hours_saved=_metric_total(stats, "hours_saved"),
        support_cost_saved=_metric_total(stats, "support_cost_saved"),
        assisted_revenue=_metric_total(stats, "assisted_revenue"),
        direct_revenue=_metric_total(stats, "direct_revenue"),
        pct_outside_office=_office_hours_pct(stats),
    )


def fetch_momants_report_stats(
    agent_id: str,
    start_date: datetime,
    end_date: datetime,
) -> MomantsReportStats:
    try:
        client = get_momants_client()
        stats = client.get_dashboard_stats(
            agent_id,
            time_unit="day",
            start_date=start_date,
            end_date=end_date,
        )
        if isinstance(stats, dict):
            return parse_momants_report_stats(stats)
    except Exception as exc:
        logger.warning("Momants report stats failed for agent %s: %s", agent_id, exc)
    return MomantsReportStats()


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
