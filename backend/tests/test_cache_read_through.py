from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from app.api.cache_read import get_or_compute, get_or_compute_model, get_or_compute_model_list


class SampleModel(BaseModel):
    name: str
    count: int = 0


def test_get_or_compute_returns_cached_without_calling_builder():
    with patch("app.api.cache_read.cache_get", return_value={"name": "cached", "count": 3}):
        builder = MagicMock()
        result = get_or_compute_model("test:key", SampleModel, builder)

    assert result.name == "cached"
    assert result.count == 3
    builder.assert_not_called()


def test_get_or_compute_miss_calls_builder_and_caches():
    stored: dict[str, object] = {}

    def fake_get(key: str):
        return stored.get(key)

    def fake_set(key: str, value: object, ttl: int | None = None) -> None:
        stored[key] = value

    builder = MagicMock(return_value={"name": "fresh", "count": 1})

    with patch("app.api.cache_read.cache_get", side_effect=fake_get):
        with patch("app.api.cache_read.cache_set", side_effect=fake_set):
            result = get_or_compute_model("test:key", SampleModel, builder)

    assert result.name == "fresh"
    builder.assert_called_once()
    assert stored["test:key"] == {"name": "fresh", "count": 1}


def test_get_or_compute_second_call_uses_cache():
    stored: dict[str, object] = {"test:key": {"name": "warm", "count": 2}}
    builder = MagicMock(return_value={"name": "should-not-run", "count": 99})

    with patch("app.api.cache_read.cache_get", side_effect=lambda key: stored.get(key)):
        with patch("app.api.cache_read.cache_set") as mock_set:
            result = get_or_compute_model("test:key", SampleModel, builder)

    assert result.name == "warm"
    builder.assert_not_called()
    mock_set.assert_not_called()


def test_get_or_compute_model_list():
    stored: dict[str, object] = {}

    def fake_get(key: str):
        return stored.get(key)

    def fake_set(key: str, value: object, ttl: int | None = None) -> None:
        stored[key] = value

    builder = MagicMock(return_value=[{"name": "a", "count": 1}, {"name": "b", "count": 2}])

    with patch("app.api.cache_read.cache_get", side_effect=fake_get):
        with patch("app.api.cache_read.cache_set", side_effect=fake_set):
            results = get_or_compute_model_list("test:list", SampleModel, builder)

    assert [item.name for item in results] == ["a", "b"]
    assert stored["test:list"] == [{"name": "a", "count": 1}, {"name": "b", "count": 2}]


def test_get_or_compute_raw_value():
    stored: dict[str, object] = {}

    with patch("app.api.cache_read.cache_get", side_effect=lambda key: stored.get(key)):
        with patch("app.api.cache_read.cache_set", side_effect=lambda key, value, ttl=None: stored.update({key: value})):
            first = get_or_compute("raw:key", lambda: {"items": [1, 2]})
            second = get_or_compute("raw:key", lambda: {"items": [9, 9]})

    assert first == {"items": [1, 2]}
    assert second == {"items": [1, 2]}
