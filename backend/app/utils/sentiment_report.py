"""Sentiment overview slide: polarity distribution, narrative copy, and donut chart."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from html import escape

from app.utils.report_data import EMOTION_LABEL_NL, intent_label
from app.utils.report_format import format_dutch_int, format_report_num

POLARITY_COLORS = {
    "positive": "#6BAA4F",
    "neutral": "#9CA3AF",
    "negative": "#E57373",
}

POLARITY_LABELS_NL = {
    "positive": "Positief",
    "neutral": "Neutraal",
    "negative": "Negatief",
}

POLARITY_ORDER = ("positive", "neutral", "negative")

POSITIVE_EMOTIONS = frozenset(
    {"curiosity", "excitement", "joy", "gratitude", "desire", "optimism", "approval", "love"}
)
NEGATIVE_EMOTIONS = frozenset(
    {"annoyance", "disappointment", "anger", "confusion", "disapproval", "sadness", "fear", "disgust"}
)

POSITIVE_TOPIC_HINTS = {
    "curiosity": "ticketoriëntatie en line-up",
    "desire": "verblijf en festivalbeleving",
    "excitement": "enthousiasme rondom het event",
    "joy": "positieve festivalervaring",
    "gratitude": "tevredenheid over service",
    "optimism": "verwachtingen rondom het event",
    "approval": "bevestiging en duidelijkheid",
}

NEGATIVE_TOPIC_HINTS = {
    "annoyance": "wachttijden en bereikbaarheid",
    "disappointment": "verwachtingen vs. realiteit",
    "anger": "frustratie over afhandeling",
    "confusion": "onduidelijke informatie en antwoorden",
    "disapproval": "prijsstelling en voorwaarden",
}


@dataclass(frozen=True)
class SentimentDistribution:
    positive: int
    neutral: int
    negative: int

    @property
    def total(self) -> int:
        return self.positive + self.neutral + self.negative

    def pct(self, key: str) -> float | None:
        if self.total <= 0:
            return None
        value = getattr(self, key, 0)
        return round(100 * value / self.total, 2)


def distribution_from_counts(counts: dict[str, int]) -> SentimentDistribution:
    return SentimentDistribution(
        positive=int(counts.get("positive", 0)),
        neutral=int(counts.get("neutral", 0)),
        negative=int(counts.get("negative", 0)),
    )


def build_sentiment_headline(dist: SentimentDistribution) -> str:
    if dist.total <= 0:
        return "Sentimentanalyse"

    pcts = {key: dist.pct(key) or 0.0 for key in POLARITY_ORDER}
    neutral = pcts["neutral"]
    positive = pcts["positive"]
    negative = pcts["negative"]

    if neutral >= 40:
        if positive > negative + 3:
            return "Overwegend neutraal tot positief"
        if negative > positive + 3:
            return "Overwegend neutraal tot negatief"
        return "Overwegend neutraal"
    if positive >= max(neutral, negative):
        return "Overwegend positief"
    if negative >= max(neutral, positive):
        return "Overwegend negatief"
    return "Gemengd sentiment"


def _top_emotions_for_polarity(
    rows: list[tuple[str | None, str | None]],
    polarity: str,
    *,
    emotion_filter: frozenset[str] | None = None,
    limit: int = 3,
) -> list[str]:
    counter: Counter[str] = Counter()
    for row_polarity, emotions_json in rows:
        if row_polarity != polarity or not emotions_json:
            continue
        try:
            emotions = json.loads(emotions_json)
        except (ValueError, TypeError):
            continue
        if not isinstance(emotions, list) or not emotions:
            continue
        top = str(emotions[0].get("label", "")).lower()
        if not top or top == "neutral":
            continue
        if emotion_filter and top not in emotion_filter:
            continue
        counter[top] += 1
    return [key for key, _ in counter.most_common(limit)]


def _topic_phrase(emotions: list[str], hints: dict[str, str], fallback: str) -> str:
    topics = []
    for emotion in emotions:
        phrase = hints.get(emotion) or EMOTION_LABEL_NL.get(emotion, emotion)
        if phrase not in topics:
            topics.append(phrase)
    if not topics:
        return fallback
    if len(topics) == 1:
        return topics[0]
    if len(topics) == 2:
        return f"{topics[0]} en {topics[1]}"
    return f"{topics[0]}, {topics[1]} en {topics[2]}"


def _intent_topics(intent_breakdown: dict[str, int], *, slugs: frozenset[str], limit: int = 3) -> str:
    labels = [
        intent_label(slug)
        for slug, _count in sorted(intent_breakdown.items(), key=lambda item: (-item[1], item[0]))
        if slug in slugs
    ][:limit]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0].lower()
    if len(labels) == 2:
        return f"{labels[0].lower()} en {labels[1].lower()}"
    return f"{labels[0].lower()}, {labels[1].lower()} en {labels[2].lower()}"


def build_sentiment_paragraphs(
    dist: SentimentDistribution,
    emotion_rows: list[tuple[str | None, str | None]],
    *,
    intent_breakdown: dict[str, int] | None = None,
) -> tuple[str, str]:
    if dist.total <= 0:
        return (
            "Er is nog onvoldoende sentimentdata om een analyse te maken.",
            "Voer sentimentanalyse opnieuw uit zodra berichten zijn verwerkt.",
        )

    positive_emotions = _top_emotions_for_polarity(
        emotion_rows, "positive", emotion_filter=POSITIVE_EMOTIONS
    )
    negative_emotions = _top_emotions_for_polarity(
        emotion_rows, "negative", emotion_filter=NEGATIVE_EMOTIONS
    )
    positive_topics = _topic_phrase(
        positive_emotions,
        POSITIVE_TOPIC_HINTS,
        "ticketoriëntatie, line-up en verblijf",
    )
    negative_topics = _topic_phrase(
        negative_emotions,
        NEGATIVE_TOPIC_HINTS,
        "prijsstelling, line-up en persoonlijke voorkeuren",
    )

    intents = intent_breakdown or {}
    complaint_topics = _intent_topics(intents, slugs=frozenset({"complaint", "pricing", "refund"}))
    if complaint_topics:
        negative_topics = complaint_topics

    neutral_pct = format_report_num(dist.pct("neutral"), 1) or "—"
    para_1 = (
        f"Het overgrote deel van de berichten — <strong>{format_dutch_int(dist.neutral)}</strong> "
        f"({neutral_pct}%) — was neutraal van toon: feitelijke vragen, bevestigingen of korte reacties. "
        f"Positief sentiment (<strong>{format_dutch_int(dist.positive)}</strong>) deed zich voor rondom "
        f"{positive_topics}."
    )

    neg_pct = format_report_num(dist.pct("negative"), 1) or "—"
    para_2 = (
        f"Negatief sentiment (<strong>{format_dutch_int(dist.negative)}</strong>, {neg_pct}%) "
        f"ontstond voornamelijk bij vragen over <strong>{negative_topics}</strong>. "
        f"Technische issues (WhatsApp-vertraging, AI-opvolgvragen) droegen soms bij aan korte frustratie, "
        f"maar leidden zelden tot langdurige ontevredenheid."
    )
    return para_1, para_2


def build_sentiment_callout(
    dist: SentimentDistribution,
    overview: dict,
    *,
    dominant_mood: str | None = None,
) -> str:
    improving = overview.get("improving_pct")
    worsening = overview.get("worsening_pct")
    if improving is not None and improving >= 35:
        return (
            f"In <strong>{format_report_num(improving, 0)}%</strong> van de gesprekken verbetert het sentiment "
            f"naarmate het gesprek vordert — de agent herstelt de toon wanneer dat nodig is."
        )
    if worsening is not None and worsening >= 25:
        return (
            f"<strong>{format_report_num(worsening, 0)}%</strong> van de gesprekken laat dalend sentiment zien. "
            f"Controleer scripts rond prijs, beschikbaarheid en escalatie naar human support."
        )
    if dist.negative > 0 and dist.total > 0 and (dist.negative / dist.total) < 0.2:
        return (
            "Campagne-gerelateerde frictie werd direct opgevangen — zonder structureel klachtenpatroon "
            "in deze periode."
        )
    if dominant_mood:
        return f"De dominante emotie in member-berichten is <strong>{escape(dominant_mood)}</strong>."
    return "Sentiment blijft overwegend stabiel gedurende de geanalyseerde periode."


def _donut_slice_path(
    cx: float,
    cy: float,
    inner_r: float,
    outer_r: float,
    start_deg: float,
    end_deg: float,
) -> str:
    span = end_deg - start_deg
    if span <= 0:
        return ""
    if span >= 360:
        span = 359.99
        end_deg = start_deg + span

    start = math.radians(start_deg)
    end = math.radians(end_deg)
    x1o = cx + outer_r * math.cos(start)
    y1o = cy + outer_r * math.sin(start)
    x2o = cx + outer_r * math.cos(end)
    y2o = cy + outer_r * math.sin(end)
    x1i = cx + inner_r * math.cos(end)
    y1i = cy + inner_r * math.sin(end)
    x2i = cx + inner_r * math.cos(start)
    y2i = cy + inner_r * math.sin(start)
    large_arc = 1 if span > 180 else 0
    return (
        f"M {x1o:.2f} {y1o:.2f} "
        f"A {outer_r} {outer_r} 0 {large_arc} 1 {x2o:.2f} {y2o:.2f} "
        f"L {x1i:.2f} {y1i:.2f} "
        f"A {inner_r} {inner_r} 0 {large_arc} 0 {x2i:.2f} {y2i:.2f} Z"
    )


def sentiment_polarity_donut_svg(dist: SentimentDistribution, *, size: int = 168) -> str:
    """Donut chart only — legend is rendered as HTML for consistent slide typography."""
    if dist.total <= 0:
        return (
            f'<text x="{size / 2:.1f}" y="{size / 2:.1f}" font-family="Inter" font-size="13" fill="#999" '
            f'text-anchor="middle">Geen sentimentdata</text>'
        )

    cx = cy = size / 2
    outer_r, inner_r = size * 0.42, size * 0.24
    parts: list[str] = []
    angle = -90.0

    for key in POLARITY_ORDER:
        value = getattr(dist, key)
        if value <= 0:
            continue
        sweep = 360.0 * value / dist.total
        path = _donut_slice_path(cx, cy, inner_r, outer_r, angle, angle + sweep)
        if path:
            parts.append(f'<path d="{path}" fill="{POLARITY_COLORS[key]}"/>')
        angle += sweep

    return "\n        ".join(parts)


def build_sentiment_legend_html(dist: SentimentDistribution) -> str:
    if dist.total <= 0:
        return ""
    rows: list[str] = []
    for key in POLARITY_ORDER:
        value = getattr(dist, key)
        pct = dist.pct(key)
        pct_label = format_report_num(pct, 1) if pct is not None else "—"
        color = POLARITY_COLORS[key]
        label = POLARITY_LABELS_NL[key]
        count_label = format_dutch_int(value)
        rows.append(
            f'<div class="sentiment-legend-row">'
            f'<span class="sentiment-swatch" style="background:{color}"></span>'
            f'<span class="sentiment-legend-label">{label}</span>'
            f'<span class="sentiment-legend-meta">{pct_label}% · {count_label}</span>'
            f"</div>"
        )
    return "\n        ".join(rows)


def build_sentiment_stat_cards(dist: SentimentDistribution) -> str:
    cards: list[str] = []
    icons = {"positive": "smile-plus", "neutral": "minus", "negative": "frown"}
    for key in POLARITY_ORDER:
        value = getattr(dist, key)
        pct = dist.pct(key)
        pct_label = format_report_num(pct, 1) if pct is not None else "—"
        label = POLARITY_LABELS_NL[key]
        icon = icons[key]
        cards.append(
            f'<div class="card sentiment-stat">'
            f'<div class="lbl"><i data-lucide="{icon}" class="ic"></i> {label}</div>'
            f'<div class="num">{pct_label}%</div>'
            f'<div class="sub">{format_dutch_int(value)} berichten</div>'
            f"</div>"
        )
    return "\n      ".join(cards)


def build_sentiment_page_html(
    dist: SentimentDistribution,
    emotion_rows: list[tuple[str | None, str | None]],
    overview: dict,
    *,
    dominant_mood: str | None = None,
    intent_breakdown: dict[str, int] | None = None,
) -> str:
    para_1, para_2 = build_sentiment_paragraphs(dist, emotion_rows, intent_breakdown=intent_breakdown)
    donut = sentiment_polarity_donut_svg(dist)
    legend = build_sentiment_legend_html(dist)
    stats = build_sentiment_stat_cards(dist)

    return (
        '<div class="sentiment-layout">'
        f'<div class="sentiment-stats">{stats}</div>'
        '<div class="card card-fill sentiment-main">'
        '<div class="sentiment-visual">'
        '<svg class="sentiment-donut" viewBox="0 0 168 168" preserveAspectRatio="xMidYMid meet" '
        'xmlns="http://www.w3.org/2000/svg">'
        f"{donut}"
        "</svg>"
        f'<div class="sentiment-legend">{legend}</div>'
        "</div>"
        '<div class="sentiment-copy">'
        '<div class="lbl"><i data-lucide="heart-handshake" class="ic"></i> Sentimentanalyse</div>'
        f"<p>{para_1}</p>"
        f"<p>{para_2}</p>"
        "</div>"
        "</div>"
        "</div>"
    )
