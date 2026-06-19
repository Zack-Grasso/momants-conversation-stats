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
    build_channel_volume_slides_html,
    build_office_hours_channel_slides_html,
    build_office_hours_page_html,
    daily_volume_chart_svg,
    hourly_bars_chart_svg,
    office_hours_pie_chart_svg,
    sentiment_arc_chart_svg,
)
from app.utils.report_data import (
    CHANNEL_LABELS,
    EMOTION_LABEL_NL,
    active_channels,
    aggregate_sentiment_arc,
    apply_momants_stats_fallback,
    build_bereikbaarheid_insight,
    build_channel_fragments,
    build_channel_timing_insight,
    build_channel_timing_intro,
    build_channel_timing_stats_html,
    build_channel_volume_insight,
    conversation_time_buckets,
    conversation_time_buckets_by_channel,
    daily_conversation_counts,
    daily_conversation_counts_by_channel,
    dominant_channel,
    fetch_momants_report_stats,
    highest_sentiment_channel,
    hourly_conversation_averages,
    hourly_conversation_counts,
    hourly_conversation_counts_by_channel,
    normalize_integration_channel,
    MomantsReportStats,
    peak_hour_range,
    peak_period_label,
)
from app.utils.question_utils import is_question
from app.utils.report_format import (
    DUTCH_WEEKDAYS,
    all_message_timestamps,
    format_date_range,
    format_dutch_int,
    format_eur,
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
SILLY_QUESTION_RE = re.compile(
    r"\b(kikker|kwak|duif|pigeon|frog|grappig|ik ben een|speel je|pretend)\b|"
    r"in die taal uitleggen",
    re.I,
)
OFF_TOPIC_TECH_RE = re.compile(
    r"\b(\.net|c#|python|javascript|dictionary|programming|code\b)\b",
    re.I,
)
FESTIVAL_TOPIC_RE = re.compile(
    r"\b(ticket|korting|discount|festival|reis|parkeren|camp|line.?up|programma|"
    r"entree|toegang|bus|trein|hotel|presale|voorverkoop|order)\b",
    re.I,
)
MISDIRECTED_TOPIC_RE = re.compile(r"\b(aangifte|belasting|tax return)\b", re.I)
URL_RE = re.compile(r"https?://\S+")
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
    "stats_conversations_total",
    "stats_hours_saved",
    "stats_support_cost_saved",
    "stats_assisted_revenue",
    "stats_direct_revenue",
    "stats_total_value",
    "stats_pct_outside_office",
    "avg_messages_per_conversation",
    "page2_channels_summary",
    "count_resolved",
    "count_referred",
    "count_other_handling",
    "stats_support_hourly_rate",
    "stats_support_calc_detail",
    "stats_assisted_revenue_detail",
    "stats_total_value_detail",
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
        other_handling_count = max(total_conversations - resolved_count - referred_count, 0)
        set_var("count_resolved", format_dutch_int(resolved_count), required=False)
        set_var("count_referred", format_dutch_int(referred_count), required=False)
        set_var("count_other_handling", format_dutch_int(other_handling_count), required=False)

        if total_conversations and messages_total:
            set_num(
                "avg_messages_per_conversation",
                messages_total / total_conversations,
                digits=1,
                required=False,
            )
        else:
            variables["avg_messages_per_conversation"] = "—"

        start_stars = [item.start_stars for item in metrics if item.start_stars is not None]
        end_stars = [item.end_stars for item in metrics if item.end_stars is not None]
        deltas = [item.delta_stars for item in metrics if item.delta_stars is not None]
        avg_start = statistics.mean(start_stars) if start_stars else None
        avg_end = statistics.mean(end_stars) if end_stars else None
        set_num("avg_start_stars", avg_start)
        set_num("avg_end_stars", avg_end)
        set_num("avg_delta_stars", statistics.mean(deltas) if deltas else None)

        channel_counts = Counter(normalize_integration_channel(conversation.integration_type) for conversation in conversations)
        set_var(
            "page2_channels_summary",
            _page2_channel_summary(dict(channel_counts), total_conversations),
            required=False,
        )
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
        daily_by_channel = daily_conversation_counts_by_channel(conversations)
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

        if message_timestamps:
            momants_stats = fetch_momants_report_stats(
                agent_id, min(message_timestamps), max(message_timestamps)
            )
        else:
            momants_stats = MomantsReportStats()

        stats_conv_raw = momants_stats.conversations_total or total_conversations
        if stats_conv_raw:
            set_var("stats_conversations_total", format_dutch_int(stats_conv_raw), required=False)
        else:
            variables["stats_conversations_total"] = "—"

        if momants_stats.hours_saved is not None:
            set_var("stats_hours_saved", format_dutch_int(momants_stats.hours_saved), required=False)
        else:
            variables["stats_hours_saved"] = "—"

        set_var("stats_support_cost_saved", format_eur(momants_stats.support_cost_saved, compact=False), required=False)
        set_var("stats_assisted_revenue", format_eur(momants_stats.assisted_revenue), required=False)
        set_var("stats_assisted_revenue_detail", format_eur(momants_stats.assisted_revenue, compact=False), required=False)
        set_var("stats_direct_revenue", format_eur(momants_stats.direct_revenue), required=False)
        total_value = momants_stats.total_value_creation
        set_var("stats_total_value", format_eur(total_value) if total_value is not None else None, required=False)
        set_var(
            "stats_total_value_detail",
            format_eur(total_value, compact=False) if total_value is not None else None,
            required=False,
        )
        set_num("stats_pct_outside_office", momants_stats.pct_outside_office, digits=1, required=False)

        hours_saved = momants_stats.hours_saved
        support_cost = momants_stats.support_cost_saved
        if hours_saved and support_cost and hours_saved > 0:
            hourly_rate = support_cost / hours_saved
            rate_fmt = format_eur(hourly_rate, compact=False)
            cost_fmt = format_eur(support_cost, compact=False)
            hours_fmt = format_dutch_int(hours_saved)
            set_var("stats_support_hourly_rate", f"{rate_fmt}/uur", required=False)
            set_var(
                "stats_support_calc_detail",
                f"{hours_fmt} uren × {rate_fmt}/uur = {cost_fmt}",
                required=False,
            )
        else:
            variables["stats_support_hourly_rate"] = "—"
            variables["stats_support_calc_detail"] = "—"

        for key in TEMPLATE_VARS:
            variables.setdefault(key, "—")

        channel_fragments = build_channel_fragments(dict(channel_counts), channel_sentiments, total_conversations)
        hourly_by_channel = hourly_conversation_counts_by_channel(conversations)
        all_days = sorted(daily_counts.keys())
        channel_volume_insight_text = build_channel_volume_insight(daily_by_channel, dict(channel_counts))
        set_var("channel_volume_insight", channel_volume_insight_text, required=False)
        set_var(
            "channels_timing_intro",
            build_channel_timing_intro(dict(channel_counts), total_conversations),
            required=False,
        )
        set_var(
            "channels_timing_insight",
            build_channel_timing_insight(
                dict(channel_counts),
                hourly_by_channel,
                peak_hour_int,
                hourly_avg.get(peak_hour_int) if peak_hour_int is not None and hourly_avg else None,
                total_conversations,
            ),
            required=False,
        )

        arc = aggregate_sentiment_arc(metrics)
        time_buckets = conversation_time_buckets(conversations)
        time_buckets_by_channel = conversation_time_buckets_by_channel(conversations)
        set_var(
            "bereikbaarheid_insight",
            build_bereikbaarheid_insight(time_buckets, time_buckets_by_channel, dict(channel_counts)),
            required=False,
        )

        active_channel_count = len(active_channels(dict(channel_counts)))
        total_pages = 9 + (2 * active_channel_count)
        channel_volume_start = 4
        channels_timing_page = 3 + active_channel_count + 1
        bereikbaarheid_page = channels_timing_page + 1
        bereikbaarheid_channels_start = bereikbaarheid_page + 1
        sentiment_page = bereikbaarheid_channels_start + active_channel_count
        unanswered_1_page = sentiment_page + 1
        unanswered_2_page = unanswered_1_page + 1
        value_page = unanswered_2_page + 1

        set_var("report_page_total", str(total_pages), required=False)
        set_var("page_num_overview", "2", required=False)
        set_var("page_num_total_volume", "3", required=False)
        set_var("page_num_channels_timing", str(channels_timing_page), required=False)
        set_var("page_num_bereikbaarheid", str(bereikbaarheid_page), required=False)
        set_var("page_num_sentiment", str(sentiment_page), required=False)
        set_var("page_num_unanswered_1", str(unanswered_1_page), required=False)
        set_var("page_num_unanswered_2", str(unanswered_2_page), required=False)
        set_var("page_num_value", str(value_page), required=False)

        date_range_text = variables.get("date_range", "—")
        fragments = {
            "channel_volume_slides": build_channel_volume_slides_html(
                daily_by_channel,
                all_days,
                dict(channel_counts),
                event_name=resolved_name,
                date_range=date_range_text,
                insight=channel_volume_insight_text,
                page_start=channel_volume_start,
                total_pages=total_pages,
            ),
            "office_hours_channel_slides": build_office_hours_channel_slides_html(
                time_buckets_by_channel,
                dict(channel_counts),
                event_name=resolved_name,
                date_range=date_range_text,
                page_start=bereikbaarheid_channels_start,
                total_pages=total_pages,
            ),
            "channel_timing_stats": build_channel_timing_stats_html(
                dict(channel_counts),
                hourly_by_channel,
                len(all_days),
                peak_hour_int,
                hourly_avg.get(peak_hour_int) if peak_hour_int is not None and hourly_avg else None,
                total_conversations,
            ),
            "office_hours_charts": build_office_hours_page_html(
                time_buckets, time_buckets_by_channel, dict(channel_counts)
            ),
            "chart_slide2_inner": daily_volume_chart_svg(daily_counts, peak_day_dt),
            "chart_slide3_inner": hourly_bars_chart_svg(hour_counts, peak_hour_int),
            "chart_slide4_inner": sentiment_arc_chart_svg(arc, avg_start, avg_end),
            "chart_slide5_inner": office_hours_pie_chart_svg(time_buckets),
            "unanswered_examples_page1": _render_unanswered_examples(examples, 0, 18),
            "unanswered_examples_page2": _render_unanswered_examples_page2(examples),
            "answered_questions_grid": _render_answered_questions(
                answered_ranked, 0, 18, compact=True, tiny=True
            ),
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
        return apply_report_template(context)

    def render_pdf(self, agent_id: str, event_name: str | None = None) -> bytes:
        """Render the report HTML and convert it to PDF via the Gotenberg (Chromium) service."""
        from app.utils.report_pdf import html_to_pdf

        return html_to_pdf(self.render_html(agent_id, event_name))


def apply_report_template(context: dict, *, template_path: Path | None = None) -> str:
    """Fill the report HTML template with variables and HTML fragments."""
    template = (template_path or TEMPLATE_PATH).read_text(encoding="utf-8")
    html = template
    for key, value in context["variables"].items():
        html = html.replace(f"{{{{{key}}}}}", _escape_html(value))
    for key, value in context["fragments"].items():
        html = html.replace(f"{{{{{key}}}}}", value)
    leftover = sorted(set(re.findall(r"\{\{([a-z_0-9]+)\}\}", html)))
    if leftover:
        raise RuntimeError(f"Unresolved template variables: {', '.join(leftover)}")
    return html


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _normalize_channel(integration_type: str | None) -> str:
    return normalize_integration_channel(integration_type)


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
        grouped[normalize_integration_channel(conversation.integration_type)].append(metric.avg_stars)
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


def _page2_channel_summary(channel_counts: dict[str, int], total: int) -> str:
    if not total:
        return "—"
    parts: list[str] = []
    for key in active_channels(channel_counts):
        count = channel_counts.get(key, 0)
        pct = round(100 * count / total)
        parts.append(f"{CHANNEL_LABELS[key]} {format_dutch_int(count)} ({pct}%)")
    return " · ".join(parts) if parts else "—"


def _render_unanswered_examples(examples: list[str], start: int, count: int) -> str:
    cells: list[str] = []
    for offset in range(count):
        idx = start + offset
        if idx >= len(examples):
            break
        text = _truncate(examples[idx], 100)
        cells.append(
            f'<div class="q-cell"><span class="q-text">"{_escape_html(text)}"</span></div>'
        )
    return "\n          ".join(cells)


def _render_unanswered_examples_page2(examples: list[str], start: int = 18, count: int = 18) -> str:
    cells: list[str] = []
    for offset in range(count):
        idx = start + offset
        if idx >= len(examples):
            break
        text = _truncate(examples[idx], 100)
        cells.append(
            f'<div class="q-cell"><span class="q-text">"{_escape_html(text)}"</span></div>'
        )

    if len(examples) <= start or len(examples) < start + count:
        cells.append(
            '<div class="q-cell q-remainder">Alle overige vragen zijn beantwoord.</div>'
        )

    return "\n          ".join(cells)


def _is_substantive_question(text: str) -> bool:
    cleaned = text.strip()
    if len(cleaned) < 35:
        return False
    if TRIVIAL_QUESTION_RE.match(cleaned):
        return False
    return True


def _is_silly_exchange(question: str, reply: str) -> bool:
    if SILLY_QUESTION_RE.search(question):
        return True
    if re.search(r"\bkwak\b", reply or "", re.I):
        return True
    return False


def _opportunity_sort_key(item: UnansweredQuestion) -> tuple:
    question = item.question_text.strip()
    reply = (item.agent_reply_text or "").strip()
    if _is_silly_exchange(question, reply):
        return (999, 999, 999, 999, 999, 0, 1.0)

    status_rank = STATUS_PRIORITY.get(item.status, 99)
    topic_bonus = 0 if FESTIVAL_TOPIC_RE.search(question) else 1
    off_topic_penalty = 1 if OFF_TOPIC_TECH_RE.search(question) else 0
    misdirected_penalty = 1 if MISDIRECTED_TOPIC_RE.search(question) else 0
    reply_bonus = 0 if reply else 1
    similarity = item.similarity_score if item.similarity_score is not None else 1.0
    return (status_rank, topic_bonus, off_topic_penalty, misdirected_penalty, reply_bonus, -len(question), similarity)


def _select_opportunity_examples(
    unanswered: list[UnansweredQuestion],
    *,
    limit: int = 4,
) -> list[UnansweredQuestion]:
    with_text = [item for item in unanswered if item.question_text.strip()]
    substantive = [item for item in with_text if _is_substantive_question(item.question_text)]
    serious = [item for item in substantive if not _is_silly_exchange(
        item.question_text, item.agent_reply_text or ""
    )]

    pool = serious or substantive or with_text
    ranked = sorted(pool, key=_opportunity_sort_key)
    return ranked[:limit]


def _sanitize_reply_for_display(text: str, max_len: int = 380) -> str:
    cleaned = " ".join(text.split())
    cleaned = URL_RE.sub("[link]", cleaned)
    return _truncate(cleaned, max_len)


def _render_opportunity_cards(
    examples: list[UnansweredQuestion],
    start: int,
    count: int,
) -> str:
    cards: list[str] = []
    for offset in range(count):
        idx = start + offset
        if idx >= len(examples):
            break
        item = examples[idx]
        question = _escape_html(_truncate(item.question_text.strip(), 200))
        reply = (item.agent_reply_text or "").strip()
        if reply:
            answer = _escape_html(_sanitize_reply_for_display(reply, max_len=1200))
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
            f"</div>"
        )
    return "\n      ".join(cards) if cards else ""


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
