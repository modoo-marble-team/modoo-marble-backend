from __future__ import annotations

from fastapi import APIRouter, Depends

from app.game.state import get_game_state
from app.models.user import User
from app.presence import get_user_status, list_online
from app.redis_client import get_redis
from app.schemas.users import (
    CurrentUserContextResponse,
    UpdateNicknameRequest,
    UpdateNicknameResponse,
    UserMeResponse,
    UserStatsResponse,
)
from app.services.room_service import RoomService
from app.services.users_service import UsersService
from app.utils.auth_dep import AuthUser, get_auth_user
from app.utils.exceptions import GuestNotAllowedError
from app.utils.redis_keys import RedisKeys

router = APIRouter(prefix="/users", tags=["Users"])

users_service = UsersService()
room_service = RoomService()


def _raise_if_guest(auth: AuthUser) -> None:
    if auth.is_guest:
        raise GuestNotAllowedError()


def _build_me_response(user: User) -> UserMeResponse:
    return UserMeResponse(
        id=int(user.id),
        nickname=user.nickname,
        profile_image_url=user.profile_image_url,
        is_guest=user.is_guest,
        stats=UserStatsResponse(
            total_games=0,
            wins=0,
            losses=0,
        ),
    )


@router.get("/me", response_model=UserMeResponse)
async def get_me(auth: AuthUser = Depends(get_auth_user)) -> UserMeResponse:
    _raise_if_guest(auth)
    user = await users_service.get_me(user_id=auth.user_id)
    return _build_me_response(user)


@router.patch("/me/nickname", response_model=UpdateNicknameResponse)
async def patch_nickname(
    payload: UpdateNicknameRequest,
    auth: AuthUser = Depends(get_auth_user),
) -> UpdateNicknameResponse:
    _raise_if_guest(auth)
    nickname = await users_service.update_nickname(
        user_id=auth.user_id,
        nickname=payload.nickname,
    )
    return UpdateNicknameResponse(id=auth.user_id, nickname=nickname)


@router.get("/me/context", response_model=CurrentUserContextResponse)
async def get_me_context(
    auth: AuthUser = Depends(get_auth_user),
) -> CurrentUserContextResponse:
    redis = get_redis()

    room_id = await room_service._get_user_room_id(auth.user_id)
    room = await room_service.get_room(room_id) if room_id else None

    active_game_id = await redis.get(RedisKeys.user_active_game(auth.user_id))
    legacy_game_id = await redis.get(RedisKeys.user_game(auth.user_id))
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
