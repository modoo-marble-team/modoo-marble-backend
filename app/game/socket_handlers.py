from __future__ import annotations

import socketio

from app.game.enums import ServerEventType
from app.game.state import get_game_state


def register_game_handlers(sio: socketio.AsyncServer) -> None:
    """
    게임 소켓 이벤트 핸들러를 등록하는 함수.
    main.py에서 sio 객체를 받아서 핸들러를 붙인다.
    """

    @sio.event
    async def game_sync(sid: str, data: dict) -> None:
        """
        재접속 또는 상태 불일치 시 클라이언트가 보내는 이벤트.
        data 예시: { "gameId": "g1", "knownRevision": 40 }

        서버는 현재 전체 상태(snapshot)를 game:patch에 담아 응답한다.
        """
        game_id = data.get("gameId")
        known_revision = data.get("knownRevision", -1)

        if not game_id:
            await sio.emit(
                "game:error",
                {
                    "game_id": None,
                    "code": "INVALID_REQUEST",
                    "message": "gameId가 필요합니다.",
                },
                to=sid,
            )
            return

        state = await get_game_state(game_id)

        if state is None:
            await sio.emit(
                "game:error",
                {
                    "game_id": game_id,
                    "code": "GAME_NOT_FOUND",
                    "message": "게임을 찾을 수 없습니다.",
                },
                to=sid,
            )
            return

        patch_payload = {
            "game_id": game_id,
            "revision": state["revision"],
            "turn": state["turn"],
            "events": [
                {
                    "type": ServerEventType.SYNCED,
                    "known_revision": known_revision,
                    "current_revision": state["revision"],
                }
            ],
            "patch": [],
            "snapshot": state,  # 전체 상태를 통째로 전송
        }

        # 요청한 클라이언트(sid)에게만 전송
        await sio.emit("game:patch", patch_payload, to=sid)
