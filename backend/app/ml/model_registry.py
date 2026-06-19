import hashlib
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
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
from app.ml.sentiment_scoring import adjust_stars_with_emotions, stars_to_label, stars_to_polarity

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

# Maps the cardiffnlp/twitter-roberta-base-sentiment-latest output (and its LABEL_n aliases)
# onto the legacy stars/label contract that metrics/reports/timeline all read.
POLARITY_LABEL_MAP: dict[str, tuple[str, int, str]] = {
    "negative": ("negative", 2, "NEGATIVE"),
    "neutral": ("neutral", 3, "NEUTRAL"),
    "positive": ("positive", 4, "POSITIVE"),
    "label_0": ("negative", 2, "NEGATIVE"),
    "label_1": ("neutral", 3, "NEUTRAL"),
    "label_2": ("positive", 4, "POSITIVE"),
}

# ISO 639-1 codes for the languages we configure lingua to detect.
LINGUA_LANGUAGE_NAMES = ("ENGLISH", "DUTCH", "GERMAN", "SPANISH", "FRENCH")


class ModelRegistry:
    """Lazy-loads Hugging Face models and reuses on-disk cache across restarts."""

    def __init__(self) -> None:
        self._load_lock = threading.Lock()
        # Separate locks so ingest sentiment is not serialized behind insights intent/embedding.
        self._sentiment_inference_lock = threading.Lock()
        self._embedding_inference_lock = threading.Lock()
        self._zero_shot_inference_lock = threading.Lock()
        # Stage 2 dual-sentiment models run in parallel, so each gets its own inference lock.
        self._polarity_inference_lock = threading.Lock()
        self._emotion_inference_lock = threading.Lock()
        self._language_detector = None
        self._translate_client = None
        self._pipelines: dict[str, object] = {}
        self._settings = get_settings()
        self._ensure_cache_dirs()

    def _ensure_cache_dirs(self) -> None:
        cache_root = Path(self._settings.hf_home)
        cache_root.mkdir(parents=True, exist_ok=True)
        (cache_root / "hub").mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(cache_root))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("MKL_NUM_THREADS", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    def preload(self) -> None:
        # Stage 2: language detector is always local (tiny). The polarity/emotion models run
        # remotely on HF when configured, so we skip loading those torch checkpoints locally.
        self.get_language_detector()
        if self._settings.sentiment_uses_remote_inference:
            from app.ml.hf_text_classification import get_hf_text_classification_client

            get_hf_text_classification_client()
            self.get_sentiment_pipeline()
        else:
            self.get_sentiment_pipeline()
            self.get_polarity_pipeline()
            self.get_emotion_pipeline()
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

    # --- Stage 2: language detection -> translation -> dual polarity + emotion ----------------

    def get_language_detector(self):
        if self._language_detector is None:
            with self._load_lock:
                if self._language_detector is None:
                    from lingua import Language, LanguageDetectorBuilder

                    languages = [getattr(Language, name) for name in LINGUA_LANGUAGE_NAMES]
                    logger.info("Loading lingua language detector (%s)", ", ".join(LINGUA_LANGUAGE_NAMES))
                    self._language_detector = LanguageDetectorBuilder.from_languages(*languages).build()
        return self._language_detector

    def detect_language(self, text: str) -> str:
        """Best-effort ISO 639-1 language code; defaults to ``en`` on empty/uncertain input."""
        if not text or not text.strip():
            return "en"
        try:
            language = self.get_language_detector().detect_language_of(text)
            if language is None:
                return "en"
            return language.iso_code_639_1.name.lower()
        except Exception:
            logger.exception("Language detection failed; defaulting to en")
            return "en"

    def get_polarity_pipeline(self):
        model_name = self._settings.polarity_model
        with self._load_lock:
            if model_name not in self._pipelines:
                logger.info("Loading polarity model '%s'", model_name)
                self._pipelines[model_name] = pipeline(
                    "sentiment-analysis",
                    model=model_name,
                    device=-1,
                )
                logger.info("Polarity model '%s' ready", model_name)
            return self._pipelines[model_name]

    def get_emotion_pipeline(self):
        model_name = self._settings.emotion_model
        with self._load_lock:
            if model_name not in self._pipelines:
                logger.info("Loading emotion model '%s'", model_name)
                self._pipelines[model_name] = pipeline(
                    "text-classification",
                    model=model_name,
                    device=-1,
                    top_k=self._settings.emotion_top_k,
                )
                logger.info("Emotion model '%s' ready", model_name)
            return self._pipelines[model_name]

    def get_translate_client(self):
        # Reuse a single pooled HTTP client for the Google Translation v2 REST endpoint. We call
        # the REST API with the API key directly (the google-cloud client library ignores api_key
        # auth and falls back to Application Default Credentials, which we don't have in-container).
        if not self._settings.google_translate_api_key.strip():
            return None
        if self._translate_client is None:
            with self._load_lock:
                if self._translate_client is None:
                    import httpx

                    logger.info("Initializing Google Cloud Translation v2 REST client")
                    self._translate_client = httpx.Client(timeout=self._settings.hf_inference_timeout_seconds)
        return self._translate_client

    def translate_to_english(self, text: str, source_lang: str) -> tuple[str, bool]:
        """Translate ``text`` to English. Returns (text, translated?)."""
        if not text or not text.strip() or source_lang == "en":
            return text, False
        return self.translate_text(text, source_lang, "en")

    def translate_to_dutch(self, text: str, source_lang: str) -> tuple[str, bool]:
        """Translate ``text`` to Dutch. Returns (text, translated?)."""
        return self.translate_text(text, source_lang, "nl")

    def translate_text(self, text: str, source_lang: str, target_lang: str) -> tuple[str, bool]:
        """Translate ``text`` to ``target_lang``. Returns (text, translated?)."""
        if not text or not text.strip() or source_lang == target_lang:
            return text, False
        client = self.get_translate_client()
        if client is None:
            return text, False

        cache_key = None
        try:
            from app.cache import cache_get

            digest = hashlib.sha256(f"{source_lang}:{target_lang}:{text}".encode("utf-8")).hexdigest()
            cache_key = f"translate:{target_lang}:{digest}"
            cached = cache_get(cache_key)
            if isinstance(cached, str):
                return cached, True
        except Exception:
            logger.exception("Translation cache read failed")
            cache_key = None

        try:
            response = client.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": self._settings.google_translate_api_key.strip()},
                json={"q": text, "source": source_lang, "target": target_lang, "format": "text"},
            )
            response.raise_for_status()
            translations = response.json()["data"]["translations"]
            translated = str(translations[0].get("translatedText") or text)
        except Exception:
            logger.exception(
                "Google translation failed for lang=%s -> %s; using original text",
                source_lang,
                target_lang,
            )
            return text, False

        if cache_key is not None:
            try:
                from app.cache import cache_set

                cache_set(cache_key, translated, ttl=self._settings.translation_cache_ttl_seconds)
            except Exception:
                logger.exception("Translation cache write failed")
        return translated, True

    def _polarity_label(self, raw_label: str) -> tuple[str, int, str]:
        key = raw_label.lower().strip()
        if key in POLARITY_LABEL_MAP:
            return POLARITY_LABEL_MAP[key]
        # Fall back to the generic resolver so unexpected labels still map to stars.
        stars, label = self._resolve_label(raw_label)
        polarity = "positive" if label == "POSITIVE" else "negative" if label == "NEGATIVE" else "neutral"
        return polarity, stars, label

    def _empty_v2_result(self) -> dict:
        return {
            "stars": 3,
            "label": "NEUTRAL",
            "score": 0.0,
            "model_name": f"{self._settings.sentiment_model}+{self._settings.emotion_model}",
            "raw_label": None,
            "raw_score": None,
            "low_confidence": True,
            "polarity": "neutral",
            "polarity_score": 0.0,
            "emotions": [],
            "original_language": "en",
            "translated": False,
        }

    def _compose_v2_result(
        self,
        *,
        raw_label: str,
        raw_score: float,
        emotions: list[dict],
        original_language: str,
        translated: bool,
    ) -> dict:
        stars, label = self._resolve_label(raw_label)
        polarity = stars_to_polarity(stars)
        adjusted_stars = adjust_stars_with_emotions(stars, emotions)
        adjusted_label = stars_to_label(adjusted_stars)
        adjusted_polarity = stars_to_polarity(adjusted_stars)
        threshold = self._settings.sentiment_confidence_threshold
        return {
            "stars": adjusted_stars,
            "label": adjusted_label,
            "score": raw_score,
            "model_name": f"{self._settings.sentiment_model}+{self._settings.emotion_model}",
            "raw_label": raw_label,
            "raw_score": raw_score,
            "low_confidence": raw_score < threshold,
            "polarity": adjusted_polarity,
            "polarity_score": raw_score,
            "emotions": emotions,
            "original_language": original_language,
            "translated": translated,
        }

    def _run_polarity_batch(self, texts: list[str]) -> list[tuple[str, float]]:
        """Run multilingual sentiment on original text (no translation step)."""
        if not texts:
            return []
        model_id = self._settings.sentiment_model
        if self._settings.sentiment_uses_remote_inference:
            from app.ml.hf_text_classification import get_hf_text_classification_client

            endpoint = self._settings.polarity_inference_endpoint or ""
            outputs = get_hf_text_classification_client().classify_batch(
                texts,
                model_id=model_id,
                endpoint=endpoint,
                top_k=1,
            )
            return [(str(out[0]["label"]), float(out[0]["score"])) for out in outputs]

        classifier = self.get_sentiment_pipeline()
        with self._polarity_inference_lock:
            outputs = classifier([t[:512] for t in texts], batch_size=max(1, self._settings.sentiment_batch_size))
        results: list[tuple[str, float]] = []
        for output in outputs:
            label, score = self._pick_best_result(output)
            results.append((label, score))
        return results

    def _run_emotion_batch(self, texts: list[str]) -> list[list[dict]]:
        if not texts:
            return []
        if self._settings.sentiment_uses_remote_inference:
            from app.ml.hf_text_classification import get_hf_text_classification_client

            outputs = get_hf_text_classification_client().classify_batch(
                texts,
                model_id=self._settings.emotion_model,
                endpoint=self._settings.emotion_inference_endpoint,
                top_k=self._settings.emotion_top_k,
            )
            return [
                [{"label": str(item["label"]), "score": round(float(item["score"]), 4)} for item in out]
                for out in outputs
            ]

        classifier = self.get_emotion_pipeline()
        with self._emotion_inference_lock:
            outputs = classifier([t[:512] for t in texts], batch_size=max(1, self._settings.sentiment_batch_size))
        emotions: list[list[dict]] = []
        for output in outputs:
            items = output if isinstance(output, list) else [output]
            emotions.append(
                [
                    {"label": str(item["label"]), "score": round(float(item["score"]), 4)}
                    for item in items
                ]
            )
        return emotions

    def analyze_sentiment_v2(self, text: str) -> dict:
        return self.analyze_sentiment_v2_batch([text])[0]

    def analyze_sentiment_v2_batch(self, texts: list[str]) -> list[dict]:
        """Stage 2 pipeline: detect language -> polarity on original text -> emotion on EN.

        Polarity uses the multilingual sentiment model without translation. Emotion still
        runs on English (translated when needed). Stars are nudged by the top emotion.
        """
        results: list[dict] = [self._empty_v2_result() for _ in texts]
        cleaned: list[tuple[int, str]] = [(i, t) for i, t in enumerate(texts) if t and t.strip()]
        if not cleaned:
            return results

        original_texts: list[str] = []
        english_texts: list[str] = []
        languages: list[str] = []
        translated_flags: list[bool] = []
        for _, text in cleaned:
            lang = self.detect_language(text)
            english, was_translated = self.translate_to_english(text, lang)
            original_texts.append(text)
            english_texts.append(english)
            languages.append(lang)
            translated_flags.append(was_translated)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                polarity_future = executor.submit(self._run_polarity_batch, original_texts)
                emotion_future = executor.submit(self._run_emotion_batch, english_texts)
                polarities = polarity_future.result()
                emotions = emotion_future.result()
        except Exception:
            logger.exception("Stage 2 dual-sentiment batch failed")
            return results

        for offset, (index, _) in enumerate(cleaned):
            raw_label, raw_score = polarities[offset]
            message_emotions = emotions[offset] if offset < len(emotions) else []
            results[index] = self._compose_v2_result(
                raw_label=raw_label,
                raw_score=raw_score,
                emotions=message_emotions,
                original_language=languages[offset],
                translated=translated_flags[offset],
            )
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
        *,
        intent_slugs: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        slugs = intent_slugs or self._settings.intent_slug_list
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
