import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.integrations.momants_auth import get_momants_auth
from app.utils.datetime_utils import format_momants_datetime

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.WriteTimeout,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


class _RateLimiter:
    """Process-wide minimum-interval limiter shared across all ingest threads.

    Each caller reserves the next time slot under the lock, then sleeps (outside the lock)
    until that slot, so concurrent batches collectively stay under max_requests_per_second.
    """

    def __init__(self, max_per_second: float) -> None:
        self._min_interval = 1.0 / max_per_second if max_per_second and max_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            scheduled = max(time.monotonic(), self._next_allowed)
            self._next_allowed = scheduled + self._min_interval
        delay = scheduled - time.monotonic()
        if delay > 0:
            time.sleep(delay)


_LIMITER_LOCK = threading.Lock()
_RATE_LIMITER: _RateLimiter | None = None


def _get_rate_limiter() -> _RateLimiter:
    global _RATE_LIMITER
    if _RATE_LIMITER is None:
        with _LIMITER_LOCK:
            if _RATE_LIMITER is None:
                _RATE_LIMITER = _RateLimiter(get_settings().momants_api_max_requests_per_second)
    return _RATE_LIMITER


class MomantsClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._auth = get_momants_auth()
        self._client: httpx.Client | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._auth.get_access_token()}"}

    def _base_url(self) -> str:
        base = self._settings.momants_api_base_url.rstrip("/")
        if not base:
            raise RuntimeError("MOMANTS_API_BASE_URL is not configured")
        return base

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self._settings.momants_api_connect_timeout_seconds,
            read=self._settings.momants_api_read_timeout_seconds,
            write=30.0,
            pool=30.0,
        )

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout())
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url()}{path}"
        max_retries = self._settings.momants_api_max_retries
        backoff = self._settings.momants_api_retry_backoff_seconds
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                _get_rate_limiter().acquire()
                response = self._get_client().request(method, url, headers=self._headers(), **kwargs)
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                    delay = self._retry_delay(response, backoff, attempt)
                    logger.warning(
                        "Momants API %s %s returned %s (attempt %s/%s), retrying in %.1fs",
                        method,
                        path,
                        response.status_code,
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                return response.json()
            except RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                if attempt >= max_retries:
                    break
                delay = backoff * (2**attempt)
                logger.warning(
                    "Momants API %s %s failed: %s (attempt %s/%s), retrying in %.1fs",
                    method,
                    path,
                    exc,
                    attempt + 1,
                    max_retries + 1,
                    delay,
                )
                time.sleep(delay)
            except httpx.HTTPStatusError:
                raise

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _retry_delay(response: httpx.Response, backoff: float, attempt: int) -> float:
        """Prefer the server's Retry-After hint (seconds) on 429s; else exponential backoff."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return backoff * (2**attempt)

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        return self._request("GET", "/studio/agent", params={"agent_id": agent_id})

    def get_me(self) -> dict[str, Any]:
        """Account profile, including the list of agents (id + name) the user owns."""
        return self._request("GET", "/dashboard/me")

    def list_agents(self) -> list[dict[str, str]]:
        """Return the user's agents as ``[{"id", "name"}]`` (skips entries without an id)."""
        data = self.get_me()
        agents = data.get("agents") or []
        result: list[dict[str, str]] = []
        for agent in agents:
            agent_id = agent.get("id")
            if not agent_id:
                continue
            result.append({"id": str(agent_id), "name": (agent.get("name") or "").strip()})
        return result

    def get_dashboard_stats(
        self,
        agent_id: str,
        *,
        time_unit: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/dashboard/stats",
            params={
                "agent_id": agent_id,
                "time_unit": time_unit,
                "start_date": format_momants_datetime(start_date),
                "end_date": format_momants_datetime(end_date),
            },
        )

    def list_inbox_page(
        self,
        agent_id: str,
        page: int = 1,
        page_size: int | None = None,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        size = page_size or self._settings.ingestion_page_size
        params: dict[str, Any] = {"agent_id": agent_id, "page": page, "page_size": size}
        if start_date is not None:
            params["start_date"] = format_momants_datetime(start_date)
        if end_date is not None:
            params["end_date"] = format_momants_datetime(end_date)
        return self._request("GET", "/dashboard/inbox", params=params)

    def collect_inbox_entries(
        self,
        agent_id: str,
        limit: int,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        page = 1

        while len(collected) < limit:
            data = self.list_inbox_page(
                agent_id,
                page=page,
                start_date=start_date,
                end_date=end_date,
            )
            entries = data.get("inbox_entries") or []
            if not entries:
                break

            for entry in entries:
                collected.append(entry)
                if len(collected) >= limit:
                    break

            total_pages = int(data.get("total_pages") or 1)
            if page >= total_pages:
                break
            page += 1

        return collected[:limit]

    def collect_inbox_entries_by_window(
        self,
        agent_id: str,
        *,
        until_date: datetime | None = None,
        now: datetime | None = None,
        window_days: int | None = None,
        max_empty_windows: int | None = None,
        hard_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Sweep the inbox backwards in fixed date windows and return deduped entries.

        Page-from-1 listing is capped (page size and page count) and can't reach every
        conversation. Instead we fetch ``[end - window_days, end]``, step the window back,
        and repeat:
          - Incremental (``until_date`` set): stop once the window reaches the watermark.
          - Full run (no ``until_date``): stop after ``max_empty_windows`` consecutive empty
            windows (default 4 weeks with zero conversations).
        """
        settings = self._settings
        window_days = window_days or settings.ingestion_window_days
        if max_empty_windows is None:
            max_empty_windows = settings.ingestion_max_empty_windows
        hard_limit = hard_limit or settings.ingestion_max_conversations
        end = now or datetime.now(timezone.utc)

        seen: set[str] = set()
        collected: list[dict[str, Any]] = []
        consecutive_empty = 0

        while len(collected) < hard_limit:
            start = end - timedelta(days=window_days)
            stop_after = False
            if until_date is not None and start <= until_date:
                start = until_date
                stop_after = True

            remaining = hard_limit - len(collected)
            window_entries = self.collect_inbox_entries(
                agent_id, remaining, start_date=start, end_date=end
            )

            if window_entries:
                consecutive_empty = 0
                for entry in window_entries:
                    cid = entry.get("conversation_id")
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    collected.append(entry)
                    if len(collected) >= hard_limit:
                        break
            else:
                consecutive_empty += 1

            if stop_after:
                break
            if until_date is None and consecutive_empty >= max_empty_windows:
                break
            end = start

        return collected[:hard_limit]

    def collect_conversation_ids(
        self,
        agent_id: str,
        limit: int,
        skip: int = 0,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if skip == 0 and start_date is None and end_date is None:
            return self.collect_inbox_entries(agent_id, limit)

        collected: list[dict[str, Any]] = []
        page = 1
        skipped = 0

        while len(collected) < limit:
            data = self.list_inbox_page(
                agent_id,
                page=page,
                start_date=start_date,
                end_date=end_date,
            )
            entries = data.get("inbox_entries") or []
            if not entries:
                break

            for entry in entries:
                if skipped < skip:
                    skipped += 1
                    continue
                collected.append(entry)
                if len(collected) >= limit:
                    break

            total_pages = int(data.get("total_pages") or 1)
            if page >= total_pages:
                break
            page += 1

        return collected[:limit]

    def get_conversation_page(
        self,
        agent_id: str,
        conversation_id: str,
        before: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"agent_id": agent_id, "limit": limit}
        if before:
            params["before"] = before
        return self._request("GET", f"/dashboard/inbox/{conversation_id}", params=params)

    def fetch_conversation(
        self, agent_id: str, conversation_id: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Return (details, all_messages) for a conversation.

        ``details`` (taken from the first page) carries member_information used to resolve
        the messaging channel — the inbox listing leaves messaging_integration_type empty
        for non-embedded channels (e.g. WhatsApp), so the channel has to be derived here.
        """
        all_messages: list[dict[str, Any]] = []
        details: dict[str, Any] | None = None
        before: str | None = None

        while True:
            data = self.get_conversation_page(agent_id, conversation_id, before=before)
            if details is None:
                details = data.get("details")
            messages = data.get("messages") or []
            if not messages:
                break

            all_messages.extend(messages)
            if len(messages) < 50:
                break

            oldest = messages[-1]
            before = oldest.get("created_at")
            if not before:
                break

            time.sleep(self._settings.ingestion_fetch_delay_seconds)

        return details, all_messages

    def fetch_all_messages(self, agent_id: str, conversation_id: str) -> list[dict[str, Any]]:
        _, messages = self.fetch_conversation(agent_id, conversation_id)
        return messages


def get_momants_client() -> MomantsClient:
    return MomantsClient()
