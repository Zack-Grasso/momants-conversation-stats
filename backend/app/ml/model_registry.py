import logging
import os
import threading
from functools import lru_cache
from pathlib import Path

from transformers import pipeline

from app.config import get_settings
from app.ml.intent_labels import (
    descriptions_for_language,
    hypothesis_template_for_language,
    resolve_intent,
    reverse_description_map,
)

logger = logging.getLogger(__name__)

TABULARISAI_LABEL_MAP: dict[str, tuple[int, str]] = {
    "very negative": (1, "NEGATIVE"),
    "negative": (2, "NEGATIVE"),
    "neutral": (3, "NEUTRAL"),
    "positive": (4, "POSITIVE"),
    "very positive": (5, "POSITIVE"),
    "label_0": (1, "NEGATIVE"),
    "label_1": (2, "NEGATIVE"),
    "label_2": (3, "NEUTRAL"),
    "label_3": (4, "POSITIVE"),
    "label_4": (5, "POSITIVE"),
}

BINARY_LABEL_MAP: dict[str, tuple[int, str]] = {
    "positive": (4, "POSITIVE"),
    "negative": (2, "NEGATIVE"),
    "neutral": (3, "NEUTRAL"),
}


class ModelRegistry:
    """Lazy-loads Hugging Face models and reuses on-disk cache across restarts."""

    def __init__(self) -> None:
        self._load_lock = threading.Lock()
        # Separate locks so ingest sentiment is not serialized behind insights intent/embedding.
        self._sentiment_inference_lock = threading.Lock()
        self._embedding_inference_lock = threading.Lock()
        self._zero_shot_inference_lock = threading.Lock()
        self._pipelines: dict[str, object] = {}
        self._settings = get_settings()
        self._ensure_cache_dirs()

    def _ensure_cache_dirs(self) -> None:
        cache_root = Path(self._settings.hf_home)
        cache_root.mkdir(parents=True, exist_ok=True)
        (cache_root / "hub").mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(cache_root))
        os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_root / "hub"))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("MKL_NUM_THREADS", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    def preload(self) -> None:
        self.get_sentiment_pipeline()
        if self._settings.app_role in ("worker", "scheduler"):
            self.get_embedding_model()
            if self._settings.intent_uses_remote_inference:
                from app.ml.hf_zero_shot import get_hf_zero_shot_client

                get_hf_zero_shot_client()
            else:
                self.get_zero_shot_pipeline()

    def get_sentiment_pipeline(self):
        model_name = self._settings.sentiment_model
        with self._load_lock:
            if model_name not in self._pipelines:
                logger.info("Loading sentiment model '%s' (cache: %s)", model_name, self._settings.hf_home)
                self._pipelines[model_name] = pipeline(
                    "text-classification",
                    model=model_name,
                    device=-1,
                    top_k=None,
                )
                logger.info("Sentiment model '%s' ready", model_name)
            return self._pipelines[model_name]

    def _resolve_label(self, raw_label: str) -> tuple[int, str]:
        key = raw_label.lower().strip().replace("_", " ")
        if key in TABULARISAI_LABEL_MAP:
            return TABULARISAI_LABEL_MAP[key]
        if key in BINARY_LABEL_MAP:
            return BINARY_LABEL_MAP[key]
        if "star" in key:
            try:
                star_num = int(key.split()[0])
                stars = max(1, min(5, star_num))
                if stars <= 2:
                    return stars, "NEGATIVE"
                if stars == 3:
                    return stars, "NEUTRAL"
                return stars, "POSITIVE"
            except ValueError:
                pass
        return 3, "NEUTRAL"

    def _pick_best_result(self, results: list | dict) -> tuple[str, float]:
        if not results:
            return "neutral", 0.0
        if isinstance(results, dict):
            candidates = [results]
        elif isinstance(results[0], dict):
            candidates = results
        else:
            candidates = results[0]
        best = max(candidates, key=lambda item: float(item["score"]))
        return str(best["label"]), float(best["score"])

    def _normalize_hf_result(self, raw_label: str, raw_score: float) -> dict[str, str | int | float | bool]:
        stars, label = self._resolve_label(raw_label)
        low_confidence = raw_score < self._settings.sentiment_confidence_threshold

        if low_confidence and label == "NEUTRAL":
            return {
                "label": "NEUTRAL",
                "stars": 3,
                "score": raw_score,
                "model_name": self._settings.sentiment_model,
                "raw_label": raw_label,
                "raw_score": raw_score,
                "low_confidence": True,
            }

        return {
            "label": label,
            "stars": stars,
            "score": raw_score,
            "model_name": self._settings.sentiment_model,
            "raw_label": raw_label,
            "raw_score": raw_score,
            "low_confidence": low_confidence,
        }

    def analyze_sentiment(self, text: str) -> dict[str, str | int | float | bool]:
        if not text.strip():
            return self._neutral_result(0.0)

        try:
            classifier = self.get_sentiment_pipeline()
            with self._sentiment_inference_lock:
                output = classifier(text[:512])
            raw_label, raw_score = self._pick_best_result(output)
            return self._normalize_hf_result(raw_label, raw_score)
        except Exception:
            logger.exception("HF sentiment analysis failed")
            return self._neutral_result(0.0)

    def analyze_sentiment_batch(self, texts: list[str]) -> list[dict[str, str | int | float | bool]]:
        if not texts:
            return []

        results: list[dict[str, str | int | float | bool]] = [self._neutral_result(0.0) for _ in texts]
        cleaned = [(i, text[:512]) for i, text in enumerate(texts) if text and text.strip()]
        if not cleaned:
            return results

        try:
            classifier = self.get_sentiment_pipeline()
            batch_size = max(1, self._settings.sentiment_batch_size)
            with self._sentiment_inference_lock:
                outputs = classifier([text for _, text in cleaned], batch_size=batch_size)
            for (index, _), output in zip(cleaned, outputs, strict=True):
                raw_label, raw_score = self._pick_best_result(output)
                results[index] = self._normalize_hf_result(raw_label, raw_score)
        except Exception:
            logger.exception("HF sentiment batch analysis failed; falling back to per-text")
            for index, _ in cleaned:
                results[index] = self.analyze_sentiment(texts[index])
        return results

    def get_embedding_model(self):
        model_name = self._settings.embedding_model
        with self._load_lock:
            if model_name not in self._pipelines:
                from sentence_transformers import SentenceTransformer

                logger.info("Loading embedding model '%s'", model_name)
                self._pipelines[model_name] = SentenceTransformer(model_name)
                logger.info("Embedding model '%s' ready", model_name)
            return self._pipelines[model_name]

    def embed_texts(self, texts: list[str], batch_size: int = 32):
        import numpy as np

        if not texts:
            return np.array([])
        model = self.get_embedding_model()
        with self._embedding_inference_lock:
            vectors = model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vectors)

    def get_zero_shot_pipeline(self):
        if self._settings.intent_uses_remote_inference:
            raise RuntimeError("Local zero-shot pipeline is disabled when INTENT_INFERENCE_MODE uses Hugging Face API")
        model_name = self._settings.intent_model
        with self._load_lock:
            if model_name not in self._pipelines:
                logger.info("Loading zero-shot model '%s'", model_name)
                self._pipelines[model_name] = pipeline(
                    "zero-shot-classification",
                    model=model_name,
                    device=-1,
                )
                logger.info("Zero-shot model '%s' ready", model_name)
            return self._pipelines[model_name]

    def _zero_shot_classify(
        self,
        texts: list[str],
        labels: list[str],
        *,
        hypothesis_template: str | None = None,
    ) -> list[dict]:
        if not texts:
            return []

        if self._settings.intent_uses_remote_inference:
            from app.ml.hf_zero_shot import get_hf_zero_shot_client

            # No lock here: the HF client bounds total in-flight requests process-wide via its
            # own executor, so letting concurrent callers overlap simply keeps that pool fed.
            return get_hf_zero_shot_client().classify_batch(
                texts,
                labels,
                hypothesis_template=hypothesis_template,
            )

        classifier = self.get_zero_shot_pipeline()
        with self._zero_shot_inference_lock:
            raw = classifier(
                texts if len(texts) > 1 else texts[0],
                labels,
                multi_label=False,
                hypothesis_template=hypothesis_template,
            )
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            return raw
        return [raw]

    def classify_zero_shot(self, text: str, labels: list[str]) -> tuple[str, float]:
        if not text.strip() or not labels:
            return labels[0] if labels else "unknown", 0.0
        try:
            result = self._zero_shot_classify([text[:512]], labels)[0]
            return str(result["labels"][0]), float(result["scores"][0])
        except Exception:
            logger.exception("Zero-shot classification failed")
            return labels[-1], 0.0

    def classify_intent(
        self,
        text: str,
        language: str,
        sentiment_stars: int | None = None,
    ) -> tuple[str, float]:
        results = self.classify_intents_batch([text], language, [sentiment_stars])
        return results[0]

    def classify_intents_batch(
        self,
        texts: list[str],
        language: str,
        sentiment_stars: list[int | None] | None = None,
    ) -> list[tuple[str, float]]:
        slugs = self._settings.intent_label_list
        if not texts or not slugs:
            return [("general", 0.0) for _ in texts]

        stars = sentiment_stars if sentiment_stars is not None else [None] * len(texts)
        if len(stars) != len(texts):
            raise ValueError("sentiment_stars length must match texts length")

        descriptions = descriptions_for_language(language, slugs)
        candidate_labels = list(descriptions.values())
        slug_by_description = reverse_description_map(language, slugs)
        hypothesis_template = hypothesis_template_for_language(
            language,
            self._settings.intent_hypothesis_template,
        )

        truncated: list[str] = []
        truncated_indexes: list[int] = []
        results: list[tuple[str, float]] = [("general", 0.0) for _ in texts]
        for index, text in enumerate(texts):
            if text.strip():
                truncated.append(text[:512])
                truncated_indexes.append(index)

        if not truncated:
            return results

        try:
            raw = self._zero_shot_classify(
                truncated,
                candidate_labels,
                hypothesis_template=hypothesis_template,
            )

            for source_index, result in zip(truncated_indexes, raw, strict=True):
                scores_by_slug: dict[str, float] = {}
                for label, score in zip(result["labels"], result["scores"], strict=True):
                    slug = slug_by_description.get(str(label))
                    if slug is None:
                        continue
                    scores_by_slug[slug] = max(scores_by_slug.get(slug, 0.0), float(score))

                results[source_index] = resolve_intent(
                    scores_by_slug,
                    threshold=self._settings.intent_confidence_threshold,
                    complaint_min_score=self._settings.intent_complaint_min_score,
                    sentiment_stars=stars[source_index],
                )
        except Exception:
            logger.exception("Batch intent classification failed")
            return [("general", 0.0) for _ in texts]

        return results

    def cosine_similarity(self, text_a: str, text_b: str) -> float:
        import numpy as np

        vectors = self.embed_texts([text_a, text_b])
        if len(vectors) != 2:
            return 0.0
        return float(np.dot(vectors[0], vectors[1]))

    def cosine_similarity_pairs(self, texts_a: list[str], texts_b: list[str]) -> list[float]:
        """Cosine similarity for many aligned pairs, embedding each side in one batched call.

        Embeddings are L2-normalised, so the dot product is the cosine. This avoids the
        per-pair re-embedding done by repeated cosine_similarity() calls.
        """
        import numpy as np

        if not texts_a:
            return []
        if len(texts_a) != len(texts_b):
            raise ValueError("texts_a and texts_b must be the same length")

        vectors_a = self.embed_texts(texts_a)
        vectors_b = self.embed_texts(texts_b)
        return [float(np.dot(vectors_a[i], vectors_b[i])) for i in range(len(texts_a))]

    def classify_zero_shot_batch(self, texts: list[str], labels: list[str]) -> list[tuple[str, float]]:
        """Zero-shot top label/score for many texts. With remote inference the underlying HF
        calls are dispatched concurrently, so passing the whole list is far faster than looping
        classify_zero_shot()."""
        if not labels:
            return [("unknown", 0.0) for _ in texts]
        if not texts:
            return []

        prepared = [text[:512] for text in texts]
        try:
            raw = self._zero_shot_classify(prepared, labels)
            return [(str(result["labels"][0]), float(result["scores"][0])) for result in raw]
        except Exception:
            logger.exception("Batch zero-shot classification failed")
            return [(labels[-1], 0.0) for _ in texts]

    def _neutral_result(self, score: float) -> dict[str, str | int | float | bool]:
        return {
            "label": "NEUTRAL",
            "stars": 3,
            "score": score,
            "model_name": self._settings.sentiment_model,
            "raw_label": None,
            "raw_score": None,
            "low_confidence": True,
        }


@lru_cache
def get_model_registry() -> ModelRegistry:
    return ModelRegistry()
