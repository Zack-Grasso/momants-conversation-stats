import json
import re

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.ml.model_registry import get_model_registry
from app.models.conversation import Conversation
from app.models.insights import QuestionCluster
from app.utils.question_utils import is_question


def _normalize_question(text: str) -> str:
    """Lowercased, punctuation/emoji-stripped key for detecting near-duplicate questions."""
    stripped = re.sub(r"[^\w\s]", "", text.lower(), flags=re.UNICODE)
    return " ".join(stripped.split())


class QuestionAnalysisService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.models = get_model_registry()

    def analyze(
        self,
        agent_id: str,
        job_id: int,
        conversation_ids: list[int] | None = None,
        scope: str = "first",
    ) -> list[QuestionCluster]:
        # Question clustering is a global operation: the top-questions list is built by
        # embedding and clustering ALL of an agent's questions together. Clearing only this
        # job's clusters let prior runs' (and parallel batch jobs') clusters accumulate, so
        # the same question surfaced once per job — the duplicate FAQ rows. Clear every
        # cluster for the agent so a single coherent set replaces the old one.
        self.db.execute(delete(QuestionCluster).where(QuestionCluster.agent_id == agent_id))

        texts: list[str] = []
        conv_ids: list[int] = []
        stmt = (
            select(Conversation)
            .where(Conversation.agent_id == agent_id)
            .options(selectinload(Conversation.messages))
        )
        if conversation_ids is not None:
            stmt = stmt.where(Conversation.id.in_(conversation_ids))
        conversations = list(self.db.scalars(stmt).all())

        for conversation in conversations:
            member_msgs = sorted(
                [m for m in conversation.messages if not m.from_agent],
                key=lambda m: (m.source_created_at or m.created_at, m.id),
            )
            candidates = [member_msgs[0]] if scope == "first" and member_msgs else member_msgs
            for message in candidates:
                if is_question(message.content):
                    texts.append(message.content)
                    conv_ids.append(conversation.id)

        if len(texts) < 2:
            return []

        embeddings = self.models.embed_texts(texts)
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=self.settings.question_cluster_distance,
            metric="cosine",
            linkage="average",
        )
        labels = clustering.fit_predict(embeddings)

        clusters: dict[int, list[int]] = {}
        for idx, label in enumerate(labels):
            clusters.setdefault(int(label), []).append(idx)

        ranked = sorted(clusters.items(), key=lambda item: len(item[1]), reverse=True)
        min_size = max(1, self.settings.question_min_cluster_size)
        results: list[QuestionCluster] = []
        seen_representations: set[str] = set()
        rank = 0
        for _, indices in ranked:
            # Drop singletons / tiny clusters — they are noise, not recurring questions.
            if len(indices) < min_size:
                continue

            cluster_vectors = embeddings[indices]
            centroid = cluster_vectors.mean(axis=0)
            distances = [float(np.linalg.norm(cluster_vectors[i] - centroid)) for i in range(len(indices))]
            # Closest to the centroid, breaking ties toward the shorter (cleaner) phrasing.
            rep_local = min(
                range(len(indices)),
                key=lambda i: (round(distances[i], 4), len(texts[indices[i]])),
            )
            rep_index = indices[rep_local]
            rep_text = texts[rep_index]

            # Skip clusters whose representative duplicates a higher-ranked one (e.g. same
            # question with/without trailing emoji or punctuation).
            normalized = _normalize_question(rep_text)
            if normalized in seen_representations:
                continue
            seen_representations.add(normalized)

            rank += 1
            examples = [{"text": texts[i], "conversation_id": conv_ids[i]} for i in indices[:3]]
            cluster = QuestionCluster(
                insights_job_id=job_id,
                agent_id=agent_id,
                rank=rank,
                count=len(indices),
                representative_text=rep_text,
                examples_json=json.dumps(examples),
            )
            self.db.add(cluster)
            results.append(cluster)
            if rank >= 20:
                break

        self.db.flush()
        return results
