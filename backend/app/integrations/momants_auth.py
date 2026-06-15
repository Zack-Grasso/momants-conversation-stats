import logging
import threading
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class MomantsAuthClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._settings = get_settings()

    def get_access_token(self) -> str:
        with self._lock:
            if self._access_token and time.time() < self._expires_at:
                return self._access_token
            return self._login()

    def _login(self) -> str:
        base_url = self._settings.momants_api_base_url.rstrip("/")
        if not base_url:
            raise RuntimeError("MOMANTS_API_BASE_URL is not configured")

        identifier = self._settings.momants_dev_identifier
        password = self._settings.momants_dev_password
        if not identifier or not password:
            raise RuntimeError("MOMANTS_DEV_IDENTIFIER and MOMANTS_DEV_PASSWORD are required")

        url = f"{base_url}/dashboard/login"
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json={"identifier": identifier, "password": password})
            response.raise_for_status()
            data = response.json()

        access = data.get("access")
        if not access:
            raise RuntimeError("Momants login response missing access token")

        lifetime = int(data.get("access_token_lifetime_seconds") or 3600)
        self._access_token = access
        self._expires_at = time.time() + max(lifetime - 60, 60)
        logger.info("Momants access token refreshed (expires in %ss)", lifetime)
        return access


_auth_client: MomantsAuthClient | None = None
_auth_lock = threading.Lock()


def get_momants_auth() -> MomantsAuthClient:
    global _auth_client
    with _auth_lock:
        if _auth_client is None:
            _auth_client = MomantsAuthClient()
        return _auth_client
