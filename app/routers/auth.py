from __future__ import annotations

from urllib.parse import urlencode
from uuid import uuid4

import httpx
import structlog
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from app.config import settings
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    AuthUserResponse,
    KakaoCallbackRequest,
    LogoutResponse,
    RefreshResponse,
)
from app.services.auth_service import AuthService
from app.utils.auth_dep import AuthUser, get_auth_user
from app.utils.jwt import create_access_token, create_refresh_token, decode_token
from app.utils.refresh_session import (
    delete_refresh_session,
    get_and_delete_refresh_session,
    save_refresh_session,
)

router = APIRouter(tags=["Auth"])
http_client = httpx.AsyncClient(timeout=10.0)
auth_service = AuthService(http_client=http_client)
logger = structlog.get_logger()


def _refresh_ttl_seconds() -> int:
    return settings.JWT_REFRESH_EXPIRE_DAYS * 24 * 60 * 60


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=bool(settings.REFRESH_COOKIE_SECURE),
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


def _raise_invalid_refresh(
    response: Response, detail: str = "Invalid refresh token"
) -> None:
    _clear_refresh_cookie(response)
    raise HTTPException(status_code=401, detail=detail)


async def _issue_refresh_token(*, user_id: int) -> str:
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
    return refresh_token


@router.get("/kakao/login", summary="카카오 로그인 시작")
async def kakao_login_start() -> RedirectResponse:
    if not settings.KAKAO_CLIENT_ID or not settings.KAKAO_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="Kakao OAuth env missing")

    query = urlencode(
        {
            "client_id": settings.KAKAO_CLIENT_ID,
            "redirect_uri": settings.KAKAO_REDIRECT_URI,
            "response_type": "code",
        }
    )
    return RedirectResponse(
        url=f"https://kauth.kakao.com/oauth/authorize?{query}",
        status_code=302,
    )


@router.get("/kakao/callback", summary="카카오 로그인 콜백")
async def kakao_callback_get(code: str = Query(..., min_length=1)) -> RedirectResponse:
    try:
        access_token, user, is_new = await auth_service.kakao_login(code=code)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not settings.FRONTEND_LOGIN_REDIRECT:
        raise HTTPException(status_code=500, detail="FRONTEND_LOGIN_REDIRECT missing")

    refresh_token = await _issue_refresh_token(user_id=int(user.id))

    redirect_url = (
        f"{settings.FRONTEND_LOGIN_REDIRECT}"
        f"?access_token={access_token}"
        f"&is_new_user={'true' if is_new else 'false'}"
    )
    response = RedirectResponse(url=redirect_url, status_code=302)
    _set_refresh_cookie(response, refresh_token)
    return response


@router.post(
    "/kakao/callback",
    response_model=AuthResponse,
    summary="카카오 로그인 콜백(프론트 전달용)",
)
async def kakao_callback_post(
    body: KakaoCallbackRequest, response: Response
) -> AuthResponse:
    try:
        access_token, user, is_new = await auth_service.kakao_login(code=body.code)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    refresh_token = await _issue_refresh_token(user_id=int(user.id))
    _set_refresh_cookie(response, refresh_token)

    return AuthResponse(
        access_token=access_token,
        user=AuthUserResponse(
            id=int(user.id),
            nickname=user.nickname,
            profile_image_url=user.profile_image_url,
            is_guest=user.is_guest,
        ),
        is_new_user=is_new,
    )


@router.post("/guest", response_model=AuthResponse, summary="게스트 로그인")
async def guest_login(_: Request, response: Response) -> AuthResponse:
    try:
        access_token, user = await auth_service.guest_login()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    refresh_token = await _issue_refresh_token(user_id=int(user.id))
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


@router.get("/session", summary="현재 세션 조회")
async def get_auth_session(auth: AuthUser = Depends(get_auth_user)) -> dict:
    user = await User.get_or_none(id=auth.user_id, deleted_at__isnull=True)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": int(user.id),
        "nickname": user.nickname,
        "profile_image_url": user.profile_image_url,
        "is_guest": user.is_guest,
    }


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
        _raise_invalid_refresh(response)

    sub = payload.get("sub")
    jti = payload.get("jti")
    if not sub or not jti:
        _raise_invalid_refresh(response)

    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        _raise_invalid_refresh(response)

    stored_user_id = await get_and_delete_refresh_session(str(jti))
    if stored_user_id is None or stored_user_id != str(user_id):
        _raise_invalid_refresh(response)

    user = await User.get_or_none(id=user_id, deleted_at__isnull=True)
    if not user:
        _raise_invalid_refresh(response)

    access_token = create_access_token(
        secret=settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
        exp_minutes=settings.JWT_ACCESS_EXPIRE_MINUTES,
        user_id=int(user.id),
        is_guest=user.is_guest,
    )
    new_refresh_token = await _issue_refresh_token(user_id=int(user.id))
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
        except (ExpiredSignatureError, InvalidTokenError):
            pass
        except Exception as e:
            logger.exception("refresh session revoke failed", error=str(e))

    _clear_refresh_cookie(response)
    return LogoutResponse(success=True)
