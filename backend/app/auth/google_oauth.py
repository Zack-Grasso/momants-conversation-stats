from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx

from app.auth.session import AuthUser
from app.config import get_settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def create_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def build_google_auth_url(state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_user(code: str) -> AuthUser:
    settings = get_settings()
    with httpx.Client(timeout=30.0) as client:
        token_response = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_response.raise_for_status()
        tokens = token_response.json()
        access_token = tokens.get("access_token")
        if not access_token:
            raise RuntimeError("Google token response missing access_token")

        user_response = client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_response.raise_for_status()
        profile = user_response.json()

    email = profile.get("email")
    if not email:
        raise RuntimeError("Google profile missing email")
    if not profile.get("email_verified", False):
        raise PermissionError("Google email is not verified")

    return AuthUser(
        email=str(email).lower(),
        name=str(profile.get("name") or email),
        picture=profile.get("picture"),
    )


def is_allowed_email(email: str) -> bool:
    settings = get_settings()
    if not settings.auth_allowed_email_domains:
        return True
    domain = email.split("@")[-1].lower()
    allowed = {item.lower() for item in settings.auth_allowed_email_domain_list}
    return domain in allowed
