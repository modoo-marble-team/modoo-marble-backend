from __future__ import annotations

import socketio

from app.game.actions.end_turn import process_end_turn
from app.game.actions.roll_dice import process_roll_dice
from app.game.enums import ActionType, ServerEventType
from app.game.state import (
    LockAcquisitionError,
    apply_patches,
    game_lock,
    get_game_state,
    save_game_state,
)
from app.game.timer import cancel_turn_timer, start_turn_timer


def register_game_handlers(
    sio: socketio.AsyncServer,
    sid_to_user: dict[str, int],  # ← main.py의 _sid_to_user를 받음
) -> None:
    """게임 소켓 이벤트 핸들러를 등록한다."""

    @sio.event
    async def game_sync(sid: str, data: dict) -> None:
        """재접속 시 전체 상태(snapshot)를 전송한다."""
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

        await sio.emit(
            "game:patch",
            {
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
                "snapshot": state,
            },
            to=sid,
        )

    @sio.on("game:action")
    async def handle_game_action(sid: str, data: dict) -> None:
        """
        클라이언트의 게임 행동 요청을 처리한다.
        data 예시: { "gameId": "g1", "actionId": "uuid", "type": "ROLL_DICE", "payload": {} }
        """
        # ── 1. 요청한 유저 확인 ─────────────────────────
        user_id = sid_to_user.get(sid)
        if user_id is None:
            await sio.emit(
                "game:error",
                {"code": "AUTH_REQUIRED", "message": "인증이 필요합니다."},
                to=sid,
            )
            return

        game_id = data.get("gameId")
        action_id = data.get("actionId", "")
        action_type = data.get("type")

        if not game_id or not action_type:
            await sio.emit(
                "game:ack",
                {
                    "game_id": game_id,
                    "action_id": action_id,
                    "ok": False,
                    "error": {
                        "code": "INVALID_REQUEST",
                        "message": "gameId와 type이 필요합니다.",
                    },
                    "revision": -1,
                },
                to=sid,
            )
            return

        # ── 2. 락 획득 + 상태 처리 ──────────────────────
        events: list[dict] = []
        patches: list[dict] = []
        state = None

        try:
            async with game_lock(game_id):
                state = await get_game_state(game_id)

                if state is None:
                    await sio.emit(
                        "game:ack",
                        {
                            "game_id": game_id,
                            "action_id": action_id,
                            "ok": False,
                            "error": {
                                "code": "GAME_NOT_FOUND",
                                "message": "게임을 찾을 수 없습니다.",
                            },
                            "revision": -1,
                        },
                        to=sid,
                    )
                    return

                # ── 3. 액션 타입별 처리 ─────────────────
                if action_type == ActionType.ROLL_DICE:
                    events, patches = process_roll_dice(state, user_id)

                elif action_type == ActionType.END_TURN:
                    events, patches = process_end_turn(state, user_id)
                    cancel_turn_timer(game_id)

                else:
                    await sio.emit(
                        "game:ack",
                        {
                            "game_id": game_id,
                            "action_id": action_id,
                            "ok": False,
                            "error": {
                                "code": "UNKNOWN_ACTION",
                                "message": f"알 수 없는 액션: {action_type}",
                            },
                            "revision": state["revision"],
                        },
                        to=sid,
                    )
                    return

                # ── 4. 상태 업데이트 + 저장 ─────────────
                apply_patches(state, patches)
                state["revision"] += 1
                await save_game_state(game_id, state)

            # ── 5. END_TURN이면 다음 턴 타이머 시작 ────
            if action_type == ActionType.END_TURN:
                start_turn_timer(game_id, sio)

        except LockAcquisitionError:
            await sio.emit(
                "game:ack",
                {
                    "game_id": game_id,
                    "action_id": action_id,
                    "ok": False,
                    "error": {
                        "code": "RETRY_LATER",
                        "message": "잠시 후 다시 시도해주세요.",
                    },
                    "revision": -1,
                },
                to=sid,
            )
            return

        except ValueError as e:
            await sio.emit(
                "game:ack",
                {
                    "game_id": game_id,
                    "action_id": action_id,
                    "ok": False,
                    "error": {"code": "INVALID_ACTION", "message": str(e)},
                    "revision": state["revision"] if state else -1,
                },
                to=sid,
            )
            return

        # ── 6. 성공 응답 브로드캐스트 ───────────────────
        await sio.emit(
            "game:ack",
            {
                "game_id": game_id,
                "action_id": action_id,
                "ok": True,
                "error": None,
                "revision": state["revision"],
            },
            to=sid,
        )

        await sio.emit(
            "game:patch",
            {
                "game_id": game_id,
                "revision": state["revision"],
                "turn": state["turn"],
                "events": events,
                "patch": patches,
                "snapshot": None,
            },
            room=f"game:{game_id}",
        )
