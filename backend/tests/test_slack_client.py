from unittest.mock import MagicMock, patch

from app.config import Settings
from app.integrations import slack_client
from app.integrations.slack_client import (
    MILESTONE_PDF_READY,
    MILESTONE_STARTED,
    SlackNotifier,
    notify_milestone,
)


def _ok(json_body: dict) -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = json_body
    return response


def test_notify_milestone_no_email_is_noop():
    assert notify_milestone(None, "agent-123", MILESTONE_STARTED) is False


def test_notify_milestone_disabled_is_noop():
    with patch.object(slack_client, "get_settings", return_value=Settings(slack_bot_token="")):
        assert notify_milestone("user@momants.ai", "agent-123", MILESTONE_STARTED) is False


def test_notify_milestone_sends_dm_when_enabled():
    settings = Settings(slack_bot_token="xoxb-test", app_base_url="https://report.momants.ai")
    lookup = _ok({"ok": True, "user": {"id": "U123"}})
    post = _ok({"ok": True})

    with (
        patch.object(slack_client, "get_settings", return_value=settings),
        patch.object(slack_client.httpx, "get", return_value=lookup) as mock_get,
        patch.object(slack_client.httpx, "post", return_value=post) as mock_post,
    ):
        sent = notify_milestone(
            "user@momants.ai",
            "agent-12345678",
            MILESTONE_PDF_READY,
            link="https://report.momants.ai/results?agent_id=agent-12345678",
        )

    assert sent is True
    mock_get.assert_called_once()
    assert mock_get.call_args.kwargs["params"] == {"email": "user@momants.ai"}
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["channel"] == "U123"
    assert "results?agent_id=agent-12345678" in payload["text"]


def test_notify_milestone_swallows_api_errors():
    settings = Settings(slack_bot_token="xoxb-test")
    with (
        patch.object(slack_client, "get_settings", return_value=settings),
        patch.object(slack_client.httpx, "get", side_effect=RuntimeError("boom")),
    ):
        assert notify_milestone("user@momants.ai", "agent-123", MILESTONE_STARTED) is False


def test_message_uses_agent_name_not_id():
    settings = Settings(slack_bot_token="xoxb-test")
    lookup = _ok({"ok": True, "user": {"id": "U1"}})
    post = _ok({"ok": True})
    with (
        patch.object(slack_client, "get_settings", return_value=settings),
        patch.object(slack_client.httpx, "get", return_value=lookup),
        patch.object(slack_client.httpx, "post", return_value=post) as mock_post,
    ):
        slack_client.notify_milestone(
            "user@momants.ai",
            "agent-12345678",
            slack_client.MILESTONE_INGEST_STARTED,
            agent_name="Acme Events",
        )
    text = mock_post.call_args.kwargs["json"]["text"]
    assert "Acme Events" in text
    assert "Ingest started" in text
    assert "agent-12345678" not in text


def test_dm_returns_false_when_user_not_found():
    notifier = SlackNotifier("xoxb-test")
    lookup = _ok({"ok": False, "error": "users_not_found"})
    with patch.object(slack_client.httpx, "get", return_value=lookup):
        assert notifier.dm("missing@momants.ai", "hello") is False
