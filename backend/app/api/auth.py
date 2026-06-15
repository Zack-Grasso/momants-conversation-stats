import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth.deps import COOKIE_NAME, get_current_user, get_optional_user
from app.auth.google_oauth import (
    build_google_auth_url,
    create_oauth_state,
    exchange_code_for_user,
    is_allowed_email,
)
from app.auth.session import AuthUser, create_access_token
from app.cache import get_cache_client
from app.config import get_settings
from app.schemas.auth import (
    AuthStatusResponse,
    AuthUserRead,
    GoogleAuthUrlResponse,
    GoogleCallbackRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()
STATE_TTL_SECONDS = 600


def _store_oauth_state(state: str) -> None:
    client = get_cache_client()
    client.setex(f"oauth:state:{state}", STATE_TTL_SECONDS, "1")


def _consume_oauth_state(state: str) -> bool:
    client = get_cache_client()
    key = f"oauth:state:{state}"
    if client.get(key) != "1":
        return False
    client.delete(key)
    return True


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        max_age=settings.auth_token_ttl_seconds,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(user: AuthUser | None = Depends(get_optional_user)) -> AuthStatusResponse:
    settings = get_settings()
    return AuthStatusResponse(
        authenticated=user is not None,
        user=AuthUserRead(email=user.email, name=user.name, picture=user.picture) if user else None,
        auth_enabled=settings.auth_enabled,
    )


@router.get("/google/url", response_model=GoogleAuthUrlResponse)
def google_auth_url() -> GoogleAuthUrlResponse:
    settings = get_settings()
    if not settings.auth_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication is disabled")
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google OAuth is not configured")

    state = create_oauth_state()
    _store_oauth_state(state)
    return GoogleAuthUrlResponse(url=build_google_auth_url(state))


@router.post("/google/callback", response_model=AuthUserRead)
def google_callback(payload: GoogleCallbackRequest, response: Response) -> AuthUserRead:
    settings = get_settings()
    if not settings.auth_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication is disabled")
    if not _consume_oauth_state(payload.state):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OAuth state")

    try:
        user = exchange_code_for_user(payload.code)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Google OAuth callback failed")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google sign-in failed") from exc

    if not is_allowed_email(user.email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only internal Momants accounts are allowed",
        )

    token = create_access_token(user)
    _set_session_cookie(response, token)
    return AuthUserRead(email=user.email, name=user.name, picture=user.picture)


@router.get("/me", response_model=AuthUserRead)
def auth_me(user: AuthUser = Depends(get_current_user)) -> AuthUserRead:
    return AuthUserRead(email=user.email, name=user.name, picture=user.picture)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    _clear_session_cookie(response)
