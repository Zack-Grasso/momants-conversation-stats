import logging
from collections import defaultdict
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.ml.intent_labels import build_intent_text, detect_language
from app.ml.model_registry import get_model_registry
from app.models.conversation import Conversation, Message
from app.models.insights import ConversationMetrics, QuestionCluster
from app.services.metrics_service import MetricsService

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]


class IntentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.models = get_model_registry()
        self.metrics = MetricsService(db)

    def label_conversations(
        self,
        agent_id: str,
        conversation_ids: list[int] | None = None,
        on_progress: ProgressCallback | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> int:
        stmt = (
            select(Conversation)
            .where(Conversation.agent_id == agent_id)
            .options(selectinload(Conversation.messages).selectinload(Message.sentiment))
        )
        if conversation_ids is not None:
            stmt = stmt.where(Conversation.id.in_(conversation_ids))
        conversations = list(self.db.scalars(stmt).all())
        total = len(conversations)
        labeled = 0
        supported_languages = self.settings.intent_supported_language_list
        batch_size = max(1, self.settings.intent_batch_size)
        intent_slugs = self.settings.intent_slug_list
        logger.info(
            "Intent labeling started for agent %s (%s conversations, batch=%s, slugs=%s)",
            agent_id,
            total,
            batch_size,
            ",".join(intent_slugs),
        )

        work_by_language: dict[str, list[tuple[int, Conversation, str, int | None]]] = defaultdict(list)
        for index, conversation in enumerate(conversations, start=1):
            ordered = self.metrics._ordered_messages(conversation.messages)
            intent_text = build_intent_text(ordered)
            if not intent_text:
                continue

            first_member = next((m for m in ordered if not m.from_agent), None)
            sentiment_stars = None
            if first_member and first_member.sentiment:
                sentiment_stars = first_member.sentiment.stars

            language = detect_language(intent_text, supported_languages)
            work_by_language[language].append((index, conversation, intent_text, sentiment_stars))

        for language, items in work_by_language.items():
            for batch_start in range(0, len(items), batch_size):
                if should_cancel and should_cancel():
                    logger.info("Intent labeling cancelled for agent %s", agent_id)
                    return labeled

                batch = items[batch_start : batch_start + batch_size]
                texts = [item[2] for item in batch]
                stars = [item[3] for item in batch]
                batch_num = batch_start // batch_size + 1
                batch_count = (len(items) + batch_size - 1) // batch_size
                if on_progress and batch:
                    on_progress(
                        max(0, batch[0][0] - 1),
                        total,
                        f"Classifying intent batch {batch_num}/{batch_count} ({len(batch)} conversations)",
                    )
                try:
                    labels_scores = self.models.classify_intents_batch(
                        texts, language, stars, intent_slugs=intent_slugs
                    )
                except Exception:
                    logger.exception("Intent labeling failed for batch (%s items)", len(batch))
                    continue

                for (index, conversation, _, _), (intent_label, intent_score) in zip(batch, labels_scores, strict=True):
                    metrics = self.db.scalar(
                        select(ConversationMetrics).where(ConversationMetrics.conversation_id == conversation.id)
                    )
                    if metrics is None:
                        continue
                    metrics.intent_label = intent_label
                    metrics.intent_score = intent_score
                    labeled += 1
                    self.db.commit()

                    if on_progress:
                        on_progress(index, total, f"Labeling conversation intents ({index}/{total})")

        logger.info("Intent labeling finished for agent %s (%s labeled)", agent_id, labeled)
        return labeled

    def label_question_clusters(
        self,
        job_id: int,
        on_progress: ProgressCallback | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> int:
        clusters = list(
            self.db.scalars(select(QuestionCluster).where(QuestionCluster.insights_job_id == job_id)).all()
        )
        total = len(clusters)
        supported_languages = self.settings.intent_supported_language_list
        batch_size = max(1, self.settings.intent_batch_size)
        intent_slugs = self.settings.intent_slug_list
        logger.info("Cluster intent labeling started for job %s (%s clusters)", job_id, total)

        work_by_language: dict[str, list[tuple[int, QuestionCluster, str]]] = defaultdict(list)
        for index, cluster in enumerate(clusters, start=1):
            language = detect_language(cluster.representative_text, supported_languages)
            work_by_language[language].append((index, cluster, cluster.representative_text))

        for language, items in work_by_language.items():
            for batch_start in range(0, len(items), batch_size):
                if should_cancel and should_cancel():
                    logger.info("Cluster intent labeling cancelled for job %s", job_id)
                    return len(clusters)

                batch = items[batch_start : batch_start + batch_size]
                texts = [item[2] for item in batch]
                try:
                    labels_scores = self.models.classify_intents_batch(
                        texts, language, intent_slugs=intent_slugs
                    )
                except Exception:
                    logger.exception("Cluster intent labeling failed for batch (%s items)", len(batch))
                    continue

                for (index, cluster, _), (intent_label, intent_score) in zip(batch, labels_scores, strict=True):
                    cluster.intent_label = intent_label
                    cluster.intent_score = intent_score
                    if on_progress and (index == 1 or index % 5 == 0 or index == total):
                        on_progress(index, total, f"Labeling question cluster intents ({index}/{total})")

        self.db.flush()
        logger.info("Cluster intent labeling finished for job %s", job_id)
        return len(clusters)
