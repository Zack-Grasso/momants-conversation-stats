import hashlib
import hmac
import time

from app.integrations.slack_verification import verify_slack_signature


def test_verify_slack_signature_accepts_valid_request():
    secret = "test-signing-secret"
    body = b"command=%2Freport&trigger_id=123"
    timestamp = str(int(time.time()))
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    signature = "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()

    assert verify_slack_signature(secret, timestamp=timestamp, body=body, signature=signature) is True


def test_verify_slack_signature_rejects_invalid_signature():
    body = b"command=%2Freport"
    timestamp = str(int(time.time()))
    assert verify_slack_signature(
        "secret",
        timestamp=timestamp,
        body=body,
        signature="v0=deadbeef",
    ) is False


def test_verify_slack_signature_rejects_stale_timestamp():
    secret = "test-signing-secret"
    body = b"command=%2Freport"
    timestamp = str(int(time.time()) - 3600)
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    signature = "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()

    assert verify_slack_signature(secret, timestamp=timestamp, body=body, signature=signature) is False
