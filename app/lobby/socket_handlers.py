from __future__ import annotations

import socketio

from app.presence import update_status
from app.services.room_service import RoomService


def register_lobby_handlers(
    sio: socketio.AsyncServer,
    sid_to_user: dict[str, int],
) -> None:
    room_service = RoomService()

    @sio.on("enter_room")
    async def enter_room(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        room_id = str(data.get("room_id") or "")

        if user_id is None:
            await sio.emit(
                "game:error",
                {"code": "AUTH_REQUIRED", "message": "Authentication required."},
                to=sid,
            )
            return

        room = await room_service.get_room(room_id)
        if room is None:
            await sio.emit(
                "game:error",
                {"code": "ROOM_NOT_FOUND", "message": "Room not found."},
                to=sid,
            )
            return

        if not any(player["id"] == str(user_id) for player in room["players"]):
            await sio.emit(
                "game:error",
                {"code": "NOT_ROOM_MEMBER", "message": "You are not in this room."},
                to=sid,
            )
            return

        await sio.enter_room(sid, f"room:{room_id}")
        await update_status(user_id=str(user_id), status="in_room")

    @sio.on("leave_room")
    async def leave_room(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        room_id = str(data.get("room_id") or "")
        if room_id:
            await sio.leave_room(sid, f"room:{room_id}")
        if user_id is not None:
            await update_status(user_id=str(user_id), status="lobby")

    @sio.on("send_chat")
    async def send_chat(sid: str, data: dict) -> None:
        user_id = sid_to_user.get(sid)
        room_id = str(data.get("room_id") or "")
        message = str(data.get("message") or "").strip()

        if user_id is None:
            await sio.emit(
                "game:error",
                {"code": "AUTH_REQUIRED", "message": "Authentication required."},
                to=sid,
            )
            return

        if not room_id or not message:
            return

        _room, chat_message = await room_service.add_chat_message(
            room_id=room_id,
            user_id=user_id,
            message=message,
        )
        await sio.emit(
            "chat", {"room_id": room_id, **chat_message}, room=f"room:{room_id}"
        )
