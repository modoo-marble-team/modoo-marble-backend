from __future__ import annotations

import random
import string
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Cookie, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from tortoise.exceptions import IntegrityError

from app.config import settings
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    AuthUserResponse,
    KakaoCallbackRequest,
    LogoutResponse,
    RefreshResponse,
)
from app.utils.jwt import create_access_token, create_refresh_token, decode_token
from app.utils.refresh_session import (
    delete_refresh_session,
    get_refresh_session,
    save_refresh_session,
)

router = APIRouter(tags=["Auth"])
http_client = httpx.AsyncClient(timeout=10.0)


def _make_guest_nickname() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"Guest_{suffix}"


async def _make_unique_nickname(base: str) -> str:
    nick = base[:20]
    users = await User.filter(
        nickname__startswith=nick[:16],
        deleted_at__isnull=True,
    ).values_list("nickname", flat=True)

    users_set = set(users)
    if nick not in users_set:
        return nick

    for i in range(1, 1000):
        candidate = f"{nick[:16]}_{i}"
        if candidate not in users_set:
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
        settings.KAKAO_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail="Kakao token exchange failed")
    return resp.json()


async def _fetch_kakao_me(*, kakao_access_token: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {kakao_access_token}"}
    resp = await http_client.get(settings.KAKAO_ME_URL, headers=headers)
    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail="Kakao user info failed")
    return resp.json()


async def _login_with_kakao_code(*, code: str) -> tuple[User, bool]:
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

    return user, is_new_user


def _refresh_ttl_seconds() -> int:
    return settings.JWT_REFRESH_EXPIRE_DAYS * 24 * 60 * 60


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
        max_age=_refresh_ttl_seconds(),
        path=settings.REFRESH_COOKIE_PATH,
        domain=settings.REFRESH_COOKIE_DOMAIN or None,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        path=settings.REFRESH_COOKIE_PATH,
        domain=settings.REFRESH_COOKIE_DOMAIN or None,
    )


async def _issue_token_pair(*, user_id: int, is_guest: bool) -> tuple[str, str]:
    access_token = create_access_token(
        secret=settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
        exp_minutes=settings.JWT_ACCESS_EXPIRE_MINUTES,
        user_id=user_id,
        is_guest=is_guest,
    )

    jti = uuid4().hex
    refresh_token = create_refresh_token(
        secret=settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
        exp_days=settings.JWT_REFRESH_EXPIRE_DAYS,
        user_id=user_id,
        jti=jti,
    )
    await save_refresh_session(
        jti=jti,
        user_id=user_id,
        ttl_seconds=_refresh_ttl_seconds(),
    )
    return access_token, refresh_token


@router.get("/kakao/callback", summary="카카오 로그인 콜백")
async def kakao_callback_get(code: str = Query(..., min_length=1)) -> RedirectResponse:
    user, is_new_user = await _login_with_kakao_code(code=code)
    access_token, refresh_token = await _issue_token_pair(
        user_id=int(user.id),
        is_guest=user.is_guest,
    )

    if not settings.FRONTEND_LOGIN_REDIRECT:
        raise HTTPException(status_code=500, detail="FRONTEND_LOGIN_REDIRECT missing")

    redirect_url = (
        f"{settings.FRONTEND_LOGIN_REDIRECT}"
        f"?access_token={access_token}"
        f"&is_new_user={'true' if is_new_user else 'false'}"
    )
    resp = RedirectResponse(url=redirect_url, status_code=302)
    _set_refresh_cookie(resp, refresh_token)
    return resp


@router.post(
    "/kakao/callback",
    response_model=AuthResponse,
    summary="카카오 로그인 콜백(프론트 전달용)",
)
async def kakao_callback_post(
    body: KakaoCallbackRequest, response: Response
) -> AuthResponse:
    user, is_new_user = await _login_with_kakao_code(code=body.code)
    access_token, refresh_token = await _issue_token_pair(
        user_id=int(user.id),
        is_guest=user.is_guest,
    )
    _set_refresh_cookie(response, refresh_token)

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


@router.post("/guest", response_model=AuthResponse, summary="게스트 로그인")
async def guest_login(response: Response) -> AuthResponse:
    nickname = await _make_unique_nickname(_make_guest_nickname())
    try:
        user = await User.create(nickname=nickname, is_guest=True)
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="Guest creation conflict") from e

    access_token, refresh_token = await _issue_token_pair(
        user_id=int(user.id),
        is_guest=True,
    )
    _set_refresh_cookie(response, refresh_token)

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


@router.post("/refresh", response_model=RefreshResponse, summary="액세스 토큰 재발급")
async def refresh_access_token(
    response: Response,
    refresh_token: str | None = Cookie(
        default=None, alias=settings.REFRESH_COOKIE_NAME
    ),
) -> RefreshResponse:
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token missing")

    try:
        payload = decode_token(
            secret=settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
            token=refresh_token,
        )
    except ExpiredSignatureError as e:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Refresh token expired") from e
    except InvalidTokenError as e:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token") from e

    if payload.get("type") != "refresh":
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    sub = payload.get("sub")
    jti = payload.get("jti")
    if not sub or not jti:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    try:
        user_id = int(sub)
    except Exception as e:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token") from e

    stored_user_id = await get_refresh_session(str(jti))
    if stored_user_id is None or stored_user_id != str(user_id):
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = await User.get_or_none(id=user_id, deleted_at__isnull=True)
    if not user:
        await delete_refresh_session(str(jti))
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    await delete_refresh_session(str(jti))
    access_token, new_refresh_token = await _issue_token_pair(
        user_id=int(user.id),
        is_guest=user.is_guest,
    )
    _set_refresh_cookie(response, new_refresh_token)

    return RefreshResponse(
        access_token=access_token,
        expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", response_model=LogoutResponse, summary="로그아웃")
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(
        default=None, alias=settings.REFRESH_COOKIE_NAME
    ),
) -> LogoutResponse:
    if refresh_token:
        try:
            payload = decode_token(
                secret=settings.JWT_SECRET,
                algorithm=settings.JWT_ALGORITHM,
                token=refresh_token,
            )
            jti = payload.get("jti")
            if jti:
                await delete_refresh_session(str(jti))
        except Exception:
            pass

    _clear_refresh_cookie(response)
    return LogoutResponse(success=True)
