import json
import statistics
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.models.conversation import Conversation, Message
from app.models.insights import ConversationMetrics, UnansweredQuestion
from app.utils.question_utils import is_question


class MetricsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def compute_for_conversation(self, conversation_id: int, tier1_only: bool = False) -> ConversationMetrics | None:
        conversation = self._load_conversation(conversation_id)
        if conversation is None:
            return None

        ordered = self._ordered_messages(conversation.messages)
        member_msgs = [m for m in ordered if not m.from_agent]
        agent_msgs = [m for m in ordered if m.from_agent]

        timeline, arc = self._sentiment_arc(member_msgs)
        response = self._response_metrics(ordered)
        depth = self._depth_metrics(ordered, member_msgs, agent_msgs)

        metrics = self.db.scalar(
            select(ConversationMetrics).where(ConversationMetrics.conversation_id == conversation.id)
        )
        if metrics is None:
            metrics = ConversationMetrics(conversation_id=conversation.id)
            self.db.add(metrics)

        metrics.agent_id = conversation.agent_id
        metrics.start_stars = arc["start_stars"]
        metrics.end_stars = arc["end_stars"]
        metrics.delta_stars = arc["delta_stars"]
        metrics.avg_stars = arc["avg_stars"]
        metrics.low_point_stars = arc["low_point_stars"]
        metrics.high_point_stars = arc["high_point_stars"]
        metrics.trajectory = arc["trajectory"]
        metrics.timeline_json = json.dumps(timeline)
        metrics.first_response_seconds = response["first_response_seconds"]
        metrics.median_response_seconds = response["median_response_seconds"]
        metrics.max_response_seconds = response["max_response_seconds"]
        metrics.unanswered_member_count = response["unanswered_member_count"]
        metrics.total_messages = depth["total_messages"]
        metrics.member_messages = depth["member_messages"]
        metrics.agent_messages = depth["agent_messages"]
        metrics.depth_ratio = depth["depth_ratio"]
        metrics.depth_bucket = depth["depth_bucket"]

        if tier1_only:
            counts = self._compute_tier1_unanswered(conversation, ordered)
            metrics.unanswered_question_count = counts["total"]
            metrics.unanswered_no_reply_count = counts["no_reply"]
            metrics.unanswered_weak_answer_count = 0
            metrics.unanswered_semantic_count = 0
            metrics.last_unanswered_question_text = counts["last_text"]

        metrics.computed_at = datetime.now().astimezone()
        self.db.flush()
        return metrics

    def _compute_tier1_unanswered(self, conversation: Conversation, ordered: list[Message]) -> dict:
        self.db.execute(
            delete(UnansweredQuestion).where(
                UnansweredQuestion.conversation_id == conversation.id,
                UnansweredQuestion.status == "no_reply",
            )
        )

        no_reply = 0
        last_text = None
        for index, message in enumerate(ordered):
            if message.from_agent or not is_question(message.content):
                continue
            has_reply = any(m.from_agent for m in ordered[index + 1 :])
            if has_reply:
                continue
            no_reply += 1
            last_text = message.content
            existing = self.db.scalar(
                select(UnansweredQuestion).where(UnansweredQuestion.message_id == message.id)
            )
            if existing is None:
                self.db.add(
                    UnansweredQuestion(
                        message_id=message.id,
                        conversation_id=conversation.id,
                        agent_id=conversation.agent_id,
                        question_text=message.content,
                        status="no_reply",
                    )
                )
            else:
                existing.status = "no_reply"
                existing.agent_reply_message_id = None
                existing.agent_reply_text = None
                existing.similarity_score = None
                existing.nli_label = None
                existing.nli_score = None

        return {"total": no_reply, "no_reply": no_reply, "last_text": last_text}

    def _load_conversation(self, conversation_id: int) -> Conversation | None:
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages).selectinload(Message.sentiment))
        )
        return self.db.scalar(stmt)

    def _ordered_messages(self, messages: list[Message]) -> list[Message]:
        return sorted(
            messages,
            key=lambda m: (m.source_created_at or m.created_at, m.id),
        )

    def _sentiment_arc(self, member_msgs: list[Message]) -> tuple[list[dict], dict]:
        timeline: list[dict] = []
        stars_list: list[int] = []
        for index, message in enumerate(member_msgs):
            stars = message.sentiment.stars if message.sentiment else 3
            stars_list.append(stars)
            timeline.append(
                {
                    "index": index,
                    "message_id": message.id,
                    "timestamp": (message.source_created_at or message.created_at).isoformat(),
                    "stars": stars,
                    "label": message.sentiment.label if message.sentiment else "NEUTRAL",
                    "content_preview": message.content[:120],
                }
            )

        if not stars_list:
            return timeline, {
                "start_stars": None,
                "end_stars": None,
                "delta_stars": None,
                "avg_stars": None,
                "low_point_stars": None,
                "high_point_stars": None,
                "trajectory": "mixed",
            }

        start = stars_list[0]
        end = stars_list[-1]
        delta = end - start
        trajectory = self._trajectory(start, end, delta, stars_list)
        return timeline, {
            "start_stars": start,
            "end_stars": end,
            "delta_stars": delta,
            "avg_stars": sum(stars_list) / len(stars_list),
            "low_point_stars": min(stars_list),
            "high_point_stars": max(stars_list),
            "trajectory": trajectory,
        }

    def _trajectory(self, start: int, end: int, delta: int, stars: list[int]) -> str:
        if delta >= 1 or (start <= 2 and end >= 4):
            return "improving"
        if delta <= -1 or (start >= 4 and end <= 2):
            return "worsening"
        if all(s >= 4 for s in stars):
            return "stable_positive"
        if all(s <= 2 for s in stars):
            return "stable_negative"
        if all(s == 3 for s in stars):
            return "stable_neutral"
        return "mixed"

    def _response_metrics(self, ordered: list[Message]) -> dict:
        gaps: list[float] = []
        unanswered_member = 0
        first_response = None

        for index, message in enumerate(ordered):
            if message.from_agent:
                continue
            member_ts = message.source_created_at or message.created_at
            next_agent = next((m for m in ordered[index + 1 :] if m.from_agent), None)
            if next_agent is None:
                unanswered_member += 1
                continue
            agent_ts = next_agent.source_created_at or next_agent.created_at
            seconds = (agent_ts - member_ts).total_seconds()
            if seconds < 0:
                continue
            gaps.append(seconds)
            if first_response is None:
                first_response = seconds

        return {
            "first_response_seconds": first_response,
            "median_response_seconds": statistics.median(gaps) if gaps else None,
            "max_response_seconds": max(gaps) if gaps else None,
            "unanswered_member_count": unanswered_member,
        }

    def _depth_metrics(self, ordered: list[Message], member_msgs: list[Message], agent_msgs: list[Message]) -> dict:
        total = len(ordered)
        member_count = len(member_msgs)
        agent_count = len(agent_msgs)
        ratio = member_count / agent_count if agent_count else float(member_count)
        if total <= 4:
            bucket = "shallow"
        elif total <= 12:
            bucket = "medium"
        else:
            bucket = "deep"
        return {
            "total_messages": total,
            "member_messages": member_count,
            "agent_messages": agent_count,
            "depth_ratio": ratio,
            "depth_bucket": bucket,
        }

    def message_response_seconds(self, ordered: list[Message], message: Message) -> float | None:
        if message.from_agent:
            return None
        member_ts = message.source_created_at or message.created_at
        index = ordered.index(message)
        next_agent = next((m for m in ordered[index + 1 :] if m.from_agent), None)
        if next_agent is None:
            return None
        agent_ts = next_agent.source_created_at or next_agent.created_at
        seconds = (agent_ts - member_ts).total_seconds()
        return seconds if seconds >= 0 else None
