from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.ml.hf_zero_shot import HfZeroShotClient


def test_classify_batch_uses_serverless_client():
    settings = Settings(
        intent_inference_mode="hf_api",
        hf_token="hf_test",
        intent_model="MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7",
    )
    client = HfZeroShotClient(settings)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = [
        {"label": "refund", "score": 0.9},
        {"label": "general", "score": 0.1},
    ]

    # The client holds a single persistent httpx.Client; per-text requests are dispatched
    # concurrently through its shared executor, so patch the instance's post method.
    with patch.object(client._client, "post", return_value=mock_response) as mock_post:
        results = client.classify_batch(
            ["I want my money back", "Where is my order?"],
            ["refund", "shipping", "general"],
            hypothesis_template="The customer message is about {}.",
        )

    assert len(results) == 2
    assert results[0]["labels"][0] == "refund"
    assert mock_post.call_count == 2
    first_call = mock_post.call_args_list[0]
    assert "MoritzLaurer" in first_call.args[0]
    assert first_call.kwargs["json"]["parameters"]["hypothesis_template"] == "The customer message is about {}."


def test_endpoint_mode_requires_url():
    settings = Settings(
        intent_inference_mode="hf_endpoint",
        hf_token="hf_test",
        intent_inference_endpoint="",
    )
    client = HfZeroShotClient(settings)
    with pytest.raises(ValueError, match="INTENT_INFERENCE_ENDPOINT"):
        client.classify_batch(["hello"], ["general"])


@patch("app.ml.hf_zero_shot.get_hf_zero_shot_client")
def test_model_registry_routes_intent_batch_to_hf(mock_get_client):
    from app.ml.model_registry import ModelRegistry

    mock_client = MagicMock()
    mock_client.classify_batch.return_value = [
        {
            "labels": ["a refund or reimbursement", "a general question"],
            "scores": [0.8, 0.2],
        }
    ]
    mock_get_client.return_value = mock_client

    registry = ModelRegistry()
    registry._settings = Settings(
        intent_inference_mode="hf_api",
        hf_token="hf_test",
        intent_labels="refund,general",
    )

    results = registry.classify_intents_batch(["I need a refund please"], "en", [None])
    assert results[0][0] == "refund"
    mock_client.classify_batch.assert_called_once()
