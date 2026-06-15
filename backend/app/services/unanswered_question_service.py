import logging
from collections.abc import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.ml.model_registry import get_model_registry
from app.models.conversation import Conversation, Message
from app.models.insights import ConversationMetrics, UnansweredQuestion
from app.services.metrics_service import MetricsService
from app.utils.question_utils import is_question

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]


class UnansweredQuestionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.models = get_model_registry()
        self.metrics = MetricsService(db)

    def analyze_agent(
        self,
        agent_id: str,
        conversation_ids: list[int] | None = None,
        on_progress: ProgressCallback | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, int]:
        stmt = (
            select(Conversation)
            .where(Conversation.agent_id == agent_id)
            .options(selectinload(Conversation.messages))
        )
        if conversation_ids is not None:
            stmt = stmt.where(Conversation.id.in_(conversation_ids))
        conversations = list(self.db.scalars(stmt).all())
        totals = {"no_reply": 0, "weak_answer": 0, "not_answered": 0, "total": 0}
        total = len(conversations)
        logger.info("Unanswered analysis started for agent %s (%s conversations)", agent_id, total)

        # Phase A — collect every (question, next agent reply) candidate across all conversations
        # and clear stale rows. We gather first so the embeddings and NLI can run in one batched,
        # concurrent sweep rather than per-pair calls.
        candidates: list[dict] = []
        for index, conversation in enumerate(conversations, start=1):
            if should_cancel and should_cancel():
                logger.info("Unanswered analysis cancelled for agent %s at %s/%s", agent_id, index, total)
                break

            self.db.execute(
                delete(UnansweredQuestion).where(
                    UnansweredQuestion.conversation_id == conversation.id,
                    UnansweredQuestion.status.in_(["weak_answer", "not_answered", "answered"]),
                )
            )

            ordered = self.metrics._ordered_messages(conversation.messages)
            for position, message in enumerate(ordered):
                if message.from_agent or not is_question(message.content):
                    continue
                next_agent = next((m for m in ordered[position + 1 :] if m.from_agent), None)
                if next_agent is None:
                    continue
                candidates.append(
                    {"conversation": conversation, "message": message, "agent": next_agent}
                )

            if on_progress and (index == 1 or index % 25 == 0 or index == total):
                on_progress(index, total, f"Collecting unanswered candidates ({index}/{total} conversations)")

        # Phase B — batched scoring. Embeddings are computed in two batched calls and the NLI
        # premises are dispatched concurrently by the HF client.
        nli_labels = self.settings.unanswered_nli_label_list
        if candidates:
            if on_progress:
                on_progress(total, total, f"Scoring {len(candidates)} candidate questions")
            questions = [c["message"].content for c in candidates]
            answers = [c["agent"].content for c in candidates]
            similarities = self.models.cosine_similarity_pairs(questions, answers)
            nli_results = self.models.classify_zero_shot_batch(
                [f"Question: {q}\nAnswer: {a}" for q, a in zip(questions, answers, strict=True)],
                nli_labels,
            )
        else:
            similarities, nli_results = [], []

        # Phase C — persist rows and aggregate per-conversation counts.
        per_conversation: dict[int, dict] = {
            conversation.id: {"weak": 0, "semantic": 0, "last": None}
            for conversation in conversations
        }
        for candidate, similarity, (nli_label, nli_score) in zip(
            candidates, similarities, nli_results, strict=True
        ):
            conversation = candidate["conversation"]
            message = candidate["message"]
            next_agent = candidate["agent"]
            agg = per_conversation[conversation.id]

            status = "answered"
            if self._is_not_answered_nli(nli_label, nli_score):
                status = "not_answered"
                agg["semantic"] += 1
                agg["last"] = message.content
            elif similarity < self.settings.unanswered_similarity_threshold:
                status = "weak_answer"
                agg["weak"] += 1
                agg["last"] = message.content

            if status == "answered":
                continue

            record = self.db.scalar(
                select(UnansweredQuestion).where(UnansweredQuestion.message_id == message.id)
            )
            if record is None:
                record = UnansweredQuestion(
                    message_id=message.id,
                    conversation_id=conversation.id,
                    agent_id=conversation.agent_id,
                    question_text=message.content,
                )
                self.db.add(record)

            record.agent_reply_message_id = next_agent.id
            record.agent_reply_text = next_agent.content[:2000]
            record.status = status
            record.similarity_score = similarity
            record.nli_label = nli_label
            record.nli_score = nli_score

        for conversation in conversations:
            agg = per_conversation[conversation.id]
            no_reply = self.db.scalar(
                select(func.count())
                .select_from(UnansweredQuestion)
                .where(
                    UnansweredQuestion.conversation_id == conversation.id,
                    UnansweredQuestion.status == "no_reply",
                )
            ) or 0

            metrics = self.db.scalar(
                select(ConversationMetrics).where(ConversationMetrics.conversation_id == conversation.id)
            )
            if metrics:
                metrics.unanswered_weak_answer_count = agg["weak"]
                metrics.unanswered_semantic_count = agg["semantic"]
                metrics.unanswered_no_reply_count = no_reply
                metrics.unanswered_question_count = no_reply + agg["weak"] + agg["semantic"]
                metrics.last_unanswered_question_text = agg["last"]

            totals["no_reply"] += no_reply
            totals["weak_answer"] += agg["weak"]
            totals["not_answered"] += agg["semantic"]
            totals["total"] += no_reply + agg["weak"] + agg["semantic"]

        self.db.flush()
        logger.info(
            "Unanswered analysis finished for agent %s (flagged=%s)",
            agent_id,
            totals["total"],
        )
        return totals

    def _is_not_answered_nli(self, label: str, score: float) -> bool:
        lowered = label.lower()
        if score < self.settings.unanswered_nli_threshold:
            return False
        return "does not answer" in lowered or lowered == "deflects"
