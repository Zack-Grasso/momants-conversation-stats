from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models.conversation import Conversation
from app.models.insights import ConversationMetrics, QuestionCluster, UnansweredQuestion
from app.services.insights_service import InsightsService
from app.utils.report_charts import (
    daily_volume_chart_svg,
    hourly_bars_chart_svg,
    sentiment_arc_chart_svg,
)
from app.utils.report_data import (
    CHANNEL_LABELS,
    RecommendationContext,
    active_channels,
    aggregate_sentiment_arc,
    apply_momants_stats_fallback,
    build_channel_fragments,
    build_recommendations,
    daily_conversation_counts,
    dominant_channel,
    highest_sentiment_channel,
    hourly_conversation_averages,
    hourly_conversation_counts,
    peak_hour_range,
)
from app.utils.report_format import (
    DUTCH_WEEKDAYS,
    all_message_timestamps,
    format_date_range,
    format_report_num,
    format_short_date,
    resolve_event_name,
)

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "conversation-analysis-template-v2.html"

TEMPLATE_VARS = [
    "event_name",
    "conversations_total",
    "avg_sentiment_label",
    "date_range",
    "messages_total",
    "pct_resolved",
    "avg_start_stars",
    "avg_end_stars",
    "channel_whatsapp_count",
    "channel_chat_count",
    "channel_instagram_count",
    "avg_stars",
    "avg_delta_stars",
    "pct_takeover",
    "peak_day_name",
    "peak_day_count",
    "peak_day_label",
    "avg_conversations_per_day",
    "peak_hour",
    "peak_hour_avg",
    "peak_hour_range",
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
    "unanswered_no_reply_count",
    "unanswered_weak_answer_count",
    "unanswered_semantic_count",
    "unanswered_example_1",
    "unanswered_example_2",
    "unanswered_example_3",
    "cluster_1_count",
    "cluster_1_text",
    "cluster_2_count",
    "cluster_2_text",
    "cluster_3_count",
    "cluster_3_text",
    "cluster_4_count",
    "cluster_4_text",
    "pct_depth_shallow",
    "pct_depth_medium",
    "pct_depth_deep",
    "median_response_fmt",
    "avg_first_response_fmt",
    "p95_response_fmt",
    "avg_depth_ratio",
    "conversations_takeover",
    "lowest_sentiment_channel",
    "lowest_sentiment_score",
    "action_cluster_body",
    "action_takeover_body",
    "action_peak_body",
    "action_channel_body",
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
                .options(selectinload(Conversation.messages))
            ).all()
        )
        metrics = list(
            self.db.scalars(select(ConversationMetrics).where(ConversationMetrics.agent_id == agent_id)).all()
        )
        metrics_by_conversation = {item.conversation_id: item for item in metrics}
        clusters = list(
            self.db.scalars(
                select(QuestionCluster)
                .where(QuestionCluster.agent_id == agent_id)
                .order_by(QuestionCluster.count.desc(), QuestionCluster.rank)
                .limit(4)
            ).all()
        )
        unanswered = list(
            self.db.scalars(
                select(UnansweredQuestion)
                .where(UnansweredQuestion.agent_id == agent_id)
                .order_by(UnansweredQuestion.computed_at.desc())
                .limit(50)
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
        set_var("avg_sentiment_label", _sentiment_label(avg_stars), required=False)

        message_timestamps = all_message_timestamps(conversations)
        set_var("date_range", format_date_range(message_timestamps) if message_timestamps else None)

        messages_total = sum(item.total_messages for item in metrics) if metrics else sum(
            len(conversation.messages) for conversation in conversations
        )
        set_var("messages_total", messages_total or None)

        resolved_count = sum(1 for conversation in conversations if conversation.resolved is True)
        takeover_count = sum(1 for conversation in conversations if conversation.takeover is True)
        set_num("pct_resolved", _pct(resolved_count, total_conversations), digits=0)
        set_num("pct_takeover", _pct(takeover_count, total_conversations), digits=0)
        set_var("conversations_takeover", takeover_count or None)

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
        elif hour_counts:
            peak_hour_int = max(hour_counts, key=hour_counts.get)
            set_var("peak_hour", f"{peak_hour_int:02d}:00")
            set_num("peak_hour_avg", hour_counts[peak_hour_int], digits=1)
            set_var("peak_hour_range", peak_hour_range(peak_hour_int), required=False)
        else:
            for key in ("peak_hour", "peak_hour_avg", "peak_hour_range"):
                missing.append(key)
                variables[key] = "—"

        set_num("pct_trajectory_improving", overview.get("improving_pct"), digits=0, required=False)
        set_num("pct_trajectory_declining", overview.get("worsening_pct"), digits=0, required=False)
        set_num("pct_trajectory_mixed", overview.get("mixed_pct"), digits=0, required=False)

        breakdown = overview.get("unanswered_breakdown") or {}
        total_unanswered = sum(breakdown.values()) if breakdown else len(unanswered)
        set_var("total_unanswered_questions", total_unanswered, required=False)
        set_var("unanswered_no_reply_count", breakdown.get("no_reply", 0), required=False)
        set_var("unanswered_weak_answer_count", breakdown.get("weak_answer", 0), required=False)
        set_var("unanswered_semantic_count", breakdown.get("not_answered", 0), required=False)

        examples = [item.question_text.strip() for item in unanswered if item.question_text.strip()]
        for index in range(3):
            key = f"unanswered_example_{index + 1}"
            if index < len(examples):
                set_var(key, _truncate(examples[index], 120), required=False)
            else:
                missing.append(key)
                variables[key] = "Geen voorbeeld beschikbaar"

        for index in range(4):
            prefix = f"cluster_{index + 1}"
            if index < len(clusters):
                cluster = clusters[index]
                set_var(f"{prefix}_count", cluster.count, required=False)
                set_var(f"{prefix}_text", _truncate(cluster.representative_text, 120), required=False)
            else:
                for suffix in ("count", "text"):
                    key = f"{prefix}_{suffix}"
                    missing.append(key)
                    variables[key] = "—"

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

        unanswered_breakdown = overview.get("unanswered_breakdown") or {}
        actions_html, actions_priority = build_recommendations(
            RecommendationContext(
                cluster_1_count=clusters[0].count if clusters else 0,
                cluster_1_text=_truncate(clusters[0].representative_text, 120) if clusters else "",
                no_reply=unanswered_breakdown.get("no_reply", 0),
                weak_answer=unanswered_breakdown.get("weak_answer", 0),
                takeover_count=takeover_count,
                total_conversations=total_conversations,
                peak_hour=variables["peak_hour"],
                peak_hour_range=variables["peak_hour_range"],
                peak_hour_avg=variables["peak_hour_avg"],
                lowest_channel=CHANNEL_LABELS.get(lowest_channel, lowest_channel) if lowest_channel else None,
                lowest_score=lowest_score,
                active_channel_count=len(active_channels(dict(channel_counts))),
                declining_pct=overview.get("worsening_pct") or 0,
                avg_stars=avg_stars,
            )
        )
        variables["actions_priority"] = actions_priority

        channel_fragments = build_channel_fragments(dict(channel_counts), channel_sentiments, total_conversations)

        arc = aggregate_sentiment_arc(metrics)
        fragments = {
            "chart_slide2_inner": daily_volume_chart_svg(daily_counts, peak_day_dt),
            "chart_slide3_inner": hourly_bars_chart_svg(hour_counts, peak_hour_int),
            "chart_slide4_inner": sentiment_arc_chart_svg(arc, avg_start, avg_end),
            "actions_html": actions_html,
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


def _truncate(value: str, max_len: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"
