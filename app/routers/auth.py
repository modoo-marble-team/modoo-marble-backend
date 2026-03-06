from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.schemas.auth import AuthResponse, AuthUserResponse, KakaoCallbackRequest
from app.services.auth_service import AuthService

router = APIRouter(tags=["Auth"])
http_client = httpx.AsyncClient(timeout=10.0)
auth_service = AuthService(http_client=http_client)


@router.get("/kakao/callback", summary="카카오 로그인 콜백")
async def kakao_callback_get(code: str = Query(..., min_length=1)) -> RedirectResponse:
    try:
        access_token, _user, is_new = await auth_service.kakao_login(code=code)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not settings.FRONTEND_LOGIN_REDIRECT:
        raise HTTPException(status_code=500, detail="FRONTEND_LOGIN_REDIRECT missing")

    redirect_url = (
        f"{settings.FRONTEND_LOGIN_REDIRECT}"
        f"?access_token={access_token}"
        f"&is_new_user={'true' if is_new else 'false'}"
    )
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post(
    "/kakao/callback",
    response_model=AuthResponse,
    summary="카카오 로그인 콜백(프론트 전달용)",
)
async def kakao_callback_post(body: KakaoCallbackRequest) -> AuthResponse:
    try:
        access_token, user, is_new = await auth_service.kakao_login(code=body.code)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

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
async def guest_login(_: Request) -> AuthResponse:
    try:
        access_token, user = await auth_service.guest_login()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

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
