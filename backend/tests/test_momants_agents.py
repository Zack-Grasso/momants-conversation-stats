from unittest.mock import patch

from app.integrations.momants_client import MomantsClient


def _client() -> MomantsClient:
    with patch.object(MomantsClient, "__init__", return_value=None):
        return MomantsClient()


def test_list_agents_parses_me_payload():
    client = _client()
    payload = {
        "email": "user@momants.ai",
        "agents": [
            {"id": "a1", "name": "Alpha"},
            {"id": "a2", "name": ""},
            {"name": "NoId"},
        ],
    }
    with patch.object(client, "get_me", return_value=payload):
        result = client.list_agents()
    assert result == [{"id": "a1", "name": "Alpha"}, {"id": "a2", "name": ""}]


def test_list_agents_handles_empty():
    client = _client()
    with patch.object(client, "get_me", return_value={"email": "x", "agents": []}):
        assert client.list_agents() == []
