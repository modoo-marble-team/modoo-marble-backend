from __future__ import annotations

import asyncio

import socketio

TURN_TIMEOUT_SECONDS = 30

# 게임별 현재 타이머 작업을 저장. key = game_id
_timers: dict[str, asyncio.Task] = {}


async def _auto_end_turn(game_id: str, sio: socketio.AsyncServer) -> None:
    """30초 대기 후 자동 END_TURN 처리."""
    await asyncio.sleep(TURN_TIMEOUT_SECONDS)

    # 여기서 import하는 이유: 파일 상단에서 하면 순환 참조 발생
    from app.game.actions.end_turn import process_end_turn
    from app.game.state import (
        LockAcquisitionError,
        apply_patches,
        game_lock,
        get_game_state,
        save_game_state,
    )

    try:
        async with game_lock(game_id):
            state = await get_game_state(game_id)

            # 게임이 이미 끝났거나 없으면 무시
            if state is None or state["status"] != "playing":
                return

            player_id = state["current_player_id"]
            events, patches = process_end_turn(state, player_id)

            apply_patches(state, patches)
            state["revision"] += 1
            await save_game_state(game_id, state)

        # 락 해제 후 브로드캐스트
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

        # 다음 턴 타이머 시작
        start_turn_timer(game_id, sio)

    except LockAcquisitionError:
        pass  # 타이머 실패는 조용히 무시
    except ValueError:
        pass


def start_turn_timer(game_id: str, sio: socketio.AsyncServer) -> None:
    """새 턴 타이머를 시작한다. 기존 타이머가 있으면 먼저 취소."""
    cancel_turn_timer(game_id)
    task = asyncio.create_task(_auto_end_turn(game_id, sio))
    _timers[game_id] = task


def cancel_turn_timer(game_id: str) -> None:
    """진행 중인 턴 타이머를 취소한다."""
    task = _timers.pop(game_id, None)
    if task and not task.done():
        task.cancel()
