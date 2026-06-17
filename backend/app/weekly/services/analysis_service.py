from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ml.model_registry import get_model_registry
from app.utils.question_utils import is_question
from app.weekly.models import (
    WeeklyAgentRun,
    WeeklyConversation,
    WeeklyMessage,
    WeeklyQuestionCluster,
    WeeklyUnansweredFinding,
)

logger = logging.getLogger(__name__)


def _ordered_messages(messages: list[WeeklyMessage]) -> list[WeeklyMessage]:
    return sorted(messages, key=lambda m: (m.source_created_at or datetime.min.replace(tzinfo=timezone.utc), m.id))


def _normalize_question(text: str) -> str:
    stripped = re.sub(r"[^\w\s]", "", text.lower(), flags=re.UNICODE)
    return " ".join(stripped.split())


class WeeklyAnalysisService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.models = get_model_registry()

    def analyze(self, agent_run: WeeklyAgentRun, conversations: list[WeeklyConversation]) -> dict[str, int]:
        self.db.execute(delete(WeeklyUnansweredFinding).where(WeeklyUnansweredFinding.agent_run_id == agent_run.id))
        self.db.execute(delete(WeeklyQuestionCluster).where(WeeklyQuestionCluster.agent_run_id == agent_run.id))
        self.db.flush()

        totals = self._analyze_unanswered(agent_run, conversations)
        self._cluster_questions(agent_run, conversations)
        self.db.flush()
        return totals

    def _analyze_unanswered(
        self, agent_run: WeeklyAgentRun, conversations: list[WeeklyConversation]
    ) -> dict[str, int]:
        totals = {"no_reply": 0, "weak_answer": 0, "not_answered": 0, "total": 0}
        candidates: list[dict] = []

        for conversation in conversations:
            ordered = _ordered_messages(conversation.messages)
            for index, message in enumerate(ordered):
                if message.from_agent or not is_question(message.content):
                    continue
                next_agent = next((m for m in ordered[index + 1 :] if m.from_agent), None)
                if next_agent is None:
                    totals["no_reply"] += 1
                    totals["total"] += 1
                    self.db.add(
                        WeeklyUnansweredFinding(
                            agent_run_id=agent_run.id,
                            conversation_id=conversation.id,
                            message_id=message.id,
                            question_text=message.content,
                            status="no_reply",
                        )
                    )
                    continue
                candidates.append(
                    {"conversation": conversation, "message": message, "agent": next_agent}
                )

        if candidates:
            questions = [c["message"].content for c in candidates]
            answers = [c["agent"].content for c in candidates]
            similarities = self.models.cosine_similarity_pairs(questions, answers)
            nli_results = self.models.classify_zero_shot_batch(
                [f"Question: {q}\nAnswer: {a}" for q, a in zip(questions, answers, strict=True)],
                self.settings.unanswered_nli_label_list,
            )
        else:
            similarities, nli_results = [], []

        for candidate, similarity, (nli_label, nli_score) in zip(
            candidates, similarities, nli_results, strict=True
        ):
            status = "answered"
            if self._is_not_answered_nli(nli_label, nli_score):
                status = "not_answered"
            elif similarity < self.settings.unanswered_similarity_threshold:
                status = "weak_answer"
            if status == "answered":
                continue

            totals[status] += 1
            totals["total"] += 1
            self.db.add(
                WeeklyUnansweredFinding(
                    agent_run_id=agent_run.id,
                    conversation_id=candidate["conversation"].id,
                    message_id=candidate["message"].id,
                    question_text=candidate["message"].content,
                    agent_reply_text=(candidate["agent"].content or "")[:2000],
                    status=status,
                    similarity_score=similarity,
                    nli_label=nli_label,
                    nli_score=nli_score,
                )
            )
        return totals

    def _cluster_questions(self, agent_run: WeeklyAgentRun, conversations: list[WeeklyConversation]) -> None:
        texts: list[str] = []
        for conversation in conversations:
            for message in _ordered_messages(conversation.messages):
                if not message.from_agent and is_question(message.content):
                    texts.append(message.content)
        if len(texts) < 2:
            return

        embeddings = self.models.embed_texts(texts)
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=self.settings.question_cluster_distance,
            metric="cosine",
            linkage="average",
        )
        labels = clustering.fit_predict(embeddings)
        clusters: dict[int, list[int]] = defaultdict(list)
        for idx, label in enumerate(labels):
            clusters[int(label)].append(idx)

        ranked = sorted(clusters.items(), key=lambda item: len(item[1]), reverse=True)
        min_size = max(1, self.settings.question_min_cluster_size)
        seen: set[str] = set()
        rank = 0
        for _, indices in ranked:
            if len(indices) < min_size:
                continue
            vectors = embeddings[indices]
            centroid = vectors.mean(axis=0)
            rep_local = min(
                range(len(indices)),
                key=lambda i: (float(np.linalg.norm(vectors[i] - centroid)), len(texts[indices[i]])),
            )
            rep_text = texts[indices[rep_local]]
            normalized = _normalize_question(rep_text)
            if normalized in seen:
                continue
            seen.add(normalized)
            rank += 1
            self.db.add(
                WeeklyQuestionCluster(
                    agent_run_id=agent_run.id,
                    rank=rank,
                    count=len(indices),
                    representative_text=rep_text,
                )
            )
            if rank >= 20:
                break

    def _is_not_answered_nli(self, label: str, score: float) -> bool:
        lowered = label.lower()
        if score < self.settings.unanswered_nli_threshold:
            return False
        return "does not answer" in lowered or lowered == "deflects"

    def load_findings(self, agent_run_id: int) -> list[WeeklyUnansweredFinding]:
        return list(
            self.db.scalars(
                select(WeeklyUnansweredFinding)
                .where(WeeklyUnansweredFinding.agent_run_id == agent_run_id)
                .order_by(WeeklyUnansweredFinding.id)
            ).all()
        )

    def load_clusters(self, agent_run_id: int) -> list[WeeklyQuestionCluster]:
        return list(
            self.db.scalars(
                select(WeeklyQuestionCluster)
                .where(WeeklyQuestionCluster.agent_run_id == agent_run_id)
                .order_by(WeeklyQuestionCluster.rank)
            ).all()
        )
