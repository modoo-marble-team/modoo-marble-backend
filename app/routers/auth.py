from __future__ import annotations

import random
import string
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from tortoise.exceptions import IntegrityError

from app.config import settings
from app.models.user import User
from app.utils.jwt import create_access_token

router = APIRouter(tags=["Auth"])
http_client = httpx.AsyncClient(timeout=10.0)


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


_KAUTH_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
_KAPI_ME_URL = "https://kapi.kakao.com/v2/user/me"


def _make_guest_nickname() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"Guest_{suffix}"


async def _make_unique_nickname(base: str) -> str:
    nick = base[:20]
    exists = await User.filter(nickname=nick, deleted_at__isnull=True).exists()
    if not exists:
        return nick

    for i in range(1, 1000):
        candidate = f"{nick[:16]}_{i}"
        exists2 = await User.filter(
            nickname=candidate, deleted_at__isnull=True
        ).exists()
        if not exists2:
            return candidate

    return f"{nick[:12]}_{random.randint(1000, 9999)}"


async def _exchange_code_for_token(*, code: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "grant_type": "authorization_code",
        "client_id": settings.KAKAO_CLIENT_ID,
        "redirect_uri": settings.KAKAO_REDIRECT_URI,
        "code": code,
    }
    if settings.KAKAO_CLIENT_SECRET:
        data["client_secret"] = settings.KAKAO_CLIENT_SECRET

    resp = await http_client.post(
        _KAUTH_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=500, detail="Kakao token exchange failed")
    return resp.json()


async def _fetch_kakao_me(*, kakao_access_token: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {kakao_access_token}"}
    resp = await http_client.get(_KAPI_ME_URL, headers=headers)
    if resp.status_code >= 400:
        raise HTTPException(status_code=500, detail="Kakao user info failed")
    return resp.json()


async def _login_with_kakao_code(*, code: str) -> AuthResponse:
    if not settings.KAKAO_CLIENT_ID or not settings.KAKAO_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="Kakao OAuth env missing")

    token = await _exchange_code_for_token(code=code)
    kakao_access = token.get("access_token")
    if not kakao_access:
        raise HTTPException(status_code=500, detail="Kakao access_token missing")

    me = await _fetch_kakao_me(kakao_access_token=kakao_access)

    kakao_id = me.get("id")
    if kakao_id is None:
        raise HTTPException(status_code=500, detail="Kakao id missing")

    account = me.get("kakao_account") or {}
    profile = account.get("profile") or {}
    raw_nickname = str(profile.get("nickname") or "KakaoUser")
    profile_image_url = profile.get("profile_image_url")

    kakao_id_str = str(kakao_id)
    user = await User.get_or_none(kakao_id=kakao_id_str, deleted_at__isnull=True)

    is_new_user = False
    if user:
        updated_fields: list[str] = []
        if (
            profile_image_url is not None
            and user.profile_image_url != profile_image_url
        ):
            user.profile_image_url = profile_image_url
            updated_fields.append("profile_image_url")
        if updated_fields:
            updated_fields.append("updated_at")
            await user.save(update_fields=updated_fields)
    else:
        nickname = await _make_unique_nickname(raw_nickname)
        try:
            user = await User.create(
                kakao_id=kakao_id_str,
                nickname=nickname,
                profile_image_url=profile_image_url,
                is_guest=False,
            )
            is_new_user = True
        except IntegrityError as e:
            raise HTTPException(status_code=409, detail="User creation conflict") from e

    access_token = create_access_token(
        secret=settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
        exp_minutes=settings.JWT_ACCESS_EXPIRE_MINUTES,
        user_id=int(user.id),
        is_guest=False,
    )

    return AuthResponse(
        access_token=access_token,
        user=AuthUserResponse(
            id=int(user.id),
            nickname=user.nickname,
            profile_image_url=user.profile_image_url,
            is_guest=user.is_guest,
        ),
        is_new_user=is_new_user,
    )


@router.get("/kakao/callback", summary="카카오 로그인 콜백")
async def kakao_callback_get(code: str = Query(..., min_length=1)) -> RedirectResponse:
    result = await _login_with_kakao_code(code=code)

    if not settings.FRONTEND_LOGIN_REDIRECT:
        raise HTTPException(status_code=500, detail="FRONTEND_LOGIN_REDIRECT missing")

    redirect_url = (
        f"{settings.FRONTEND_LOGIN_REDIRECT}"
        f"?access_token={result.access_token}"
        f"&is_new_user={'true' if result.is_new_user else 'false'}"
    )
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post(
    "/kakao/callback",
    response_model=AuthResponse,
    summary="카카오 로그인 콜백(프론트 전달용)",
)
async def kakao_callback_post(body: KakaoCallbackRequest) -> AuthResponse:
    return await _login_with_kakao_code(code=body.code)


@router.post("/guest", response_model=AuthResponse, summary="게스트 로그인")
async def guest_login(request: Request) -> AuthResponse:
    nickname = await _make_unique_nickname(_make_guest_nickname())
    try:
        user = await User.create(nickname=nickname, is_guest=True)
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="Guest creation conflict") from e

    access_token = create_access_token(
        secret=settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
        exp_minutes=settings.JWT_ACCESS_EXPIRE_MINUTES,
        user_id=int(user.id),
        is_guest=True,
    )

    return AuthResponse(
        access_token=access_token,
        user=AuthUserResponse(
            id=int(user.id),
            nickname=user.nickname,
            profile_image_url=user.profile_image_url,
            is_guest=user.is_guest,
        ),
        is_new_user=False,
    )
