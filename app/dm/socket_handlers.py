from __future__ import annotations

from uuid import uuid4

import socketio

from app.presence import get_user_status

DM_MAX_LENGTH = 500


def register_dm_handlers(
    sio: socketio.AsyncServer,
    sid_to_user: dict[str, int],
) -> None:
    def _find_sid_by_user_id(user_id: int) -> str | None:
        for sid, uid in sid_to_user.items():
            if uid == user_id:
                return sid
        return None

    @sio.on("dm_send")
    async def dm_send(sid: str, data: dict) -> dict:
        """DM 전송 핸들러.

        Returns:
            ACK dict — {"ok": True, "message_id": "...", "client_message_id": "..."}
                     — {"ok": False, "code": "...", "message": "..."}
        """
        sender_id = sid_to_user.get(sid)
        if sender_id is None:
            await sio.emit(
                "dm:error",
                {"code": "AUTH_REQUIRED", "message": "인증이 필요합니다."},
                to=sid,
            )
            return {"ok": False, "code": "AUTH_REQUIRED", "message": "인증이 필요합니다."}

        receiver_id = data.get("receiver_id")
        message = str(data.get("message") or "").strip()
        client_message_id = data.get("client_message_id")

        if not message:
            await sio.emit(
                "dm:error",
                {"code": "DM_EMPTY_MESSAGE", "message": "메시지가 비어있습니다."},
                to=sid,
            )
            return {"ok": False, "code": "DM_EMPTY_MESSAGE", "message": "메시지가 비어있습니다."}

        if len(message) > DM_MAX_LENGTH:
            await sio.emit(
                "dm:error",
                {
                    "code": "DM_MESSAGE_TOO_LONG",
                    "message": f"메시지는 {DM_MAX_LENGTH}자 이내여야 합니다.",
                },
                to=sid,
            )
            return {
                "ok": False,
                "code": "DM_MESSAGE_TOO_LONG",
                "message": f"메시지는 {DM_MAX_LENGTH}자 이내여야 합니다.",
            }

        if receiver_id is None:
            await sio.emit(
                "dm:error",
                {"code": "DM_TARGET_NOT_FOUND", "message": "수신 대상을 지정해주세요."},
                to=sid,
            )
            return {"ok": False, "code": "DM_TARGET_NOT_FOUND", "message": "수신 대상을 지정해주세요."}

        receiver_id = int(receiver_id)
        if receiver_id == sender_id:
            await sio.emit(
                "dm:error",
                {
                    "code": "DM_TARGET_NOT_FOUND",
                    "message": "자기 자신에게 DM을 보낼 수 없습니다.",
                },
                to=sid,
            )
            return {
                "ok": False,
                "code": "DM_TARGET_NOT_FOUND",
                "message": "자기 자신에게 DM을 보낼 수 없습니다.",
            }

        sender_status = await get_user_status(str(sender_id))
        if sender_status == "playing":
            await sio.emit(
                "dm:error",
                {
                    "code": "DM_NOT_ALLOWED_IN_PLAYING",
                    "message": "게임 중에는 DM을 보낼 수 없습니다.",
                },
                to=sid,
            )
            return {
                "ok": False,
                "code": "DM_NOT_ALLOWED_IN_PLAYING",
                "message": "게임 중에는 DM을 보낼 수 없습니다.",
            }

        receiver_status = await get_user_status(str(receiver_id))
        if receiver_status is None:
            await sio.emit(
                "dm:error",
                {
                    "code": "DM_TARGET_OFFLINE",
                    "message": "상대방이 오프라인 상태입니다.",
                },
                to=sid,
            )
            return {
                "ok": False,
                "code": "DM_TARGET_OFFLINE",
                "message": "상대방이 오프라인 상태입니다.",
            }

        if receiver_status == "playing":
            await sio.emit(
                "dm:error",
                {
                    "code": "DM_NOT_ALLOWED_IN_PLAYING",
                    "message": "상대방이 게임 중이라 DM을 보낼 수 없습니다.",
                },
                to=sid,
            )
            return {
                "ok": False,
                "code": "DM_NOT_ALLOWED_IN_PLAYING",
                "message": "상대방이 게임 중이라 DM을 보낼 수 없습니다.",
            }

        sender_info = await _get_sender_info(sender_id, sid_to_user)

        message_id = f"msg-{uuid4().hex[:12]}"
        from datetime import UTC, datetime

        dm_payload = {
            "message_id": message_id,
            "sender_id": sender_id,
            "sender_nickname": sender_info["nickname"],
            "message": message,
            "sent_at": datetime.now(UTC).isoformat(),
        }
        if client_message_id:
            dm_payload["client_message_id"] = client_message_id

        await sio.emit("dm_receive", dm_payload, room=f"user:{receiver_id}")

        ack: dict = {"ok": True, "message_id": message_id}
        if client_message_id:
            ack["client_message_id"] = client_message_id
        return ack


async def _get_sender_info(sender_id: int, sid_to_user: dict) -> dict:
    from app.presence import get_user_info

    info = await get_user_info(str(sender_id))
    if info and info.get("nickname"):
        return {"nickname": info["nickname"]}
    return {"nickname": f"Player_{sender_id}"}
