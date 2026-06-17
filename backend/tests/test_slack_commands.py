import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import pipeline_launcher


def _sign_request(secret: str, body: bytes) -> tuple[str, str]:
    timestamp = str(int(time.time()))
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    signature = "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    return timestamp, signature


@pytest.fixture
def slack_settings():
    return Settings(
        slack_bot_token="xoxb-test",
        slack_signing_secret="test-signing-secret",
        momants_api_base_url="https://momants.example",
        auth_allowed_email_domains="momants.ai",
    )


@pytest.fixture
def client(slack_settings):
    from app.config import get_settings

    get_settings.cache_clear()
    with (
        patch("app.config.get_settings", return_value=slack_settings),
        patch("app.api.slack.get_settings", return_value=slack_settings),
        patch("app.main.get_settings", return_value=slack_settings),
    ):
        with TestClient(create_app()) as test_client:
            yield test_client
    get_settings.cache_clear()


def test_slack_commands_requires_signature(client):
    body = urlencode({"command": "/report", "trigger_id": "123.456"}).encode()
    response = client.post(
        "/api/slack/commands",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 401


def test_slack_commands_opens_modal_for_report(client, slack_settings):
    body = urlencode({"command": "/report", "trigger_id": "123.456"}).encode()
    timestamp, signature = _sign_request(slack_settings.slack_signing_secret, body)
    mock_notifier = MagicMock()
    agents = [{"id": "agent-1", "name": "Acme Events"}]

    with (
        patch("app.api.slack.get_slack_notifier", return_value=mock_notifier),
        patch("app.api.slack._load_agent_options", return_value=agents),
    ):
        response = client.post(
            "/api/slack/commands",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": signature,
            },
        )

    assert response.status_code == 200
    mock_notifier.open_modal.assert_called_once()
    view = mock_notifier.open_modal.call_args.args[1]
    assert view["callback_id"] == "report_run"
    assert view["title"]["text"] == "Start report"


def test_slack_commands_opens_modal_for_reanalyze(client, slack_settings):
    body = urlencode({"command": "/report-reanalyze", "trigger_id": "123.456"}).encode()
    timestamp, signature = _sign_request(slack_settings.slack_signing_secret, body)
    mock_notifier = MagicMock()

    with (
        patch("app.api.slack.get_slack_notifier", return_value=mock_notifier),
        patch("app.api.slack._load_agent_options", return_value=[{"id": "agent-1", "name": "Acme"}]),
    ):
        response = client.post(
            "/api/slack/commands",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": signature,
            },
        )

    assert response.status_code == 200
    view = mock_notifier.open_modal.call_args.args[1]
    assert view["callback_id"] == "report_reanalyze"
    assert view["title"]["text"] == "Reanalyze report"


def test_slack_interactions_launches_run(client, slack_settings):
    payload = {
        "type": "view_submission",
        "user": {"id": "U123"},
        "container": {"channel_id": "C123"},
        "view": {
            "callback_id": "report_run",
            "state": {
                "values": {
                    "agent_block": {
                        "agent_select": {
                            "selected_option": {
                                "value": "agent-1",
                                "text": {"text": "Acme Events"},
                            }
                        }
                    }
                }
            },
        },
    }
    body = urlencode({"payload": json.dumps(payload)}).encode()
    timestamp, signature = _sign_request(slack_settings.slack_signing_secret, body)
    mock_notifier = MagicMock()
    mock_notifier.lookup_user_email.return_value = "user@momants.ai"

    with (
        patch("app.api.slack.get_slack_notifier", return_value=mock_notifier),
        patch("app.api.slack.launch_run") as mock_launch,
    ):
        response = client.post(
            "/api/slack/interactions",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": signature,
            },
        )

    assert response.status_code == 200
    assert response.json() == {"response_action": "clear"}
    mock_launch.assert_called_once_with("agent-1", "user@momants.ai")
    mock_notifier.post_ephemeral.assert_called_once()


def test_slack_interactions_launches_reanalyze(client, slack_settings):
    payload = {
        "type": "view_submission",
        "user": {"id": "U123"},
        "container": {"channel_id": "C123"},
        "view": {
            "callback_id": "report_reanalyze",
            "state": {
                "values": {
                    "agent_block": {
                        "agent_select": {
                            "selected_option": {
                                "value": "agent-2",
                                "text": {"text": "Beta Events"},
                            }
                        }
                    }
                }
            },
        },
    }
    body = urlencode({"payload": json.dumps(payload)}).encode()
    timestamp, signature = _sign_request(slack_settings.slack_signing_secret, body)
    mock_notifier = MagicMock()
    mock_notifier.lookup_user_email.return_value = "user@momants.ai"

    with (
        patch("app.api.slack.get_slack_notifier", return_value=mock_notifier),
        patch("app.api.slack.launch_reanalyze") as mock_launch,
    ):
        response = client.post(
            "/api/slack/interactions",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": signature,
            },
        )

    assert response.status_code == 200
    mock_launch.assert_called_once_with("agent-2", "user@momants.ai")


def test_slack_interactions_rejects_unauthorized_domain(client, slack_settings):
    payload = {
        "type": "view_submission",
        "user": {"id": "U999"},
        "view": {
            "callback_id": "report_run",
            "state": {"values": {"agent_block": {"agent_select": {"selected_option": {"value": "agent-1", "text": {"text": "Acme"}}}}}},
        },
    }
    body = urlencode({"payload": json.dumps(payload)}).encode()
    timestamp, signature = _sign_request(slack_settings.slack_signing_secret, body)
    mock_notifier = MagicMock()
    mock_notifier.lookup_user_email.return_value = "user@other.com"

    with patch("app.api.slack.get_slack_notifier", return_value=mock_notifier):
        response = client.post(
            "/api/slack/interactions",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": signature,
            },
        )

    assert response.status_code == 403


def test_slack_interactions_handles_busy_pipeline(client, slack_settings):
    payload = {
        "type": "view_submission",
        "user": {"id": "U123"},
        "container": {"channel_id": "C123"},
        "view": {
            "callback_id": "report_run",
            "state": {
                "values": {
                    "agent_block": {
                        "agent_select": {
                            "selected_option": {
                                "value": "agent-1",
                                "text": {"text": "Acme Events"},
                            }
                        }
                    }
                }
            },
        },
    }
    body = urlencode({"payload": json.dumps(payload)}).encode()
    timestamp, signature = _sign_request(slack_settings.slack_signing_secret, body)
    mock_notifier = MagicMock()
    mock_notifier.lookup_user_email.return_value = "user@momants.ai"

    with (
        patch("app.api.slack.get_slack_notifier", return_value=mock_notifier),
        patch("app.api.slack.launch_run", side_effect=pipeline_launcher.PipelineBusyError("busy")),
    ):
        response = client.post(
            "/api/slack/interactions",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": signature,
            },
        )

    assert response.status_code == 200
    assert response.json() == {"response_action": "clear"}
    mock_notifier.post_ephemeral.assert_called_once()
