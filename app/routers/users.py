from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.game.state import get_game_state
from app.presence import get_user_status, list_online
from app.redis_client import get_redis
from app.schemas.users import CurrentUserContextResponse, UpdateNicknameRequest
from app.services.room_service import RoomService
from app.services.users_service import UsersService
from app.utils.auth_dep import AuthUser, get_auth_user

router = APIRouter(prefix="/users", tags=["Users"])
users_service = UsersService()
room_service = RoomService()


@router.get("/me")
async def get_me(auth: AuthUser = Depends(get_auth_user)) -> dict:
    if auth.is_guest:
        raise HTTPException(status_code=403, detail="Guest not allowed")

    try:
        user, stats = await asyncio.gather(
            users_service.get_me(user_id=auth.user_id),
            users_service.get_stats(user_id=auth.user_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return {
        "id": int(user.id),
        "nickname": user.nickname,
        "profile_image_url": user.profile_image_url,
        "is_guest": user.is_guest,
        "stats": stats,
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


@router.get("/me/context", response_model=CurrentUserContextResponse)
async def get_me_context(
    auth: AuthUser = Depends(get_auth_user),
) -> CurrentUserContextResponse:
    redis = get_redis()

    room_id = await room_service._get_user_room_id(auth.user_id)
    room = await room_service.get_room(room_id) if room_id else None

    active_game_id = await redis.get(f"game:user:{auth.user_id}:active")
    legacy_game_id = await redis.get(f"user:{auth.user_id}:game")
    room_game_id = str(room.get("game_id")) if room and room.get("game_id") else None
    game_id = room_game_id or active_game_id or legacy_game_id

    if game_id and room is None:
        state = await get_game_state(game_id)
        if state is not None:
            room_id = state.room_id
            room = await room_service.get_room(room_id)

    if room is None:
        room_id = None

    room_status = str(room.get("status")) if room else None
    room_title = str(room.get("title")) if room else None
    presence_status = await get_user_status(str(auth.user_id))

    if game_id:
        resume_target = "game"
    elif room_id:
        resume_target = "room"
    else:
        resume_target = "lobby"

    return CurrentUserContextResponse(
        room_id=room_id,
        room_title=room_title,
        room_status=room_status,
        game_id=game_id,
        presence_status=presence_status,
        resume_target=resume_target,
    )


@router.get("/online")
async def get_online_users(_: AuthUser = Depends(get_auth_user)) -> dict:
    return {"users": await list_online()}
