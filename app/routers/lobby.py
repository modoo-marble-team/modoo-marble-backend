from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.errors import ApiError
from app.game.presentation import serialize_game_snapshot
from app.game.sync_runtime import leave_game_for_user
from app.game.timer import start_turn_timer
from app.schemas.lobby import (
    CreateRoomRequest,
    JoinRoomRequest,
    LeaveRoomResponse,
    RoomListResponse,
    RoomSnapshotResponse,
    StartGameResponse,
    ToggleReadyResponse,
)
from app.services.room_service import RoomService
from app.utils.auth_dep import AuthUser, get_auth_user

router = APIRouter()
room_service = RoomService()


async def _emit_lobby_updated(request: Request, action: str, room: dict) -> None:
    await request.app.state.sio.emit(
        "lobby_updated",
        {
            "action": action,
            "room": room_service.room_card(room),
        },
    )


async def _emit_room_updated(request: Request, room: dict) -> None:
    await request.app.state.sio.emit(
        "room_updated",
        room_service.room_snapshot(room),
        room=f"room:{room['id']}",
    )


@router.get("/rooms", response_model=RoomListResponse)
async def get_rooms(
    status: str | None = Query(default=None),
    exclude_private: bool = Query(default=False),
    keyword: str | None = Query(default=None),
) -> dict:
    rooms = await room_service.list_rooms(
        status=status,
        exclude_private=exclude_private,
        keyword=keyword,
    )
    return {"rooms": rooms, "total": len(rooms)}


@router.get("/rooms/{room_id}", response_model=RoomSnapshotResponse)
async def get_room_snapshot(
    room_id: str,
    auth: AuthUser = Depends(get_auth_user),
) -> dict:
    room = await room_service.get_room(room_id)
    if room is None:
        raise ApiError(
            status_code=404,
            code="ROOM_NOT_FOUND",
            message="방을 찾을 수 없습니다.",
        )

    member = next(
        (player for player in room["players"] if player["id"] == str(auth.user_id)),
        None,
    )
    if member is None:
        raise ApiError(
            status_code=403,
            code="NOT_ROOM_MEMBER",
            message="방 멤버가 아닙니다.",
        )

    return room_service.room_snapshot(room)


@router.post("/rooms", response_model=dict)
async def create_room(
    payload: CreateRoomRequest,
    request: Request,
    auth: AuthUser = Depends(get_auth_user),
) -> dict:
    room = await room_service.create_room(
        user_id=auth.user_id,
        title=payload.title,
        is_private=payload.is_private,
        password=payload.password,
        max_players=payload.max_players,
    )
    await _emit_lobby_updated(request, "created", room)
    return room_service.room_card(room)


@router.post("/rooms/{room_id}/join", response_model=RoomSnapshotResponse)
async def join_room(
    room_id: str,
    request: Request,
    payload: JoinRoomRequest | None = None,
    auth: AuthUser = Depends(get_auth_user),
) -> dict:
    room = await room_service.join_room(
        room_id=room_id,
        user_id=auth.user_id,
        password=payload.password if payload else None,
    )
    await _emit_lobby_updated(request, "updated", room)
    await _emit_room_updated(request, room)
    return room_service.room_snapshot(room)


@router.post("/rooms/{room_id}/leave", response_model=LeaveRoomResponse)
async def leave_room(
    room_id: str,
    request: Request,
    auth: AuthUser = Depends(get_auth_user),
) -> dict:
    room = await room_service.get_room(room_id)
    if room is None:
        raise ApiError(
            status_code=404,
            code="ROOM_NOT_FOUND",
            message="방을 찾을 수 없습니다.",
        )

    member = next(
        (player for player in room["players"] if player["id"] == str(auth.user_id)),
        None,
    )
    if member is None:
        raise ApiError(
            status_code=403,
            code="NOT_ROOM_MEMBER",
            message="방 멤버가 아닙니다.",
        )

    if room["status"] == "playing":
        game_id = str(room.get("game_id") or "")
        if not game_id:
            raise ApiError(
                status_code=409,
                code="GAME_NOT_PLAYING",
                message="진행 중인 게임이 아닙니다.",
            )

        predicted_new_host_id: str | None = None
        if member.get("is_host"):
            remaining_players = [
                player
                for player in room["players"]
                if player["id"] != str(auth.user_id)
            ]
            if remaining_players:
                predicted_new_host_id = str(remaining_players[0]["id"])

        left = await leave_game_for_user(game_id=game_id, user_id=auth.user_id)
        if not left:
            raise ApiError(
                status_code=409,
                code="GAME_LEAVE_FAILED",
                message="게임 나가기를 처리할 수 없습니다.",
            )
        return {"success": True, "new_host_id": predicted_new_host_id}

    room, new_host_id = await room_service.leave_room(
        room_id=room_id,
        user_id=auth.user_id,
    )
    if room is None:
        await request.app.state.sio.emit(
            "lobby_updated",
            {"action": "removed", "room": {"id": room_id}},
        )
        return {"success": True, "new_host_id": None}

    await _emit_lobby_updated(request, "updated", room)
    await _emit_room_updated(request, room)
    if new_host_id:
        new_host = next(
            player for player in room["players"] if player["id"] == new_host_id
        )
        await request.app.state.sio.emit(
            "host_changed",
            {
                "new_host_id": new_host_id,
                "new_host_nickname": new_host["nickname"],
            },
            room=f"room:{room_id}",
        )
    return {"success": True, "new_host_id": new_host_id}


@router.patch("/rooms/{room_id}/ready", response_model=ToggleReadyResponse)
async def toggle_ready(
    room_id: str,
    request: Request,
    auth: AuthUser = Depends(get_auth_user),
) -> dict:
    room, is_ready = await room_service.toggle_ready(
        room_id=room_id,
        user_id=auth.user_id,
    )
    await request.app.state.sio.emit(
        "player_ready",
        {
            "player_id": str(auth.user_id),
            "is_ready": is_ready,
            "all_ready": room_service.all_ready(room),
        },
        room=f"room:{room_id}",
    )
    await _emit_room_updated(request, room)
    return {"is_ready": is_ready}


@router.post("/rooms/{room_id}/start", response_model=StartGameResponse)
async def start_room_game(
    room_id: str,
    request: Request,
    auth: AuthUser = Depends(get_auth_user),
) -> dict:
    room, game_state = await room_service.start_game(
        room_id=room_id,
        user_id=auth.user_id,
    )
    await _emit_lobby_updated(request, "status_changed", room)
    start_turn_timer(game_state.game_id, request.app.state.sio)

    payload = {
        "game_id": game_state.game_id,
        "room_id": room_id,
        "game_state": serialize_game_snapshot(game_state),
    }
    await request.app.state.sio.emit("game_start", payload, room=f"room:{room_id}")
    for player in room["players"]:
        await request.app.state.sio.emit(
            "game_start", payload, room=f"user:{player['id']}"
        )

    return {"success": True, "game_id": game_state.game_id}
