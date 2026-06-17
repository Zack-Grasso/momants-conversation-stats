from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models.conversation import Conversation, Message, SentimentAnalysis
from app.models.insights import ConversationMetrics, UnansweredQuestion
from app.services.insights_service import InsightsService
from app.utils.report_charts import (
    daily_volume_chart_svg,
    hourly_bars_chart_svg,
    sentiment_arc_chart_svg,
)
from app.utils.report_data import (
    CHANNEL_LABELS,
    aggregate_sentiment_arc,
    apply_momants_stats_fallback,
    build_channel_fragments,
    daily_conversation_counts,
    dominant_channel,
    highest_sentiment_channel,
    hourly_conversation_averages,
    hourly_conversation_counts,
    peak_hour_range,
    peak_period_label,
)
from app.utils.question_utils import is_question
from app.utils.report_format import (
    DUTCH_WEEKDAYS,
    all_message_timestamps,
    format_date_range,
    format_report_num,
    format_short_date,
    resolve_event_name,
)

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "conversation-analysis-template-v2.html"

# Skip short greetings and other trivial member messages when picking opportunity examples.
TRIVIAL_QUESTION_RE = re.compile(
    r"^(hallo|hoi|hey|hi|dag|dank|thanks|bedankt|oké?|oke|ja|nee|top|super|goed|mooi)\b",
    re.I,
)
STATUS_PRIORITY = {"weak_answer": 0, "not_answered": 1, "no_reply": 2}
# A conversation counts as "doorverwezen" (referred to the event's customer service) when any
# agent message contains an email address.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

TEMPLATE_VARS = [
    "event_name",
    "conversations_total",
    "avg_sentiment_label",
    "dominant_mood",
    "date_range",
    "messages_total",
    "pct_resolved",
    "pct_referred",
    "avg_start_stars",
    "avg_end_stars",
    "channel_whatsapp_count",
    "channel_chat_count",
    "channel_instagram_count",
    "avg_stars",
    "avg_delta_stars",
    "peak_day_name",
    "peak_day_count",
    "peak_day_label",
    "avg_conversations_per_day",
    "peak_hour",
    "peak_hour_avg",
    "peak_hour_range",
    "peak_period_label",
    "channel_whatsapp_pct",
    "channel_chat_pct",
    "channel_instagram_pct",
    "sentiment_whatsapp",
    "sentiment_chat",
    "sentiment_instagram",
    "dominant_channel",
    "dominant_channel_pct",
    "highest_sentiment_channel",
    "highest_sentiment_score",
    "pct_trajectory_improving",
    "pct_trajectory_declining",
    "pct_trajectory_mixed",
    "total_unanswered_questions",
    "pct_unanswered",
    "unanswered_insight",
    "answered_questions_insight",
    "unanswered_no_reply_count",
    "unanswered_weak_answer_count",
    "unanswered_semantic_count",
    "pct_depth_shallow",
    "pct_depth_medium",
    "pct_depth_deep",
    "median_response_fmt",
    "avg_first_response_fmt",
    "p95_response_fmt",
    "avg_depth_ratio",
    "lowest_sentiment_channel",
    "lowest_sentiment_score",
]


class ReportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.insights = InsightsService(db)

    def build_context(self, agent_id: str, event_name: str | None = None) -> dict:
        conversations = list(
            self.db.scalars(
                select(Conversation)
                .where(Conversation.agent_id == agent_id)
                .options(selectinload(Conversation.messages).selectinload(Message.sentiment))
            ).all()
        )
        metrics = list(
            self.db.scalars(select(ConversationMetrics).where(ConversationMetrics.agent_id == agent_id)).all()
        )
        metrics_by_conversation = {item.conversation_id: item for item in metrics}
        unanswered_message_ids = set(
            self.db.scalars(
                select(UnansweredQuestion.message_id).where(UnansweredQuestion.agent_id == agent_id)
            )
        )
        answered_ranked = _rank_answered_questions(conversations, unanswered_message_ids, limit=18)
        unanswered = list(
            self.db.scalars(
                select(UnansweredQuestion)
                .where(UnansweredQuestion.agent_id == agent_id)
                .order_by(UnansweredQuestion.computed_at.desc())
                .limit(100)
            ).all()
        )

        overview = self.insights.get_overview(agent_id)
        resolved_name, event_name_missing = resolve_event_name(agent_id, event_name)
        variables: dict[str, str] = {}
        missing: list[str] = list(event_name_missing)

        def set_var(key: str, value: str | int | float | None, *, required: bool = True) -> None:
            if value is None or value == "":
                if required:
                    missing.append(key)
                variables[key] = "—"
                return
            variables[key] = str(value)

        def set_num(key: str, value: float | int | None, *, digits: int = 1, required: bool = True) -> None:
            if value is None:
                if required:
                    missing.append(key)
                variables[key] = "—"
                return
            variables[key] = format_report_num(value, digits)

        total_conversations = len(conversations) or overview.get("conversation_count", 0)
        set_var("event_name", resolved_name, required=False)
        set_var("conversations_total", total_conversations or None)

        avg_stars = overview.get("average_stars")
        set_num("avg_stars", avg_stars)

        dominant_polarity, dominant_mood = self._sentiment_summary(agent_id)
        sentiment_label = POLARITY_LABEL_NL.get(dominant_polarity) if dominant_polarity else None
        set_var("avg_sentiment_label", sentiment_label or _sentiment_label(avg_stars), required=False)
        set_var("dominant_mood", dominant_mood, required=False)

        message_timestamps = all_message_timestamps(conversations)
        set_var("date_range", format_date_range(message_timestamps) if message_timestamps else None)

        messages_total = sum(item.total_messages for item in metrics) if metrics else sum(
            len(conversation.messages) for conversation in conversations
        )
        set_var("messages_total", messages_total or None)

        referred_count = sum(
            1
            for conversation in conversations
            if _conversation_referred(conversation)
        )
        resolved_count = sum(
            1
            for conversation in conversations
            if conversation.resolved is True and not _conversation_referred(conversation)
        )
        set_num("pct_resolved", _pct(resolved_count, total_conversations), digits=0)
        set_num("pct_referred", _pct(referred_count, total_conversations), digits=0)

        start_stars = [item.start_stars for item in metrics if item.start_stars is not None]
        end_stars = [item.end_stars for item in metrics if item.end_stars is not None]
        deltas = [item.delta_stars for item in metrics if item.delta_stars is not None]
        avg_start = statistics.mean(start_stars) if start_stars else None
        avg_end = statistics.mean(end_stars) if end_stars else None
        set_num("avg_start_stars", avg_start)
        set_num("avg_end_stars", avg_end)
        set_num("avg_delta_stars", statistics.mean(deltas) if deltas else None)

        channel_counts = Counter(_normalize_channel(conversation.integration_type) for conversation in conversations)
        for channel in ("whatsapp", "chat", "instagram"):
            set_var(f"channel_{channel}_count", channel_counts.get(channel, 0), required=False)
            set_num(f"channel_{channel}_pct", _pct(channel_counts.get(channel, 0), total_conversations), digits=0)

        channel_sentiments = _channel_sentiments(conversations, metrics_by_conversation)
        for channel in ("whatsapp", "chat", "instagram"):
            set_num(f"sentiment_{channel}", channel_sentiments.get(channel), required=False)

        lowest_channel, lowest_score = _lowest_channel(channel_sentiments)
        set_var("lowest_sentiment_channel", CHANNEL_LABELS.get(lowest_channel, lowest_channel or "—"), required=False)
        set_num("lowest_sentiment_score", lowest_score, required=False)

        dom_channel = dominant_channel(dict(channel_counts))
        set_var("dominant_channel", dom_channel or "—", required=False)
        if dom_channel:
            dom_key = next((k for k, label in CHANNEL_LABELS.items() if label == dom_channel), None)
            if dom_key:
                set_num(
                    "dominant_channel_pct",
                    _pct(channel_counts.get(dom_key, 0), total_conversations),
                    digits=0,
                    required=False,
                )
            else:
                variables["dominant_channel_pct"] = "—"
        else:
            variables["dominant_channel_pct"] = "—"

        high_channel, high_score = highest_sentiment_channel(channel_sentiments)
        set_var("highest_sentiment_channel", high_channel or "—", required=False)
        set_num("highest_sentiment_score", high_score, required=False)

        daily_counts = daily_conversation_counts(conversations)
        hour_counts = hourly_conversation_counts(conversations)
        chart_source = "local"
        daily_counts, hour_counts, chart_source = apply_momants_stats_fallback(
            agent_id,
            message_timestamps,
            daily_counts,
            hour_counts,
            chart_source,
        )

        peak_day_dt: datetime | None = None
        if daily_counts:
            peak_day_dt, peak_count = max(daily_counts.items(), key=lambda item: item[1])
            set_var("peak_day_name", format_short_date(peak_day_dt))
            set_var("peak_day_count", peak_count)
            set_var("peak_day_label", DUTCH_WEEKDAYS[peak_day_dt.weekday()])
            set_num("avg_conversations_per_day", total_conversations / len(daily_counts), digits=1)
        else:
            for key in ("peak_day_name", "peak_day_count", "peak_day_label", "avg_conversations_per_day"):
                missing.append(key)
                variables[key] = "—"

        hourly_avg = hourly_conversation_averages(conversations) if conversations else {}
        if hour_counts and not hourly_avg:
            days = max(len(daily_counts), 1)
            hourly_avg = {hour: round(count / days, 1) for hour, count in hour_counts.items()}

        peak_hour_int: int | None = None
        if hourly_avg:
            peak_hour_int = max(hourly_avg, key=hourly_avg.get)
            set_var("peak_hour", f"{peak_hour_int:02d}:00")
            set_num("peak_hour_avg", hourly_avg[peak_hour_int], digits=1)
            set_var("peak_hour_range", peak_hour_range(peak_hour_int), required=False)
            set_var("peak_period_label", peak_period_label(peak_hour_int), required=False)
        elif hour_counts:
            peak_hour_int = max(hour_counts, key=hour_counts.get)
            set_var("peak_hour", f"{peak_hour_int:02d}:00")
            set_num("peak_hour_avg", hour_counts[peak_hour_int], digits=1)
            set_var("peak_hour_range", peak_hour_range(peak_hour_int), required=False)
            set_var("peak_period_label", peak_period_label(peak_hour_int), required=False)
        else:
            for key in ("peak_hour", "peak_hour_avg", "peak_hour_range"):
                missing.append(key)
                variables[key] = "—"
            variables["peak_period_label"] = "piek"

        set_num("pct_trajectory_improving", overview.get("improving_pct"), digits=0, required=False)
        set_num("pct_trajectory_declining", overview.get("worsening_pct"), digits=0, required=False)
        set_num("pct_trajectory_mixed", overview.get("mixed_pct"), digits=0, required=False)

        breakdown = overview.get("unanswered_breakdown") or {}
        total_unanswered = sum(breakdown.values()) if breakdown else len(unanswered)
        set_var("total_unanswered_questions", total_unanswered, required=False)
        set_num("pct_unanswered", overview.get("unanswered_pct"), digits=0, required=False)
        set_var("unanswered_no_reply_count", breakdown.get("no_reply", 0), required=False)
        set_var("unanswered_weak_answer_count", breakdown.get("weak_answer", 0), required=False)
        set_var("unanswered_semantic_count", breakdown.get("not_answered", 0), required=False)

        examples = [item.question_text.strip() for item in unanswered if item.question_text.strip()]
        set_var(
            "unanswered_insight",
            _build_unanswered_insight(
                breakdown,
                total_unanswered,
                overview.get("unanswered_pct"),
                examples[0] if examples else "",
            ),
            required=False,
        )
        set_var(
            "answered_questions_insight",
            _build_answered_questions_insight(answered_ranked),
            required=False,
        )

        depth = overview.get("depth_distribution") or {}
        depth_total = sum(depth.values()) or 1
        set_num("pct_depth_shallow", 100 * depth.get("shallow", 0) / depth_total, digits=0, required=False)
        set_num("pct_depth_medium", 100 * depth.get("medium", 0) / depth_total, digits=0, required=False)
        set_num("pct_depth_deep", 100 * depth.get("deep", 0) / depth_total, digits=0, required=False)

        first_responses = [item.first_response_seconds for item in metrics if item.first_response_seconds is not None]
        set_var("median_response_fmt", _format_duration(overview.get("median_response_seconds")))
        set_var(
            "avg_first_response_fmt",
            _format_duration(statistics.mean(first_responses) if first_responses else None),
        )
        set_var("p95_response_fmt", _format_duration(overview.get("p95_response_seconds")))

        depth_ratios = [item.depth_ratio for item in metrics if item.depth_ratio is not None]
        set_num("avg_depth_ratio", statistics.mean(depth_ratios) if depth_ratios else None, digits=1, required=False)

        for key in TEMPLATE_VARS:
            variables.setdefault(key, "—")

        channel_fragments = build_channel_fragments(dict(channel_counts), channel_sentiments, total_conversations)

        arc = aggregate_sentiment_arc(metrics)
        opportunity_examples = _select_opportunity_examples(unanswered, limit=4)
        fragments = {
            "chart_slide2_inner": daily_volume_chart_svg(daily_counts, peak_day_dt),
            "chart_slide3_inner": hourly_bars_chart_svg(hour_counts, peak_hour_int),
            "chart_slide4_inner": sentiment_arc_chart_svg(arc, avg_start, avg_end),
            "unanswered_examples_page1": _render_unanswered_examples(examples, 0, 18),
            "unanswered_examples_page2": _render_unanswered_examples(examples, 18, 18),
            "answered_questions_grid": _render_answered_questions(
                answered_ranked, 0, 18, compact=True, tiny=True
            ),
            "opportunity_cards": _render_opportunity_cards(opportunity_examples),
            **channel_fragments,
        }

        return {
            "agent_id": agent_id,
            "event_name": resolved_name,
            "variables": variables,
            "fragments": fragments,
            "missing": sorted(set(missing)),
            "static_sections": [],
            "charts_generated": bool(daily_counts or hour_counts or arc),
            "chart_source": chart_source,
        }

    def _sentiment_summary(self, agent_id: str) -> tuple[str | None, str | None]:
        """Return (dominant_polarity, dominant_mood_nl) across the agent's member-message sentiment.

        Polarity drives the headline label (most common of positive/neutral/negative). The mood is
        the most common top emotion, excluding the catch-all "neutral" so a meaningful feeling
        surfaces (falls back to "neutraal" only when nothing else is present).
        """
        base_join = (
            select(SentimentAnalysis.polarity, func.count())
            .join(Message, Message.id == SentimentAnalysis.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.agent_id == agent_id, SentimentAnalysis.polarity.is_not(None))
            .group_by(SentimentAnalysis.polarity)
        )
        polarity_counts = {polarity: count for polarity, count in self.db.execute(base_join).all()}
        dominant_polarity = max(polarity_counts, key=polarity_counts.get) if polarity_counts else None

        emotion_jsons = self.db.scalars(
            select(SentimentAnalysis.emotions_json)
            .join(Message, Message.id == SentimentAnalysis.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.agent_id == agent_id, SentimentAnalysis.emotions_json.is_not(None))
        ).all()

        mood_counter: Counter[str] = Counter()
        neutral_count = 0
        for raw in emotion_jsons:
            try:
                emotions = json.loads(raw) if raw else []
            except (ValueError, TypeError):
                continue
            if not isinstance(emotions, list) or not emotions:
                continue
            top = str(emotions[0].get("label", "")).lower()
            if not top:
                continue
            if top == "neutral":
                neutral_count += 1
            else:
                mood_counter[top] += 1

        if mood_counter:
            dominant_mood_key = mood_counter.most_common(1)[0][0]
        elif neutral_count:
            dominant_mood_key = "neutral"
        else:
            dominant_mood_key = None
        dominant_mood = EMOTION_LABEL_NL.get(dominant_mood_key, dominant_mood_key) if dominant_mood_key else None

        return dominant_polarity, dominant_mood

    def render_html(self, agent_id: str, event_name: str | None = None) -> str:
        context = self.build_context(agent_id, event_name)
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        html = template
        for key, value in context["variables"].items():
            html = html.replace(f"{{{{{key}}}}}", _escape_html(value))
        for key, value in context["fragments"].items():
            html = html.replace(f"{{{{{key}}}}}", value)
        leftover = sorted(set(re.findall(r"\{\{([a-z_0-9]+)\}\}", html)))
        if leftover:
            raise RuntimeError(f"Unresolved template variables: {', '.join(leftover)}")
        return html

    def render_pdf(self, agent_id: str, event_name: str | None = None) -> bytes:
        """Render the report HTML and convert it to PDF via the Gotenberg (Chromium) service."""
        html = self.render_html(agent_id, event_name)
        settings = get_settings()
        url = f"{settings.gotenberg_url.rstrip('/')}/forms/chromium/convert/html"
        files = {"files": ("index.html", html.encode("utf-8"), "text/html")}
        # printBackground keeps the styled background/colours; waitDelay lets the external
        # Google Fonts load before Chromium captures the page.
        data = {
            "printBackground": "true",
            "preferCssPageSize": "true",
            # Allow the external fonts + Lucide icon script to finish rendering before capture.
            "waitDelay": "3s",
        }
        with httpx.Client(timeout=settings.gotenberg_timeout_seconds) as client:
            response = client.post(url, files=files, data=data)
            response.raise_for_status()
            return response.content


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _normalize_channel(integration_type: str | None) -> str:
    if not integration_type:
        return "chat"
    lowered = integration_type.lower()
    if "whatsapp" in lowered or lowered in {"wa"}:
        return "whatsapp"
    if "instagram" in lowered or lowered in {"ig"}:
        return "instagram"
    return "chat"


