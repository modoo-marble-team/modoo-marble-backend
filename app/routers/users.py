from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.presence import list_online
from app.schemas.users import UpdateNicknameRequest
from app.services.users_service import UsersService
from app.utils.auth_dep import AuthUser, get_auth_user

router = APIRouter(prefix="/users", tags=["Users"])
users_service = UsersService()


@router.get("/me")
async def get_me(auth: AuthUser = Depends(get_auth_user)) -> dict:
    if auth.is_guest:
        raise HTTPException(status_code=403, detail="Guest not allowed")

    try:
        user = await users_service.get_me(user_id=auth.user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return {
        "id": int(user.id),
        "nickname": user.nickname,
        "profile_image_url": user.profile_image_url,
        "is_guest": user.is_guest,
        "stats": {"total_games": 0, "wins": 0, "losses": 0},
    }


@router.patch("/me/nickname")
async def patch_nickname(
    payload: UpdateNicknameRequest, auth: AuthUser = Depends(get_auth_user)
) -> dict:
    if auth.is_guest:
        raise HTTPException(status_code=403, detail="Guest not allowed")

    try:
        user = await users_service.update_nickname(
            user_id=auth.user_id, nickname=payload.nickname
        )
    except ValueError as e:
        msg = str(e)
        if msg == "Invalid nickname":
            raise HTTPException(status_code=400, detail=msg) from e
        if msg == "Nickname already exists":
            raise HTTPException(status_code=409, detail=msg) from e
        raise HTTPException(status_code=404, detail=msg) from e

    return {"id": int(user.id), "nickname": user.nickname}


@router.get("/online")
async def get_online_users(_: AuthUser = Depends(get_auth_user)) -> dict:
    return {"users": await list_online()}
