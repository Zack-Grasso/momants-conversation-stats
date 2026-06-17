"""Remote text-classification inference via Hugging Face Serverless API or Inference Endpoint.

Used for the stage-2 polarity + emotion models so they run on HF infrastructure instead of
loading large torch checkpoints into the local container.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

HF_ROUTER_URL = "https://router.huggingface.co/hf-inference/models/{model_id}"
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

# One label/score prediction.
Prediction = dict[str, Any]


class HfTextClassificationClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.Client(timeout=settings.hf_inference_timeout_seconds)
        concurrency = max(1, settings.hf_inference_concurrency)
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="hf-textcls")

    def _resolve_url(self, model_id: str, endpoint: str | None) -> str:
        if endpoint and endpoint.strip():
            return endpoint.strip().rstrip("/")
        return HF_ROUTER_URL.format(model_id=model_id)

    def _auth_headers(self) -> dict[str, str]:
        token = self._settings.hf_token.strip()
        if not token:
            raise ValueError("HF_TOKEN is required when SENTIMENT_INFERENCE_MODE is hf_api or hf_endpoint")
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _normalize_result(raw: Any) -> list[Prediction]:
        # The serverless API returns either a flat list of {label,score} for a single input,
        # or a nested [[...]] when it echoes the batch dimension. Collapse to a flat list.
        if isinstance(raw, list) and raw and isinstance(raw[0], list):
            raw = raw[0]
        if not isinstance(raw, list):
            raise ValueError(f"Unexpected text-classification response shape: {raw!r}")
        predictions = [
            {"label": str(item["label"]), "score": float(item["score"])}
            for item in raw
            if isinstance(item, dict) and "label" in item and "score" in item
        ]
        if not predictions:
            raise ValueError(f"Empty text-classification response: {raw!r}")
        predictions.sort(key=lambda item: item["score"], reverse=True)
        return predictions

    @staticmethod
    def _retry_delay(response: httpx.Response, backoff: float, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return backoff * (2**attempt)

    def _classify_one(self, url: str, headers: dict[str, str], text: str, parameters: dict[str, Any]) -> list[Prediction]:
        max_retries = self._settings.hf_inference_max_retries
        backoff = self._settings.hf_inference_retry_backoff_seconds
        payload: dict[str, Any] = {"inputs": text[:512]}
        if parameters:
            payload["parameters"] = parameters
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                response = self._client.post(url, headers=headers, json=payload)
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                    delay = self._retry_delay(response, backoff, attempt)
                    logger.warning(
                        "HF text-classification returned %s (attempt %s/%s), retrying in %.1fs",
                        response.status_code,
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                return self._normalize_result(response.json())
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt >= max_retries:
                    break
                delay = backoff * (2**attempt)
                logger.warning(
                    "HF text-classification error %s (attempt %s/%s), retrying in %.1fs",
                    exc,
                    attempt + 1,
                    max_retries + 1,
                    delay,
                )
                time.sleep(delay)

        assert last_exc is not None
        raise last_exc

    def classify_batch(
        self,
        texts: list[str],
        *,
        model_id: str,
        endpoint: str | None = None,
        top_k: int | None = None,
    ) -> list[list[Prediction]]:
        if not texts:
            return []
        url = self._resolve_url(model_id, endpoint)
        headers = self._auth_headers()
        parameters: dict[str, Any] = {}
        if top_k is not None:
            parameters["top_k"] = top_k
        return list(
            self._executor.map(
                lambda text: self._classify_one(url, headers, text, parameters),
                texts,
            )
        )


@lru_cache
def get_hf_text_classification_client() -> HfTextClassificationClient:
    return HfTextClassificationClient(get_settings())
