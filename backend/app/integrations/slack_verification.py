"""Verify inbound Slack HTTP requests using the signing secret."""

from __future__ import annotations

import hashlib
import hmac
import time


def verify_slack_signature(
    signing_secret: str,
    *,
    timestamp: str,
    body: bytes,
    signature: str,
    max_age_seconds: int = 60 * 5,
) -> bool:
    """Return True when the request matches Slack's v0 signature scheme."""
    secret = signing_secret.strip()
    if not secret or not timestamp or not signature:
        return False
    try:
        request_age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    if request_age > max_age_seconds:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    digest = hmac.new(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)
