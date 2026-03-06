from __future__ import annotations

from typing import Any

import httpx
from tortoise.exceptions import IntegrityError

from app.config import settings
from app.models.user import User
from app.utils.jwt import create_access_token
from app.utils.nickname import make_guest_nickname, make_unique_nickname


class AuthService:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    async def _exchange_code_for_token(self, *, code: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "grant_type": "authorization_code",
            "client_id": settings.KAKAO_CLIENT_ID,
            "redirect_uri": settings.KAKAO_REDIRECT_URI,
            "code": code,
        }
        if settings.KAKAO_CLIENT_SECRET:
            data["client_secret"] = settings.KAKAO_CLIENT_SECRET

        resp = await self._http.post(
            settings.KAKAO_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        )
        if resp.status_code >= 400:
            raise ValueError("Kakao token exchange failed")
        return resp.json()

    async def _fetch_kakao_me(self, *, kakao_access_token: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {kakao_access_token}"}
        resp = await self._http.get(settings.KAKAO_ME_URL, headers=headers)
        if resp.status_code >= 400:
            raise ValueError("Kakao user info failed")
        return resp.json()

    async def kakao_login(self, *, code: str) -> tuple[str, User, bool]:
        if not settings.KAKAO_CLIENT_ID or not settings.KAKAO_REDIRECT_URI:
            raise RuntimeError("Kakao OAuth env missing")

        token = await self._exchange_code_for_token(code=code)
        kakao_access = token.get("access_token")
        if not kakao_access:
            raise ValueError("Kakao access_token missing")

        me = await self._fetch_kakao_me(kakao_access_token=str(kakao_access))

        kakao_id = me.get("id")
        if kakao_id is None:
            raise ValueError("Kakao id missing")

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
            nickname = await make_unique_nickname(raw_nickname)
            try:
                user = await User.create(
                    kakao_id=kakao_id_str,
                    nickname=nickname,
                    profile_image_url=profile_image_url,
                    is_guest=False,
                )
                is_new_user = True
            except IntegrityError as e:
                raise ValueError("User creation conflict") from e

        access_token = create_access_token(
            secret=settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
            exp_minutes=settings.JWT_ACCESS_EXPIRE_MINUTES,
            user_id=int(user.id),
            is_guest=False,
        )
        return access_token, user, is_new_user

    async def guest_login(self) -> tuple[str, User]:
        nickname = await make_unique_nickname(make_guest_nickname())
        try:
            user = await User.create(nickname=nickname, is_guest=True)
        except IntegrityError as e:
            raise ValueError("Guest creation conflict") from e

        access_token = create_access_token(
            secret=settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
            exp_minutes=settings.JWT_ACCESS_EXPIRE_MINUTES,
            user_id=int(user.id),
            is_guest=True,
        )
        return access_token, user
