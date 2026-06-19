from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.utils.unanswered_report import translate_questions_to_dutch
from app.utils.report_data import fetch_momants_report_stats
from app.utils.report_format import format_date_range, format_dutch_int, format_eur, resolve_event_name
from app.utils.report_html import (
    build_top_questions_insight,
    build_unanswered_insight,
    escape_html,
    render_examples_page2_slide,
    render_top_questions_grid,
    render_unanswered_examples,
    render_unanswered_examples_page2,
)
from app.utils.report_pdf import html_to_pdf
from app.weekly.models import WeeklyAgentRun
from app.weekly.services.analysis_service import WeeklyAnalysisService

logger = logging.getLogger(__name__)

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3] / "templates" / "unanswered-weekly-template.html"
)


class WeeklyReportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.analysis = WeeklyAnalysisService(db)

    def build_context(self, agent_run: WeeklyAgentRun) -> dict[str, str]:
        run = agent_run.weekly_run
        since, until = run.since, run.until
        event_name, _ = resolve_event_name(agent_run.agent_id, agent_run.agent_name)
        momants_stats = fetch_momants_report_stats(agent_run.agent_id, since, until)

        findings = self.analysis.load_findings(agent_run.id)
        clusters = self.analysis.load_clusters(agent_run.id)
        cluster_pairs = [(c.representative_text, c.count) for c in clusters]
        if cluster_pairs:
            translated = translate_questions_to_dutch([text for text, _ in cluster_pairs])
            cluster_pairs = [(translated[index], count) for index, (_, count) in enumerate(cluster_pairs)]

        breakdown = {"no_reply": 0, "weak_answer": 0, "not_answered": 0}
        for item in findings:
            if item.status in breakdown:
                breakdown[item.status] += 1
        total_flagged = sum(breakdown.values())

        from app.utils.question_utils import is_question

        member_questions = 0
        for conversation in getattr(agent_run, "conversations", []) or []:
            for message in conversation.messages:
                if not message.from_agent and is_question(message.content):
                    member_questions += 1
        pct = (100 * total_flagged / member_questions) if member_questions else 0.0

        conversations = getattr(agent_run, "conversations", []) or []
        if momants_stats.conversations_total is not None:
            conversations_total = int(momants_stats.conversations_total)
        else:
            conversations_total = len(conversations)

        example_questions = [f.question_text for f in findings]
        has_page2 = len(example_questions) > 18
        slide_count = "5" if has_page2 else "4"
        unanswered_insight = build_unanswered_insight(breakdown, total_flagged, pct, member_questions)
        date_range = format_date_range([since, until])
        page1_html = render_unanswered_examples(example_questions, 0, 18)
        page2_html = render_unanswered_examples_page2(example_questions) if has_page2 else ""

        variables = {
            "event_name": event_name,
            "date_range": date_range,
            "week_id": run.week_id,
            "stats_assisted_revenue": format_eur(momants_stats.assisted_revenue, compact=False),
            "stats_direct_revenue": format_eur(momants_stats.direct_revenue, compact=False),
            "stats_hours_saved": format_dutch_int(momants_stats.hours_saved)
            if momants_stats.hours_saved is not None
            else "—",
            "stats_support_cost_saved": format_eur(momants_stats.support_cost_saved, compact=False),
            "stats_total_value": format_eur(momants_stats.total_value_creation, compact=False)
            if momants_stats.total_value_creation is not None
            else "—",
            "top_questions_insight": build_top_questions_insight(cluster_pairs),
            "unanswered_insight": unanswered_insight,
            "conversations_total": format_dutch_int(conversations_total),
            "unanswered_no_reply": str(breakdown["no_reply"]),
            "unanswered_weak_answer": str(breakdown["weak_answer"]),
            "unanswered_not_answered": str(breakdown["not_answered"]),
            "slide_count": slide_count,
            "examples_page_label": "(1/2)" if has_page2 else "",
        }
        fragments = {
            "top_questions_grid": render_top_questions_grid(cluster_pairs),
            "unanswered_examples_page1": page1_html,
            "examples_page2_slide": render_examples_page2_slide(
                page2_html=page2_html,
                insight=unanswered_insight,
                event_name=event_name,
                date_range=date_range,
                slide_num="5",
                slide_count=slide_count,
            ),
        }
        return {"variables": variables, "fragments": fragments}

    def render_html(self, agent_run: WeeklyAgentRun) -> str:
        context = self.build_context(agent_run)
        html = TEMPLATE_PATH.read_text(encoding="utf-8")
        for key, value in context["variables"].items():
            html = html.replace(f"{{{{{key}}}}}", escape_html(value))
        for key, value in context["fragments"].items():
            html = html.replace(f"{{{{{key}}}}}", value)
        leftover = sorted(set(re.findall(r"\{\{([a-z_0-9]+)\}\}", html)))
        if leftover:
            raise RuntimeError(f"Unresolved weekly template variables: {', '.join(leftover)}")
        return html

    def render_pdf(self, agent_run: WeeklyAgentRun) -> bytes:
        return html_to_pdf(self.render_html(agent_run))

    def persist_value_stats(self, agent_run: WeeklyAgentRun) -> None:
        run = agent_run.weekly_run
        stats = fetch_momants_report_stats(agent_run.agent_id, run.since, run.until)
        agent_run.value_stats_json = json.dumps(
            {
                "assisted_revenue": stats.assisted_revenue,
                "direct_revenue": stats.direct_revenue,
                "hours_saved": stats.hours_saved,
                "support_cost_saved": stats.support_cost_saved,
            }
        )
