from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import HTTPException, status
from pydantic import BaseModel

from app.cache import CACHE_NOT_READY_DETAIL, cache_get, cache_set

T = TypeVar("T", bound=BaseModel)


def require_cached(key: str) -> Any:
    cached = cache_get(key)
    if cached is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=CACHE_NOT_READY_DETAIL)
    return cached


def require_cached_model(key: str, model: type[T]) -> T:
    return model(**require_cached(key))


def require_cached_model_list(key: str, model: type[T]) -> list[T]:
    return [model(**item) for item in require_cached(key)]


def get_or_compute(key: str, builder: Callable[[], Any], *, ttl: int | None = None) -> Any:
    """Return cached payload or compute, store, and return on miss."""
    cached = cache_get(key)
    if cached is not None:
        return cached
    value = builder()
    cache_set(key, value, ttl=ttl)
    return value


def get_or_compute_model(key: str, model: type[T], builder: Callable[[], Any], *, ttl: int | None = None) -> T:
    return model(**get_or_compute(key, builder, ttl=ttl))


def get_or_compute_model_list(
    key: str,
    model: type[T],
    builder: Callable[[], list[Any]],
    *,
    ttl: int | None = None,
) -> list[T]:
    return [model(**item) for item in get_or_compute(key, builder, ttl=ttl)]
