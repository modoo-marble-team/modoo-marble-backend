from __future__ import annotations

import asyncio

import structlog

from app.game.enums import ServerEventType
from app.redis_client import get_redis

logger = structlog.get_logger()

RECONNECT_TIMEOUT_SECONDS = 30
_DISCONNECT_KEY_PREFIX = "game:disconnect:"
_reconnect_tasks: dict[str, asyncio.Task] = {}


def _task_key(game_id: str, user_id: int) -> str:
    return f"{game_id}:{user_id}"


async def mark_disconnected(
    *,
    game_id: str,
    user_id: int,
    sio,
    sid_to_user: dict[str, int],
) -> None:
    redis = get_redis()
    key = f"{_DISCONNECT_KEY_PREFIX}{game_id}:{user_id}"
    await redis.set(key, "disconnected", ex=RECONNECT_TIMEOUT_SECONDS + 5)

    task_key = _task_key(game_id, user_id)
    old_task = _reconnect_tasks.pop(task_key, None)
    if old_task and not old_task.done():
        old_task.cancel()

    # 다른 플레이어들에게 연결 끊김 알림
    await sio.emit(
        "game:patch",
        {
            "events": [
                {
                    "type": ServerEventType.PLAYER_DISCONNECTED,
                    "playerId": user_id,
                    "timeoutSeconds": RECONNECT_TIMEOUT_SECONDS,
                }
            ],
        },
        room=f"game:{game_id}",
    )

    task = asyncio.create_task(
        _auto_bankrupt_after_timeout(
            game_id=game_id,
            user_id=user_id,
            sio=sio,
            sid_to_user=sid_to_user,
        )
    )
    _reconnect_tasks[task_key] = task


async def mark_reconnected(*, game_id: str, user_id: int) -> bool:
    redis = get_redis()
    key = f"{_DISCONNECT_KEY_PREFIX}{game_id}:{user_id}"
    was_disconnected = await redis.delete(key)

    task_key = _task_key(game_id, user_id)
    task = _reconnect_tasks.pop(task_key, None)
    if task and not task.done():
        task.cancel()

    return bool(was_disconnected)


async def is_disconnected(*, game_id: str, user_id: int) -> bool:
    redis = get_redis()
    key = f"{_DISCONNECT_KEY_PREFIX}{game_id}:{user_id}"
    return await redis.exists(key) > 0


async def _auto_bankrupt_after_timeout(
    *,
    game_id: str,
    user_id: int,
    sio,
    sid_to_user: dict[str, int],
) -> None:
    try:
        await asyncio.sleep(RECONNECT_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return

    try:
        from app.game.actions.end_turn import process_end_turn
        from app.game.enums import PlayerState, ServerEventType
        from app.game.presentation import serialize_game_patch
        from app.game.rules import _bankrupt_player_events, _bankrupt_player_patches
        from app.game.state import (
            apply_patches,
            game_lock,
            get_game_state,
            save_game_state,
        )

        async with game_lock(game_id):
            state = await get_game_state(game_id)
            if state is None or state["status"] != "playing":
                return

            player = state["players"].get(str(user_id))
            if player is None or player["playerState"] == PlayerState.BANKRUPT:
                return

            patches = _bankrupt_player_patches(state, user_id)
            events = _bankrupt_player_events(user_id)
            events[0]["reason"] = "disconnect_timeout"

            apply_patches(state, patches)
            state["revision"] += 1

            alive_players = [
                p
                for p in state["players"].values()
                if p["playerState"] != PlayerState.BANKRUPT
            ]
            if len(alive_players) <= 1:
                winner_id = alive_players[0]["playerId"] if alive_players else None
                state["status"] = "finished"
                state["winner_id"] = winner_id
                patches.append({"op": "set", "path": "status", "value": "finished"})
                events.append(
                    {
                        "type": ServerEventType.GAME_OVER,
                        "reason": "last_survivor",
                        "winnerUserId": winner_id,
                    }
                )

            if state["current_player_id"] == user_id and state["status"] == "playing":
                end_events, end_patches = process_end_turn(state, user_id)
                events.extend(end_events)
                patches.extend(end_patches)
                apply_patches(state, end_patches)
                state["revision"] += 1

            await save_game_state(game_id, state)

            patch_payload = serialize_game_patch(state, events=events, patches=patches)
            await sio.emit("game:patch", patch_payload, room=f"game:{game_id}")

        logger.info(
            "재연결 타임아웃: 자동 파산 처리",
            game_id=game_id,
            user_id=user_id,
        )
    except Exception:
        logger.exception(
            "재연결 타임아웃 파산 처리 중 오류",
            game_id=game_id,
            user_id=user_id,
        )
    finally:
        redis = get_redis()
        key = f"{_DISCONNECT_KEY_PREFIX}{game_id}:{user_id}"
        await redis.delete(key)
        _reconnect_tasks.pop(_task_key(game_id, user_id), None)
