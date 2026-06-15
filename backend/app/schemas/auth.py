from pydantic import BaseModel, EmailStr


class GoogleAuthUrlResponse(BaseModel):
    url: str


class GoogleCallbackRequest(BaseModel):
    code: str
    state: str


class AuthUserRead(BaseModel):
    email: EmailStr
    name: str
    picture: str | None = None


class AuthStatusResponse(BaseModel):
    authenticated: bool
    user: AuthUserRead | None = None
    auth_enabled: bool
