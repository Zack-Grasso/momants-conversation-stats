import logging
import os
import threading
from functools import lru_cache
from pathlib import Path

from transformers import pipeline

from app.config import get_settings

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Lazy-loads Hugging Face models and reuses on-disk cache across restarts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
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

    def preload(self) -> None:
        self.get_sentiment_pipeline()

    def get_sentiment_pipeline(self):
        model_name = self._settings.sentiment_model
        with self._lock:
            if model_name not in self._pipelines:
                logger.info("Loading sentiment model '%s' (cache: %s)", model_name, self._settings.hf_home)
                self._pipelines[model_name] = pipeline(
                    "sentiment-analysis",
                    model=model_name,
                    device=-1,
                )
                logger.info("Sentiment model '%s' ready", model_name)
            return self._pipelines[model_name]

    def analyze_sentiment(self, text: str) -> dict[str, float | str]:
        result = self.get_sentiment_pipeline()(text[:512])[0]
        return {"label": result["label"], "score": float(result["score"])}


@lru_cache
def get_model_registry() -> ModelRegistry:
    return ModelRegistry()
