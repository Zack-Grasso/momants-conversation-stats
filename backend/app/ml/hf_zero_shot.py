"""Remote zero-shot / NLI inference via Hugging Face Serverless API or Inference Endpoint."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

ZeroShotResult = dict[str, list[str] | list[float]]
HF_ROUTER_URL = "https://router.huggingface.co/hf-inference/models/{model_id}"
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


class HfZeroShotClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # One shared, thread-safe client (connection pooling) reused across all calls, and a
        # single process-wide executor so the *total* number of in-flight HF requests is bounded
        # by hf_inference_concurrency regardless of how many callers dispatch batches.
        self._client = httpx.Client(timeout=settings.hf_inference_timeout_seconds)
        concurrency = max(1, settings.hf_inference_concurrency)
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="hf-infer")

    def _inference_url(self) -> str:
        mode = self._settings.intent_inference_mode
        if mode == "hf_endpoint":
            endpoint = self._settings.intent_inference_endpoint.strip()
            if not endpoint:
                raise ValueError("INTENT_INFERENCE_ENDPOINT is required when INTENT_INFERENCE_MODE is hf_endpoint")
            return endpoint.rstrip("/")

        return HF_ROUTER_URL.format(model_id=self._settings.intent_model)

    def _auth_headers(self) -> dict[str, str]:
        token = self._settings.hf_token.strip()
        if not token:
            raise ValueError("HF_TOKEN is required when INTENT_INFERENCE_MODE is hf_api or hf_endpoint")
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _normalize_result(raw: Any) -> ZeroShotResult:
        if isinstance(raw, list):
            if not raw:
                raise ValueError("Empty zero-shot response from Hugging Face")
            if isinstance(raw[0], dict) and "label" in raw[0]:
                return {
                    "labels": [str(item["label"]) for item in raw],
                    "scores": [float(item["score"]) for item in raw],
                }
            raw = raw[0]

        if isinstance(raw, dict) and "labels" in raw and "scores" in raw:
            return {"labels": list(raw["labels"]), "scores": [float(s) for s in raw["scores"]]}

        raise ValueError(f"Unexpected zero-shot response shape: {raw!r}")

    @staticmethod
    def _retry_delay(response: httpx.Response, backoff: float, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return backoff * (2**attempt)

    def _classify_one(
        self,
        url: str,
        headers: dict[str, str],
        text: str,
        parameters: dict[str, Any],
    ) -> ZeroShotResult:
        max_retries = self._settings.hf_inference_max_retries
        backoff = self._settings.hf_inference_retry_backoff_seconds
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                response = self._client.post(
                    url,
                    headers=headers,
                    json={"inputs": text, "parameters": parameters},
                )
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                    delay = self._retry_delay(response, backoff, attempt)
                    logger.warning(
                        "HF inference returned %s (attempt %s/%s), retrying in %.1fs",
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
                    "HF inference error %s (attempt %s/%s), retrying in %.1fs",
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
        candidate_labels: list[str],
        *,
        hypothesis_template: str | None = None,
    ) -> list[ZeroShotResult]:
        if not texts:
            return []

        url = self._inference_url()
        headers = self._auth_headers()
        parameters: dict[str, Any] = {
            "candidate_labels": candidate_labels,
            "multi_label": False,
        }
        if hypothesis_template:
            parameters["hypothesis_template"] = hypothesis_template

        # Dispatch the per-text requests concurrently; executor.map preserves input order and
        # re-raises the first failure (matching the previous fail-fast behaviour).
        return list(
            self._executor.map(
                lambda text: self._classify_one(url, headers, text, parameters),
                texts,
            )
        )


@lru_cache
def get_hf_zero_shot_client() -> HfZeroShotClient:
    return HfZeroShotClient(get_settings())
