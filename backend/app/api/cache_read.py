from typing import Any, TypeVar

from fastapi import HTTPException, status
from pydantic import BaseModel

from app.cache import CACHE_NOT_READY_DETAIL, cache_get

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
