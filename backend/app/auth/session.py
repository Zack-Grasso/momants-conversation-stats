from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings


@dataclass(frozen=True)
class AuthUser:
    email: str
    name: str
    picture: str | None = None


def create_access_token(user: AuthUser) -> str:
    settings = get_settings()
    expires = datetime.now(UTC) + timedelta(seconds=settings.auth_token_ttl_seconds)
    payload = {
        "sub": user.email,
        "name": user.name,
        "picture": user.picture,
        "exp": expires,
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.auth_secret, algorithm="HS256")


def decode_access_token(token: str) -> AuthUser:
    settings = get_settings()
    payload = jwt.decode(token, settings.auth_secret, algorithms=["HS256"])
    email = payload.get("sub")
    if not email:
        raise jwt.InvalidTokenError("Token missing subject")
    return AuthUser(
        email=str(email),
        name=str(payload.get("name") or email),
        picture=payload.get("picture"),
    )
