from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, Request, status

from app.auth.session import AuthUser, decode_access_token
from app.config import get_settings

COOKIE_NAME = "session"


def _dev_user() -> AuthUser:
    return AuthUser(email="dev@local", name="Dev User")


def get_optional_user(request: Request) -> AuthUser | None:
    settings = get_settings()
    if not settings.auth_enabled:
        return _dev_user()

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        return decode_access_token(token)
    except jwt.PyJWTError:
        return None


def get_current_user(user: AuthUser | None = Depends(get_optional_user)) -> AuthUser:
    settings = get_settings()
    if not settings.auth_enabled:
        return _dev_user()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user
