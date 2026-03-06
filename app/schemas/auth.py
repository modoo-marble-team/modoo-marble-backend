from __future__ import annotations

from pydantic import BaseModel, Field


class KakaoCallbackRequest(BaseModel):
    code: str = Field(..., min_length=1)


class AuthUserResponse(BaseModel):
    id: int
    nickname: str
    profile_image_url: str | None = None
    is_guest: bool


class AuthResponse(BaseModel):
    access_token: str
    user: AuthUserResponse
    is_new_user: bool
