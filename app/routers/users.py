from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from tortoise.exceptions import IntegrityError

from app.models.user import User
from app.presence import list_online
from app.utils.auth_dep import AuthUser, get_auth_user

router = APIRouter(prefix="/users", tags=["Users"])

_NICKNAME_RE = re.compile(r"^[0-9A-Za-z가-힣]{2,10}$")


class UpdateNicknameRequest(BaseModel):
    nickname: str = Field(..., min_length=2, max_length=10)


@router.get("/me")
async def get_me(auth: AuthUser = Depends(get_auth_user)) -> dict:
    if auth.is_guest:
        raise HTTPException(status_code=403, detail="Guest not allowed")

    user = await User.get_or_none(id=auth.user_id, deleted_at__isnull=True)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": int(user.id),
        "nickname": user.nickname,
        "profile_image_url": user.profile_image_url,
        "is_guest": user.is_guest,
        "stats": {
            "total_games": 0,
            "wins": 0,
            "losses": 0,
        },
    }


@router.patch("/me/nickname")
async def patch_nickname(
    payload: UpdateNicknameRequest, auth: AuthUser = Depends(get_auth_user)
) -> dict:
    if auth.is_guest:
        raise HTTPException(status_code=403, detail="Guest not allowed")

    if not _NICKNAME_RE.match(payload.nickname):
        raise HTTPException(status_code=400, detail="Invalid nickname")

    user = await User.get_or_none(id=auth.user_id, deleted_at__isnull=True)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.nickname = payload.nickname
    try:
        await user.save(update_fields=["nickname", "updated_at"])
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="Nickname already exists") from e

    return {"id": int(user.id), "nickname": user.nickname}


@router.get("/online")
async def get_online_users(_: AuthUser = Depends(get_auth_user)) -> dict:
    users = await list_online()
    return {"users": users}