def _pct(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return round(100 * part / whole, 1)


def _sentiment_label(stars: float | None) -> str:
    if stars is None:
        return "onbekend"
    if stars >= 4.0:
        return "positief"
    if stars >= 3.0:
        return "neutraal"
    return "negatief"


# Dominant polarity -> Dutch sentiment word for the headline (more robust than the avg-stars
# threshold, which clusters around 3.0 and flips to "negatief" on the slightest skew).
POLARITY_LABEL_NL = {"positive": "positief", "neutral": "neutraal", "negative": "negatief"}


def _channel_sentiments(
    conversations: list[Conversation],
    metrics_by_conversation: dict[int, ConversationMetrics],
) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for conversation in conversations:
        metric = metrics_by_conversation.get(conversation.id)
        if metric is None or metric.avg_stars is None:
            continue
        grouped[_normalize_channel(conversation.integration_type)].append(metric.avg_stars)
    return {channel: round(statistics.mean(values), 1) for channel, values in grouped.items() if values}


def _lowest_channel(channel_sentiments: dict[str, float]) -> tuple[str | None, float | None]:
    if not channel_sentiments:
        return None, None
    channel = min(channel_sentiments, key=channel_sentiments.get)
    return channel, channel_sentiments[channel]


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m" if minutes < 10 else f"{minutes:.0f}m"
    hours = seconds / 3600
    return f"{hours:.1f}u" if hours < 10 else f"{hours:.0f}u"


def _conversation_referred(conversation: Conversation) -> bool:
    return any(
        message.from_agent and EMAIL_RE.search(message.content or "")
        for message in conversation.messages
    )


def _render_unanswered_examples(examples: list[str], start: int, count: int) -> str:
    cells: list[str] = []
    for offset in range(count):
        idx = start + offset
        text = _truncate(examples[idx], 100) if idx < len(examples) else "Geen voorbeeld beschikbaar"
        cells.append(
            f'<div class="q-cell"><span class="q-text">"{_escape_html(text)}"</span></div>'
        )
    return "\n          ".join(cells)


def _is_substantive_question(text: str) -> bool:
    cleaned = text.strip()
    if len(cleaned) < 35:
        return False
    if TRIVIAL_QUESTION_RE.match(cleaned):
        return False
    return True


def _select_opportunity_examples(
    unanswered: list[UnansweredQuestion],
    *,
    limit: int = 4,
) -> list[UnansweredQuestion]:
    candidates = [
        item
        for item in unanswered
        if item.question_text.strip() and _is_substantive_question(item.question_text)
    ]
    if not candidates:
        candidates = [item for item in unanswered if item.question_text.strip()]

    def sort_key(item: UnansweredQuestion) -> tuple:
        status_rank = STATUS_PRIORITY.get(item.status, 99)
        reply_bonus = 0 if (item.agent_reply_text or "").strip() else 1
        similarity = item.similarity_score if item.similarity_score is not None else 1.0
        return (status_rank, reply_bonus, -len(item.question_text), similarity)

    candidates.sort(key=sort_key)
    return candidates[:limit]


def _render_opportunity_cards(examples: list[UnansweredQuestion]) -> str:
    cards: list[str] = []
    for index in range(4):
        if index < len(examples):
            item = examples[index]
            question = _escape_html(_truncate(item.question_text.strip(), 160))
            reply = (item.agent_reply_text or "").strip()
            if reply:
                answer = _escape_html(_truncate(reply, 220))
            elif item.status == "no_reply":
                answer = "Geen antwoord gegeven"
            else:
                answer = "—"
            cards.append(
                f'<div class="opportunity">'
                f'<div class="olbl">Vraag</div>'
                f'<div class="oquestion">"{question}"</div>'
                f'<div class="olbl">Huidig antwoord</div>'
                f'<div class="oanswer">{answer}</div>'
                f'<div class="olbl">Nieuw antwoord</div>'
                f'<div class="oblank"></div>'
                f"</div>"
            )
        else:
            cards.append(
                '<div class="opportunity">'
                '<div class="olbl">Vraag</div>'
                '<div class="oquestion">&nbsp;</div>'
                '<div class="olbl">Huidig antwoord</div>'
                '<div class="oanswer">&nbsp;</div>'
                '<div class="olbl">Nieuw antwoord</div>'
                '<div class="oblank"></div>'
                "</div>"
            )
    return "\n      ".join(cards)


def _build_unanswered_insight(
    breakdown: dict[str, int],
    total_unanswered: int,
    unanswered_pct: float | None,
    top_example: str,
) -> str:
    if total_unanswered <= 0:
        return "Geen onbeantwoorde vragen in deze periode — de agent beantwoordt alles wat mensen vroegen."

    weak = breakdown.get("weak_answer", 0)
    no_reply = breakdown.get("no_reply", 0)
    semantic = breakdown.get("not_answered", 0)
    breakdown_total = weak + no_reply + semantic or total_unanswered

    categories = [
        (weak, "onvoldoende respons", "Verrijk de kennisbank zodat de agent de vraag volledig afdekt."),
        (no_reply, "geen reactie", "Zet vaste flows of escalatie op zodat geen enkele vraag onbeantwoord blijft."),
        (semantic, "antwoord miste de kern", "Train de agent om de kern van de vraag te herkennen en direct te beantwoorden."),
    ]
    dominant_count, label, advice = max(categories, key=lambda item: item[0])
    dominant_pct = round(100 * dominant_count / breakdown_total) if breakdown_total else 0
    pct_label = format_report_num(unanswered_pct, 0) if unanswered_pct is not None else "—"

    parts = [
        f"{dominant_count} van {total_unanswered} onbeantwoorde vragen ({dominant_pct}%) hadden een {label}",
        f"— dat is {pct_label}% van alle vragen van mensen.",
        advice,
    ]
    if top_example.strip():
        parts.append(f'Voorbeeld: "{_truncate(top_example, 90)}".')
    return " ".join(parts)


def _normalize_question_key(text: str) -> str:
    stripped = re.sub(r"[^\w\s]", "", text.lower(), flags=re.UNICODE)
    return " ".join(stripped.split())


def _rank_answered_questions(
    conversations: list[Conversation],
    unanswered_message_ids: set[int],
    *,
    limit: int = 18,
) -> list[tuple[str, int]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for conversation in conversations:
        for message in conversation.messages:
            if message.from_agent or message.id in unanswered_message_ids:
                continue
            if not is_question(message.content):
                continue
            key = _normalize_question_key(message.content)
            if not key:
                continue
            groups[key].append(" ".join(message.content.split()))

    ranked: list[tuple[str, int]] = []
    for texts in groups.values():
        rep = min(texts, key=len)
        ranked.append((rep, len(texts)))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked[:limit]


def _render_answered_questions(
    ranked: list[tuple[str, int]],
    start: int = 0,
    count: int = 18,
    *,
    compact: bool = False,
    tiny: bool = False,
) -> str:
    cells: list[str] = []
    cluster_class = "cluster"
    if compact:
        cluster_class += " compact"
    if tiny:
        cluster_class += " tiny"
    max_text = 70 if tiny else 100
    for offset in range(count):
        index = start + offset
        if index < len(ranked):
            text, freq = ranked[index]
            top_class = " top" if index == 0 else ""
            cells.append(
                f'<div class="{cluster_class}{top_class}">'
                f'<div class="chead">'
                f'<span class="clbl"><i data-lucide="message-circle-question" class="ic"></i> '
                f"Vraag #{index + 1}</span>"
                f'<span class="badge">{freq}×</span>'
                f"</div>"
                f'<div class="ctext">"{_escape_html(_truncate(text, max_text))}"</div>'
                f"</div>"
            )
        else:
            cells.append(
                f'<div class="{cluster_class}">'
                '<div class="chead"><span class="clbl">—</span></div>'
                '<div class="ctext">Geen voorbeeld beschikbaar</div>'
                "</div>"
            )
    return "\n        ".join(cells)


def _build_answered_questions_insight(ranked: list[tuple[str, int]]) -> str:
    if not ranked:
        return "Geen terugkerende vragen met een bevestigd antwoord in deze periode."

    top_text, top_count = ranked[0]
    total_occurrences = sum(count for _, count in ranked)
    unique_count = len(ranked)
    return (
        f"De meest gestelde vraag (“{_truncate(top_text, 80)}”) kwam {top_count}× voor "
        f"en werd door de agent beantwoord. In totaal {unique_count} unieke vragen "
        f"({total_occurrences}×) werden goed afgehandeld — sterke kandidaten voor FAQ en agent-training."
    )


def _truncate(value: str, max_len: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"
